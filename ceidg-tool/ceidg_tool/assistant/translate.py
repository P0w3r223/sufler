"""`AssistantAnswer` → `Criteria` — moduł czysty (ADR-0011, decyzja 3).

Tu realizuje się wymaganie instrukcji: „każdy kod PKD i każdy parametr przechodzi przez
walidator z `criteria.py`". Nie jako obietnica, tylko jako graf wywołań — jedyną drogą z
odpowiedzi modelu do pobrania jest ta funkcja, a ona kończy konstrukcją `Criteria`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from pydantic import ValidationError

from ..criteria import Criteria
from ..errors import ConfigError
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
            f"Asystent zaproponował kryteria, których nie da się użyć:\n{_po_polsku(exc)}"
        ) from exc


def _po_polsku(exc: ValidationError) -> str:
    """Błędy pydantica jako zdania dla operatora, nie zrzut dla programisty.

    Surowy `ValidationError` niesie `[type=value_error, input_value=…, input_type=tuple]`
    i odnośnik do errors.pydantic.dev — dla kogoś, kto z założenia nie zna API, to zagadka.
    Decyzja 3 z ADR-0011 odrzuciła generowanie `Criteria` wprost przez model właśnie po to,
    żeby błąd walidacji wracał jako nasze zdanie; zrzut był połową tego wyniku."""
    linie = []
    for blad in exc.errors():
        pole = ".".join(str(part) for part in blad["loc"]) or "kryteria"
        # Pydantic dokleja do komunikatu własnego walidatora angielski prefiks
        # („Value error, "), więc nasze polskie zdanie zaczynałoby się od cudzego.
        tresc = blad["msg"].removeprefix("Value error, ").removeprefix("Assertion failed, ")
        linie.append(f"  {pole}: {tresc}")
    return "\n".join(linie)


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
    )
