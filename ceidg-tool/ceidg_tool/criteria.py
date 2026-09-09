"""`Criteria` — jedyny kontrakt między wejściem (CLI, kreator, YAML, asystent) a pobieraniem.

Moduł jest czysty: waliduje semantykę (daty, sumy kontrolne, kształt PKD, statusy),
a rendering do parametrów API zależy od `ApiProfile` przekazanego do `to_params`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import date
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .apiprofile import ApiProfile

StatusJdg = Literal[
    "AKTYWNY",
    "WYKRESLONY",
    "ZAWIESZONY",
    "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",
    "WYLACZNIE_W_FORMIE_SPOLKI",
]

STATUSY: tuple[StatusJdg, ...] = (
    "AKTYWNY",
    "WYKRESLONY",
    "ZAWIESZONY",
    "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",
    "WYLACZNIE_W_FORMIE_SPOLKI",
)

WOJEWODZTWA: tuple[str, ...] = (
    "dolnośląskie",
    "kujawsko-pomorskie",
    "lubelskie",
    "lubuskie",
    "łódzkie",
    "małopolskie",
    "mazowieckie",
    "opolskie",
    "podkarpackie",
    "podlaskie",
    "pomorskie",
    "śląskie",
    "świętokrzyskie",
    "warmińsko-mazurskie",
    "wielkopolskie",
    "zachodniopomorskie",
)

_PKD_RE = re.compile(r"^\d{4}[A-Z]$")
_KOD_POCZTOWY_RE = re.compile(r"^\d{2}-\d{3}$")
MAX_TEXT_LEN = 200
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_REGON9_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
_REGON14_WEIGHTS = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)

_LIST_FIELDS = (
    "nazwa",
    "nip",
    "regon",
    "imie",
    "nazwisko",
    "wojewodztwo",
    "powiat",
    "gmina",
    "miasto",
    "ulica",
    "kod",
    "pkd",
    "pkd_2007",
    "status",
)


def _digits(value: str) -> str:
    return re.sub(r"[\s\-]", "", value)


def nip_checksum_ok(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    control = sum(w * int(d) for w, d in zip(_NIP_WEIGHTS, digits[:9], strict=True)) % 11
    return control != 10 and control == int(digits[9])


def normalize_nip(value: str) -> str:
    digits = _digits(value)
    if not digits.isdigit() or len(digits) != 10:
        raise ValueError(f"NIP {value!r} musi mieć 10 cyfr")
    if not nip_checksum_ok(digits):
        raise ValueError(f"NIP {value!r} ma błędną sumę kontrolną")
    return digits


def _regon_control(digits: str, weights: tuple[int, ...]) -> int:
    control = sum(w * int(d) for w, d in zip(weights, digits, strict=True)) % 11
    return 0 if control == 10 else control


def regon_checksum_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) not in (9, 14):
        return False
    if _regon_control(digits[:8], _REGON9_WEIGHTS) != int(digits[8]):
        return False
    if len(digits) == 14 and _regon_control(digits[:13], _REGON14_WEIGHTS) != int(digits[13]):
        return False
    return True


def normalize_regon(value: str) -> str:
    digits = _digits(value)
    if not digits.isdigit() or len(digits) not in (9, 14):
        raise ValueError(f"REGON {value!r} musi mieć 9 albo 14 cyfr")
    if not regon_checksum_ok(digits):
        raise ValueError(f"REGON {value!r} ma błędną sumę kontrolną")
    return digits


def normalize_pkd(value: str) -> str:
    """`62.01.Z`, `6201z`, `62 01 Z` → `6201Z` (postać kanoniczna, kompaktowa)."""
    compact = re.sub(r"[\s.]", "", value).upper()
    if not _PKD_RE.fullmatch(compact):
        raise ValueError(f"PKD {value!r} musi mieć postać 62.01.Z albo 6201Z")
    return compact


def format_pkd(compact: str, fmt: Literal["compact", "dotted"]) -> str:
    if fmt == "dotted":
        return f"{compact[:2]}.{compact[2:4]}.{compact[4]}"
    return compact


def bledy_po_polsku(exc: ValidationError) -> str:
    """Błędy pydantica jako zdania dla operatora, nie zrzut dla programisty.

    Surowy `ValidationError` niesie `[type=value_error, input_value=(...), input_type=tuple]`
    i odnośnik do errors.pydantic.dev. Dla kogoś, kto z założenia nie zna API — a taki jest
    odbiorca tego narzędzia — użyteczna jest w tym jedna fraza w środku, reszta zasłania.

    Funkcja stoi **tutaj**, przy walidatorach, bo trzy różne wejścia dostawały ten sam zrzut:
    `sprawdz-nip` przy złej sumie kontrolnej, formularz kreatora przy złym roczniku daty
    i asystent przy kryteriach nie do użycia. Trzecie miejsce miało własną kopię tej funkcji
    od 2026-09-07; dwa pierwsze nie miały żadnej, więc poprawka w jednym z nich nie ruszała
    pozostałych.
    """
    linie = []
    for blad in exc.errors():
        pole = ".".join(str(part) for part in blad["loc"]) or "kryteria"
        # Pydantic dokleja do komunikatu własnego walidatora angielski prefiks
        # („Value error, "), więc nasze polskie zdanie zaczynałoby się od cudzego.
        tresc = blad["msg"].removeprefix("Value error, ").removeprefix("Assertion failed, ")
        linie.append(f"  {pole}: {tresc}")
    return "\n".join(linie)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Iterable):
        return tuple(v for v in value if v is not None and str(v).strip() != "")
    return (value,)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    """Bez duplikatów, w stałej kolejności — `fingerprint()` nie zależy od kolejności wejścia."""
    return tuple(sorted(set(values)))


class Criteria(BaseModel):
    """Kryteria wyszukiwania odwzorowujące parametry `/firmy` plus dwa pola biznesowe.

    Świadomie poza modelem: `page`, `limit`, `base_url`, token — to parametry transportu.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    nazwa: tuple[str, ...] = ()
    nip: tuple[str, ...] = ()
    regon: tuple[str, ...] = ()
    imie: tuple[str, ...] = ()
    nazwisko: tuple[str, ...] = ()
    wojewodztwo: tuple[str, ...] = ()
    powiat: tuple[str, ...] = ()
    gmina: tuple[str, ...] = ()
    miasto: tuple[str, ...] = ()
    ulica: tuple[str, ...] = ()
    kod: tuple[str, ...] = ()
    pkd: tuple[str, ...] = ()
    # Poprzednicy z PKD 2007 dokładani do zapytania w okresie przejściowym (ADR-0012). Osobne
    # pole, a nie dosypanie do `pkd`, bo inaczej `describe()` podawałby nasz dodatek jako wybór
    # operatora — a to jedyne miejsce, gdzie widzi różnicę między tym, o co prosił, a tym, co
    # naprawdę leci. Do `to_params` idą jako te same parametry `pkd`, bo API zna jeden filtr.
    pkd_2007: tuple[str, ...] = ()
    status: tuple[StatusJdg, ...] = ()
    data_od: date | None = None
    data_do: date | None = None
    szczegoly: bool = False
    max_rekordow: int | None = Field(default=None, ge=1)

    @field_validator(*_LIST_FIELDS, mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> tuple[Any, ...]:
        return _as_tuple(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status_upper(cls, value: Any) -> tuple[str, ...]:
        return _dedupe(str(v).strip().upper() for v in _as_tuple(value))

    @field_validator("nip")
    @classmethod
    def _nip(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe(normalize_nip(v) for v in value)

    @field_validator("regon")
    @classmethod
    def _regon(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe(normalize_regon(v) for v in value)

    @field_validator("pkd", "pkd_2007")
    @classmethod
    def _pkd(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _dedupe(normalize_pkd(v) for v in value)

    @field_validator("kod")
    @classmethod
    def _kod(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for v in value:
            if not _KOD_POCZTOWY_RE.fullmatch(v):
                raise ValueError(f"kod pocztowy {v!r} musi mieć postać 15-333")
        return _dedupe(value)

    @field_validator("wojewodztwo")
    @classmethod
    def _wojewodztwo(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        out: list[str] = []
        for v in value:
            name = v.strip().lower()
            if name not in WOJEWODZTWA:
                raise ValueError(f"nieznane województwo {v!r}; dozwolone: {', '.join(WOJEWODZTWA)}")
            out.append(name)
        return _dedupe(out)

    @field_validator("nazwa", "imie", "nazwisko", "powiat", "gmina", "miasto", "ulica")
    @classmethod
    def _plain_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for v in value:
            if len(v) > MAX_TEXT_LEN:
                raise ValueError(f"wartość {v[:20]!r}... ma {len(v)} znaków, limit {MAX_TEXT_LEN}")
        return _dedupe(v.strip() for v in value if v.strip())

    @model_validator(mode="after")
    def _dates_in_order(self) -> Criteria:
        if self.data_od and self.data_do and self.data_od > self.data_do:
            raise ValueError(
                f"data_od ({self.data_od}) nie może być późniejsza niż data_do ({self.data_do})"
            )
        return self

    def is_empty(self) -> bool:
        """Brak jakiegokolwiek filtra — zapytanie objęłoby cały rejestr."""
        return not any(getattr(self, f) for f in _LIST_FIELDS) and not (
            self.data_od or self.data_do
        )

    def poszerzenia(self, *, limit: int = 4) -> tuple[tuple[str, Criteria], ...]:
        """Kandydaci na zdjęcie **jednego** filtra — od najczęstszej przyczyny pustego wyniku.

        Zwraca pary `(pole, kryteria bez tego pola)`. Nazwę pola po polsku układa `ui/texts`;
        tutaj zostaje sama decyzja, które filtry są najbardziej podejrzane i w jakiej
        kolejności — bo to jest twierdzenie o rejestrze, nie o wyglądzie ekranu, i jako takie
        daje się sprawdzić testem bez terminala.

        Kolejność nie jest dowolna. Filtry tekstowe dopasowują się dosłownie, więc jedna
        literówka zeruje wynik i to one idą pierwsze. `miasto` jest w tej grupie mimo pozorów:
        rejestr trzyma nazwę urzędową, a operator pisze potocznie albo podaje dzielnicę.
        `pkd` jest osobnym przypadkiem i dlatego stoi zaraz za nimi — kod może być poprawny,
        żywy w PKD 2025 i mimo to nie występować na żadnym wpisie w regionie, bo 58,6 %
        rejestru zostało jeszcze przy roczniku 2007 (`docs/adr/0012`). Statusy i daty są
        najmniej podejrzane, bo pochodzą ze zbiorów zamkniętych i z kalendarza.

        Zdjęcie `pkd` czyści **oba** pola: `pkd_2007` jest rozszerzeniem tego samego filtru
        (ADR-0012), więc kandydat, który zostawiłby stare kody, nie byłby poszerzeniem — dalej
        odsiewałby po branży, tylko innym rocznikiem.

        Kandydat, który opróżniłby kryteria do zera, nie powstaje: zapytanie bez ani jednego
        filtra obejmuje cały rejestr i `Criteria` odmawia go gdzie indziej. Lepsze „to był
        jedyny filtr" niż propozycja, której nie da się przyjąć.
        """
        kolejnosc = (
            "nazwa",
            "ulica",
            "kod",
            "imie",
            "nazwisko",
            "miasto",
            "pkd",
            "gmina",
            "powiat",
            "status",
            "daty",
            "wojewodztwo",
        )
        out: list[tuple[str, Criteria]] = []
        for pole in kolejnosc:
            if pole == "daty":
                if not (self.data_od or self.data_do):
                    continue
                kandydat = self.model_copy(update={"data_od": None, "data_do": None})
            elif pole == "pkd":
                if not self.wszystkie_pkd():
                    continue
                kandydat = self.model_copy(update={"pkd": (), "pkd_2007": ()})
            else:
                if not getattr(self, pole):
                    continue
                kandydat = self.model_copy(update={pole: ()})
            if kandydat.is_empty():
                continue
            out.append((pole, kandydat))
            if len(out) >= limit:
                break
        return tuple(out)

    def to_params(self, profile: ApiProfile) -> list[tuple[str, str]]:
        """Parametry zapytania `/firmy` w dialekcie profilu, posortowane kanonicznie."""
        suffix = profile.list_param_suffix
        pairs: list[tuple[str, str]] = []

        def add(name: str, values: Iterable[str]) -> None:
            pairs.extend((name + suffix, v) for v in values)

        add("nazwa", self.nazwa)
        add("nip", self.nip)
        add("regon", self.regon)
        add("imie", self.imie)
        add("nazwisko", self.nazwisko)
        add(
            "wojewodztwo",
            (v.upper() if profile.wojewodztwo_case == "upper" else v for v in self.wojewodztwo),
        )
        add("powiat", self.powiat)
        add("gmina", self.gmina)
        add("miasto", self.miasto)
        add("ulica", self.ulica)
        add("kod", self.kod)
        # Oba pola renderują się do tego samego parametru: API zna jeden filtr `pkd`, a
        # powtórzone `pkd=` działa jak OR (zmierzone 2026-09-07, `docs/decisions.md`).
        add("pkd", (format_pkd(v, profile.pkd_format) for v in self.wszystkie_pkd()))
        add("status", self.status)
        if self.data_od:
            pairs.append(("dataod", self.data_od.strftime(profile.date_format)))
        if self.data_do:
            pairs.append(("datado", self.data_do.strftime(profile.date_format)))
        return sorted(pairs)

    def wszystkie_pkd(self) -> tuple[str, ...]:
        """Kody PKD faktycznie wysyłane: wybrane przez operatora plus poprzednicy z 2007."""
        return _dedupe((*self.pkd, *self.pkd_2007))

    def canonical_json(self) -> str:
        dane = self.model_dump(mode="json")
        # Puste `pkd_2007` znika z odcisku, żeby dodanie tego pola (ADR-0012) nie unieważniło
        # odcisków wszystkich wcześniejszych przebiegów — a wraz z nimi możliwości wznowienia
        # tego, co ktoś zaczął przed aktualizacją. Zapytanie rozszerzone ma pole niepuste, więc
        # od wąskiego różni się nadal, co jest tym, czego wymaga wznawianie.
        if not dane.get("pkd_2007"):
            dane.pop("pkd_2007", None)
        return json.dumps(dane, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        """Stabilny skrót kryteriów — klucz do odnalezienia runu do wznowienia."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    def describe(self) -> str:
        """Podsumowanie po polsku generowane przez kod (nie przez model) — faza 3/4."""
        parts: list[str] = []
        labels = {
            "nazwa": "nazwa",
            "nip": "NIP",
            "regon": "REGON",
            "imie": "imię",
            "nazwisko": "nazwisko",
            "wojewodztwo": "województwo",
            "powiat": "powiat",
            "gmina": "gmina",
            "miasto": "miasto",
            "ulica": "ulica",
            "kod": "kod pocztowy",
            "pkd": "PKD",
            "status": "status",
        }
        for field_name, label in labels.items():
            values: tuple[str, ...] = getattr(self, field_name)
            if values:
                parts.append(f"{label}: {', '.join(values)}")
        if self.pkd_2007:
            # Świadomie po `pkd` i osobnym zdaniem: operator ma widzieć, że część kodów
            # dołożyliśmy my, a nie on. Bez tego rozszerzenie wygląda na jego własny wybór.
            parts.append(f"dodatkowo kody PKD 2007: {', '.join(self.pkd_2007)}")
        if self.data_od or self.data_do:
            od = self.data_od.isoformat() if self.data_od else "…"
            do = self.data_do.isoformat() if self.data_do else "…"
            parts.append(f"rozpoczęcie działalności: {od} – {do}")
        if self.max_rekordow:
            parts.append(f"maksymalnie {self.max_rekordow} rekordów")
        parts.append("ze szczegółami" if self.szczegoly else "tylko lista podstawowa")
        return "; ".join(parts)
