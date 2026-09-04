"""Adapter LLM oparty o Claude API (Anthropic). Import ``anthropic`` jest LENIWY.

Wymaga extra ``agent`` (``anthropic``). Klucz API to sekret — czytany z konfiguracji
(``Settings.anthropic_api_key``), nigdy nie logowany.

Adapter jest cienki z premedytacją: cała logika obiegu (pętla narzędziowa, limity, decyzja) siedzi
w ``interpreter``, tutaj zostaje wyłącznie tłumaczenie na SDK i z powrotem. Dzięki temu testy
podstawiają atrapę spełniającą ``LlmClient`` i nie potrzebują ani ``anthropic``, ani sieci.
"""
from __future__ import annotations

import logging
from typing import Any

from powiadomienia_teams.agent.interpreter import (
    LlmNiedostepnyError,
    OdpowiedzLlm,
    WywolanieNarzedzia,
)

logger = logging.getLogger(__name__)

# Haiku 4.5 — szybka, tania ekstrakcja strukturalna. Obsługuje tool use i structured outputs,
# więc pętla narzędziowa działa bez zmiany modelu. Konfigurowalne przez POWIADOMIENIA_LLM_MODEL,
# gdyby interpretacja wymagała więcej mocy.
_DEFAULT_MODEL = "claude-haiku-4-5"
_TIMEOUT_S = 30.0  # patrz `_get_client` — chroni pętlę nasłuchu przed zawieszeniem
# Sufit wyjścia. 1024 wystarczało na jednostrzałowy JSON; z narzędziami jedna tura potrafi zawierać
# wywołanie narzędzia PLUS pełny tydzień w kontrakcie wyjścia, a ucięcie na limicie daje pusty
# tekst i degradację do »unclear« — czyli dokładnie ten cichy błąd, który usuwamy.
_MAX_TOKENS = 4096
# Wejście ma TRZY składniki, nie jeden. API podaje osobno tokeny policzone na nowo i te obsłużone
# cache'em promptu, a `input_tokens` obejmuje wyłącznie pierwsze — więc przy włączonym cache'owaniu
# sama ta liczba mówi o objętości promptu nieprawdę.
#
# Do logu idą WSZYSTKIE TRZY osobno, obok sumy, i to jest ustalenie przeglądu: te składniki mają
# różne ceny (zapis do cache'u ~1,25×, odczyt ~0,1×, reszta 1×), więc z samej sumy kosztu już się
# nie odtworzy — zawyżałaby go tym mocniej, im lepiej cache działa. Dziś cache'owania nie włączamy
# i dwa ostatnie pola są zerami, ale pomiar z pilotażu (C2) ma przeżyć decyzję o jego włączeniu:
# wtedy liczby będą już zebrane i nikt nie wróci do ich podstawy.
_POLA_WEJSCIA = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _odrzucono_wylaczone_myslenie(blad: Exception) -> bool:
    """Czy API odrzuciło żądanie WŁAŚNIE z powodu jawnie wyłączonego myślenia.

    Rozpoznajemy po kodzie 400 i wzmiance o ``thinking`` w treści, zamiast utrzymywać listę
    modeli: reguły różnią się między rodzinami (część nowszych odrzuca wyłączone myślenie zawsze,
    część powyżej pewnego poziomu wysiłku), a lista i tak zdezaktualizowałaby się przy pierwszym
    nowym modelu. Każdy inny błąd propaguje bez zmian.
    """
    if getattr(blad, "status_code", None) != 400:
        return False
    return "thinking" in str(blad).lower()


# Kody, przy których problem NIE leży w treści tej jednej wiadomości: brak/zły klucz, cofnięte
# uprawnienie, wyczerpany limit. 5xx dokładamy zakresem — lista konkretnych kodów dostawcy
# dezaktualizuje się szybciej niż reguła „to awaria po ich stronie".
_KODY_NIEDOSTEPNOSCI = frozenset({401, 403, 429})
# Rodziny wyjątków SDK oznaczające, że żądanie w ogóle nie doszło (DNS, TLS, zerwane połączenie,
# timeout). Rozpoznajemy po NAZWACH klas w MRO, żeby nie importować `anthropic` w tym miejscu —
# import jest leniwy z premedytacją (patrz `_get_client`) i ma taki zostać.
_RODZINY_BRAKU_POLACZENIA = frozenset({"APIConnectionError", "APITimeoutError"})


def _niedostepnosc_uslugi(blad: Exception) -> str:
    """Powód, dla którego to awaria GRANICY, a nie problem tej rozmowy. Pusty napis = nie wiadomo.

    Rozdział przebiega dokładnie tam, gdzie leży różnica dla pracownika. Awaria granicy dotyczy
    wszystkich naraz i musi dojść do operatora; błąd zależny od ŁADUNKU (400 o kształcie żądania,
    413 przy monstrualnej wiadomości) dotyczy jednej rozmowy i ma zostać w izolacji per-osoba —
    inaczej jeden pracownik z wklejonym dokumentem przerywałby obieg wszystkim pozostałym.

    ``4xx`` spoza listy celowo NIE jest niedostępnością: to najczęściej nasz własny ładunek, a więc
    coś, co naprawia zmiana kodu, nie czekanie.
    """
    kod = getattr(blad, "status_code", None)
    if isinstance(kod, int):
        if kod in _KODY_NIEDOSTEPNOSCI or kod >= 500:
            return f"HTTP {kod}"
        return ""
    if _RODZINY_BRAKU_POLACZENIA & {klasa.__name__ for klasa in type(blad).__mro__}:
        return "brak połączenia z API modelu"
    return ""


class AnthropicLlm:
    """Implementacja ``interpreter.LlmClient`` wołająca Claude Messages API.

    Klient Anthropic tworzony jest RAZ (leniwie) i reużywany — unika kosztu budowy puli
    połączeń przy każdej odpowiedzi, co przyspiesza kolejne interpretacje w listenerze.
    """

    def __init__(
        self, api_key: str, *, model: str = _DEFAULT_MODEL, max_tokens: int = _MAX_TOKENS
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client: Any = None
        # Czy wolno jawnie wyłączyć myślenie. Ustalane w locie przy pierwszej odmowie API — model
        # jest konfigurowalny (POWIADOMIENIA_LLM_MODEL), a reguły dla tego parametru zależą od
        # rodziny modelu. Patrz `_odrzucono_wylaczone_myslenie`.
        self._bez_myslenia = True

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            # Timeout JAWNIE: domyślne 10 min SDK × 2 ponowienia to do ~30 min zegara ściennego
            # wewnątrz obsługi JEDNEJ odpowiedzi. Przez ten czas nasłuch nie obsługuje nikogo
            # innego, a proces wygląda na zdrowy — najgorszy możliwy stan dla pracy bezobsługowej.
            self._client = anthropic.Anthropic(
                api_key=self._api_key, timeout=_TIMEOUT_S, max_retries=1
            )
        return self._client

    def _wywolaj(self, argumenty: dict[str, Any]) -> Any:
        """Jedno żądanie do API, z awarią granicy przetłumaczoną na ``LlmNiedostepnyError``.

        Tłumaczenie siedzi TU, a nie w wołającym, bo tylko adapter zna kształt wyjątków SDK —
        i tylko dzięki temu ``listener`` oraz pętla usługi mogą reagować na „model nie działa",
        nie wiedząc nic o Anthropicu.
        """
        try:
            return self._get_client().messages.create(**argumenty)
        except Exception as blad:
            powod = _niedostepnosc_uslugi(blad)
            if not powod:
                raise
            raise LlmNiedostepnyError(
                f"Interpretacja odpowiedzi niedostępna ({powod})"
            ) from blad

    def rozmawiaj(
        self,
        *,
        system: str,
        wiadomosci: list[dict[str, Any]],
        narzedzia: list[dict[str, Any]],
        schemat: dict[str, Any],
    ) -> OdpowiedzLlm:
        """Jedna tura modelu: wyślij historię, zwróć tekst i ewentualne żądania narzędzi.

        ``output_config.format`` wymusza kształt odpowiedzi po stronie API, więc wyjście nie
        wymaga już wyłuskiwania regexem. Narzędzia i wymuszony format działają w jednym żądaniu:
        tury z wywołaniem narzędzia niosą bloki ``tool_use``, a tura końcowa — zwalidowany JSON.
        """
        argumenty: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": wiadomosci,
            "output_config": {"format": {"type": "json_schema", "schema": schemat}},
        }
        if narzedzia:
            argumenty["tools"] = narzedzia
        if self._bez_myslenia:
            # Deterministyczna ekstrakcja strukturalna — bez myślenia, żeby nie zjadało budżetu
            # max_tokens (na nowszych modelach pominięcie thinking uruchamia myślenie adaptacyjne).
            argumenty["thinking"] = {"type": "disabled"}

        try:
            message = self._wywolaj(argumenty)
        except LlmNiedostepnyError:
            raise  # awaria granicy — nie ma czego ponawiać bez `thinking`
        except Exception as blad:
            if not self._bez_myslenia or not _odrzucono_wylaczone_myslenie(blad):
                raise
            # Model skonfigurowany przez POWIADOMIENIA_LLM_MODEL nie przyjmuje jawnie wyłączonego
            # myślenia (część nowszych modeli zwraca 400). Zapamiętujemy to i ponawiamy bez tego
            # parametru — inaczej podniesienie modelu wywracałoby KAŻDĄ interpretację, a pracownik
            # widziałby tylko „nie zrozumiałem" po trzech nieudanych próbach.
            logger.warning(
                "Model %s odrzucił wyłączone myślenie — ponawiam bez tego parametru.", self._model
            )
            self._bez_myslenia = False
            argumenty.pop("thinking")
            message = self._wywolaj(argumenty)

        zuzycie = getattr(message, "usage", None)
        skladniki_wejscia = tuple(int(getattr(zuzycie, pole, 0) or 0) for pole in _POLA_WEJSCIA)
        tokeny_wejscia = sum(skladniki_wejscia)
        tokeny_wyjscia = int(getattr(zuzycie, "output_tokens", 0) or 0)
        # Jedyne miejsce, w którym koszt tej rozmowy jest w ogóle znany. Do 0.2.17 liczba tokenów
        # miała jednego czytelnika (`interpreter.interpret_reply`) i tylko w gałęzi, w której model
        # NIE zwrócił treści — czyli przebieg udany nie zostawiał po sobie żadnego śladu kosztu.
        # Pilotaż (§7 planu, C2) zebrałby wtedy zero danych, a sufit z E1 („kontrola kosztu
        # modelu") powstałby bez jakiejkolwiek podstawy pomiarowej. Log wystarcza: pilotaż nie
        # potrzebuje trwałego licznika, ten wymaga koperty stanu (E0).
        # Wzorzec „tokeny wejścia=" należy WYŁĄCZNIE do tej linii i to nią liczy się przebiegi
        # (`interpret_reply` ma własny log z „tokeny wyjścia=", więc szukanie po samym wyjściu
        # policzyłoby nieudane interpretacje dwa razy — polecenie stoi w §7 planu).
        logger.info(
            "Model %s: tokeny wejścia=%d (nowe=%d, cache-zapis=%d, cache-odczyt=%d), wyjścia=%d",
            self._model,
            tokeny_wejscia,
            *skladniki_wejscia,
            tokeny_wyjscia,
        )

        return OdpowiedzLlm(
            tekst="".join(
                getattr(block, "text", "") for block in message.content
                if getattr(block, "type", "") == "text"
            ),
            narzedzia=tuple(
                WywolanieNarzedzia(
                    id=str(getattr(block, "id", "")),
                    nazwa=str(getattr(block, "name", "")),
                    wejscie=dict(getattr(block, "input", {}) or {}),
                )
                for block in message.content
                if getattr(block, "type", "") == "tool_use"
            ),
            # Bloki odsyłamy NIEZMIENIONE jako turę asystenta — model musi zobaczyć własne
            # wywołanie obok wyniku narzędzia, inaczej API odrzuci parę tool_use/tool_result.
            surowe=message.content,
            zatrzymanie=str(getattr(message, "stop_reason", "") or ""),
            tokeny_wyjscia=tokeny_wyjscia,
        )
