"""`AssistantAnswer` → `Criteria` — moduł czysty (ADR-0011, decyzja 3).

Tu realizuje się wymaganie instrukcji: „każdy kod PKD i każdy parametr przechodzi przez
walidator z `criteria.py`". Nie jako obietnica, tylko jako graf wywołań — jedyną drogą z
odpowiedzi modelu do pobrania jest ta funkcja, a ona kończy konstrukcją `Criteria`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from pydantic import ValidationError

from ..criteria import Criteria, bledy_po_polsku
from ..errors import ConfigError
from ..safetext import strip_control
from . import AssistantResult
from .pkd import validate_codes
from .schema import AssistantAnswer


def _parse_date(value: str | None, *, field: str) -> date | None:
    """Data w ISO albo `None`. Model dostaje w promptcie dzisiejszą datę i ma zwracać
    daty bezwzględne — „w zeszłym roku" liczy on, a nie my, bo tylko on zna kontekst zdania."""
    if value is None or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ConfigError(
            f"Asystent podał {field} jako {value!r}; oczekiwano daty w formacie RRRR-MM-DD."
        ) from exc


def to_criteria(answer: AssistantAnswer, slownik: Mapping[str, str]) -> Criteria:
    """Buduje `Criteria` z odpowiedzi modelu. Rzuca `ConfigError` z czytelnym zdaniem.

    Kolejność sprawdzeń: PKD wobec słownika, potem daty, potem cała reszta wewnątrz
    `Criteria`. Każdy z tych kroków może odmówić i każdy mówi wtedy, co konkretnie jest nie tak
    — bo to jest komunikat dla kogoś, kto nie zna API, a nie ślad w logu.
    """
    pkd = validate_codes(answer.pkd, slownik)
    try:
        return Criteria(
            nazwa=tuple(answer.nazwa),
            nip=tuple(answer.nip),
            regon=tuple(answer.regon),
            imie=tuple(answer.imie),
            nazwisko=tuple(answer.nazwisko),
            wojewodztwo=tuple(answer.wojewodztwo),
            powiat=tuple(answer.powiat),
            gmina=tuple(answer.gmina),
            miasto=tuple(answer.miasto),
            ulica=tuple(answer.ulica),
            kod=tuple(answer.kod),
            pkd=pkd,
            # `Criteria.status` jest typowane jako `Literal`, a od modelu przychodzi zwykły
            # napis — walidator `_status_upper` normalizuje go i odrzuca wartość spoza zbioru
            # w czasie wykonania, więc statyczne dopasowanie typu nic tu nie doda.
            status=tuple(answer.status),  # type: ignore[arg-type]
            data_od=_parse_date(answer.data_od, field="datę początkową"),
            data_do=_parse_date(answer.data_do, field="datę końcową"),
            szczegoly=answer.szczegoly,
        )
    except ValidationError as exc:
        raise ConfigError(
            f"Asystent zaproponował kryteria, których nie da się użyć:\n{bledy_po_polsku(exc)}"
        ) from exc


def to_result(answer: AssistantAnswer, slownik: Mapping[str, str]) -> AssistantResult:
    """Pełny wynik interpretacji: kryteria plus to, co ekran ma o nich powiedzieć.

    Parowanie kodu z nazwą siedzi tutaj, w warstwie czystej, a nie w module sieciowym — bo to
    jest ta własność, na której stoi całe potwierdzenie: nazwa pochodzi ze **słownika
    lokalnego**, nigdy od modelu. Zły kod jest dla operatora niewidoczny, zła nazwa branży
    rzuca się w oczy. Gdyby składanie wyniku zostało w `caller.py`, istniałby szew, przez
    który dałoby się podać nazwę skądinąd — i nic by tego nie sprawdzało.
    """
    kryteria = to_criteria(answer, slownik)
    pary = tuple((kod, slownik[kod]) for kod in kryteria.pkd)
    return AssistantResult(
        kryteria=kryteria,
        kody_pkd=pary,
        ograniczenia=tuple(answer.ograniczenia),
        pytanie=_przytnij(answer.pytanie, DLUGOSC_PYTANIA),
        propozycje=_propozycje(answer.propozycje),
    )


# --------------------------------------------------------------- runda dopytania (ADR-0017)

DLUGOSC_PYTANIA = 200
DLUGOSC_PROPOZYCJI = 120
MAKS_PROPOZYCJI = 4


def _przytnij(tekst: str, limit: int) -> str:
    """Ucina i **odkaża** zdanie od modelu: znaki sterujące won, długość pod sufit.

        To jest walidacja na granicy, nie ozdoba. `pytanie` i `propozycje` są jedynymi polami,
        w których model pisze **zdanie dla operatora**; bez sufitu jedna rozgadana odpowiedź
        zamienia menu wyboru w ścianę tekstu, a przy `propozycje` — w ścianę razy cztery.

        `strip_control` dołożone po przeglądzie 2026-09-09 i to jest tu ważniejsza połowa.
        `pytanie` trafia na ekran przez `Block`, więc łapie je `richtext.safe` (reguła granic 10),
        ale **propozycje idą do etykiet opcji**, a `ConsolePrompter.ask` renderuje je do
        `questionary`, a na ścieżce awaryjnej wprost do `input(...)` — czyli surowym zapisem na
        stdout, którego skan reguły 10 nie widzi, bo to nie jest wywołanie `print` ani `rich`.
        Samo `" ".join(split())` usuwa `
    ` i `	`, ale nie ESC, BEL ani backspace. Ten kanał
        powstał razem z rundą dopytania i cała jej argumentacja mówi, że gwarancja nie może
        opierać się na tym, że model się zachowa."""
    czysty = " ".join(strip_control(tekst).split())
    if len(czysty) <= limit:
        return czysty
    return czysty[: limit - 1].rstrip() + "…"


def _propozycje(surowe: tuple[str, ...]) -> tuple[str, ...]:
    """Gotowe zdania do wyboru: bez pustych, bez powtórzeń, najwyżej `MAKS_PROPOZYCJI`.

    Powtórzenia odsiewamy, bo dwie identyczne pozycje w menu są dla operatora usterką
    programu, a nie propozycją — i dlatego, że wybór między nimi niczego nie rozstrzyga."""
    out: list[str] = []
    for zdanie in surowe:
        krotkie = _przytnij(zdanie, DLUGOSC_PROPOZYCJI)
        if krotkie and krotkie not in out:
            out.append(krotkie)
        if len(out) >= MAKS_PROPOZYCJI:
            break
    return tuple(out)
