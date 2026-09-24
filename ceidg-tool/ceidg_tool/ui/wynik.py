"""Koperta wyniku — wartość czysta, jedno źródło prawdy o tym, co się stało (ADR-0024).

Ekran i koperta są **dwoma renderowaniami tych samych wartości**, nie dwoma pomiarami (decyzja
1B). Dlatego nic się tu nie liczy: pola przychodzą policzone, a moduł tylko nadaje im nazwy,
które wołający może zapamiętać. Odrzucona alternatywa — zserializowany `Block` — uczyniłaby
z `texts` interfejs programistyczny, czyli odebrałaby ekranowi prawo do przeredagowania zdania.

**Czysty, i to jest twierdzenie o imporcie, nie tylko o wejściu-wyjściu.** Skan reguły 6 patrzy
na korzenie importów (`httpx`, `sqlite3`, `rich`, `openpyxl`), więc sięgnięcie tutaj po
`ExportSummary` albo `RunResult` z `pipeline` przeszłoby przez skan, znosząc powód, dla którego
reguła istnieje: koperta dałaby się wtedy zbudować tylko tam, gdzie da się zbudować `pipeline`.
Kształt jest więc taki sam jak `texts.SummaryInput` — zwykłe wartości, które wypełnia korzeń
kompozycji.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, assert_never

from ..criteria import Criteria

# Numer kontraktu, nie numer wydania. Rośnie wyłącznie przy **usunięciu albo zmianie nazwy**
# pola; dołożenie nowego klucza nie psuje niczyjego odczytu, więc nie jest zmianą wersji.
WERSJA_KOPERTY: Final = 1

Status = Literal["ok", "brak_trafien", "nic_do_zrobienia", "przerwano", "blad"]
"""Zamknięty zbiór — po to, żeby wołający mógł się rozgałęzić i skończyć rozgałęzianie.

`brak_trafien` i `przerwano` to dwa różne fakty i koperta ma je rozróżniać: przy `count == 0`
pod `--tak` pytanie o poszerzenie rozstrzyga się bezpieczną domyślną „wyjdź" (ADR-0017 —
harmonogramowi nie wolno poszerzyć populacji), czyli **zero trafień**; operator, który
przeczytał tabelę kosztów i zrezygnował, to **przerwano**.
"""

# Kod dla obu odmian „skończyło się i nie ma nic do roboty". Osobny od zera, bo wołający pyta
# najpierw o kod wyjścia, a jedno zero dla „pobrano 1234 firmy" i dla „nie ma ani jednej"
# znaczy, że musi rozpakować kopertę, zanim się dowie, czy w ogóle coś ma.
KOD_PUSTO: Final = 4


def kod_wyjscia(status: Status, kod_bledu: int | None = None) -> int:
    """Kod wyjścia wyprowadzony ze statusu — jedna funkcja, żeby te dwa nie mogły się rozjechać.

    `blad` nie ma własnego kodu: bierze go z taksonomii `errors.py` (1 nieodwracalny,
    2 wznawialny, 3 konfiguracja/uwierzytelnienie), której ta koperta nie zmienia.

    Wyczerpanie niesie `match` z `assert_never`, a nie słownik `dict[Status, int]`. Różnica jest
    nośna: mypy nie sprawdza, czy literał słownika pokrywa wszystkie warianty `Literal`, więc
    szósty status wszedłby do zbioru cicho i wywrócił program dopiero na `KeyError`. Tutaj nie
    da się dopisać wariantu bez czerwonego mypy.
    """
    match status:
        case "ok" | "przerwano":
            return 0
        case "brak_trafien" | "nic_do_zrobienia":
            return KOD_PUSTO
        case "blad":
            if kod_bledu is None:
                raise ValueError("Status 'blad' wymaga kodu z taksonomii errors.py.")
            return kod_bledu
    assert_never(status)


@dataclass(frozen=True)
class Uwaga:
    """Uwaga o przebiegu: kod do rozgałęziania, zdanie do przeczytania.

    **`tekst` nie jest kontraktem i jest to zapisane celowo** (ADR-0024, poddecyzja do 1).
    Ten projekt przeredagowuje zdania za każdym razem, gdy znajdzie defekt, więc wołający
    dopasowujący się do napisu dopasowuje się do czegoś, co się zmieni. `kod` jest tam, gdzie
    zamknięty zbiór już istnieje (`PowodBrakuRaportu`, `OgraniczenieKod`, `DLACZEGO_PUSTO`);
    gdzie nie istnieje, zostaje sama proza i pusty kod — zamiana wszystkich naraz nie leży na
    ścieżce krytycznej, a kody dochodzą wtedy, gdy ktoś ich potrzebuje.
    """

    tekst: str
    kod: str = ""


@dataclass(frozen=True)
class Blad:
    """Błąd w postaci, którą wołający czyta, plus kod, którym program wychodzi.

    `kod` nie trafia do koperty osobnym polem, bo już tam jest — jako `kod_wyjscia` na
    najwyższym poziomie. Siedzi tutaj, żeby „status jest błędem" i „kod wyjścia pochodzi
    z taksonomii" były jedną wartością, a nie dwoma polami, które da się ustawić niezgodnie.
    """

    typ: str
    komunikat: str
    kod: int


# Klucze, które ma każda koperta, niezależnie od polecenia i wyniku. Wypisane osobno, bo
# `dodatki` nie wolno ich przykryć: cicha podmiana `status` polem właściwym dla jednego
# polecenia byłaby najgorszym możliwym defektem tego modułu.
KLUCZE_WSPOLNE: Final = frozenset(
    {
        "wersja",
        "polecenie",
        "status",
        "kod_wyjscia",
        "demo",
        "zapytania",
        "uwagi",
        "srodowisko",
        "kryteria",
        "run_ids",
        "rekordy",
        "pliki",
        "blad",
    }
)


@dataclass(frozen=True)
class Wynik:
    """Co się stało — w wartościach, nie w zdaniach.

    Pola wspólne (`wersja`, `polecenie`, `status`, `kod_wyjscia`, `demo`, `zapytania`, `uwagi`)
    są w kopercie **zawsze**; reszta pojawia się wtedy, gdy dane polecenie albo dane zakończenie
    ją wytworzyło. `szukaj-pkd` nie ma środowiska i nie udaje, że ma.

    `zapytania` jest zawsze, również jako zero: „to nie kosztowało żadnego żądania" jest
    twierdzeniem prawdziwym i przydatnym, a nie brakiem danych.
    """

    polecenie: str
    status: Status
    demo: bool = False
    zapytania: int = 0
    srodowisko: str | None = None
    kryteria: Criteria | None = None
    run_ids: tuple[str, ...] = ()
    rekordy: int | None = None
    pliki: tuple[Path, ...] = ()
    uwagi: tuple[Uwaga, ...] = ()
    blad: Blad | None = None
    # Pola własne polecenia: `firma`, `runy`, `raporty`, `trafienia`, czwórka `aktualizuj`.
    dodatki: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Dwa niezmienniki, których złamanie nie miałoby bez tego żadnego obserwatora.

        Pierwszy: `status == "blad"` **wtedy i tylko wtedy**, gdy jest `blad`. Koperta mówiąca
        „ok" i niosąca błąd — albo odwrotnie — jest gorsza niż brak koperty, bo wołający
        rozgałęzia się na statusie i do reszty nigdy nie zajrzy.

        Drugi: `dodatki` nie przykrywają pól wspólnych. Polecenie z własnymi „rekordami"
        podmieniłoby znaczenie klucza, który czytają wszyscy.
        """
        if (self.status == "blad") != (self.blad is not None):
            raise ValueError(
                f"Status {self.status!r} nie zgadza się z obecnością błędu "
                f"({'jest' if self.blad else 'brak'})."
            )
        kolizje = KLUCZE_WSPOLNE & set(self.dodatki)
        if kolizje:
            raise ValueError(f"Dodatki polecenia przykrywają pola wspólne: {sorted(kolizje)}.")

    @property
    def kod_wyjscia(self) -> int:
        return kod_wyjscia(self.status, self.blad.kod if self.blad else None)

    def koperta(self) -> dict[str, object]:
        """Słownik do zapisania — same typy, które JSON zna, plus `Path` zamieniona na napis.

        Maskowania tu nie ma i być nie powinno: należy do `jsonout`, czyli do jedynego modułu,
        który naprawdę pisze (reguła granic 15). Dwa miejsca maskujące to ta sama pomyłka co
        dwa egzemplarze `richtext.safe`, a tutaj kosztowałaby ładunek tokenu z numerem PESEL.
        """
        koperta: dict[str, object] = {
            "wersja": WERSJA_KOPERTY,
            "polecenie": self.polecenie,
            "status": self.status,
            "kod_wyjscia": self.kod_wyjscia,
            # Szósty znacznik pokazu (ADR-0014). Pozostałe pięć opisują ekran, skoroszyt
            # i nazwę pliku — czyli kanały, których agent nie czyta. Za agentem nikt nie
            # ogląda pierwszego ekranu, więc to jest **jedyne** miejsce, w którym może się
            # dowiedzieć, że dane są wymyślone.
            "demo": self.demo,
            "zapytania": self.zapytania,
            "uwagi": [{"kod": uwaga.kod, "tekst": uwaga.tekst} for uwaga in self.uwagi],
        }
        if self.srodowisko is not None:
            koperta["srodowisko"] = self.srodowisko
        if self.kryteria is not None:
            koperta["kryteria"] = self.kryteria.model_dump(mode="json")
        if self.run_ids:
            koperta["run_ids"] = list(self.run_ids)
        if self.rekordy is not None:
            koperta["rekordy"] = self.rekordy
        if self.pliki:
            # Zamiana na napis **tutaj**, a nie w `jsonout`: tam maskowanie chodzi po napisach,
            # więc `Path`, która dojechałaby nietknięta, ominęłaby maskę (ADR-0024, pułapka 2).
            koperta["pliki"] = [str(sciezka) for sciezka in self.pliki]
        if self.blad is not None:
            koperta["blad"] = {"typ": self.blad.typ, "komunikat": self.blad.komunikat}
        koperta.update(self.dodatki)
        return koperta
