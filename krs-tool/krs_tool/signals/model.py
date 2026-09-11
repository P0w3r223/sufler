"""Trójwartościowy wynik reguły sygnałowej. Moduł czysty — bez wejścia/wyjścia i bez zegara.

`Sygnal | Wykluczony | Nieustalony` to ADR-0001 decyzja 4, przeniesiona z dokumentu do typów.

**Każdy z trzech werdyktów niesie `zalozenia`, nie tylko nieustalony.** Założenie, przy którym
policzono termin, znika najgroźniej właśnie tam, gdzie werdykt jest najmocniejszy — więc slot na
nie mają wszystkie trzy, a przegląd kodu pokazał, że przy dwóch go brakowało.
Sedno jest w tym, czego tu **nie ma**: nie ma wartości „prawdopodobnie", nie ma wyniku
domyślnego i nie ma drogi od nierozstrzygniętej przesłanki do sygnału. Przesłanka, której nie
umiemy rozstrzygnąć, kończy się `Nieustalony` z jej kodem na liście — i to jest jedyne
wyjście, jakie ma.

**Wynik nie układa zdań.** Niesie kody: kod obserwacji albo kod przesłanki z katalogu. Zdania
mieszkają w `texts.py` i tam podlegają skanowi zamkniętego leksykonu (reguła granic 11);
gdyby warstwa sygnałów pisała treść dla człowieka, ten skan trzeba by rozciągnąć na cały
pakiet, zamiast czytać jeden plik.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..identity import NumerKRS
from ..odpis.model import DzienBilansowy
from .katalog import Poziom, Regula
from .terminy import TerminUstawowy


class Obserwacja(Enum):
    """Co w odpisie zobaczono — albo czego zobaczyć się nie dało.

    Kody, nie zdania. Rozróżnienie „dział pusty" od „działu nie było w pliku" jest tu, bo bez
    niego sygnał najwyższego poziomu opierałby się na dwuznaczności (`odpis/model.py`).
    """

    DZIAL_NIEPUSTY = "dzial_niepusty"
    DZIAL_PUSTY = "dzial_pusty"
    DZIAL_NIEOBECNY_W_PLIKU = "dzial_nieobecny_w_pliku"
    WPIS_NIEROZROZNIALNY_W_DZIALE = "wpis_nierozroznialny_w_dziale"
    BRAK_WZMIANKI_ZA_OKRES = "brak_wzmianki_za_okres"
    BRAK_CZYTELNEGO_OKRESU = "brak_czytelnego_okresu"
    WZMIANKA_O_NIECZYTELNYM_OKRESIE = "wzmianka_o_nieczytelnym_okresie"
    OGRANICZNIK_NIE_UPLYNAL = "ogranicznik_nie_uplynal"
    ZALOZENIE_CIAGLOSCI_ROKU_OBROTOWEGO = "zalozenie_ciaglosci_roku_obrotowego"


class Powod(Enum):
    """Dlaczego czegoś nie ustalono. Cztery powody, a każdy zamyka kto inny.

    To rozróżnienie jest w wyniku, a nie w komentarzu, bo czytelnik raportu ma prawo wiedzieć,
    na co właściwie czeka: na stanowisko ministerstwa i pomiar, na jedną zmianę w czytniku,
    czy na inny odpis.
    """

    KATALOG_DEKLARUJE_NIEUSTALALNOSC = "katalog_deklaruje_nieustalalnosc"
    CZYTNIK_NIE_WYCIAGA_DANEJ = "czytnik_nie_wyciaga_danej"
    ODPIS_NIE_ROZSTRZYGA = "odpis_nie_rozstrzyga"
    OBSERWACJA = "obserwacja"


class Werdykt(Enum):
    """Rozstrzygnięcie pojedynczej przesłanki wykluczającej.

    Trzy wartości, nie dwie: „nie wiadomo" nie jest odmianą „nie zachodzi". Gdyby było,
    nierozstrzygnięta przesłanka milcząco przepuszczałaby sygnał — czyli dokładnie to, przed
    czym stoi ADR-0001 decyzja 4.
    """

    ZACHODZI = "zachodzi"
    NIE_ZACHODZI = "nie_zachodzi"
    NIEUSTALONA = "nieustalona"


@dataclass(frozen=True)
class Niewiadoma:
    """Jedna pozycja, której nie ustalono: kod przesłanki albo obserwacji, plus powód.

    Tej samej postaci używają `zalozenia` każdego werdyktu — z rozmysłem. Założenie nie jest
    słabszą odmianą niewiedzy, ale ma tę samą własność: **musi dojechać na wydruk**, a wydruk
    ma je czytać tym samym mechanizmem, żeby nie dało się go zgubić przy jednej gałęzi.
    """

    kod: str
    powod: Powod


@dataclass(frozen=True)
class Sygnal:
    """Reguła zapłonęła: w odpisie stoi to, o czym reguła mówi."""

    regula: Regula
    obserwacja: Obserwacja
    po_okresie: DzienBilansowy | None = None
    termin: TerminUstawowy | None = None
    zalozenia: tuple[Niewiadoma, ...] = ()

    @property
    def poziom(self) -> Poziom:
        return self.regula.poziom


@dataclass(frozen=True)
class Wykluczony:
    """Reguła nie zapłonęła, i wiadomo dlaczego.

    `powod` to kod przesłanki wykluczającej z katalogu albo kod obserwacji. Wykluczenie jest
    wynikiem **rozstrzygniętym** — stwierdzeniem, że tego wpisu nie ma — i tym różni się od
    `Nieustalony`, który mówi tylko, że nie wiemy.
    """

    regula: Regula
    powod: str
    po_okresie: DzienBilansowy | None = None
    termin: TerminUstawowy | None = None
    zalozenia: tuple[Niewiadoma, ...] = ()


@dataclass(frozen=True)
class Nieustalony:
    """Reguły nie dało się rozstrzygnąć, a `nierozstrzygniete` mówi, co stanęło na przeszkodzie.

    Lista nigdy nie jest pusta: wynik „nieustalony bez powodu" byłby nieodróżnialny od usterki.
    """

    regula: Regula
    nierozstrzygniete: tuple[Niewiadoma, ...]
    po_okresie: DzienBilansowy | None = None
    termin: TerminUstawowy | None = None
    zalozenia: tuple[Niewiadoma, ...] = ()

    def __post_init__(self) -> None:
        if not self.nierozstrzygniete:
            raise ValueError(
                f"Reguła {self.regula.kod}: wynik nieustalony musi nazwać, czego nie ustalono."
            )


Wynik = Sygnal | Wykluczony | Nieustalony


@dataclass(frozen=True)
class Ocena:
    """Wynik przejścia całego katalogu po jednym odpisie.

    `stan_z_dnia` pochodzi z odpisu i jest jedyną datą, wobec której cokolwiek tu obliczono
    (ADR-0001 decyzja 6). Dzięki temu ta sama ocena, powtórzona jutro, da ten sam wynik —
    na tym stoi polecenie `odtworz` z kroku 6.
    """

    numer: NumerKRS
    nazwa: str
    stan_z_dnia: date
    syntetyczny: bool
    wyniki: tuple[Wynik, ...]

    def sygnaly(self) -> tuple[Sygnal, ...]:
        return tuple(w for w in self.wyniki if isinstance(w, Sygnal))

    def nieustalone(self) -> tuple[Nieustalony, ...]:
        return tuple(w for w in self.wyniki if isinstance(w, Nieustalony))

    def wykluczone(self) -> tuple[Wykluczony, ...]:
        return tuple(w for w in self.wyniki if isinstance(w, Wykluczony))
