"""Okno wysyłki wiadomości inicjowanych przez bota (godziny ciszy) — czysta logika."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.scheduler.send_window import in_send_window, next_send_window

WARSZAWA = ZoneInfo("Europe/Warsaw")
ROBOCZE = (0, 1, 2, 3, 4)
OKNO = {"start_hour": 8, "end_hour": 18, "weekdays": ROBOCZE}


def _lokalnie(rok: int, miesiac: int, dzien: int, godzina: int, minuta: int = 0) -> datetime:
    return datetime(rok, miesiac, dzien, godzina, minuta, tzinfo=WARSZAWA)


def test_dzien_roboczy_w_godzinach_jest_w_oknie():
    assert in_send_window(_lokalnie(2026, 8, 12, 9), WARSZAWA, **OKNO)  # środa 9:00


def test_krawedzie_okna_sa_domkniete_z_lewej_i_otwarte_z_prawej():
    assert in_send_window(_lokalnie(2026, 8, 12, 8, 0), WARSZAWA, **OKNO)
    assert in_send_window(_lokalnie(2026, 8, 12, 17, 59), WARSZAWA, **OKNO)
    assert not in_send_window(_lokalnie(2026, 8, 12, 18, 0), WARSZAWA, **OKNO)
    assert not in_send_window(_lokalnie(2026, 8, 12, 7, 59), WARSZAWA, **OKNO)


def test_weekend_jest_poza_oknem():
    assert not in_send_window(_lokalnie(2026, 8, 15, 12), WARSZAWA, **OKNO)  # sobota
    assert not in_send_window(_lokalnie(2026, 8, 16, 12), WARSZAWA, **OKNO)  # niedziela


def test_okno_liczone_w_STREFIE_ZESPOLU_nie_w_utc():
    """22:00 UTC w środę to już czwartek 0:00 w Warszawie — cisza, mimo „środy" w UTC."""
    moment = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
    assert not in_send_window(moment, WARSZAWA, **OKNO)


def test_najblizsze_okno_zwraca_biezaca_chwile_gdy_juz_otwarte():
    teraz = _lokalnie(2026, 8, 12, 9, 30)
    assert next_send_window(teraz, WARSZAWA, **OKNO) == teraz


def test_po_godzinach_najblizsze_okno_to_nastepny_ranek():
    wieczor = _lokalnie(2026, 8, 12, 22, 0)  # środa wieczorem
    assert next_send_window(wieczor, WARSZAWA, **OKNO) == _lokalnie(2026, 8, 13, 8, 0)


def test_piatek_wieczorem_czeka_do_poniedzialku():
    """Domknięcie odłożone w piątek po 18:00 nie może wyjść w sobotę o świcie."""
    piatek = _lokalnie(2026, 8, 14, 20, 0)
    assert next_send_window(piatek, WARSZAWA, **OKNO) == _lokalnie(2026, 8, 17, 8, 0)


def test_przed_otwarciem_tego_samego_dnia_czeka_kilka_godzin():
    rano = _lokalnie(2026, 8, 12, 6, 0)
    assert next_send_window(rano, WARSZAWA, **OKNO) == _lokalnie(2026, 8, 12, 8, 0)


def test_nieciale_dni_tygodnia_sa_obslugiwane():
    """Dozwolone dni bywają nieciągłe — szukamy dnia po dniu, nie arytmetyką na minutach."""
    okno = {"start_hour": 8, "end_hour": 18, "weekdays": (1, 3)}  # wtorek i czwartek
    poniedzialek = _lokalnie(2026, 8, 10, 12, 0)
    assert next_send_window(poniedzialek, WARSZAWA, **okno) == _lokalnie(2026, 8, 11, 8, 0)
    wtorek_wieczor = _lokalnie(2026, 8, 11, 19, 0)
    assert next_send_window(wtorek_wieczor, WARSZAWA, **okno) == _lokalnie(2026, 8, 13, 8, 0)
