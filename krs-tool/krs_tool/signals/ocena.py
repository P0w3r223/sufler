"""Przejście katalogu reguł po jednym odpisie. Moduł czysty — bez plików, bez zegara.

Cała treść tego modułu sprowadza się do jednego zdania: **czego z odpisu nie wynika, tego
wynik nie twierdzi**. Stąd trzy rzeczy, które przy pierwszym czytaniu wyglądają na braki:

**Reguła o pojedynczym wpisie, dzieląca dział z inną taką regułą, jest nierozstrzygalna.**
Model odczytu wie o dziale tyle, czy jest pusty; nazw kluczy wewnątrz działu nikt nie zmierzył
(`docs/pomiary.md`). Cztery reguły działu 4 są więc dziś nie do odróżnienia od siebie —
i zamiast wybierać między nimi po nazwie, którą ktoś kiedyś zgadł, wszystkie cztery kończą
jako `Nieustalony`. Sygnał, który z tego działu naprawdę wynika, niesie osobna reguła
o całym dziale.

**Przesłanka, której nie umie ustalić katalog ALBO nie umie wyciągnąć czytnik, kończy tak
samo — nieustaleniem — ale wynik rozróżnia który to przypadek.** Pierwsze zamyka stanowisko
ministerstwa albo pomiar, drugie zamyka jedna zmiana w `odpis/czytanie.py`, i czytelnik
raportu ma prawo wiedzieć, na co czeka.

**Jedynym „dziś" jest `stanZDnia` z odpisu** (ADR-0001 decyzja 6). Reguła granic 4 zabrania
tu zegara, więc powtórzenie oceny jutro da ten sam wynik — na tym stoi `odtworz` z kroku 6.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ..errors import ConfigError
from ..odpis.model import (
    REJESTR_PRZEDSIEBIORCOW,
    RODZAJ_SPRAWOZDANIE_FINANSOWE,
    DzienBilansowy,
    Odpis,
)
from .katalog import ZRODLA_DZIALOW, Przeslanka, Regula, Rodzaj, Termin, Zakres
from .model import (
    Nieustalony,
    Niewiadoma,
    Obserwacja,
    Ocena,
    Powod,
    Sygnal,
    Werdykt,
    Wykluczony,
    Wynik,
)
from .terminy import termin_nastepnego_okresu, termin_zastepczy


def ocen_odpis(odpis: Odpis, reguly: Sequence[Regula]) -> Ocena:
    """Wynik dla każdej reguły katalogu — po jednym, w kolejności katalogu."""
    wspoldzielone = _dzialy_z_wieloma_wpisami(reguly)
    return Ocena(
        numer=odpis.numer,
        nazwa=odpis.nazwa,
        stan_z_dnia=odpis.stan_z_dnia,
        syntetyczny=odpis.syntetyczny,
        wyniki=tuple(_ocen_regule(regula, odpis, wspoldzielone) for regula in reguly),
    )


def _dzialy_z_wieloma_wpisami(reguly: Sequence[Regula]) -> frozenset[str]:
    """Działy, w których o pojedynczy wpis dobija się więcej niż jedna reguła."""
    pojedyncze = [r.zrodlo for r in reguly if r.zakres is Zakres.POJEDYNCZY_WPIS]
    return frozenset(zrodlo for zrodlo in pojedyncze if pojedyncze.count(zrodlo) > 1)


def _ocen_regule(regula: Regula, odpis: Odpis, wspoldzielone: frozenset[str]) -> Wynik:
    if regula.rodzaj is Rodzaj.BRAK_DOKUMENTU:
        return _ocen_brak_dokumentu(regula, odpis)
    return _ocen_obecnosc_wpisu(regula, odpis, regula.zrodlo in wspoldzielone)


# --------------------------------------------------------------------------------------
# Obecność wpisu w dziale
# --------------------------------------------------------------------------------------


def _ocen_obecnosc_wpisu(regula: Regula, odpis: Odpis, wspoldzielony: bool) -> Wynik:
    """Trzy stany działu dają trzy różne wyniki, i to jest cała ta reguła.

    Dział pusty **wyklucza** wpis — to jest rozstrzygnięcie, nie niewiedza, i dlatego u spółki
    bez kłopotów ocena mówi coś mocnego, a nie milczy.
    """
    dzial = odpis.dzial(_numer_dzialu(regula))
    if dzial is None or not dzial.obecny:
        return Nieustalony(
            regula,
            (Niewiadoma(Obserwacja.DZIAL_NIEOBECNY_W_PLIKU.value, Powod.OBSERWACJA),),
        )
    if dzial.pusty:
        return Wykluczony(regula, Obserwacja.DZIAL_PUSTY.value)
    if regula.zakres is Zakres.CALY_DZIAL or not wspoldzielony:
        return Sygnal(regula, Obserwacja.DZIAL_NIEPUSTY)
    return Nieustalony(
        regula,
        (
            Niewiadoma(
                Obserwacja.WPIS_NIEROZROZNIALNY_W_DZIALE.value,
                Powod.CZYTNIK_NIE_WYCIAGA_DANEJ,
            ),
        ),
    )


def _numer_dzialu(regula: Regula) -> int:
    numer = regula.numer_dzialu
    if numer is None:  # pragma: no cover - loader odmawia takiej reguły przy wczytaniu
        raise ConfigError(f"Reguła {regula.kod} bada obecność wpisu poza działem: {regula.zrodlo}")
    return numer


# --------------------------------------------------------------------------------------
# Brak dokumentu
# --------------------------------------------------------------------------------------


def _ocen_brak_dokumentu(regula: Regula, odpis: Odpis) -> Wynik:
    """Reguła o braku wzmianki — jedyna, która potrzebuje arytmetyki terminu.

    Okresem kandydującym jest rok obrotowy **następujący po ostatnim, za który wzmianka
    stoi w odpisie**. Kolejne lata wymagałyby wyprowadzania kolejnych dni bilansowych, czyli
    mnożenia założenia o ciągłości roku obrotowego; jeden kandydat wystarcza, żeby wynik był
    rozstrzygnięty albo uczciwie nierozstrzygnięty, i nie kosztuje ani jednego założenia
    więcej.
    """
    termin_katalogu = _termin_reguly(regula)
    ostatni = _ostatni_dzien_bilansowy(odpis)
    niewiadome = _niewiadome_przeslanki(regula, odpis)
    if ostatni is None:
        return Nieustalony(
            regula,
            (Niewiadoma(Obserwacja.BRAK_CZYTELNEGO_OKRESU.value, Powod.OBSERWACJA), *niewiadome),
        )
    termin = termin_nastepnego_okresu(termin_zastepczy(ostatni, termin_katalogu))
    if odpis.stan_z_dnia <= termin:
        return Wykluczony(
            regula, Obserwacja.OGRANICZNIK_NIE_UPLYNAL.value, po_okresie=ostatni, termin=termin
        )
    zachodzaca = _pierwsza_zachodzaca(regula, odpis)
    if zachodzaca is not None:
        return Wykluczony(regula, zachodzaca, po_okresie=ostatni, termin=termin)
    if niewiadome:
        zalozenie = Niewiadoma(
            Obserwacja.ZALOZENIE_CIAGLOSCI_ROKU_OBROTOWEGO.value, Powod.OBSERWACJA
        )
        return Nieustalony(regula, (*niewiadome, zalozenie), po_okresie=ostatni, termin=termin)
    return Sygnal(regula, Obserwacja.BRAK_WZMIANKI_ZA_OKRES, po_okresie=ostatni, termin=termin)


def _termin_reguly(regula: Regula) -> Termin:
    termin = regula.termin
    if termin is None:  # pragma: no cover - loader odmawia reguły braku dokumentu bez terminu
        raise ConfigError(f"Reguła {regula.kod} bada brak dokumentu, a nie niesie terminu.")
    return termin


def _ostatni_dzien_bilansowy(odpis: Odpis) -> DzienBilansowy | None:
    """Najpóźniejszy dzień bilansowy, za jaki w odpisie stoi wzmianka o sprawozdaniu.

    Wzmianka z nieczytelnym zapisem okresu nie bierze udziału — jej okresu nie znamy, a
    zgadywanie go byłoby wymyślaniem roku obrotowego (`odpis/czytanie.py`).
    """
    dni = [
        w.okres.do
        for w in odpis.wzmianki
        if w.rodzaj == RODZAJ_SPRAWOZDANIE_FINANSOWE and w.okres is not None
    ]
    return max(dni) if dni else None


# --------------------------------------------------------------------------------------
# Przesłanki wykluczające
# --------------------------------------------------------------------------------------


def _werdykt(przeslanka: Przeslanka, odpis: Odpis) -> Werdykt:
    """Dwie bramki, w tej kolejności: co mówi katalog, a potem co umie czytnik.

    Katalog mówi o **rejestrze** — czy przesłanka w ogóle daje się ustalić z odpisu. Czytnik
    mówi o **nas** — czy już umiemy tę daną z odpisu wyciągnąć. Zlanie tych dwóch w jedno
    „nie wiadomo" kosztowałoby czytelnika raportu informację o tym, na co czeka.
    """
    if not przeslanka.ustalalna:
        return Werdykt.NIEUSTALONA
    rozstrzygacz = _ROZSTRZYGACZE.get(przeslanka.kod)
    if rozstrzygacz is None:
        return Werdykt.NIEUSTALONA
    return rozstrzygacz(przeslanka, odpis)


def _powod_niewiedzy(przeslanka: Przeslanka) -> Powod:
    if not przeslanka.ustalalna:
        return Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC
    if przeslanka.kod not in _ROZSTRZYGACZE:
        return Powod.CZYTNIK_NIE_WYCIAGA_DANEJ
    return Powod.ODPIS_NIE_ROZSTRZYGA


def _niewiadome_przeslanki(regula: Regula, odpis: Odpis) -> tuple[Niewiadoma, ...]:
    return tuple(
        Niewiadoma(p.kod, _powod_niewiedzy(p))
        for p in regula.przeslanki_wykluczajace
        if _werdykt(p, odpis) is Werdykt.NIEUSTALONA
    )


def _pierwsza_zachodzaca(regula: Regula, odpis: Odpis) -> str | None:
    """Kod pierwszej zachodzącej przesłanki — powodu, dla którego dokumentu być nie musi."""
    for przeslanka in regula.przeslanki_wykluczajace:
        if _werdykt(przeslanka, odpis) is Werdykt.ZACHODZI:
            return przeslanka.kod
    return None


def _z_naglowka(przeslanka: Przeslanka, odpis: Odpis) -> Werdykt:
    """Czy podmiot stoi poza rejestrem przedsiębiorców — jedyna przesłanka czytana z nagłówka.

    Pusty rejestr w odpisie to niewiedza, nie „jest w rejestrze przedsiębiorców". Sprawozdania
    podmiotów spoza tego rejestru trafiają do Szefa KAS i są objęte tajemnicą skarbową, więc
    pomyłka w tę stronę produkowałaby ocenę o dokumencie, którego nie ma gdzie szukać.
    """
    if not odpis.rejestr:
        return Werdykt.NIEUSTALONA
    if odpis.rejestr == REJESTR_PRZEDSIEBIORCOW:
        return Werdykt.NIE_ZACHODZI
    return Werdykt.ZACHODZI


def _z_dzialow(przeslanka: Przeslanka, odpis: Odpis) -> Werdykt:
    """Przesłanka ustalana z działów — czytana dokładnie z tych, które wskazuje katalog.

    Numery działów biorą się z `ustalane_z` samej przesłanki, a nie z listy obok: gdyby prawnik
    dopisał trzeci dział, kod ma tam zajrzeć bez zmiany kodu.

    Niepusty dział kończy się niewiedzą, nie potwierdzeniem. Stoi w nim wpis, ale który —
    z odpisu nie wynika, więc „w dziale 4 coś jest" nie znaczy „to jest upadłość".
    """
    numery = [z for z in przeslanka.ustalane_z if z in ZRODLA_DZIALOW]
    stany = [odpis.dzial(int(z.removeprefix("dzial"))) for z in numery]
    if not stany or any(d is None or not d.obecny for d in stany):
        return Werdykt.NIEUSTALONA
    if all(d.pusty for d in stany if d is not None):
        return Werdykt.NIE_ZACHODZI
    return Werdykt.NIEUSTALONA


# Przesłanki, które czytnik umie dziś rozstrzygnąć. Klucze pochodzą z zamrożonej szóstki
# (`katalog.PRZESLANKI_BRAKU_DOKUMENTU`) i test pilnuje, żeby nie było tu klucza spoza niej —
# rozstrzygacz przypisany do nieistniejącej przesłanki jest martwy i nikt tego nie zauważy.
_ROZSTRZYGACZE: dict[str, Callable[[Przeslanka, Odpis], Werdykt]] = {
    "poza_rejestrem_przedsiebiorcow": _z_naglowka,
    "upadlosc_lub_restrukturyzacja": _z_dzialow,
}
