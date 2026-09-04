"""Deterministyczne rozwiązywanie wyrażeń czasowych z odpowiedzi pracownika (czysta logika).

Rozszerzenie zasady, która jest w tym kodzie od początku (``interpreter._coerce_weekday``: nazwę
dnia mapuje KOD, bo model bywa zawodny w liczeniu 0–6). Tutaj to samo dotyczy dat i tygodni:
model przekazuje wyrażenie tak, jak napisał je pracownik („w przyszły czwartek", „od 15-go",
„za dwa tygodnie"), a numer dnia i przynależność do tygodnia wylicza ta funkcja.

Dwie decyzje projektowe, które trzeba znać czytając wynik:

1. **Nierozpoznane wyrażenie zwraca ``None``, nigdy zgadniętą datę.** Zgadywanie jest dokładnie
   tym trybem awarii, który ta warstwa ma usuwać — dzień wpisany „na oko" trafia do grafiku
   i pracownik dowiaduje się o tym dopiero w Shifts.
2. **Sama nazwa dnia („czwartek") oznacza dzień TYGODNIA DOCELOWEGO**, nie najbliższy czwartek od
   dziś. Rozmowa dotyczy jednego konkretnego tygodnia, więc to jest odczytanie zgodne z intencją
   pracownika i spójne z ``_coerce_weekday``, które od zawsze mapuje weekday na dzień celu.
   Jawne wskazanie tygodnia („przyszły czwartek", „za dwa tygodnie") przesuwa punkt odniesienia.

``w_zakresie=False`` to jawny sygnał „to nie dotyczy tygodnia, o który pytamy" — warstwa wyżej
zamienia go na uczciwy komunikat zamiast po cichu zapisać zły tydzień.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# Nazwa dnia → numer 0–6. JEDNO źródło prawdy dla całego pakietu (importuje je też
# ``interpreter._coerce_weekday``). Warianty bez ogonków i skróty = odporność na to, że model
# odbiegnie od proszonej pełnej nazwy albo że pracownik napisze skrótem.
NAZWY_DNI: dict[str, int] = {
    "poniedziałek": 0, "poniedzialek": 0, "poniedziałku": 0, "poniedzialku": 0, "pon": 0, "pn": 0,
    "wtorek": 1, "wtorku": 1, "wt": 1,
    "środa": 2, "sroda": 2, "środę": 2, "srode": 2, "środy": 2, "srody": 2, "śr": 2, "sr": 2,
    "czwartek": 3, "czwartku": 3, "czw": 3, "cz": 3,
    "piątek": 4, "piatek": 4, "piątku": 4, "piatku": 4, "pt": 4, "pi": 4,
    "sobota": 5, "sobotę": 5, "sobote": 5, "soboty": 5, "sob": 5, "sb": 5,
    "niedziela": 6, "niedzielę": 6, "niedziele": 6, "niedzieli": 6, "niedz": 6, "ndz": 6, "nd": 6,
}

# Kanoniczne nazwy dni w kolejności ``weekday()`` — kontrakt wyjścia modelu (``agent.schema``)
# dopuszcza wyłącznie te napisy, dzięki czemu nieznany dzień jest niemożliwy, a nie „pomijany".
PELNE_NAZWY_DNI: tuple[str, ...] = (
    "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela",
)

# Nazwa miesiąca → numer. Dopełniacz („15 stycznia") i mianownik („styczeń”), oba bez ogonków.
NAZWY_MIESIECY: dict[str, int] = {
    "stycznia": 1, "styczeń": 1, "styczen": 1,
    "lutego": 2, "luty": 2,
    "marca": 3, "marzec": 3,
    "kwietnia": 4, "kwiecień": 4, "kwiecien": 4,
    "maja": 5, "maj": 5,
    "czerwca": 6, "czerwiec": 6,
    "lipca": 7, "lipiec": 7,
    "sierpnia": 8, "sierpień": 8, "sierpien": 8,
    "września": 9, "wrzesnia": 9, "wrzesień": 9, "wrzesien": 9,
    "października": 10, "pazdziernika": 10, "październik": 10, "pazdziernik": 10,
    "listopada": 11, "listopad": 11,
    "grudnia": 12, "grudzień": 12, "grudzien": 12,
}

# Wyrażenia wskazujące tydzień, jako przesunięcie względem tygodnia zawierającego `dzis`.
# Kolejność ma znaczenie: dłuższe frazy najpierw, żeby „w przyszłym tygodniu" nie zostało
# dopasowane jako „w tym tygodniu" po odcięciu przedrostka.
_FRAZY_TYGODNIA: tuple[tuple[str, int], ...] = (
    ("w przyszłym tygodniu", 1), ("w przyszlym tygodniu", 1),
    ("przyszłego tygodnia", 1), ("przyszlego tygodnia", 1),
    ("przyszły tydzień", 1), ("przyszly tydzien", 1),
    ("następny tydzień", 1), ("nastepny tydzien", 1),
    ("w następnym tygodniu", 1), ("w nastepnym tygodniu", 1),
    ("w zeszłym tygodniu", -1), ("w zeszlym tygodniu", -1),
    ("zeszły tydzień", -1), ("zeszly tydzien", -1),
    ("w ubiegłym tygodniu", -1), ("w ubieglym tygodniu", -1),
    ("poprzedni tydzień", -1), ("poprzedni tydzien", -1),
    ("w tym tygodniu", 0), ("ten tydzień", 0), ("ten tydzien", 0),
    ("bieżący tydzień", 0), ("biezacy tydzien", 0),
)

# „przyszły czwartek" — przedrostek TUŻ PRZED nazwą dnia, więc obsługiwany osobno od fraz wyżej.
_PRZEDROSTKI_DNIA: tuple[tuple[str, int], ...] = (
    ("przyszły", 1), ("przyszly", 1), ("przyszłym", 1), ("przyszlym", 1),
    ("następny", 1), ("nastepny", 1), ("następnym", 1), ("nastepnym", 1),
    ("zeszły", -1), ("zeszly", -1), ("zeszłym", -1), ("zeszlym", -1),
    ("ubiegły", -1), ("ubiegly", -1),
)

# Przyimki i wypełniacze bez wpływu na znaczenie („od poniedziałku", „we wtorek").
_POMIJALNE = frozenset({"w", "we", "od", "do", "na", "dnia", "to", "jest", "będzie", "bedzie"})

_ZNAKI_DO_ODCIECIA = ".,!?…:;-–„”\"'()"
_LICZEBNIKI: dict[str, int] = {
    "jeden": 1, "jedna": 1, "dwa": 2, "dwie": 2, "trzy": 3, "cztery": 4, "pięć": 5, "piec": 5,
}
_MAX_PRZESUNIECIE_TYGODNI = 8  # dalej niż 2 miesiące to na pewno nie jest grafik na ten tydzień


@dataclass(frozen=True)
class Dzien:
    """Rozwiązany pojedynczy dzień. ``w_zakresie`` = mieści się w tygodniu docelowym."""

    weekday: int
    data_iso: str
    tydzien_iso: str
    w_zakresie: bool


@dataclass(frozen=True)
class Tydzien:
    """Rozwiązany tydzień. ``przesuniecie`` liczone względem tygodnia DOCELOWEGO (0 = docelowy)."""

    poniedzialek_iso: str
    przesuniecie: int
    w_zakresie: bool


def _normalizuj(tekst: str) -> str:
    """Małe litery, pojedyncze spacje, bez interpunkcji brzegowej przy słowach."""
    slowa = [slowo.strip(_ZNAKI_DO_ODCIECIA) for slowo in tekst.lower().split()]
    return " ".join(slowo for slowo in slowa if slowo)


def _poniedzialek(dzien: date) -> date:
    return dzien - timedelta(days=dzien.weekday())


def _wytnij_fraze_tygodnia(tekst: str) -> tuple[int | None, str]:
    """Zwróć (przesunięcie tygodnia względem `dzis`, tekst bez tej frazy).

    ``None`` znaczy „pracownik nie wskazał tygodnia" — punkt odniesienia zostaje domyślny.
    """
    for fraza, przesuniecie in _FRAZY_TYGODNIA:
        if fraza in tekst:
            return przesuniecie, _normalizuj(tekst.replace(fraza, " "))
    dopasowanie = re.search(r"\bza\s+(\d+|[a-ząćęłńóśźż]+)\s+tygodn\w*", tekst)
    if dopasowanie:
        ile = _na_liczbe(dopasowanie.group(1))
        if ile is not None and 0 <= ile <= _MAX_PRZESUNIECIE_TYGODNI:
            return ile, _normalizuj(tekst.replace(dopasowanie.group(0), " "))
    return None, tekst


def _na_liczbe(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _LICZEBNIKI.get(token)


def _data_jawna(tekst: str, dzis: date) -> date | None:
    """Data podana wprost: „15 stycznia", „15-go", „15.01", „15.01.2026". ``None`` gdy brak.

    Bez roku bierzemy najbliższe wystąpienie NIE WCZEŚNIEJ niż `dzis` — pracownik uzupełnia
    grafik na przyszłość, więc cofanie się o rok byłoby zawsze błędem.
    """
    kropkowa = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", tekst)
    if kropkowa:
        rok = int(kropkowa.group(3)) if kropkowa.group(3) else None
        return _zbuduj_date(int(kropkowa.group(1)), int(kropkowa.group(2)), rok, dzis)

    z_miesiacem = re.search(r"\b(\d{1,2})(?:-?go)?\s+([a-ząćęłńóśźż]+)", tekst)
    if z_miesiacem:
        miesiac = NAZWY_MIESIECY.get(z_miesiacem.group(2))
        if miesiac is not None:
            return _zbuduj_date(int(z_miesiacem.group(1)), miesiac, None, dzis)

    sam_dzien = re.search(r"\b(\d{1,2})-?go\b", tekst)
    if sam_dzien:
        return _dzien_miesiaca(int(sam_dzien.group(1)), dzis)
    return None


def _zbuduj_date(dzien: int, miesiac: int, rok: int | None, dzis: date) -> date | None:
    for kandydat_roku in ([rok] if rok is not None else [dzis.year, dzis.year + 1]):
        try:
            kandydat = date(kandydat_roku, miesiac, dzien)
        except ValueError:
            continue  # np. 29 lutego w roku nieprzestępnym — spróbuj kolejnego roku
        if rok is not None or kandydat >= dzis:
            return kandydat
    return None


def _dzien_miesiaca(dzien: int, dzis: date) -> date | None:
    """Sam numer dnia („15-go") → najbliższe takie wystąpienie od `dzis` włącznie."""
    for przesuniecie_miesiaca in (0, 1):
        miesiac = dzis.month + przesuniecie_miesiaca
        rok = dzis.year + (miesiac - 1) // 12
        miesiac = (miesiac - 1) % 12 + 1
        try:
            kandydat = date(rok, miesiac, dzien)
        except ValueError:
            continue
        if kandydat >= dzis:
            return kandydat
    return None


def _nazwa_dnia(tekst: str) -> tuple[int, int] | None:
    """Znajdź nazwę dnia w tekście → (weekday, przesunięcie z przedrostka »przyszły«)."""
    tokeny = [t for t in tekst.split() if t not in _POMIJALNE]
    for indeks, token in enumerate(tokeny):
        weekday = NAZWY_DNI.get(token)
        if weekday is None:
            continue
        przesuniecie = 0
        if indeks > 0:
            przesuniecie = dict(_PRZEDROSTKI_DNIA).get(tokeny[indeks - 1], 0)
        return weekday, przesuniecie
    return None


def _wynik_dnia(data: date, week_start: date) -> Dzien:
    return Dzien(
        weekday=data.weekday(),
        data_iso=data.isoformat(),
        tydzien_iso=_poniedzialek(data).isoformat(),
        w_zakresie=week_start <= data < week_start + timedelta(days=7),
    )


def rozwiaz_dzien(wyrazenie: str, *, week_start: date, dzis: date) -> Dzien | None:
    """Wyrażenie pracownika → konkretny dzień. ``None``, gdy nie da się rozstrzygnąć jednoznacznie.

    ``week_start`` to poniedziałek tygodnia DOCELOWEGO (tego, o który bot pyta), ``dzis`` to
    dzisiejsza data lokalna — punkt odniesienia dla „jutro", „za dwa tygodnie", „15-go".
    """
    tekst = _normalizuj(wyrazenie)
    if not tekst:
        return None

    if tekst in {"dzisiaj", "dzis", "dziś"}:
        return _wynik_dnia(dzis, week_start)
    if tekst == "jutro":
        return _wynik_dnia(dzis + timedelta(days=1), week_start)
    if tekst == "pojutrze":
        return _wynik_dnia(dzis + timedelta(days=2), week_start)

    za_dni = re.fullmatch(r"za\s+(\d+|[a-ząćęłńóśźż]+)\s+dni?", tekst)
    if za_dni:
        ile = _na_liczbe(za_dni.group(1))
        if ile is None or ile > _MAX_PRZESUNIECIE_TYGODNI * 7:
            return None
        return _wynik_dnia(dzis + timedelta(days=ile), week_start)

    jawna = _data_jawna(tekst, dzis)
    if jawna is not None:
        return _wynik_dnia(jawna, week_start)

    przesuniecie_frazy, reszta = _wytnij_fraze_tygodnia(tekst)
    dzien = _nazwa_dnia(reszta)
    if dzien is None:
        return None
    weekday, przesuniecie_przedrostka = dzien

    wskazany = przesuniecie_frazy if przesuniecie_frazy is not None else przesuniecie_przedrostka
    if przesuniecie_frazy is None and not przesuniecie_przedrostka:
        baza = week_start  # sama nazwa dnia = dzień tygodnia docelowego (patrz docstring modułu)
    else:
        baza = _poniedzialek(dzis) + timedelta(weeks=wskazany)
    return _wynik_dnia(baza + timedelta(days=weekday), week_start)


def rozwiaz_tydzien(wyrazenie: str, *, week_start: date, dzis: date) -> Tydzien | None:
    """Wyrażenie pracownika → tydzień (poniedziałek). ``None``, gdy nie wskazuje żadnego tygodnia.

    ``przesuniecie`` jest liczone względem tygodnia DOCELOWEGO, nie względem `dzis`: warstwa wyżej
    pyta „czy to jeszcze mój tydzień", a nie „jak daleko od dziś".
    """
    tekst = _normalizuj(wyrazenie)
    if not tekst:
        return None

    przesuniecie_frazy, _reszta = _wytnij_fraze_tygodnia(tekst)
    if przesuniecie_frazy is not None:
        poniedzialek = _poniedzialek(dzis) + timedelta(weeks=przesuniecie_frazy)
    else:
        jawna = _data_jawna(tekst, dzis)
        if jawna is None:
            return None
        poniedzialek = _poniedzialek(jawna)

    return Tydzien(
        poniedzialek_iso=poniedzialek.isoformat(),
        przesuniecie=(poniedzialek - week_start).days // 7,
        w_zakresie=poniedzialek == week_start,
    )
