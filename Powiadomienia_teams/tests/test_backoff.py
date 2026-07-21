from datetime import datetime, timedelta, timezone

from powiadomienia_teams.scheduler.backoff import next_poll_delay

UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _delay(idle_s, base=10.0, max_s=300.0, factor=2.0):
    return next_poll_delay(
        now=NOW, last_activity=NOW - timedelta(seconds=idle_s),
        base_s=base, max_s=max_s, factor=factor,
    )


def test_base_delay_right_after_activity():
    assert _delay(0) == 10.0


def test_doubles_across_idle_thresholds():
    assert _delay(15) == 20.0   # 10 < 15 → 20
    assert _delay(25) == 40.0   # 10 → 20 → 40
    assert _delay(100) == 160.0  # 10 → 20 → 40 → 80 → 160


def test_caps_at_max():
    assert _delay(10_000) == 300.0  # cisza godzinami → limit


def test_reaches_hourly_ceiling_for_absent_employee():
    """Sufit produkcyjny (3600 s) — nieobecny pracownik sprawdzany raz na godzinę."""
    assert _delay(2560, max_s=3600.0) == 2560.0  # ~43 min ciszy: ramp jeszcze pod sufitem
    assert _delay(2561, max_s=3600.0) == 3600.0  # pierwszy krok, który sufit przycina
    assert _delay(3 * 3600, max_s=3600.0) == 3600.0  # cisza godzinami → nadal godzina


def test_negative_idle_clamped_to_base():
    # serwer przed zegarem lokalnym: last_activity „w przyszłości" → idle < 0 → base
    d = next_poll_delay(
        now=NOW, last_activity=NOW + timedelta(seconds=30), base_s=10.0, max_s=300.0
    )
    assert d == 10.0


def test_factor_one_is_constant_base():
    assert _delay(10_000, factor=1.0) == 10.0  # brak wzrostu i brak pętli nieskończonej
