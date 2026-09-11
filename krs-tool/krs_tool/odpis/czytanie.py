"""Odczyt odpisu ze struktury wczytanej z pliku. Moduł czysty.

**Każde założenie o kształcie odpisu jest tu nazwane i wypisane w `docs/pomiary.md`.** Na dziś
żadne z nich nie zostało zmierzone na prawdziwym pliku — model powstał na odpisach
syntetycznych, czyli na tym, co sami napisaliśmy. To jest dokładnie ten układ, w którym
`ceidg-tool` raz już zapłacił: ręcznie napisana linia w atrapie została zacytowana przez ADR
jako pomiar. Dlatego tutaj nie ma cichych domyślności — brakujące pole kończy się wyjątkiem
`NieznanyKsztaltOdpisuError`, który nazywa pole, a nie pustą wartością.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from ..errors import (
    BrakZrodlaPublicznegoError,
    NieznanyKsztaltOdpisuError,
    OdpisNieczytelnyError,
)
from ..identity import NumerKRS, numer_krs
from .model import REJESTR_PRZEDSIEBIORCOW, Dzial, DzienBilansowy, Odpis, Okres, Wzmianka

# Klucz, którego rejestr nigdy nie wystawi. Stawia go wyłącznie budowniczy odpisów
# syntetycznych, żeby karta z takiego pliku była nie do pomylenia z prawdziwą.
KLUCZ_SYNTETYCZNY = "_syntetyczny"

NUMERY_DZIALOW = (1, 2, 3, 4, 5, 6)

_DATA_KROPKOWA = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_OKRES_KROPKOWY = re.compile(r"^OD\s+([\d.]+)\s+DO\s+([\d.]+)$", re.IGNORECASE)
_OKRES_SLOWNY = re.compile(
    r"^OD\s+(\d{1,2})\s+([A-ZŁŃŚŻŹĄĆĘÓ]+)\s+(\d{4})\s+ROKU"
    r"\s+DO\s+(\d{1,2})\s+([A-ZŁŃŚŻŹĄĆĘÓ]+)\s+(\d{4})\s+ROKU$",
    re.IGNORECASE,
)

# Miesiące w dopełniaczu — w tej postaci rejestr zapisuje starsze okresy.
_MIESIACE = {
    "STYCZNIA": 1,
    "LUTEGO": 2,
    "MARCA": 3,
    "KWIETNIA": 4,
    "MAJA": 5,
    "CZERWCA": 6,
    "LIPCA": 7,
    "SIERPNIA": 8,
    "WRZESNIA": 9,
    "WRZEŚNIA": 9,
    "PAZDZIERNIKA": 10,
    "PAŹDZIERNIKA": 10,
    "LISTOPADA": 11,
    "GRUDNIA": 12,
}


def _wymagane(zrodlo: Mapping[str, Any], sciezka: str) -> Any:
    """Schodzi po ścieżce `a.b.c`; brak pola kończy się wyjątkiem nazywającym ścieżkę."""
    biezace: Any = zrodlo
    for czesc in sciezka.split("."):
        if not isinstance(biezace, Mapping) or czesc not in biezace:
            raise NieznanyKsztaltOdpisuError(
                f"W odpisie brakuje pola {sciezka!r}. To jest założenie o kształcie odpisu, "
                f"nie usterka pliku — dopisz je do docs/pomiary.md albo popraw czytanie."
            )
        biezace = biezace[czesc]
    return biezace


def _opcjonalne(zrodlo: Mapping[str, Any], sciezka: str) -> Any:
    biezace: Any = zrodlo
    for czesc in sciezka.split("."):
        if not isinstance(biezace, Mapping) or czesc not in biezace:
            return None
        biezace = biezace[czesc]
    return biezace


def czytaj_date(tekst: str) -> date:
    """Data w zapisie `DD.MM.RRRR` — jedynym, w jakim rejestr podaje datę złożenia."""
    dopasowanie = _DATA_KROPKOWA.match(tekst.strip())
    if dopasowanie is None:
        raise OdpisNieczytelnyError(f"Nie umiem odczytać daty {tekst!r}")
    dzien, miesiac, rok = (int(g) for g in dopasowanie.groups())
    return date(rok, miesiac, dzien)


def _data_slowna(dzien: str, miesiac: str, rok: str) -> date | None:
    numer = _MIESIACE.get(miesiac.upper())
    if numer is None:
        return None
    return date(int(rok), numer, int(dzien))


def czytaj_okres(zapis: str) -> Okres | None:
    """Okres sprawozdawczy w obu zapisach, jakie rejestr stosuje.

    Zwraca `None`, gdy zapis jest nieznany. **To jest wynik poprawny, nie usterka** — wzmianka
    zachowuje wtedy swój surowy tekst i zostaje zgłoszona jako nieczytelna. Zgadywanie roku
    kalendarzowego z niepełnego zapisu byłoby cichym wymyślaniem okresu sprawozdawczego.
    """
    tekst = " ".join(zapis.split())
    kropkowy = _OKRES_KROPKOWY.match(tekst)
    if kropkowy is not None:
        try:
            od, do = (czytaj_date(g) for g in kropkowy.groups())
        except OdpisNieczytelnyError:
            return None
        return Okres(od=od, do=DzienBilansowy(do))
    slowny = _OKRES_SLOWNY.match(tekst)
    if slowny is None:
        return None
    dzien_od, miesiac_od, rok_od, dzien_do, miesiac_do, rok_do = slowny.groups()
    od_slownie = _data_slowna(dzien_od, miesiac_od, rok_od)
    do_slownie = _data_slowna(dzien_do, miesiac_do, rok_do)
    if od_slownie is None or do_slownie is None:
        return None
    return Okres(od=od_slownie, do=DzienBilansowy(do_slownie))


def _wzmianki(dzial3: Any) -> tuple[Wzmianka, ...]:
    if not isinstance(dzial3, Mapping):
        return ()
    blok = dzial3.get("wzmiankiOZlozonychDokumentach")
    if not isinstance(blok, Mapping):
        return ()
    zebrane: list[Wzmianka] = []
    for rodzaj, pozycje in blok.items():
        if not isinstance(pozycje, list):
            continue
        zebrane.extend(
            _wzmianka(rodzaj, pozycja) for pozycja in pozycje if isinstance(pozycja, Mapping)
        )
    return tuple(zebrane)


def _wzmianka(rodzaj: str, pozycja: Mapping[str, Any]) -> Wzmianka:
    zapis = str(pozycja.get("zaOkresOdDo", ""))
    return Wzmianka(
        rodzaj=rodzaj,
        data_zlozenia=czytaj_date(str(pozycja["dataZlozenia"])),
        zapis_okresu=zapis,
        okres=czytaj_okres(zapis) if zapis else None,
    )


def _dzialy(dane: Mapping[str, Any]) -> tuple[Dzial, ...]:
    zebrane: list[Dzial] = []
    for numer in NUMERY_DZIALOW:
        klucz = f"dzial{numer}"
        obecny = klucz in dane
        zawartosc = dane.get(klucz)
        zebrane.append(
            Dzial(
                numer=numer,
                obecny=obecny,
                pusty=not zawartosc,
                klucze=_klucze_dzialu(zawartosc),
            )
        )
    return tuple(zebrane)


def _klucze_dzialu(zawartosc: Any) -> tuple[str, ...]:
    """Nazwy pól w dziale, przepisane dosłownie i w kolejności z pliku.

    Dział, który nie jest odwzorowaniem, oddaje pustą krotkę zamiast wyjątku — to nie jest
    założenie o rejestrze, tylko odmowa jego robienia. Raport mówi wtedy, że nie ma czego
    zacytować, i jest to zdanie prawdziwe.
    """
    if not isinstance(zawartosc, Mapping):
        return ()
    return tuple(str(k) for k in zawartosc)


def wczytaj_odpis(surowe: Mapping[str, Any], *, numer: NumerKRS | None = None) -> Odpis:
    """Sprowadza strukturę z pliku do modelu odczytu.

    `numer` podaje wołający, gdy plik go nie niesie. Czy odpis w ogóle zawiera własny numer,
    **nie zostało zmierzone** — patrz `docs/pomiary.md`.
    """
    odpis = _wymagane(surowe, "odpis")
    naglowek = _wymagane(odpis, "naglowekA")
    dane = _wymagane(odpis, "dane")
    wlasny = _opcjonalne(naglowek, "numerKRS")
    if wlasny is None and numer is None:
        raise NieznanyKsztaltOdpisuError(
            "Odpis nie niesie numeru KRS, a wołający go nie podał. Czy rejestr w ogóle umieszcza "
            "numer w odpisie, jest pozycją niezmierzoną (docs/pomiary.md)."
        )
    _sprawdz_rejestr(str(_opcjonalne(naglowek, "rejestr") or ""))
    podmiot = _wymagane(dane, "dzial1.danePodmiotu")
    data_wpisu = _opcjonalne(naglowek, "dataOstatniegoWpisu")
    return Odpis(
        numer=numer_krs(str(wlasny)) if wlasny is not None else _wymagany_numer(numer),
        rejestr=str(_opcjonalne(naglowek, "rejestr") or ""),
        stan_z_dnia=czytaj_date(str(_wymagane(naglowek, "stanZDnia"))),
        data_ostatniego_wpisu=czytaj_date(str(data_wpisu)) if data_wpisu else None,
        nazwa=str(_wymagane(podmiot, "nazwa")),
        forma_prawna=str(_opcjonalne(podmiot, "formaPrawna") or ""),
        nip=_tekst_lub_none(_opcjonalne(podmiot, "identyfikatory.nip")),
        regon=_tekst_lub_none(_opcjonalne(podmiot, "identyfikatory.regon")),
        dzien_konczacy_rok_obrotowy=_tekst_lub_none(
            _opcjonalne(dane, "dzial3.informacjaODniuKonczacymRokObrotowy")
        ),
        wzmianki=_wzmianki(dane.get("dzial3")),
        dzialy=_dzialy(dane),
        syntetyczny=bool(surowe.get(KLUCZ_SYNTETYCZNY, False)),
    )


def _sprawdz_rejestr(rejestr: str) -> None:
    """Odpis spoza rejestru przedsiębiorców jest odrzucany, a nie oceniany.

    Powód zobaczyłem na wydruku, nie w rozumowaniu: odpis z `rejestr=S` przechodził przez cały
    potok i dawał raport, w którym **wszystkie dziesięć reguł jest wykluczonych**, z podpisem
    „każda reguła katalogu została rozstrzygnięta". Czyta się to jak zaświadczenie o czystości,
    a reguły działów 4 i 6 stoją na art. 41 i 44 ustawy o KRS, które opisują rejestr
    przedsiębiorców — czyli nie ten, z którego ten odpis pochodzi. Uspokajający raport
    o podmiocie spoza zakresu to najgorszy tryb awarii, jaki ten produkt ma.

    Pusta wartość przepuszcza: to niewiedza, nie przynależność, i tak samo traktuje ją
    przesłanka o rejestrze. Wartość `P` jest ZAŁOŻENIEM (`docs/pomiary.md`, wiersz 10) —
    gdyby okazało się błędne, narzędzie odmówi wszystkim i powie, jaką wartość zobaczyło,
    zamiast po cichu oceniać nie ten rejestr.
    """
    if rejestr and rejestr != REJESTR_PRZEDSIEBIORCOW:
        raise BrakZrodlaPublicznegoError(
            f"Odpis pochodzi z rejestru {rejestr!r}, a to narzędzie opisuje wyłącznie rejestr "
            f"przedsiębiorców ({REJESTR_PRZEDSIEBIORCOW!r}). Reguły katalogu powołują się na "
            "przepisy o tym rejestrze, a sprawozdania podmiotów spoza niego trafiają do Szefa "
            "KAS i są objęte tajemnicą skarbową."
        )


def _wymagany_numer(numer: NumerKRS | None) -> NumerKRS:
    if numer is None:  # pragma: no cover - odcięte wcześniej
        raise NieznanyKsztaltOdpisuError("Brak numeru KRS")
    return numer


def _tekst_lub_none(wartosc: Any) -> str | None:
    if wartosc is None:
        return None
    tekst = str(wartosc).strip()
    return tekst or None
