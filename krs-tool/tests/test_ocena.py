"""Przejście katalogu po odpisie: co się zapala, co wyklucza, a czego nie da się ustalić.

Dwie rzeczy, które ten plik sprawdza uparcie, bo obie są nie do odróżnienia od usterki, dopóki
nie stoi przy nich test. **Dział pusty wyklucza wpis** — to jest rozstrzygnięcie, nie niewiedza.
I **dział niepusty niczego nie przesądza** tam, gdzie o ten sam dział dobija się kilka reguł:
z odpisu nie wynika, który wpis w nim stoi, a wybór po nazwie byłby zgadywaniem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from krs_tool.odpis.czytanie import wczytaj_odpis
from krs_tool.odpis.model import Odpis
from krs_tool.signals import ocena as modul_oceny
from krs_tool.signals.katalog import PRZESLANKI_BRAKU_DOKUMENTU, Poziom, wczytaj_katalog
from krs_tool.signals.model import (
    Nieustalony,
    Obserwacja,
    Powod,
    Sygnal,
    Werdykt,
    Wykluczony,
    Wynik,
)
from krs_tool.signals.ocena import ocen_odpis
from tests.budowniczy import OKRES_KROPKOWY, OKRES_NIEZNANY, wzmianka, zbuduj_odpis

REGULA_BRAKU = "brak_wpisu_o_sprawozdaniu"
REGULA_CALEGO_DZIALU = "dzial4_niepusty"
REGULA_KURATORA = "dzial5_kurator"
WPIS = {"cokolwiek": [{"pozycja": 1}]}

# Sprawozdanie za rok obrotowy kończący się 31.12.2023 — ogranicznik zastępczy dla roku
# NASTĘPNEGO wypada wtedy 15.07.2025, a domyślny stan rejestru z budowniczego jest późniejszy.
SPRAWOZDANIE_ZA_2023 = (wzmianka("10.05.2024", OKRES_KROPKOWY),)


def ocen(**kwargs: Any) -> dict[str, Wynik]:
    """Ocena odpisu syntetycznego, po kodzie reguły — testy mówią o regułach, nie o indeksach."""
    odpis = wczytaj_odpis(zbuduj_odpis(**kwargs))
    return {wynik.regula.kod: wynik for wynik in ocen_odpis(odpis, wczytaj_katalog()).wyniki}


def test_katalog_przechodzi_w_calosci_i_nic_nie_ginie() -> None:
    wyniki = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023)

    assert len(wyniki) == len(wczytaj_katalog())


def test_pusty_dzial_wyklucza_wpis_zamiast_milczec() -> None:
    """U spółki bez kłopotów ocena mówi coś mocnego: tego wpisu w rejestrze nie ma."""
    wyniki = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023)

    wykluczone = [w for w in wyniki.values() if isinstance(w, Wykluczony)]
    assert {w.regula.kod for w in wykluczone} == {
        "dzial4_zaleglosci_podatkowe_i_celne",
        "dzial4_zaleglosci_wobec_zus",
        "dzial4_wierzyciele_z_tytulami_wykonawczymi",
        "dzial4_postepowanie_upadlosciowe",
        REGULA_CALEGO_DZIALU,
        REGULA_KURATORA,
        "dzial6_otwarcie_likwidacji",
        "dzial6_rozwiazanie_podmiotu",
        "dzial6_polaczenie_podzial_przeksztalcenie",
    }
    assert all(w.powod == Obserwacja.DZIAL_PUSTY.value for w in wykluczone)


def test_niepusty_dzial_4_zapala_regule_o_calym_dziale() -> None:
    """Najtańszy sygnał poziomu terminalnego, jaki ten produkt ma — i jedyny, który tu wynika."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, dzial4=WPIS)[REGULA_CALEGO_DZIALU]

    assert isinstance(wynik, Sygnal)
    assert wynik.obserwacja is Obserwacja.DZIAL_NIEPUSTY
    assert wynik.poziom is Poziom.TERMINALNY


def test_niepusty_dzial_4_nie_wskazuje_ktory_to_wpis() -> None:
    """Cztery reguły na jednym dziale są dziś nierozróżnialne i tak mają się przedstawiać.

    Nazwy kluczy wewnątrz działu nie zostały zmierzone (`docs/pomiary.md`), więc wybór jednej
    z czterech byłby zgadywaniem — a zgadywanie w tę stronę produkuje zarzut.
    """
    wyniki = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, dzial4=WPIS)

    cztery = [
        w for kod, w in wyniki.items() if kod.startswith("dzial4_") and kod != REGULA_CALEGO_DZIALU
    ]
    assert len(cztery) == 4
    for wynik in cztery:
        assert isinstance(wynik, Nieustalony)
        assert wynik.nierozstrzygniete[0].kod == Obserwacja.WPIS_NIEROZROZNIALNY_W_DZIALE.value
        assert wynik.nierozstrzygniete[0].powod is Powod.CZYTNIK_NIE_WYCIAGA_DANEJ


def test_jedyna_regula_dzialu_jest_rozstrzygalna() -> None:
    """Dział 5 niesie jedną regułę, więc niepustość mówi wprost, o co chodzi."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, dzial5=WPIS)[REGULA_KURATORA]

    assert isinstance(wynik, Sygnal)
    assert wynik.poziom is Poziom.WYPRZEDZAJACY


def test_niepusty_dzial_6_nie_zapala_niczego() -> None:
    """Bo poziomy kandydatów się różnią: likwidacja jest terminalna, przekształcenie to kontekst.

    Reguły o całym dziale 6 nie ma i nie będzie, dopóki jeden wpis w tym dziale potrafi
    znaczyć koniec spółki, a drugi zwykłą reorganizację.
    """
    wyniki = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, dzial6=WPIS)

    szostki = [w for kod, w in wyniki.items() if kod.startswith("dzial6_")]
    assert len(szostki) == 3
    assert all(isinstance(w, Nieustalony) for w in szostki)


def test_brak_dzialu_w_pliku_to_nie_to_samo_co_dzial_pusty() -> None:
    """Trzy stany działu, nie dwa — inaczej sygnał najwyższego poziomu stoi na dwuznaczności."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, bez_dzialu=4)[REGULA_CALEGO_DZIALU]

    assert isinstance(wynik, Nieustalony)
    assert wynik.nierozstrzygniete[0].kod == Obserwacja.DZIAL_NIEOBECNY_W_PLIKU.value


# --------------------------------------------------------------------------------------
# Reguła braku wzmianki o sprawozdaniu
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "odpis",
    [
        pytest.param({"sprawozdania": SPRAWOZDANIE_ZA_2023}, id="zdrowa"),
        pytest.param({"sprawozdania": SPRAWOZDANIE_ZA_2023, "dzial4": WPIS}, id="dzial4"),
        pytest.param({"sprawozdania": SPRAWOZDANIE_ZA_2023, "dzial6": WPIS}, id="dzial6"),
        pytest.param({"sprawozdania": ()}, id="bez_wzmianek"),
        pytest.param(
            {"sprawozdania": (wzmianka("15.07.2020", OKRES_NIEZNANY),)}, id="okres_nieczytelny"
        ),
    ],
)
def test_regula_braku_sprawozdania_nigdy_nie_zapala_sie_sama(odpis: dict[str, Any]) -> None:
    """Stan wymagany przez tabelę bramek ADR-0023, sprawdzany na każdym kształcie odpisu.

    Nie chodzi o to, że reguła „jeszcze nie działa" — chodzi o to, że w etapie 1 **nie ma
    drogi** od nierozstrzygniętej przesłanki do zarzutu, i to jest własność, którą trzeba
    umieć pokazać, a nie zadeklarować.
    """
    wynik = ocen(**odpis)[REGULA_BRAKU]

    assert not isinstance(wynik, Sygnal)


def test_nieustalenie_wymienia_przeslanki_i_powody() -> None:
    """Wynik nazywa, czego nie ustalono — i po czyjej stronie leży domknięcie każdej pozycji."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023)[REGULA_BRAKU]

    assert isinstance(wynik, Nieustalony)
    kody = {n.kod: n.powod for n in wynik.nierozstrzygniete}
    assert kody["zawieszenie_caloroczne"] is Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC
    assert kody["dzialalnosc_w_spadku"] is Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC
    assert kody["oswiadczenie_art_70a"] is Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC
    # Katalog mówi, że tę przesłankę da się ustalić z działów 1 i 3. Nasz czytnik nie wyciąga
    # z nich daty rejestracji, więc niewiedza jest po NASZEJ stronie i tak się przedstawia.
    assert kody["rozpoczecie_w_ii_polroczu"] is Powod.CZYTNIK_NIE_WYCIAGA_DANEJ


def test_niepusty_dzial_nie_potwierdza_upadlosci() -> None:
    """W dziale 4 coś stoi — ale „coś" to nie „upadłość", więc przesłanka zostaje nieustalona."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, dzial4=WPIS)[REGULA_BRAKU]

    assert isinstance(wynik, Nieustalony)
    kody = {n.kod: n.powod for n in wynik.nierozstrzygniete}
    assert kody["upadlosc_lub_restrukturyzacja"] is Powod.ODPIS_NIE_ROZSTRZYGA


def test_puste_dzialy_rozstrzygaja_przeslanke_o_postepowaniu() -> None:
    """Tam, gdzie odpis wystarcza, przesłanka schodzi z listy — inaczej lista nic nie znaczy."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023)[REGULA_BRAKU]

    assert isinstance(wynik, Nieustalony)
    assert "upadlosc_lub_restrukturyzacja" not in {n.kod for n in wynik.nierozstrzygniete}


def test_nieuplyniety_ogranicznik_wyklucza_regule() -> None:
    """Zanim ogranicznik upłynie, nie ma o co pytać — i to jest wykluczenie, nie niewiedza."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, stan_z_dnia="01.03.2025")[REGULA_BRAKU]

    assert isinstance(wynik, Wykluczony)
    assert wynik.powod == Obserwacja.OGRANICZNIK_NIE_UPLYNAL.value
    assert wynik.termin is not None


def test_podmiot_spoza_rejestru_przedsiebiorcow_wyklucza_regule() -> None:
    """Sprawozdania spoza rejestru przedsiębiorców idą do Szefa KAS i są objęte tajemnicą.

    Jedyne prawdziwe zdanie o takim podmiocie brzmi „brak źródła publicznego" — pomylenie go
    z „brak sprawozdania" było jednym z pierwszych ustaleń rozpoznania.
    """
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, rejestr="S")[REGULA_BRAKU]

    assert isinstance(wynik, Wykluczony)
    assert wynik.powod == "poza_rejestrem_przedsiebiorcow"


def test_pusty_rejestr_w_odpisie_to_niewiedza_a_nie_przynaleznosc() -> None:
    """Brak wartości nie znaczy „w rejestrze przedsiębiorców"; pomyłka idzie w bezpieczną stronę."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023, rejestr="")[REGULA_BRAKU]

    assert isinstance(wynik, Nieustalony)
    assert "poza_rejestrem_przedsiebiorcow" in {n.kod for n in wynik.nierozstrzygniete}


def test_bez_czytelnego_okresu_nie_ma_od_czego_liczyc_terminu() -> None:
    wynik = ocen(sprawozdania=(wzmianka("15.07.2020", OKRES_NIEZNANY),))[REGULA_BRAKU]

    assert isinstance(wynik, Nieustalony)
    assert wynik.nierozstrzygniete[0].kod == Obserwacja.BRAK_CZYTELNEGO_OKRESU.value
    assert wynik.termin is None


def test_nieczytelna_wzmianka_nie_psuje_okresu_odczytanego_z_innej() -> None:
    """Nieczytelny zapis jest zgłaszany, a nie zgadywany — i nie zabiera głosu czytelnemu."""
    wynik = ocen(
        sprawozdania=(
            wzmianka("10.05.2024", OKRES_KROPKOWY),
            wzmianka("15.07.2020", OKRES_NIEZNANY),
        )
    )[REGULA_BRAKU]

    assert wynik.po_okresie is not None
    assert wynik.po_okresie.isoformat() == "2023-12-31"


def test_okres_kandydujacy_jest_nazwany_przez_okres_ktory_go_poprzedza() -> None:
    """Dnia bilansowego okresu, którego w odpisie nie ma, nie wyprowadzamy (reguła granic 9)."""
    wynik = ocen(sprawozdania=SPRAWOZDANIE_ZA_2023)[REGULA_BRAKU]

    assert wynik.po_okresie is not None
    assert wynik.termin is not None
    assert wynik.termin.isoformat() == "2025-07-15"


# --------------------------------------------------------------------------------------
# Mechanizm, którego katalog dziś nie przechodzi
# --------------------------------------------------------------------------------------


def test_droga_od_szesciu_wykluczonych_przeslanek_do_sygnalu_istnieje(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gałąź, której katalog nie potrafi dziś przejść — a której działanie trzeba pokazać.

    Reguła braku sprawozdania nie może wystrzelić w etapie 1 i to jest stan wymagany, nie
    usterka. Ale „nie może" musi znaczyć „bo przesłanki są nierozstrzygnięte", a nie „bo kodu
    tej ścieżki nie ma".

    Ten test zdejmuje **obie** blokady naraz, i to jest jego treść: katalog przestaje
    deklarować nieustalalność (zmieniony plik reguł), a czytnik dostaje rozstrzygacz do każdej
    z sześciu przesłanek (podstawiona mapa). Dopiero wtedy zapada sygnał — czyli między dniem
    dzisiejszym a zapłonem stoją dokładnie te dwie rzeczy i nic poza nimi.
    """
    monkeypatch.setattr(
        modul_oceny,
        "_ROZSTRZYGACZE",
        {kod: lambda _p, _o: Werdykt.NIE_ZACHODZI for kod in PRZESLANKI_BRAKU_DOKUMENTU},
    )
    odpis = wczytaj_odpis(zbuduj_odpis(sprawozdania=SPRAWOZDANIE_ZA_2023))

    wyniki = ocen_odpis(odpis, wczytaj_katalog(_katalog_bez_deklaracji_nieustalalnosci(tmp_path)))

    (wynik,) = [w for w in wyniki.wyniki if w.regula.kod == REGULA_BRAKU]
    assert isinstance(wynik, Sygnal)
    assert wynik.obserwacja is Obserwacja.BRAK_WZMIANKI_ZA_OKRES


def _katalog_bez_deklaracji_nieustalalnosci(tmp_path: Path) -> Path:
    """Kopia reguły braku sprawozdania, w której każda przesłanka ma źródło rozstrzygalne."""
    zrodlo = Path(modul_oceny.__file__).parent / "reguly" / "sprawozdania.yaml"
    dane = yaml.safe_load(zrodlo.read_text(encoding="utf-8"))
    for przeslanka in dane["reguly"][0]["przeslanki_wykluczajace"]:
        przeslanka["ustalane_z"] = ["naglowek"]
    (tmp_path / "sprawozdania.yaml").write_text(
        yaml.safe_dump(dane, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


def test_rozstrzygacze_dotycza_wylacznie_zamrozonej_szostki() -> None:
    """Rozstrzygacz przypisany do nieistniejącej przesłanki jest martwy i nikt tego nie zauważy."""
    assert set(modul_oceny._ROZSTRZYGACZE) <= set(PRZESLANKI_BRAKU_DOKUMENTU)


def test_ocena_niesie_stan_rejestru_a_nie_dzien_uruchomienia() -> None:
    """ADR-0001 decyzja 6: jedynym „dziś" jest data z odpisu — na tym stoi odtwarzalność."""
    odpis: Odpis = wczytaj_odpis(zbuduj_odpis(stan_z_dnia="04.02.2026"))

    wynikowa = ocen_odpis(odpis, wczytaj_katalog())

    assert wynikowa.stan_z_dnia.isoformat() == "2026-02-04"
