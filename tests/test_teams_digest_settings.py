"""Testy ``TeamsDigestSettings`` (F6, ADR 0053) — bramka odbiorców i sensowność harmonogramu.

Ustawienia są czyste, testujemy je bez sieci: parsowanie env (odbiorcy z listy), dwustopniowa
bramka (``enabled``/``dry_run``), fail-fast gdy ON bez odbiorców, oraz zakresy harmonogramu.
"""

from __future__ import annotations

import pytest

from workmate.config import TeamsDigestSettings

_VARS = (
    "WORKMATE_TEAMS_DIGEST_ENABLED",
    "WORKMATE_TEAMS_DIGEST_DRY_RUN",
    "WORKMATE_TEAMS_DIGEST_RECIPIENTS",
    "WORKMATE_TEAMS_DIGEST_RUN_WEEKDAY",
    "WORKMATE_TEAMS_DIGEST_RUN_HOUR",
    "WORKMATE_TEAMS_DIGEST_RUN_MINUTE",
    "WORKMATE_TEAMS_DIGEST_WINDOW_DAYS",
    "WORKMATE_TEAMS_DIGEST_TZ",
    "WORKMATE_TEAMS_DIGEST_STATE",
    "WORKMATE_TEAMS_DIGEST_MAX_CATCHUP_DAYS",
)


def _clear(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)


# --- validate: bramka odbiorców --------------------------------------------


def test_validate_disabled_passes_without_recipients():
    """Domyślnie OFF: brak odbiorców jest w porządku (nic nie wysyłamy)."""
    TeamsDigestSettings(enabled=False).validate()  # nie rzuca


def test_validate_enabled_without_recipients_fails():
    """Bramka ON bez odbiorców = drzwi bez adresata → fail-fast (nie cicha martwa bramka)."""
    with pytest.raises(ValueError, match="RECIPIENTS"):
        TeamsDigestSettings(enabled=True, recipients=()).validate()


def test_validate_enabled_with_recipients_passes():
    TeamsDigestSettings(enabled=True, recipients=("aad-1", "aad-2")).validate()  # nie rzuca


# --- validate: zakresy harmonogramu (sprawdzane ZAWSZE) --------------------


@pytest.mark.parametrize("weekday", [-1, 7])
def test_validate_rejects_weekday_out_of_range(weekday):
    with pytest.raises(ValueError, match="RUN_WEEKDAY"):
        TeamsDigestSettings(run_weekday=weekday).validate()


@pytest.mark.parametrize("hour", [-1, 24])
def test_validate_rejects_hour_out_of_range(hour):
    with pytest.raises(ValueError, match="RUN_HOUR"):
        TeamsDigestSettings(run_hour=hour).validate()


def test_validate_rejects_window_below_one():
    with pytest.raises(ValueError, match="WINDOW_DAYS"):
        TeamsDigestSettings(window_days=0).validate()


@pytest.mark.parametrize("days", [-1, 99])
def test_validate_rejects_catchup_out_of_range(days):
    with pytest.raises(ValueError, match="MAX_CATCHUP_DAYS"):
        TeamsDigestSettings(max_catchup_days=days).validate()


def test_validate_ranges_checked_even_when_disabled():
    """Literówka w harmonogramie nie może spać do dnia flipa bramki — zakresy ZAWSZE."""
    with pytest.raises(ValueError, match="RUN_HOUR"):
        TeamsDigestSettings(enabled=False, run_hour=99).validate()


def test_validate_rejects_unknown_timezone():
    """Zła strefa wywala się fail-fast w validate (przed lockiem), nie surowo w pętli."""
    with pytest.raises(ValueError, match="TZ"):
        TeamsDigestSettings(tz_name="Europe/Warszawa").validate()


# --- from_env --------------------------------------------------------------


def test_from_env_defaults_when_unset(monkeypatch):
    _clear(monkeypatch)

    settings = TeamsDigestSettings.from_env()

    assert settings.enabled is False
    assert settings.dry_run is True  # domyślnie próbny (staged)
    assert settings.recipients == ()
    assert (settings.run_weekday, settings.run_hour, settings.run_minute) == (0, 8, 0)
    assert settings.window_days == 7


def test_from_env_parses_recipient_list(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_RECIPIENTS", "aad-1, aad-2 ,,aad-3")

    settings = TeamsDigestSettings.from_env()

    # Rozdzielone przecinkiem, przycięte, puste odrzucone.
    assert settings.recipients == ("aad-1", "aad-2", "aad-3")


def test_from_env_reads_gate_and_schedule(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_ENABLED", "true")
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_DRY_RUN", "false")
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_RUN_WEEKDAY", "0")
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_RUN_HOUR", "9")
    monkeypatch.setenv("WORKMATE_TEAMS_DIGEST_WINDOW_DAYS", "14")

    settings = TeamsDigestSettings.from_env()

    assert settings.enabled is True
    assert settings.dry_run is False
    assert settings.run_hour == 9
    assert settings.window_days == 14
