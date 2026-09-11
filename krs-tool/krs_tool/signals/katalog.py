"""Katalog reguł sygnałowych — dane wczytywane przez strażnika, nie kod.

Reguła mieszka w YAML-u, bo jest przedmiotem przeglądu przez człowieka znającego prawo, a nie
przez programistę. Loader jest strażnikiem: **odmawia wczytania reguły niekompletnej**, zamiast
uzupełniać ją domyślnością. Domyślność jest tu najgorszym możliwym zachowaniem — reguła
o brakującej przesłance wykluczającej wygląda identycznie jak reguła kompletna, a produkuje
oskarżenie tam, gdzie prawo przewiduje wyjątek.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum, auto
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigError

KATALOG_REGUL = Path(__file__).resolve().parent / "reguly"
PLIK_ZABLOKOWANYCH = KATALOG_REGUL / "zablokowane.yaml"


class Poziom(IntEnum):
    """Waga sygnału.

    Wartości pochodzą z `auto()`, a nie z literałów — reguła granic 10 zabrania w tym pakiecie
    liczb innych niż 0 i 1, i zabrania ich także tutaj, żeby wyjątek nie stał się furtką.

    **Nie ma tu członu znaczącego „po terminie" i nie będzie go, dopóki nie zostanie zmierzone
    opóźnienie publikacji wzmianki.** To jest połowa mechanizmu z `docs/adr/0001` decyzja 5:
    brak wartości, która mogłaby unieść zarzut. Drugą połową jest zamknięty leksykon.
    """

    TERMINALNY = auto()
    WYPRZEDZAJACY = auto()
    KONTEKST = auto()


class Rodzaj(Enum):
    """Co reguła bada."""

    OBECNOSC_WPISU = "obecnosc_wpisu"
    BRAK_DOKUMENTU = "brak_dokumentu"


class Zakres(Enum):
    """O czym reguła orzeka: o jednym wpisie w dziale czy o całym dziale.

    Rozróżnienie doszło w kroku 4 i wzięło się z pomiaru, nie z upodobania. Model odczytu wie
    o dziale trzy rzeczy — nieobecny, pusty, niepusty — i **nie wie, który wpis w nim stoi**,
    bo nazwy kluczy wewnątrz działu nie zostały zmierzone (`docs/pomiary.md`). Reguła
    `pojedynczy_wpis` dzieląca dział z inną regułą jest więc dziś nierozstrzygalna, a reguła
    `caly_dzial` rozstrzygalna — i to jest różnica, którą trzeba widzieć w danych, zanim
    zobaczy się ją w wyniku.
    """

    POJEDYNCZY_WPIS = "pojedynczy_wpis"
    CALY_DZIAL = "caly_dzial"


# Sześć zgodnych z prawem powodów, dla których sprawozdania może nie być. Krotka jest
# ZAMROŻONA: reguła rodzaju `brak_dokumentu` musi wymienić wszystkie sześć, inaczej się nie
# wczyta. Skreślenie jednej zapala bramkę zamiast po cichu poszerzyć zakres oskarżenia.
PRZESLANKI_BRAKU_DOKUMENTU = (
    "zawieszenie_caloroczne",
    "rozpoczecie_w_ii_polroczu",
    "upadlosc_lub_restrukturyzacja",
    "dzialalnosc_w_spadku",
    "oswiadczenie_art_70a",
    "poza_rejestrem_przedsiebiorcow",
)

# Termin liczy się od dnia bilansowego i od niczego innego. Rok obrotowy nie musi być
# kalendarzowy, a po zmianie bywa dłuższy niż dwanaście miesięcy — „31 grudnia" jako punkt
# odniesienia jest po prostu nieprawdą dla części populacji.
JEDYNY_PUNKT_ODNIESIENIA = "dzien_bilansowy"

# Skąd przesłankę da się ustalić. Dwie ostatnie wartości to jawne przyznanie się do niewiedzy
# i one właśnie sprawiają, że reguła braku sprawozdania nie może dziś wystrzelić.
ZRODLA_USTALENIA = frozenset(
    {
        "naglowek",
        "dzial1",
        "dzial2",
        "dzial3",
        "dzial4",
        "dzial5",
        "dzial6",
        "nieustalalne_z_odpisu",
        "nieprobkowane",
    }
)

# Działy rejestru wyprowadzone ze zbioru źródeł, a nie wypisane po raz drugi: dwie listy
# tych samych nazw rozjeżdżają się przy pierwszym dopisku.
ZRODLA_DZIALOW = frozenset(z for z in ZRODLA_USTALENIA if z.startswith("dzial"))

_POLA_REGULY = ("kod", "poziom", "rodzaj", "zrodlo", "opis", "podstawa_prawna", "zywotnosc")


@dataclass(frozen=True)
class Przeslanka:
    """Powód, dla którego brak dokumentu jest zgodny z prawem."""

    kod: str
    podstawa: str
    ustalane_z: tuple[str, ...]

    @property
    def ustalalna(self) -> bool:
        """Czy da się ją dziś rozstrzygnąć z odpisu."""
        return all(z not in {"nieustalalne_z_odpisu", "nieprobkowane"} for z in self.ustalane_z)


@dataclass(frozen=True)
class Termin:
    """Termin zastępczy — jedyny, który da się policzyć z danych publicznych.

    Termin właściwy biegnie od dnia zatwierdzenia sprawozdania, a rejestr publikuje wyłącznie
    datę złożenia. Dlatego katalog zna tylko ogranicznik z art. 69 ust. 2 i nie zna terminu
    indywidualnego.
    """

    rodzaj: str
    od: str
    miesiecy: int
    dni: int


@dataclass(frozen=True)
class Regula:
    """Jedna reguła sygnałowa."""

    kod: str
    poziom: Poziom
    rodzaj: Rodzaj
    zakres: Zakres | None
    zrodlo: str
    opis: str
    podstawa_prawna: str
    podstawa_potwierdzona: bool
    zywotnosc: str
    termin: Termin | None
    przeslanki_wykluczajace: tuple[Przeslanka, ...]

    @property
    def numer_dzialu(self) -> int | None:
        """Numer działu, o którym reguła orzeka — albo `None`, gdy nie orzeka o dziale."""
        if self.zrodlo not in ZRODLA_DZIALOW:
            return None
        return int(self.zrodlo.removeprefix("dzial"))

    @property
    def moze_wystrzelic(self) -> bool:
        """Czy reguła jest dziś w stanie wyprodukować sygnał.

        Dla reguły badającej brak dokumentu odpowiedź brzmi „tak" dopiero wtedy, gdy **każda**
        przesłanka wykluczająca daje się rozstrzygnąć. Dziś tak nie jest i raport ma o tym
        mówić wprost, zamiast milczeć albo oskarżać.
        """
        if self.rodzaj is not Rodzaj.BRAK_DOKUMENTU:
            return True
        return all(p.ustalalna for p in self.przeslanki_wykluczajace)


def _zablokowane() -> dict[str, str]:
    if not PLIK_ZABLOKOWANYCH.is_file():
        raise ConfigError(f"Brak pliku reguł zablokowanych: {PLIK_ZABLOKOWANYCH}")
    dane = yaml.safe_load(PLIK_ZABLOKOWANYCH.read_text(encoding="utf-8")) or {}
    return {str(w["kod"]): str(w["powod"]) for w in dane.get("zablokowane", [])}


def _przeslanka(surowa: Any, kod_reguly: str) -> Przeslanka:
    if not isinstance(surowa, dict) or not surowa.get("kod"):
        raise ConfigError(f"Reguła {kod_reguly}: przesłanka bez kodu")
    zrodla = tuple(str(z) for z in surowa.get("ustalane_z", []))
    nieznane = set(zrodla) - ZRODLA_USTALENIA
    if not zrodla or nieznane:
        raise ConfigError(
            f"Reguła {kod_reguly}, przesłanka {surowa['kod']}: "
            f"nieznane źródło ustalenia {nieznane or '(brak)'}"
        )
    if not surowa.get("podstawa"):
        raise ConfigError(f"Reguła {kod_reguly}, przesłanka {surowa['kod']}: brak podstawy prawnej")
    return Przeslanka(kod=str(surowa["kod"]), podstawa=str(surowa["podstawa"]), ustalane_z=zrodla)


def _termin(surowy: Any, kod_reguly: str) -> Termin:
    if not isinstance(surowy, dict):
        raise ConfigError(f"Reguła {kod_reguly}: reguła badająca brak dokumentu wymaga terminu")
    if surowy.get("od") != JEDYNY_PUNKT_ODNIESIENIA:
        raise ConfigError(
            f"Reguła {kod_reguly}: termin biegnie od {JEDYNY_PUNKT_ODNIESIENIA!r}, "
            f"a nie od {surowy.get('od')!r}. Rok obrotowy nie musi być kalendarzowy."
        )
    return Termin(
        rodzaj=str(surowy["rodzaj"]),
        od=str(surowy["od"]),
        miesiecy=int(surowy["miesiecy"]),
        dni=int(surowy["dni"]),
    )


def _sprawdz_przeslanki(surowa: dict[str, Any], kod: str) -> tuple[Przeslanka, ...]:
    przeslanki = tuple(_przeslanka(p, kod) for p in surowa.get("przeslanki_wykluczajace", []))
    kody = tuple(p.kod for p in przeslanki)
    if set(kody) != set(PRZESLANKI_BRAKU_DOKUMENTU):
        brakujace = set(PRZESLANKI_BRAKU_DOKUMENTU) - set(kody)
        nadmiarowe = set(kody) - set(PRZESLANKI_BRAKU_DOKUMENTU)
        raise ConfigError(
            f"Reguła {kod}: lista przesłanek wykluczających musi pokrywać zamrożoną szóstkę. "
            f"Brakuje {sorted(brakujace)}, nadmiarowe {sorted(nadmiarowe)}."
        )
    return przeslanki


def _zakres(surowa: dict[str, Any], kod: str) -> Zakres:
    """Zakres reguły badającej obecność wpisu. Bez domyślności — jak wszystko tutaj.

    Domyślność byłaby tu szczególnie droga: `pojedynczy_wpis` przyjęty milcząco robi z reguły
    o całym dziale regułę o jednym wpisie, czyli zamienia sygnał rozstrzygalny w nierozstrzygalny
    i odwrotnie, w zależności od tego, którą stronę ktoś wybierze na domyślną.
    """
    surowy = surowa.get("zakres")
    if not surowy:
        raise ConfigError(
            f"Reguła {kod}: pole `zakres` jest obowiązkowe przy badaniu obecności wpisu "
            f"i nie ma wartości domyślnej. Dozwolone: "
            f"{sorted(z.value for z in Zakres)}."
        )
    try:
        return Zakres(str(surowy))
    except ValueError as blad:
        raise ConfigError(f"Reguła {kod}: nieznany zakres {surowy!r}") from blad


def _sprawdz_zrodlo_dzialu(surowa: dict[str, Any], kod: str) -> None:
    """Reguła o obecności wpisu musi wskazywać dział, bo tylko dział ma stan w modelu odczytu."""
    zrodlo = str(surowa["zrodlo"])
    if zrodlo not in ZRODLA_DZIALOW:
        raise ConfigError(
            f"Reguła {kod}: obecność wpisu bada się w dziale, a {zrodlo!r} działem nie jest. "
            f"Dozwolone: {sorted(ZRODLA_DZIALOW)}."
        )


def _regula(surowa: Any, zablokowane: dict[str, str]) -> Regula:
    if not isinstance(surowa, dict):
        raise ConfigError("Wpis katalogu nie jest odwzorowaniem")
    brakujace = [pole for pole in _POLA_REGULY if not surowa.get(pole)]
    if brakujace:
        raise ConfigError(f"Reguła {surowa.get('kod', '?')}: brakuje pól {brakujace}")
    if "podstawa_potwierdzona" not in surowa:
        raise ConfigError(
            f"Reguła {surowa['kod']}: pole `podstawa_potwierdzona` jest obowiązkowe i nie ma "
            "wartości domyślnej — inaczej niezweryfikowany przepis wygląda jak zweryfikowany."
        )
    kod = str(surowa["kod"])
    if kod in zablokowane:
        raise ConfigError(f"Reguła {kod} jest zablokowana: {zablokowane[kod]}")
    rodzaj = Rodzaj(str(surowa["rodzaj"]))
    brak_dokumentu = rodzaj is Rodzaj.BRAK_DOKUMENTU
    if not brak_dokumentu:
        _sprawdz_zrodlo_dzialu(surowa, kod)
    return Regula(
        kod=kod,
        poziom=Poziom[str(surowa["poziom"]).upper()],
        rodzaj=rodzaj,
        zakres=None if brak_dokumentu else _zakres(surowa, kod),
        zrodlo=str(surowa["zrodlo"]),
        opis=str(surowa["opis"]),
        podstawa_prawna=str(surowa["podstawa_prawna"]),
        podstawa_potwierdzona=bool(surowa["podstawa_potwierdzona"]),
        zywotnosc=str(surowa["zywotnosc"]),
        termin=_termin(surowa.get("termin"), kod) if brak_dokumentu else None,
        przeslanki_wykluczajace=_sprawdz_przeslanki(surowa, kod) if brak_dokumentu else (),
    )


def wczytaj_katalog(katalog: Path | None = None) -> tuple[Regula, ...]:
    """Wczytuje wszystkie reguły, odmawiając przy pierwszej niekompletnej."""
    zrodlo = katalog if katalog is not None else KATALOG_REGUL
    zablokowane = _zablokowane()
    reguly: list[Regula] = []
    for plik in sorted(zrodlo.glob("*.yaml")):
        if plik.name == PLIK_ZABLOKOWANYCH.name:
            continue
        dane = yaml.safe_load(plik.read_text(encoding="utf-8")) or {}
        reguly.extend(_regula(surowa, zablokowane) for surowa in dane.get("reguly", []))
    kody = [r.kod for r in reguly]
    if len(set(kody)) != len(kody):
        raise ConfigError("Katalog zawiera powtórzone kody reguł")
    _sprawdz_reguly_calego_dzialu(reguly)
    return tuple(reguly)


def _sprawdz_reguly_calego_dzialu(reguly: list[Regula]) -> None:
    """W jednym dziale wolno mieć najwyżej jedną regułę o całym dziale.

    Dwie znaczyłyby, że ten sam fakt — niepustość działu — zapala dwa sygnały, w dodatku
    mogące różnić się poziomem. Czytelnik raportu zobaczyłby wtedy dwa zdarzenia tam, gdzie
    rejestr niesie jedno.
    """
    dzialy = [r.zrodlo for r in reguly if r.zakres is Zakres.CALY_DZIAL]
    powtorzone = sorted({d for d in dzialy if dzialy.count(d) > 1})
    if powtorzone:
        raise ConfigError(
            f"Więcej niż jedna reguła o całym dziale w działach {powtorzone} — "
            "niepustość działu zapaliłaby wtedy dwa sygnały z jednego faktu."
        )
