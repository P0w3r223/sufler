"""Godziny ciszy — kiedy usługa nie pisze do pracowników.

Ten plik zastępuje `test_send_window.py`. Tamten świadczył o `scheduler.send_window` z linii
repozytorium, której obraz 0.2.19 nigdy nie dostał (ADR 0005, status NOT SHIPPED). Produkcja
rozwiązuje ten sam problem inaczej — oknem ciszy z `CISZA_OD_H`/`CISZA_DO_H` — i nie miała ani
jednego testu, mimo że rozstrzyga, czy bot wolno się w ogóle odezwać do człowieka.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from powiadomienia_teams.config import OknoCiszy
from powiadomienia_teams.runtime.cisza import (
    cisza_pomiedzy,
    najblizsza_dozwolona,
    wolno_pisac,
)

TZ = ZoneInfo("Europe/Warsaw")
NOCNE = OknoCiszy(od_h=20, do_h=7, tz=TZ)  # okno przez północ — układ produkcyjny
DZIENNE = OknoCiszy(od_h=1, do_h=5, tz=TZ)  # okno w obrębie doby
WYLACZONE = OknoCiszy(od_h=0, do_h=0, tz=TZ)


def _t(dzien: int, godzina: int, minuta: int = 0) -> datetime:
    return datetime(2026, 9, dzien, godzina, minuta, tzinfo=TZ)


@pytest.mark.parametrize(
    "godzina, wolno", [(19, True), (20, False), (23, False), (6, False), (7, True)]
)
def test_okno_przez_polnoc_zamyka_sie_o_od_h_i_otwiera_o_do_h(godzina, wolno):
    assert wolno_pisac(_t(4, godzina), NOCNE) is wolno


@pytest.mark.parametrize(
    "godzina, wolno", [(0, True), (1, False), (4, False), (5, True), (12, True)]
)
def test_okno_w_obrebie_doby(godzina, wolno):
    assert wolno_pisac(_t(4, godzina), DZIENNE) is wolno


def test_rowne_godziny_znacza_brak_ciszy_o_kazdej_porze():
    """Jedyny sposób wyłączenia okna — osobna flaga dawałaby dwa źródła prawdy
    (config.OknoCiszy).
    """
    assert all(wolno_pisac(_t(4, h), WYLACZONE) for h in range(24))
    assert WYLACZONE.wylaczone


def test_godzina_liczy_sie_w_strefie_zespolu_nie_w_utc():
    """22:30 UTC to 00:30 w Warszawie — po UTC bot uznałby, że wolno mu pisać w środku nocy."""
    utc_2230 = datetime(2026, 9, 4, 22, 30, tzinfo=ZoneInfo("UTC"))
    assert wolno_pisac(utc_2230, NOCNE) is False


def test_najblizsza_dozwolona_zwraca_teraz_gdy_juz_wolno():
    teraz = _t(4, 12)
    assert najblizsza_dozwolona(teraz, NOCNE) == teraz


def test_najblizsza_dozwolona_przed_polnoca_wskazuje_ranek_NASTEPNEGO_dnia():
    assert najblizsza_dozwolona(_t(4, 21, 15), NOCNE) == _t(5, 7)


def test_najblizsza_dozwolona_po_polnocy_wskazuje_ranek_TEGO_dnia():
    assert najblizsza_dozwolona(_t(5, 3, 40), NOCNE) == _t(5, 7)


def test_cisza_pomiedzy_wylaczona_i_zakres_odwrocony_daja_zero():
    assert cisza_pomiedzy(_t(4, 20), _t(5, 8), WYLACZONE) == timedelta(0)
    assert cisza_pomiedzy(_t(5, 8), _t(4, 20), NOCNE) == timedelta(0)


def test_cisza_pomiedzy_liczy_pelne_okno_przez_polnoc():
    assert cisza_pomiedzy(_t(4, 21), _t(5, 8), NOCNE) == timedelta(hours=10)


def test_cisza_pomiedzy_zeruje_sie_dla_zakresu_w_calosci_poza_oknem():
    assert cisza_pomiedzy(_t(4, 10), _t(4, 12), NOCNE) == timedelta(0)


def test_cisza_pomiedzy_broni_okna_laski_nadrabiania():
    """Powód istnienia tej funkcji, wprost z docstringa modułu.

    Usługa wstaje po awarii w piątek o 21:00; termin przebiegu był o 16:00, okno łaski to 6 h.
    Bez odliczenia ciszy nadrabianie „wygasa" o 22:00 — w godzinach, w których i tak nie wolno
    pisać — i CAŁY ZESPÓŁ nie dostaje w tym tygodniu prośby o grafik.
    """
    zjedzone = cisza_pomiedzy(_t(4, 16), _t(4, 21), NOCNE)
    assert zjedzone == timedelta(hours=1)
    assert _t(4, 16) + timedelta(hours=6) + zjedzone == _t(4, 23)


def test_cisza_pomiedzy_sumuje_wiecej_niz_jedna_dobe():
    """Zakres dłuższy niż doba — dwa pełne okna 20:00→07:00."""
    assert cisza_pomiedzy(_t(4, 12), _t(6, 12), NOCNE) == timedelta(hours=22)
