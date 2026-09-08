"""Spłaszczanie surowego JSON do stałego schematu kolumn (ADR-0005). Moduł czysty.

Każdy arkusz jest opisany listą `FieldSpec` — jedno miejsce definiuje kolejność kolumn,
typ Excela i polski opis, z którego generowany jest arkusz `Slownik`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from .records import ZRODLO_RAPORT, RawRecord, RowContext

Kind = Literal["text", "date", "int", "bool"]
Extractor = str | Callable[[Mapping[str, Any]], Any]

SHEET_FIRMY = "Firmy"
SHEET_PKD = "PKD"
SHEET_SPOLKI = "Spolki"
SHEET_ADRESY = "Adresy"
DATA_SHEETS: tuple[str, ...] = (SHEET_FIRMY, SHEET_PKD, SHEET_SPOLKI, SHEET_ADRESY)

MIN_COLUMN_WIDTH = 8
"""Dolna granica szerokości kolumny. Mieszka tu, a nie w eksporterze, bo jest częścią
kontraktu `FieldSpec.max_width` — inaczej walidacja i zapis miałyby dwie różne granice."""

_SNAKE_ASCII = re.compile(r"^[a-z][a-z0-9_]*$")
_GUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
CEIDG_PUBLIC_URL = "https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/SearchDetails.aspx?Id={id}"


@dataclass(frozen=True)
class FieldSpec:
    """Kolumna arkusza: nazwa techniczna, ekstraktor, typ Excela, opis po polsku."""

    name: str
    extractor: Extractor
    kind: Kind
    opis: str
    max_width: int | None = None
    """Górny limit szerokości kolumny w Excelu. Domyślnie decyduje o niej najdłuższa wartość
    (do `MAX_COLUMN_WIDTH`), co dla GUID-ów i adresów URL daje kolumny szerokie na 40-60 znaków,
    których nikt nie czyta w komórce. Limit dotyczy tylko wyświetlania — wartość jest pełna."""

    def __post_init__(self) -> None:
        if not _SNAKE_ASCII.fullmatch(self.name):
            raise ValueError(f"nazwa kolumny {self.name!r} musi być snake_case ASCII")
        # `max_width=0` przeszłoby przez `or` w eksporterze jako „bez limitu", a wartość
        # poniżej dolnej granicy szerokości zbiłaby kolumnę do nieczytelnej — oba po cichu.
        if self.max_width is not None and self.max_width < MIN_COLUMN_WIDTH:
            raise ValueError(
                f"max_width kolumny {self.name!r} musi wynosić co najmniej {MIN_COLUMN_WIDTH}"
            )


@dataclass(frozen=True)
class NormalizedRecord:
    """Jeden rekord po spłaszczeniu: wiersz `Firmy` i wiersze arkuszy powiązanych."""

    firmy: dict[str, Any]
    pkd: list[dict[str, Any]] = field(default_factory=list)
    spolki: list[dict[str, Any]] = field(default_factory=list)
    adresy: list[dict[str, Any]] = field(default_factory=list)


# ----------------------------------------------------------------------------- ekstrakcja


def get_path(data: Mapping[str, Any], path: str) -> Any:
    """Wartość spod ścieżki kropkowej; `None`, gdy któregokolwiek poziomu brakuje."""
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _first(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _pkd_items(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = rec.get("pkd")
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            kod = _first(item, "kod", "symbol")
            if kod is not None:
                items.append({"kod": str(kod), "nazwa": item.get("nazwa")})
        elif isinstance(item, str) and item:
            items.append({"kod": item, "nazwa": None})
    return items


def _pkd_key(kod: Any) -> str | None:
    """`62.01.Z` i `6201z` to ten sam kod — porównujemy po postaci zwartej."""
    return re.sub(r"[\s.]", "", str(kod)).upper() if kod is not None else None


def _pkd_glowny(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = rec.get("pkdGlowny")
    if not isinstance(raw, Mapping):
        return None
    kod = _first(raw, "kod", "symbol")
    if kod is None:
        return None
    return {"kod": str(kod), "nazwa": raw.get("nazwa")}


def _spolki_items(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = _first(rec, "spolki", "spolka")
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _adresy_items(rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = rec.get("adresyDzialalnosciDodatkowe")
    if isinstance(raw, Mapping):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, Mapping)]


def _adres_linia(adres: Mapping[str, Any] | None) -> str | None:
    if not isinstance(adres, Mapping):
        return None
    ulica = adres.get("ulica")
    numer = adres.get("budynek")
    if numer and adres.get("lokal"):
        numer = f"{numer}/{adres['lokal']}"
    kod_miasto = " ".join(str(p) for p in (adres.get("kod"), adres.get("miasto")) if p)
    parts = [" ".join(str(p) for p in (ulica, numer) if p), kod_miasto]
    line = ", ".join(p for p in parts if p)
    return line or None


def _public_link(record_id: Any) -> str | None:
    if isinstance(record_id, str) and _GUID.fullmatch(record_id):
        return CEIDG_PUBLIC_URL.format(id=record_id.lower())
    return None


def _liczba_spolek(rec: Mapping[str, Any]) -> int | None:
    """Puste, a nie zero, gdy rekord pochodzi z dziennego raportu.

    Raport nie niesie spółek cywilnych, więc zliczanie pustej listy dawało `0` w każdym
    wierszu — zdanie „ta firma nie ma spółki cywilnej", którego źródło nie jest w stanie
    powiedzieć. Filtr `liczba_spolek = 0` zwracał wtedy wszystko. W ścieżce API brak klucza
    `spolki` naprawdę znaczy zero, bo API pomija pola puste zamiast wysyłać `null`."""
    if rec.get("zrodlo") == ZRODLO_RAPORT:
        return None
    return len(_spolki_items(rec))


def _obywatelstwa(rec: Mapping[str, Any]) -> str | None:
    raw = rec.get("obywatelstwa")
    if not isinstance(raw, list):
        return None
    names = [
        str(_first(item, "kraj", "symbol"))
        for item in raw
        if isinstance(item, Mapping) and _first(item, "kraj", "symbol") is not None
    ]
    return "; ".join(names) or None


# ----------------------------------------------------------------------------- koercja typów


def _to_date(value: Any) -> date | str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for parser in (date.fromisoformat, lambda s: datetime.fromisoformat(s).date()):
        try:
            return parser(text[:10] if parser is date.fromisoformat else text)
        except ValueError:
            continue
    return text  # niezrozumiały format zostaje jako tekst, nie znika


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "tak", "t", "yes")


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value if v is not None) or None
    text = str(value)
    return text if text != "" else None


def coerce(value: Any, kind: Kind) -> Any:
    if kind == "date":
        return _to_date(value)
    if kind == "int":
        return _to_int(value)
    if kind == "bool":
        return _to_bool(value)
    return _to_text(value)


def extract(data: Mapping[str, Any], spec: FieldSpec) -> Any:
    raw = (
        get_path(data, spec.extractor) if isinstance(spec.extractor, str) else spec.extractor(data)
    )
    return coerce(raw, spec.kind)


# ----------------------------------------------------------------------------- schemat arkuszy

PROVENANCE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("zrodlo", "zrodlo", "text", "Pochodzenie wiersza: CEIDG_API albo CEIDG_RAPORT"),
    FieldSpec("srodowisko", "srodowisko", "text", "Środowisko API: test albo prod"),
    FieldSpec("pobrano_utc", "pobrano_utc", "text", "Data i czas pobrania rekordu (UTC, ISO 8601)"),
)

FIRMY_FIELDS: tuple[FieldSpec, ...] = (
    # Kolejność nie jest przypadkowa. `nip` i `nazwa` stoją pierwsze, bo to one zostają
    # zamrożone przy przewijaniu w prawo (FREEZE_AFTER): arkusz ma 43 kolumny i bez tego
    # po dojściu do `telefon` nie widać już, czyj to telefon. `id` to GUID do łączenia
    # arkuszy, a nie do czytania, więc siedzi w bloku technicznym przy `link`.
    FieldSpec("nip", "wlasciciel.nip", "text", "NIP przedsiębiorcy"),
    FieldSpec("nazwa", "nazwa", "text", "Pełna nazwa firmy", max_width=45),
    FieldSpec("regon", "wlasciciel.regon", "text", "REGON przedsiębiorcy"),
    FieldSpec("imie", "wlasciciel.imie", "text", "Imię przedsiębiorcy"),
    FieldSpec("nazwisko", "wlasciciel.nazwisko", "text", "Nazwisko przedsiębiorcy"),
    FieldSpec(
        "status",
        "status",
        "text",
        "Status działalności wg CEIDG (AKTYWNY, ZAWIESZONY, WYKRESLONY, …)",
    ),
    FieldSpec("data_rozpoczecia", "dataRozpoczecia", "date", "Data rozpoczęcia działalności"),
    FieldSpec("data_zawieszenia", "dataZawieszenia", "date", "Data zawieszenia działalności"),
    FieldSpec("data_wznowienia", "dataWznowienia", "date", "Data wznowienia działalności"),
    FieldSpec("data_zakonczenia", "dataZakonczenia", "date", "Data zakończenia działalności"),
    FieldSpec("data_wykreslenia", "dataWykreslenia", "date", "Data wykreślenia z rejestru"),
    FieldSpec("ulica", "adresDzialalnosci.ulica", "text", "Adres działalności: ulica"),
    FieldSpec("budynek", "adresDzialalnosci.budynek", "text", "Adres działalności: numer budynku"),
    FieldSpec("lokal", "adresDzialalnosci.lokal", "text", "Adres działalności: numer lokalu"),
    FieldSpec("miasto", "adresDzialalnosci.miasto", "text", "Adres działalności: miejscowość"),
    FieldSpec("kod_pocztowy", "adresDzialalnosci.kod", "text", "Adres działalności: kod pocztowy"),
    FieldSpec("gmina", "adresDzialalnosci.gmina", "text", "Adres działalności: gmina"),
    FieldSpec("powiat", "adresDzialalnosci.powiat", "text", "Adres działalności: powiat"),
    FieldSpec(
        "wojewodztwo", "adresDzialalnosci.wojewodztwo", "text", "Adres działalności: województwo"
    ),
    FieldSpec("kraj", "adresDzialalnosci.kraj", "text", "Adres działalności: kod kraju"),
    FieldSpec("terc", "adresDzialalnosci.terc", "text", "Kod TERC (gmina) adresu działalności"),
    FieldSpec(
        "simc", "adresDzialalnosci.simc", "text", "Kod SIMC (miejscowość) adresu działalności"
    ),
    FieldSpec("ulic", "adresDzialalnosci.ulic", "text", "Kod ULIC (ulica) adresu działalności"),
    FieldSpec(
        "adres_korespondencyjny",
        lambda r: _adres_linia(r.get("adresKorespondencyjny")),
        "text",
        "Adres korespondencyjny w jednej linii",
    ),
    FieldSpec("telefon", "telefon", "text", "Numer telefonu (jeśli przedsiębiorca podał)"),
    FieldSpec("email", "email", "text", "Adres e-mail (jeśli przedsiębiorca podał)"),
    FieldSpec("www", "www", "text", "Adres strony WWW (jeśli przedsiębiorca podał)"),
    FieldSpec(
        "adres_doreczen_elektronicznych",
        "adresDoreczenElektronicznych",
        "text",
        "Adres do doręczeń elektronicznych (e-Doręczenia)",
    ),
    FieldSpec(
        "pkd_glowny_kod",
        lambda r: (_pkd_glowny(r) or {}).get("kod"),
        "text",
        "Kod przeważającej działalności PKD",
    ),
    FieldSpec(
        "pkd_glowny_nazwa",
        lambda r: (_pkd_glowny(r) or {}).get("nazwa"),
        "text",
        "Nazwa przeważającej działalności PKD",
    ),
    FieldSpec(
        "pkd_wszystkie",
        lambda r: ";".join(item["kod"] for item in _pkd_items(r)) or None,
        "text",
        "Wszystkie kody PKD rozdzielone średnikiem",
    ),
    FieldSpec("liczba_pkd", lambda r: len(_pkd_items(r)), "int", "Liczba kodów PKD we wpisie"),
    FieldSpec(
        "liczba_spolek",
        _liczba_spolek,
        "int",
        "Liczba spółek cywilnych we wpisie; puste, gdy źródło (dzienny raport) tego nie podaje",
    ),
    FieldSpec("obywatelstwa", _obywatelstwa, "text", "Obywatelstwa przedsiębiorcy"),
    FieldSpec(
        "wspolnosc_majatkowa",
        "wspolnoscMajatkowa",
        "int",
        "Małżeńska wspólność majątkowa: 0 = nie, 1 = tak, 2 = nie dotyczy",
    ),
    FieldSpec("rok_pkd", "rokPkd", "text", "Rok klasyfikacji PKD (2007 lub 2025)"),
    FieldSpec(
        "id",
        "id",
        "text",
        "Identyfikator wpisu w CEIDG (GUID); klucz do arkuszy PKD, Spolki i Adresy",
        max_width=18,
    ),
    FieldSpec(
        "link",
        "link",
        "text",
        "Link do danych szczegółowych wpisu w API (wymaga tokenu)",
        max_width=24,
    ),
    FieldSpec(
        "link_ceidg",
        lambda r: _public_link(r.get("id")),
        "text",
        "Link do wpisu w publicznej wyszukiwarce CEIDG — do ręcznej weryfikacji w przeglądarce",
        max_width=24,
    ),
    FieldSpec(
        "dane_szczegolowe",
        "dane_szczegolowe",
        "bool",
        "Czy pobrano dane szczegółowe z API (/firma); dla źródła CEIDG_RAPORT kolumny PKD "
        "i kontaktowe pochodzą z raportu, a adres korespondencyjny, obywatelstwa i spółki "
        "są niedostępne",
    ),
)

_CHILD_KEY_FIELDS: tuple[FieldSpec, ...] = (
    # Ten sam limit co w `Firmy.id`, i to nie dla symetrii: w arkuszach podrzędnych `id` stoi
    # w kolumnie A, a `FREEZE_AFTER` zamraża tam A i B — bez limitu 38-znakowy GUID zostawał
    # przypięty do ekranu na stałe, czyli dokładnie ta wada, którą zamrożenie miało usunąć.
    FieldSpec("id", "id", "text", "Identyfikator wpisu (klucz do arkusza Firmy)", max_width=18),
    FieldSpec("nip", "nip", "text", "NIP przedsiębiorcy (dla wygody filtrowania)"),
)

PKD_FIELDS: tuple[FieldSpec, ...] = _CHILD_KEY_FIELDS + (
    FieldSpec("kolejnosc", "kolejnosc", "int", "Pozycja kodu PKD we wpisie (1 = pierwszy)"),
    FieldSpec("pkd_kod", "kod", "text", "Kod PKD"),
    FieldSpec("pkd_nazwa", "nazwa", "text", "Nazwa kodu PKD"),
    FieldSpec("czy_glowny", "czy_glowny", "bool", "Czy to przeważająca działalność"),
)

SPOLKI_FIELDS: tuple[FieldSpec, ...] = _CHILD_KEY_FIELDS + (
    FieldSpec("spolka_nip", "nip_spolki", "text", "NIP spółki cywilnej"),
    FieldSpec("spolka_regon", "regon_spolki", "text", "REGON spółki cywilnej"),
    FieldSpec(
        "spolka_data_zawieszenia", "data_zawieszenia", "date", "Data zawieszenia spółki cywilnej"
    ),
)

ADRESY_FIELDS: tuple[FieldSpec, ...] = _CHILD_KEY_FIELDS + (
    FieldSpec("kolejnosc", "kolejnosc", "int", "Pozycja adresu dodatkowego we wpisie"),
    FieldSpec("ulica", "ulica", "text", "Adres dodatkowy: ulica"),
    FieldSpec("budynek", "budynek", "text", "Adres dodatkowy: numer budynku"),
    FieldSpec("lokal", "lokal", "text", "Adres dodatkowy: numer lokalu"),
    FieldSpec("miasto", "miasto", "text", "Adres dodatkowy: miejscowość"),
    FieldSpec("kod_pocztowy", "kod", "text", "Adres dodatkowy: kod pocztowy"),
    FieldSpec("gmina", "gmina", "text", "Adres dodatkowy: gmina"),
    FieldSpec("powiat", "powiat", "text", "Adres dodatkowy: powiat"),
    FieldSpec("wojewodztwo", "wojewodztwo", "text", "Adres dodatkowy: województwo"),
    FieldSpec("kraj", "kraj", "text", "Adres dodatkowy: kod kraju"),
    FieldSpec("terc", "terc", "text", "Adres dodatkowy: kod TERC"),
    FieldSpec("simc", "simc", "text", "Adres dodatkowy: kod SIMC"),
    FieldSpec("ulic", "ulic", "text", "Adres dodatkowy: kod ULIC"),
    FieldSpec("opis", "opisNietypowegoMiejsca", "text", "Opis nietypowego miejsca"),
)

SHEETS: Mapping[str, tuple[FieldSpec, ...]] = {
    SHEET_FIRMY: FIRMY_FIELDS + PROVENANCE_FIELDS,
    SHEET_PKD: PKD_FIELDS + PROVENANCE_FIELDS,
    SHEET_SPOLKI: SPOLKI_FIELDS + PROVENANCE_FIELDS,
    SHEET_ADRESY: ADRESY_FIELDS + PROVENANCE_FIELDS,
}

FREEZE_AFTER: Mapping[str, str] = {
    SHEET_FIRMY: "nazwa",
    SHEET_PKD: "nip",
    SHEET_SPOLKI: "nip",
    SHEET_ADRESY: "nip",
}
"""Ostatnia kolumna, która zostaje na ekranie przy przewijaniu w prawo. Samo zamrożenie
nagłówka nie wystarcza przy 43 kolumnach: po dojściu do kontaktów albo PKD widać wartości
bez wiersza, do którego należą. Kolumna musi istnieć w schemacie arkusza — pilnuje tego test."""


def columns(sheet: str) -> tuple[str, ...]:
    return tuple(spec.name for spec in SHEETS[sheet])


def slownik_rows() -> list[tuple[str, str, str]]:
    """(arkusz, kolumna, opis) dla każdej kolumny każdego arkusza z danymi."""
    return [(sheet, spec.name, spec.opis) for sheet, specs in SHEETS.items() for spec in specs]


# ----------------------------------------------------------------------------- normalizacja


def merge_sources(raw: RawRecord) -> dict[str, Any]:
    """Świeższe źródło wygrywa na wspólnych polach; starsze uzupełnia braki.

    Do 2026-09-08 szczegół wygrywał **zawsze**, bez patrzenia na czas pobrania. To jest
    poprawne dopóty, dopóki szczegół jest młodszy — i fałszywe, gdy nie jest. Zmierzony objaw
    (audyt, A5): wiersz eksportu mówił `status = 'AKTYWNY'`, podczas gdy `list_json` **tego
    samego wiersza** i kolumna indeksowa `status_api` mówiły już `WYKRESLONY`. Skoroszyt
    zaprzeczał wtedy danym, z których powstał, a operator nie ma jak tego zauważyć.

    Zdarza się to normalnie: szczegóły siedzą w cache, a `pobierz` odświeża listę przy każdym
    przebiegu, więc lista bywa młodsza od szczegółu o tyle, ile minęło między nimi.

    `None` nadal nie kasuje wartości — brak pola w świeższym źródle znaczy „nie wiem", a nie
    „puste"; to jest ta sama reguła co dotąd, tylko po właściwej stronie porównania.
    """
    lista, szczegol = raw.list_json or {}, raw.detail_json or {}
    # Porównanie napisów ISO jest tu bezpieczne: oba znaczniki zapisuje `utc_iso`, więc mają
    # ten sam kształt i tę samą strefę. Przy braku któregokolwiek zostaje dotychczasowa
    # kolejność — szczegół jest wtedy jedynym źródłem albo jedynym datowanym.
    lista_swiezsza = bool(raw.list_utc and raw.detail_utc and raw.list_utc > raw.detail_utc)
    starsze, mlodsze = (szczegol, lista) if lista_swiezsza else (lista, szczegol)
    merged: dict[str, Any] = dict(starsze)
    for key, value in mlodsze.items():
        if value is not None or key not in merged:
            merged[key] = value
    return merged


def _row(data: Mapping[str, Any], specs: tuple[FieldSpec, ...]) -> dict[str, Any]:
    return {spec.name: extract(data, spec) for spec in specs}


def normalize(raw: RawRecord, ctx: RowContext) -> NormalizedRecord:
    """Spłaszcza jeden rekord do wiersza `Firmy` i wierszy arkuszy powiązanych."""
    merged = merge_sources(raw)
    merged["id"] = raw.id
    merged["dane_szczegolowe"] = raw.detail_json is not None
    provenance = {
        "zrodlo": raw.zrodlo,
        "srodowisko": ctx.srodowisko,
        "pobrano_utc": raw.detail_utc or raw.list_utc or ctx.pobrano_utc,
    }
    merged.update(provenance)
    firmy_row = _row(merged, SHEETS[SHEET_FIRMY])
    firmy_row["id"] = raw.id

    keys = {"id": raw.id, "nip": firmy_row.get("nip"), **provenance}
    glowny = _pkd_key((_pkd_glowny(merged) or {}).get("kod"))

    pkd_rows = [
        _row(
            {**item, **keys, "kolejnosc": i, "czy_glowny": _pkd_key(item["kod"]) == glowny},
            SHEETS[SHEET_PKD],
        )
        for i, item in enumerate(_pkd_items(merged), start=1)
    ]
    spolki_rows = [
        _row(
            {
                **keys,
                "nip_spolki": _first(item, "nip"),
                "regon_spolki": _first(item, "regon"),
                "data_zawieszenia": _first(item, "zawieszenia", "dataZawieszenia"),
            },
            SHEETS[SHEET_SPOLKI],
        )
        for item in _spolki_items(merged)
    ]
    adresy_rows = [
        _row({**item, **keys, "kolejnosc": i}, SHEETS[SHEET_ADRESY])
        for i, item in enumerate(_adresy_items(merged), start=1)
    ]
    return NormalizedRecord(firmy=firmy_row, pkd=pkd_rows, spolki=spolki_rows, adresy=adresy_rows)
