"""Jedyny moduł znający SDK modelu — reguła granic 12 (ADR-0011, decyzja 2).

Reguła ma dwie połowy i obie są sprawdzane skanem: **kto** tworzy klienta (tylko ten moduł)
oraz **z czym** — `api_key=` i `http_client=` jawnie w każdym wywołaniu. Druga połowa jest tu
ważniejsza, bo SDK bez niej sięga po poświadczenia z własnego łańcucha (`ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, profil `ant auth login` z dysku, zmienne federacyjne) i buduje własny
transport z `trust_env` na wartości domyślnej. Znaczyłoby to, że narzędzie wydaje cudze
poświadczenie przez gniazdo, którego nikt nie pilnuje, a `sprawdz-token` nie umie o tym
opowiedzieć.

Reguła granic 13 obowiązuje ten moduł tak samo jak resztę `assistant/`: nie ma tu importu
`client`, `store` ani `pipeline`, więc pobrane rekordy nie mają którędy tu trafić. Do modelu
idzie wyłącznie to, co złoży `prompt.py` — blok systemowy ze słownikiem i tura użytkownika
z dzisiejszą datą oraz zdaniem operatora (§B).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from ..config import register_secret
from ..errors import CeidgError, ConfigError, UntrustedLinkError
from ..httpclient import build_model_http_client
from ..progress import Events, NullEvents
from ..ratelimit import REASON_MODEL_RETRY
from . import AssistantResult
from .prompt import build_question, build_system
from .schema import AssistantAnswer, json_schema
from .translate import to_result

if TYPE_CHECKING:
    import anthropic

MODEL = "claude-opus-5"

# Sufit **z zapasem na myślenie**, nie na sam wynik. Opus 5 ma myślenie włączone domyślnie
# (pominięcie `thinking` znaczy „adaptive", inaczej niż w 4.7/4.8), a tokeny myślenia liczą się
# do `max_tokens`. Przy 2048 nad promptem ~25 tys. tokenów odpowiedź kończyłaby się
# `stop_reason="max_tokens"` **zanim** padłby JSON — i wracała do operatora jako „to nie jest
# poprawny JSON", czyli komunikat mylący co do przyczyny. Sufit nic nie kosztuje: płaci się za
# wygenerowane tokeny, nie za limit.
MAX_TOKENS = 16000

# `high` jest domyślne na Opus 5, a to zadanie jest proste: zamiana zdania na zestaw filtrów.
# ADR-0011 (decyzja 4) liczył koszt przy niskim wysiłku — implementacja musi go ustawić, żeby
# ta liczba była prawdziwa.
EFFORT = "low"

# Limit czasu jednego żądania. SDK domyślnie daje dziesięć minut — to nie jest liczba
# interaktywna: operator siedzi przed ekranem i czeka na jedno zdanie.
TIMEOUT_S = 90.0

# Jedno ponowienie, nie drabinka. Odmowa modelu jest inna niż limit CEIDG: nie ma tu budżetu do
# odczekania ani checkpointu do wznowienia, a asystent **nigdy nie jest krytyczny** — gdy się nie
# uda, kreator wraca do pytań po kolei i operator dochodzi do pliku bez niego.
RETRY_AFTER_S = 5.0

# Sufit żądań na **jedną interpretację**, nie na jedno wywołanie. Wcześniej ponowienie i próba
# naprawy mnożyły się (2 × 2 = 4), a docstring obiecywał dwa — i to właśnie ta liczba stoi
# w tabeli kosztów pokazywanej operatorowi. Budżet jest wspólny, więc obietnica jest prawdziwa
# niezależnie od tego, która ścieżka go zużyje.
MAX_ZADAN = 2


class AssistantUnavailableError(ConfigError):
    """Asystent nie może pracować (brak klucza, brak SDK, odmowa uwierzytelnienia).

    Osobny typ, bo wywołujący ma na to jedną reakcję: powiedzieć operatorowi, że asystenta nie
    ma, i przejść do pytań po kolei. `ConfigError` w rodzicach daje kod wyjścia 3 w CLI."""


class ModelRefusedError(CeidgError):
    """Model odpowiedział czymś, czego nie da się użyć — po jednej próbie naprawy."""


def _wycisz_sdk() -> None:
    """Loggery SDK poza nasz mechanizm maskowania — i poza żywy pasek postępu.

    `anthropic/__init__.py` woła `setup_logging()` **przy imporcie**: z `ANTHROPIC_LOG=debug`
    w środowisku instaluje to `logging.basicConfig()` (uchwyt na stderr) i ustawia `anthropic`
    oraz `httpx2` na DEBUG. Od tej chwili SDK loguje opcje żądań i nagłówki odpowiedzi na
    loggerze, którego `MaskingFormatter` nie widzi, a uchwyt pisze na stderr **pod** żywym
    paskiem `rich`. ADR-0011 (decyzja 7) obiecywał to wyciszyć; to jest ta obietnica.
    """
    os.environ.pop("ANTHROPIC_LOG", None)
    for nazwa in ("anthropic", "httpx2", "httpcore2"):
        logger = logging.getLogger(nazwa)
        logger.setLevel(logging.WARNING)
        logger.propagate = False


def _odmowa_bramki(exc: BaseException) -> UntrustedLinkError | None:
    """Szuka w łańcuchu przyczyn naszej odmowy wyjścia — SDK zawija ją w błąd połączenia."""
    przyczyna: BaseException | None = exc
    while przyczyna is not None:
        if isinstance(przyczyna, UntrustedLinkError):
            return przyczyna
        przyczyna = przyczyna.__cause__ or przyczyna.__context__
    return None


class AnthropicCaller:
    """Realizacja protokołu `Assistant` przez API modelu. Jedno pytanie, jedna odpowiedź."""

    def __init__(
        self,
        *,
        api_key: str,
        slownik: Mapping[str, str],
        http_client: anthropic.DefaultHttpxClient | Any | None = None,
        events: Events | None = None,
        model: str = MODEL,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover — zależy od instalacji, nie od kodu
            raise AssistantUnavailableError(
                "Asystent wymaga pakietu `anthropic`. Zainstaluj extrę: pip install -e .[asystent]"
            ) from exc
        if not api_key:
            raise AssistantUnavailableError(
                "Asystent wymaga klucza API — patrz `ceidg-tool sprawdz-token`."
            )

        _wycisz_sdk()
        # Wartość klucza do rejestru maskowania: `ANTHROPIC_KEY_RE` łapie kształt `sk-ant-…`,
        # ale rejestr jest tym mechanizmem, który uczynił maskowanie generycznym wobec sekretu,
        # a nie wobec jego wyglądu. Klucz podany tu wprost (testy, biblioteka) też ma być kryty.
        register_secret(api_key)
        self._slownik = slownik
        self._events: Events = events or NullEvents()
        self._model = model
        self._system = build_system(slownik)
        # `max_retries=0`: ponowienia prowadzimy sami, żeby **zapowiedzieć** pauzę przez `on_wait`.
        # Milczące czekanie w środku SDK to dokładnie ta cisza, którą CLAUDE.md nazywa defektem.
        self._client = anthropic.Anthropic(
            api_key=api_key,
            http_client=http_client or build_model_http_client(),
            max_retries=0,
            timeout=TIMEOUT_S,
        )

    def interpret(self, opis: str, *, dzisiaj: date) -> AssistantResult:
        """Zamienia zdanie na `AssistantResult`. Najwyżej `MAX_ZADAN` żądań — łącznie."""
        pytanie = build_question(opis, dzisiaj=dzisiaj)
        surowa, pozostalo = self._ask([{"role": "user", "content": pytanie}], MAX_ZADAN)
        try:
            return self._parse(surowa)
        except CeidgError as pierwszy:
            if pozostalo <= 0:
                raise ModelRefusedError(
                    f"Asystent nie zwrócił kryteriów, których dałoby się użyć: {pierwszy}"
                ) from pierwszy
            # Jedna próba naprawy: komunikat walidatora wraca jako druga tura nad **tym samym**
            # prefiksem, więc poprawka jest tania (cache). Budżet jest wspólny z ponowieniami,
            # więc suma nigdy nie przekroczy `MAX_ZADAN`.
            naprawa: list[Any] = [
                {"role": "user", "content": pytanie},
                {"role": "assistant", "content": surowa},
                {
                    "role": "user",
                    "content": f"Odpowiedź jest nie do użycia: {pierwszy}. Popraw ją.",
                },
            ]
            druga, _ = self._ask(naprawa, pozostalo)
            try:
                return self._parse(druga)
            except CeidgError as drugi:
                raise ModelRefusedError(
                    f"Asystent nie zwrócił kryteriów, których dałoby się użyć: {drugi}"
                ) from drugi

    # ------------------------------------------------------------------ środek

    def _ask(self, messages: list[Any], pozostalo: int) -> tuple[str, int]:
        """Żądanie strumieniowe z ponowieniem w ramach wspólnego budżetu.

        Zwraca tekst odpowiedzi i to, ile żądań jeszcze wolno wydać w tej interpretacji.
        """
        import anthropic

        while pozostalo > 0:
            pozostalo -= 1
            try:
                return self._stream(messages), pozostalo
            except anthropic.APIStatusError as exc:
                status = getattr(exc, "status_code", 0)
                if pozostalo <= 0 or not (status == 429 or status >= 500):
                    raise self._przetlumacz(exc) from exc
                self._poczekaj(exc)
            except anthropic.APIConnectionError as exc:
                # SDK zawija **każdy** wyjątek transportu w `APIConnectionError`
                # (`_base_client.py`), więc odmowa naszej bramki wyjścia przychodzi tu jako
                # „brak połączenia". Ponawianie hosta, który nigdy nie będzie dozwolony, jest
                # bezcelowe, a komunikat myliłby najbardziej wtedy, gdy najbardziej trzeba go
                # zrozumieć — np. przy ustawionym `ANTHROPIC_BASE_URL`.
                if odmowa := _odmowa_bramki(exc):
                    raise odmowa from exc
                if pozostalo <= 0:
                    raise AssistantUnavailableError(
                        "Brak połączenia z asystentem. Kreator działa dalej bez niego — wybierz "
                        "pytania po kolei."
                    ) from exc
                self._poczekaj(exc)
            except anthropic.AnthropicError as exc:
                # Domknięcie taksonomii: `APIResponseValidationError`, `RetryableError` i gołe
                # `AnthropicError` stoją poza gałęziami wyżej i wychodziły poza `CeidgError`,
                # czyli poza obsługę w `cli.py` — traceback zamiast zejścia do pytań po kolei.
                raise AssistantUnavailableError(
                    f"Asystent zawiódł ({type(exc).__name__}). Wybierz pytania po kolei."
                ) from exc
        raise AssistantUnavailableError("Asystent nie odpowiedział.")  # pragma: no cover

    def _poczekaj(self, exc: Exception) -> None:
        """Zapowiada pauzę i czeka. Długość bierze z `Retry-After`, gdy serwer ją poda."""
        odpowiedz = getattr(exc, "response", None)
        naglowek = getattr(odpowiedz, "headers", {}).get("retry-after") if odpowiedz else None
        try:
            czekaj = float(naglowek) if naglowek else RETRY_AFTER_S
        except ValueError:
            czekaj = RETRY_AFTER_S
        self._events.on_wait(czekaj, REASON_MODEL_RETRY, time.time() + czekaj)
        time.sleep(czekaj)

    def _stream(self, messages: list[Any]) -> str:
        """Strumieniuje odpowiedź i melduje postęp — napływające zdarzenia **są** oznaką życia.

        Iterujemy surowy strumień zdarzeń, a nie `text_stream`: ten drugi oddaje wyłącznie
        `text_delta`, a przy myśleniu adaptacyjnym (domyślnym na Opus 5) faza myślenia jest
        **większością** opóźnienia i nie niesie ani jednego tekstu. Operator dostawałby wtedy
        jedno zgłoszenie na starcie i ciszę do końca — dokładnie to, co CLAUDE.md nazywa defektem.
        """
        started = time.monotonic()
        kawalki: list[str] = []
        znakow = 0
        self._events.on_model(0.0, 0)
        with self._client.messages.stream(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": self._system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            # `cast`, bo `OutputConfigParam` to `TypedDict` z SDK, a schemat składamy z pydantica
            # w czasie wykonania — statyczne dopasowanie nic tu nie sprawdzi poza kształtem,
            # który i tak pilnuje `test_the_json_schema_is_closed`.
            output_config=cast(
                "Any",
                {
                    "format": {"type": "json_schema", "schema": json_schema()},
                    "effort": EFFORT,
                },
            ),
        ) as stream:
            for event in stream:
                delta = getattr(event, "delta", None)
                if delta is None:
                    self._events.on_model(time.monotonic() - started, znakow // 4)
                    continue
                rodzaj = getattr(delta, "type", "")
                if rodzaj == "text_delta":
                    tekst = str(delta.text)
                    kawalki.append(tekst)
                    znakow += len(tekst)
                elif rodzaj == "thinking_delta":
                    # Myślenie liczymy do tego samego wskaźnika, bo operator patrzy na sygnał
                    # życia, a nie na rozliczenie tokenów. Do 2026-09-07 liczył się wyłącznie
                    # `text_delta`, więc przez całą fazę myślenia — czyli przez większość
                    # oczekiwania — licznik stał w miejscu. Zdarzenia padały (poprawka fazy 4d),
                    # ale z niezmienną wartością, co na ekranie wygląda identycznie jak zwis.
                    # Znalazł to właściciel przy przejściu bramki 3.
                    znakow += len(str(getattr(delta, "thinking", "")))
                self._events.on_model(time.monotonic() - started, znakow // 4)
            wiadomosc = stream.get_final_message()

        # Cache czyta się albo nie — a gdy nie, każde pytanie płaci pełną cenę po cichu.
        self.ostatni_cache_read = getattr(wiadomosc.usage, "cache_read_input_tokens", None)
        if wiadomosc.stop_reason == "max_tokens":
            raise ModelRefusedError(
                f"Odpowiedź asystenta została ucięta na limicie {MAX_TOKENS} tokenów. "
                "To nie jest wina Twojego zapytania — zgłoś to jako usterkę narzędzia."
            )
        if wiadomosc.stop_reason == "refusal":
            raise ModelRefusedError("Asystent odmówił odpowiedzi na to zapytanie.")
        return "".join(kawalki)

    def _parse(self, surowa: str) -> AssistantResult:
        """Tekst → `AssistantAnswer` → `Criteria`. Każdy krok może odmówić własnym zdaniem."""
        try:
            dane = json.loads(surowa)
        except json.JSONDecodeError as exc:
            raise ModelRefusedError(
                f"Odpowiedź asystenta nie jest poprawnym JSON-em: {exc}"
            ) from exc
        try:
            answer = AssistantAnswer.model_validate(dane)
        except ValueError as exc:
            raise ModelRefusedError(f"Odpowiedź asystenta nie pasuje do schematu: {exc}") from exc
        return to_result(answer, self._slownik)

    @staticmethod
    def _przetlumacz(exc: Exception) -> CeidgError:
        """Błąd SDK jako nasze zdanie. Treść przechodzi przez maskowanie razem z resztą wyjścia."""
        import anthropic

        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return AssistantUnavailableError(
                "Klucz asystenta został odrzucony. Sprawdź go: `ceidg-tool sprawdz-token`."
            )
        if isinstance(exc, anthropic.RateLimitError):
            return AssistantUnavailableError(
                "Asystent odmawia z powodu limitu zapytań. Spróbuj za chwilę albo wybierz "
                "pytania po kolei."
            )
        return ModelRefusedError(f"Asystent odpowiedział błędem: {exc}")
