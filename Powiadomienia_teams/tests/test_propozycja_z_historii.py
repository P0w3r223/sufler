"""Propozycja z ostatnich czterech tygodni (0.2.26): „typowy tydzień", w którym urlop nie głosuje.

Każdy test opisuje jeden niuans, dla którego propozycja NIE jest prostą kopią zeszłego tygodnia.
Tydzień docelowy we wszystkich testach: poniedziałek 2026-10-05; historia to tygodnie od 07.09,
14.09, 21.09 i 28.09 (od najstarszego).
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Shift, TimeOff
from powiadomienia_teams.reminders.propose import proposal_from_history

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")
CEL = date(2026, 10, 5)
TYGODNIE = [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]


def _zmiana(dzien: date, od: str, do: str, theme: str = "green", uid: str = "u1") -> Shift:
    h1, m1 = map(int, od.split(":"))
    h2, m2 = map(int, do.split(":"))
    start = datetime(dzien.year, dzien.month, dzien.day, h1, m1, tzinfo=WAW)
    koniec = datetime(dzien.year, dzien.month, dzien.day, h2, m2, tzinfo=WAW)
    if koniec <= start:
        koniec += timedelta(days=1)
    return Shift(uid, start.astimezone(UTC), koniec.astimezone(UTC), "TAG", theme)


_PN_PT = (0, 1, 2, 3, 4)


def _tydzien(pon: date, dni=_PN_PT, od="08:00", do="16:00", theme="green") -> list[Shift]:
    return [_zmiana(pon + timedelta(days=d), od, do, theme) for d in dni]


def _wolne(od: date, dni: int, uid: str = "u1") -> TimeOff:
    start = datetime(od.year, od.month, od.day, tzinfo=WAW)
    return TimeOff(uid, start.astimezone(UTC), (start + timedelta(days=dni)).astimezone(UTC), "R")


def _plan(propozycja) -> list[tuple[int, str, str, str | None]]:
    return [
        (
            s.start.astimezone(WAW).weekday(),
            f"{s.start.astimezone(WAW):%H:%M}",
            f"{s.end.astimezone(WAW):%H:%M}",
            s.theme,
        )
        for s in propozycja.grafik.shifts
    ]


def test_staly_rytm_daje_ten_sam_tydzien():
    historia = [z for t in TYGODNIE for z in _tydzien(t)]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert _plan(p) == [(d, "08:00", "16:00", "green") for d in range(5)]
    assert p.tygodnie_uzyte == 4
    assert all(
        s.start.astimezone(WAW).date() == CEL + timedelta(days=i)
        for i, s in enumerate(p.grafik.shifts)
    )


def test_URLOP_w_zeszlym_tygodniu_nie_robi_pustej_propozycji():
    """Dawne „jak w zeszłym tygodniu" dawało tu pusty grafik — człowiek wraca z urlopu do pracy."""
    historia = [z for t in TYGODNIE[:3] for z in _tydzien(t)]
    p = proposal_from_history("u1", historia, [_wolne(TYGODNIE[3], 7)], CEL, tz=WAW)
    assert [d for d, *_ in _plan(p)] == [0, 1, 2, 3, 4]
    assert p.tygodnie_z_urlopem == 1
    assert "urlop" in p.opis_podstawy


def test_pojedynczy_wolny_piatek_nie_zabiera_piatku():
    historia = [
        z for t in TYGODNIE for z in _tydzien(t, dni=range(4) if t == TYGODNIE[3] else range(5))
    ]
    p = proposal_from_history(
        "u1", historia, [_wolne(TYGODNIE[3] + timedelta(days=4), 1)], CEL, tz=WAW
    )
    assert 4 in [d for d, *_ in _plan(p)]


def test_okres_mniejszej_aktywnosci_przy_remisie_zostaje_praca():
    """Dwa tygodnie po trzy dni, dwa pełne → czwartek i piątek zostają (remis = na rzecz pracy)."""
    historia = [
        z for i, t in enumerate(TYGODNIE) for z in _tydzien(t, dni=range(3) if i >= 2 else range(5))
    ]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert [d for d, *_ in _plan(p)] == [0, 1, 2, 3, 4]


def test_trwale_mniej_dni_propozycja_sie_dostosowuje():
    historia = [
        z for i, t in enumerate(TYGODNIE) for z in _tydzien(t, dni=range(3) if i >= 1 else range(5))
    ]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert [d for d, *_ in _plan(p)] == [0, 1, 2]


def test_tydzien_bez_zadnego_wpisu_nie_glosuje():
    """Pusty tydzień to najczęściej NIEUZUPEŁNIONY grafik, nie tydzień bez pracy."""
    p = proposal_from_history("u1", _tydzien(TYGODNIE[1]), [], CEL, tz=WAW)
    assert [d for d, *_ in _plan(p)] == [0, 1, 2, 3, 4]
    assert (p.tygodnie_uzyte, p.tygodnie_bez_wpisow) == (1, 3)


def test_powtarzajace_sie_godziny_wygrywaja_z_jednorazowym_odstepstwem():
    """Średnia z 8–16, 8–16, 8–16, 10–14 to 8:30–15:30 — grafik, którego nie było ani razu."""
    historia = [z for t in TYGODNIE[1:] for z in _tydzien(t, dni=[0])]
    historia += _tydzien(TYGODNIE[0], dni=[0], od="10:00", do="14:00")
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert _plan(p) == [(0, "08:00", "16:00", "green")]


def test_bez_powtorzen_mediana_zaokraglona_do_kwadransa():
    historia = [
        *_tydzien(TYGODNIE[0], dni=[0], od="07:00", do="15:00"),
        *_tydzien(TYGODNIE[1], dni=[0], od="08:00", do="16:00"),
        *_tydzien(TYGODNIE[2], dni=[0], od="08:30", do="16:30"),
        *_tydzien(TYGODNIE[3], dni=[0], od="09:00", do="17:00"),
    ]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert _plan(p) == [(0, "08:15", "16:15", "green")]


def test_tryb_pracy_wiekszosciowy():
    historia = [z for t in TYGODNIE[1:] for z in _tydzien(t, dni=[1], theme="blue")]
    historia += _tydzien(TYGODNIE[0], dni=[1], theme="green")
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert _plan(p) == [(1, "08:00", "16:00", "blue")]


def test_nocka_przez_polnoc_zachowuje_dlugosc():
    historia = [_zmiana(t + timedelta(days=4), "22:00", "06:00") for t in TYGODNIE]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    (s,) = p.grafik.shifts
    assert (s.start.astimezone(WAW).weekday(), f"{s.start.astimezone(WAW):%H:%M}") == (4, "22:00")
    assert s.end - s.start == timedelta(hours=8)


def test_dni_wolne_w_tygodniu_docelowym_sa_pomijane_i_cudze_zmiany_ignorowane():
    historia = [z for t in TYGODNIE for z in _tydzien(t)]
    historia += [_zmiana(t, "06:00", "18:00", uid="u2") for t in TYGODNIE]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW, skip_weekdays=frozenset({2}))
    assert [d for d, *_ in _plan(p)] == [0, 1, 3, 4]
    assert all(s.user_id == "u1" for s in p.grafik.shifts)


def test_zmiana_czasu_w_srodku_historii_nie_przesuwa_godzin():
    """Historia sprzed 25.10 (czas letni), cel po zmianie — lokalnie ma zostać 08:00–16:00."""
    cel = date(2026, 10, 26)
    tygodnie = [cel - timedelta(weeks=k) for k in range(1, 5)]
    historia = [z for t in tygodnie for z in _tydzien(t)]
    p = proposal_from_history("u1", historia, [], cel, tz=WAW)
    assert _plan(p) == [(d, "08:00", "16:00", "green") for d in range(5)]


def test_brak_historii_to_pusta_propozycja_bez_opisu():
    p = proposal_from_history("u1", [], [], CEL, tz=WAW)
    assert p.grafik.is_empty
    assert p.opis_podstawy == ""


def test_nocka_w_noc_zmiany_czasu_nie_wydluza_propozycji():
    """Przegląd 0.2.26: długość liczona upływem czasu dawała 22:00–06:30 z nocy 24/25.10."""
    cel = date(2026, 11, 2)
    historia = [
        _zmiana(date(2026, 10, 24), "22:00", "06:00"),  # przez zmianę czasu — 9 h upływu
        _zmiana(date(2026, 10, 17), "22:00", "06:00"),
    ]
    p = proposal_from_history("u1", historia, [], cel, tz=WAW)
    (s,) = p.grafik.shifts
    assert f"{s.start.astimezone(WAW):%H:%M}–{s.end.astimezone(WAW):%H:%M}" == "22:00–06:00"


def test_tryb_glosuje_tryb_nie_kolor():
    """green (najnowszy), blue, blue, None → remis 2:2 trybów, wygrywa najnowszy: stacjonarnie."""
    motywy = ["green", "blue", "blue", None]  # od najnowszego tygodnia
    historia = [
        _zmiana(t + timedelta(days=1), "08:00", "16:00", theme=m)  # type: ignore[arg-type]
        for t, m in zip(reversed(TYGODNIE), motywy, strict=True)
    ]
    p = proposal_from_history("u1", historia, [], CEL, tz=WAW)
    assert _plan(p) == [(1, "08:00", "16:00", "green")]


def test_urlop_z_identyfikatorem_w_innej_wielkosci_liter_nadal_nie_glosuje():
    historia = [z for t in TYGODNIE[:3] for z in _tydzien(t)]
    p = proposal_from_history("u1", historia, [_wolne(TYGODNIE[3], 7, uid="U1")], CEL, tz=WAW)
    assert p.tygodnie_z_urlopem == 1
