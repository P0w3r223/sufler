"""Narzędzia interpretera: definicje dla API + dyspozytor wywołań (czysta logika + Protocol I/O).

Model dostaje DWA narzędzia, oba WYŁĄCZNIE DO ODCZYTU. To nie jest oszczędność, tylko granica
bezpieczeństwa: wiadomość pracownika jest wejściem NIEZAUFANYM, więc rozdzielamy rozumowanie od
egzekwowania — model *proponuje*, kod *decyduje*. Konsekwencje w kształcie schematów:

- Żadne narzędzie nie przyjmuje ``user_id`` ani identyfikatora zespołu. Zakres (czyj grafik,
  który tydzień) ustala ``KontekstNarzedzi`` budowany przez orkiestrację z otwartego przypomnienia.
  Nie da się więc odpowiedzią wydobyć cudzego grafiku, bo nie ma pola, w które można by go wpisać.
- Żadne narzędzie nie zapisuje. Zapis do Shifts następuje wyłącznie po jawnym „tak" pracownika,
  poza pętlą modelu, i przechodzi przez ``reminders.guards.ensure_single_owner``.

Pusty wynik jest zawsze JAWNY (``{"pusto": true, "znaczenie": ...}``). Cicha pusta odpowiedź
czyta się jak awaria narzędzia i prowokuje ponowienia — a każda dodatkowa iteracja pętli to
kolejne wywołanie modelu opłacane przy KAŻDEJ wiadomości pracownika.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from powiadomienia_teams.agent import kalendarz

logger = logging.getLogger(__name__)

NARZEDZIE_GRAFIK = "shifts_read"
NARZEDZIE_KALENDARZ = "kalendarz_rozwiaz"

# Sufit długości wyrażenia czasowego. ``wyrazenie`` to JEDYNE pole, w które model wpisuje wolny
# tekst przepisany z wiadomości pracownika, a ``strict`` nie wspiera ``maxLength`` (patrz nagłówek
# ``agent.schema``) — czyli jedyne miejsce, w którym treść pracownika dociera do kodu bez ram.
# Realne wyrażenie („w przyszły czwartek", „od 15-go") ma kilkanaście znaków; dłuższe i tak nie
# zostanie rozpoznane, a bez sufitu ciąg kilku tysięcy cyfr wywracał parser dat (``int()`` ma
# w Pythonie limit 4300 cyfr).
_MAX_WYRAZENIE = 200

_TYGODNIE = ("docelowy", "poprzedni")
_AKCJE_GRAFIKU = ("bazowy", "proponowany", "zapisany", "wolne", "powody")
_AKCJE_KALENDARZA = ("dzien", "tydzien")


class OdczytNiedostepny(RuntimeError):
    """Nie udało się odczytać stanu z Shifts — wynik jest NIEZNANY, nie pusty.

    Rozróżnienie jest tu krytyczne, nie stylistyczne: pusta lista znaczy „pracownik nie ma
    zapisanej ani jednej zmiany", a na tej podstawie model proponuje wpisanie całego tygodnia.
    Gdyby awaria odczytu udawała pustkę, jedno mrugnięcie Graph kończyłoby się propozycją
    zdublowania istniejącego grafiku. Dyspozytor zamienia ten wyjątek na jawny błąd narzędzia.
    """


class GrafikReader(Protocol):
    """Dostęp do żywego stanu Shifts, zawężony do JEDNEJ osoby — adresata przypomnienia.

    Implementacja produkcyjna (``app``) opakowuje ``GraphClient``; testy podają atrapę. Zawężenie
    do jednej osoby jest w implementacji, nie w wywołaniu — model nie ma jak go rozszerzyć.
    """

    def zmiany(self, tydzien: str) -> list[dict[str, Any]]:
        """Zmiany tej osoby we wskazanym tygodniu: ``[{dzien, start, end, tryb}]``."""

    def wolne(self, tydzien: str) -> list[dict[str, Any]]:
        """Dni wolne tej osoby we wskazanym tygodniu: ``[{dzien, powod}]``."""

    def powody(self) -> list[dict[str, Any]]:
        """Powody czasu wolnego zespołu: ``[{kanoniczny, nazwa_w_zespole, dostepny}]``."""


@dataclass(frozen=True)
class KontekstNarzedzi:
    """Zakres, w którym działają narzędzia — ustalany przez KOD, nigdy przez model."""

    reader: GrafikReader
    proponowany: list[dict[str, Any]]  # gotowiec »jak w zeszłym tygodniu« (już policzony)
    week_start: date  # poniedziałek tygodnia docelowego
    dzis: date  # dzisiejsza data lokalna — punkt odniesienia dla „jutro", „za dwa tygodnie"
    # Grafik, który zapiszemy, jeśli pracownik nie poprosi o zmianę: po pierwszej poprawce jest to
    # grafik już uzgodniony, wcześniej — ten sam gotowiec co w `proponowany`. Rozdzielone, bo są to
    # DWIE różne rzeczy w dłuższej rozmowie, a nazywanie ich jednym słowem prowadziło model do
    # nanoszenia kolejnej poprawki na oryginał. Pusty = nic jeszcze nie uzgodniono.
    bazowy: list[dict[str, Any]] = field(default_factory=list)


def definicje() -> list[dict[str, Any]]:
    """Definicje narzędzi dla Messages API.

    Opisy mówią KIEDY wołać, nie tylko co narzędzie robi — to tam model podejmuje decyzję
    o wywołaniu, więc warunek uruchomienia musi stać obok schematu, a nie w promptcie systemowym.
    ``strict`` gwarantuje, że ``tool_use.input`` przejdzie walidację schematu po stronie API.
    """
    return [
        {
            "name": NARZEDZIE_GRAFIK,
            "description": (
                "Odczytaj stan grafiku pracownika, z którym rozmawiasz, w Microsoft Shifts. "
                'Zawołaj action="powody" ZANIM zaproponujesz jakikolwiek czas wolny — wolno Ci '
                "użyć tylko powodu oznaczonego jako dostępny w tym zespole. "
                'Zawołaj action="zapisany", gdy pracownik pyta, co ma w grafiku, twierdzi, że '
                "już go uzupełnił, albo gdy chcesz sprawdzić, czy Twoja propozycja czegoś nie "
                'nadpisuje. Zawołaj action="wolne", zanim dopiszesz urlop — dzień już objęty '
                "urlopem nie może dostać drugiego wpisu. "
                'action="bazowy" zwraca grafik, który zostanie zapisany, jeśli pracownik nie '
                "poprosi o zmianę — czyli to, co już z nim uzgodniono w tej rozmowie; to na NIM "
                'nanosisz kolejne poprawki. action="proponowany" zwraca pierwotny gotowiec »jak '
                "w zeszłym tygodniu«, ten sam, który pracownik zobaczył w pierwszej wiadomości od "
                "bota — sięgnij po niego, gdy pracownik chce wrócić do tego, jak było. "
                "To narzędzie NIE zapisuje niczego i nie ma jak zapisać — zapis do grafiku "
                "następuje dopiero po jawnym »tak« pracownika, poza Twoim zasięgiem."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_AKCJE_GRAFIKU),
                        "description": "Co odczytać.",
                    },
                    "tydzien": {
                        "type": "string",
                        "enum": list(_TYGODNIE),
                        "description": (
                            '"docelowy" = tydzień, o który bot pyta (domyślny wybór); '
                            '"poprzedni" = tydzień przed nim. Ignorowane dla action="powody".'
                        ),
                    },
                },
                "required": ["action", "tydzien"],
                "additionalProperties": False,
            },
        },
        {
            "name": NARZEDZIE_KALENDARZ,
            "description": (
                "Zamień wyrażenie czasowe napisane przez pracownika na konkretny dzień lub "
                "tydzień. Zawołaj ZAWSZE, gdy pracownik użył czegoś innego niż zwykła nazwa dnia "
                'tygodnia — „w przyszły czwartek", „od 15-go", „za dwa tygodnie", „jutro", '
                '„15.01". NIE licz dat samodzielnie: to narzędzie zna dzisiejszą datę i granice '
                "tygodnia, o który pytamy, a Ty nie. "
                "Odpowiedź zawiera pole w_zakresie — gdy jest false, pracownik mówi o INNYM "
                "tygodniu niż ten uzupełniany; nie przenoś takiego dnia na tydzień docelowy, "
                'tylko zwróć action="unclear" z powod_niejasnosci="inny_tydzien".'
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_AKCJE_KALENDARZA),
                        "description": (
                            '"dzien" = rozwiąż pojedynczy dzień; '
                            '"tydzien" = rozwiąż, o który tydzień chodzi.'
                        ),
                    },
                    "wyrazenie": {
                        "type": "string",
                        "description": (
                            "Fragment odpowiedzi pracownika opisujący czas, przepisany dosłownie, "
                            'np. "w przyszły czwartek", "od 15-go", "za dwa tygodnie".'
                        ),
                    },
                },
                "required": ["action", "wyrazenie"],
                "additionalProperties": False,
            },
        },
    ]


def _blad(narzedzie: str, komunikat: str, podpowiedz: str, *, akcja: str = "") -> dict[str, Any]:
    """Ustrukturyzowany błąd — model poprawia wywołanie bez ponownego czytania schematu."""
    wynik: dict[str, Any] = {
        "status": "blad",
        "narzedzie": narzedzie,
        "blad": komunikat,
        "podpowiedz": podpowiedz,
    }
    if akcja:
        wynik["action"] = akcja
    return wynik


def _pusto(znaczenie: str) -> dict[str, Any]:
    """Jawny pusty wynik — »nic tu nie ma« to informacja, nie awaria (patrz nagłówek modułu)."""
    return {"status": "ok", "pusto": True, "znaczenie": znaczenie}


def _lista(klucz: str, pozycje: list[dict[str, Any]], znaczenie_pustki: str) -> dict[str, Any]:
    if not pozycje:
        return _pusto(znaczenie_pustki)
    return {"status": "ok", "pusto": False, klucz: pozycje}


def _wykonaj_grafik(wejscie: dict[str, Any], ctx: KontekstNarzedzi) -> dict[str, Any]:
    akcja = str(wejscie.get("action", ""))
    tydzien = str(wejscie.get("tydzien") or "docelowy")
    if akcja not in _AKCJE_GRAFIKU:
        return _blad(
            NARZEDZIE_GRAFIK,
            f"nieznane action: {akcja!r}",
            f"dozwolone wartości: {', '.join(_AKCJE_GRAFIKU)}",
        )
    if tydzien not in _TYGODNIE:
        return _blad(
            NARZEDZIE_GRAFIK,
            f"nieznany tydzien: {tydzien!r}",
            f"dozwolone wartości: {', '.join(_TYGODNIE)}",
            akcja=akcja,
        )

    if akcja == "proponowany":
        return _lista(
            "zmiany",
            list(ctx.proponowany),
            "pracownik nie miał zmian w zeszłym tygodniu — grafik powstaje od zera, to normalne",
        )
    if akcja == "bazowy":
        return _lista(
            "zmiany",
            list(ctx.bazowy or ctx.proponowany),
            "nic jeszcze nie uzgodniono i nie ma gotowca — grafik powstaje od zera, to normalne",
        )
    try:
        if akcja == "zapisany":
            return _lista(
                "zmiany",
                ctx.reader.zmiany(tydzien),
                f"pracownik nie ma jeszcze żadnej zmiany zapisanej w tygodniu {tydzien}",
            )
        if akcja == "wolne":
            return _lista(
                "wolne",
                ctx.reader.wolne(tydzien),
                f"pracownik nie ma wpisanego czasu wolnego w tygodniu {tydzien}",
            )
        return _lista(
            "powody",
            ctx.reader.powody(),
            "zespół nie ma ANI JEDNEGO aktywnego powodu czasu wolnego — nie da się zapisać urlopu, "
            'zwróć action="unclear" z powod_niejasnosci="brak_powodu_wolnego"',
        )
    except OdczytNiedostepny:
        # Awaria odczytu MUSI być odróżnialna od pustki (patrz `OdczytNiedostepny`).
        return _blad(
            NARZEDZIE_GRAFIK,
            "nie udało się odczytać stanu z Shifts — wynik NIEZNANY, nie pusty",
            "nie zakładaj, że pracownik nie ma nic zapisane; oprzyj się na gotowcu i jego "
            'odpowiedzi, a przy wątpliwości zwróć action="unclear"',
            akcja=akcja,
        )


def _wykonaj_kalendarz(wejscie: dict[str, Any], ctx: KontekstNarzedzi) -> dict[str, Any]:
    akcja = str(wejscie.get("action", ""))
    wyrazenie = str(wejscie.get("wyrazenie", "")).strip()
    if akcja not in _AKCJE_KALENDARZA:
        return _blad(
            NARZEDZIE_KALENDARZ,
            f"nieznane action: {akcja!r}",
            f"dozwolone wartości: {', '.join(_AKCJE_KALENDARZA)}",
        )
    if not wyrazenie:
        return _blad(
            NARZEDZIE_KALENDARZ,
            "puste wyrazenie",
            "przepisz dosłownie fragment odpowiedzi pracownika opisujący czas",
            akcja=akcja,
        )
    if len(wyrazenie) > _MAX_WYRAZENIE:
        # Odmowa, a NIE ciche ucięcie. Ucinanie wygląda niewinnie, bo najczęściej kończy się
        # „nie rozpoznano", ale potrafi też zostawić fragment rozpoznawalny jako INNE wyrażenie:
        # „nie w przyszłym tygodniu, tylko 15.01" ucięte w złym miejscu daje pewną, ale błędną
        # datę. Pewna zła odpowiedź jest gorsza niż jawny błąd, na który model umie zareagować.
        return _blad(
            NARZEDZIE_KALENDARZ,
            f"wyrazenie dłuższe niż {_MAX_WYRAZENIE} znaków",
            "przepisz sam fragment opisujący czas, np. „w przyszły czwartek”, a nie całą wypowiedź",
            akcja=akcja,
        )

    try:
        return _rozwiaz(akcja, wyrazenie, ctx)
    except Exception:
        # Ostatnia siatka pod kontraktem „``wykonaj`` nigdy nie rzuca". Rozwiązywanie dat pracuje
        # na tekście przepisanym przez model z wiadomości pracownika, czyli na wejściu niezaufanym
        # o nieprzewidywalnym kształcie. Wyjątek stąd przerwałby obsługę CAŁEJ odpowiedzi, a że
        # watermark rośnie dopiero po sukcesie (``runtime.listener._commit``), ta sama wiadomość
        # wracałaby
        # w każdym ticku aż do wyczerpania licznika prób.
        logger.warning("Nie udało się rozwiązać wyrażenia czasowego %r", wyrazenie, exc_info=True)
        return _blad(
            NARZEDZIE_KALENDARZ,
            "nie udało się rozwiązać tego wyrażenia",
            "przepisz krótszy fragment odpowiedzi pracownika albo dopytaj go o konkretny dzień",
            akcja=akcja,
        )


def _rozwiaz(akcja: str, wyrazenie: str, ctx: KontekstNarzedzi) -> dict[str, Any]:
    if akcja == "dzien":
        dzien = kalendarz.rozwiaz_dzien(wyrazenie, week_start=ctx.week_start, dzis=ctx.dzis)
        if dzien is None:
            return {
                "status": "ok",
                "rozpoznano": False,
                "znaczenie": (
                    'nie da się jednoznacznie ustalić dnia — nie zgaduj, zwróć action="unclear" '
                    'z powod_niejasnosci="nieznany_dzien"'
                ),
            }
        return {
            "status": "ok",
            "rozpoznano": True,
            "dzien": kalendarz.PELNE_NAZWY_DNI[dzien.weekday],
            "data": dzien.data_iso,
            "poniedzialek_tygodnia": dzien.tydzien_iso,
            "w_zakresie": dzien.w_zakresie,
        }

    tydzien = kalendarz.rozwiaz_tydzien(wyrazenie, week_start=ctx.week_start, dzis=ctx.dzis)
    if tydzien is None:
        return {
            "status": "ok",
            "rozpoznano": False,
            "znaczenie": (
                "wyrażenie nie wskazuje żadnego tygodnia — nie zgaduj, dopytaj pracownika"
            ),
        }
    return {
        "status": "ok",
        "rozpoznano": True,
        "poniedzialek": tydzien.poniedzialek_iso,
        "przesuniecie_wzgledem_docelowego": tydzien.przesuniecie,
        "w_zakresie": tydzien.w_zakresie,
    }


def wykonaj(nazwa: str, wejscie: dict[str, Any], ctx: KontekstNarzedzi) -> dict[str, Any]:
    """Wykonaj wywołanie narzędzia. Błąd wraca do modelu jako DANE, nie jako wyjątek.

    Wyjątek z narzędzia przerwałby obsługę JEDNEJ odpowiedzi pracownika, a że watermark rośnie
    dopiero po sukcesie (``runtime.listener._commit``), ta sama wiadomość wracałaby w każdym ticku.
    Zwracamy
    więc opisany błąd i zostawiamy modelowi szansę na poprawkę w tej samej pętli.

    JEDYNY wyjątek, który stąd wychodzi, to utrata sesji (``AuthExpiredError`` z czytnika Graph)
    — i to celowo: dotyczy całej usługi, nie tej jednej odpowiedzi, więc ma zatrzymać obieg,
    a nie zamienić się w podpowiedź dla modelu (patrz ``agent.odczyt``). Wszystko inne jest
    przechwytywane: awaria odczytu przez ``OdczytNiedostepny``, a błędy rozwiązywania dat przez
    siatkę w ``_wykonaj_kalendarz``.
    """
    if nazwa == NARZEDZIE_GRAFIK:
        return _wykonaj_grafik(wejscie, ctx)
    if nazwa == NARZEDZIE_KALENDARZ:
        return _wykonaj_kalendarz(wejscie, ctx)
    return _blad(
        nazwa,
        f"nieznane narzędzie: {nazwa!r}",
        f"dostępne narzędzia: {NARZEDZIE_GRAFIK}, {NARZEDZIE_KALENDARZ}",
    )
