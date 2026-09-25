"""Testy ``TeamsDigestSettings`` (F6, ADR 0053) — bramka odbiorców i sensowność harmonogramu.

Ustawienia są czyste, testujemy je bez sieci: parsowanie env (odbiorcy z listy), dwustopniowa
bramka (``enabled``/``dry_run``), fail-fast gdy ON bez odbiorców, oraz zakresy harmonogramu.

Środowisko czyści globalny fixture z ``tests/conftest.py`` (zdejmuje wszystkie ``SUFLER_*``),
więc ręczna lista zmiennych do wyczyszczenia — i ryzyko, że ktoś zapomni jej uzupełnić przy
nowym polu — są tu zbędne. Test ustawia tylko to, co faktycznie bada.
"""

from __future__ import annotations

import pytest

from sufler.config import TeamsDigestSettings

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


def test_from_env_defaults_when_unset():

    settings = TeamsDigestSettings.from_env()

    assert settings.enabled is False
    assert settings.dry_run is True  # domyślnie próbny (staged)
    assert settings.recipients == ()
    assert (settings.run_weekday, settings.run_hour, settings.run_minute) == (0, 8, 0)
    assert settings.window_days == 7


def test_from_env_parses_recipient_list(monkeypatch):
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_RECIPIENTS", "aad-1, aad-2 ,,aad-3")

    settings = TeamsDigestSettings.from_env()

    # Rozdzielone przecinkiem, przycięte, puste odrzucone.
    assert settings.recipients == ("aad-1", "aad-2", "aad-3")


def test_from_env_reads_gate_and_schedule(monkeypatch):
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_ENABLED", "true")
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_DRY_RUN", "false")
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_RUN_WEEKDAY", "0")
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_RUN_HOUR", "9")
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_WINDOW_DAYS", "14")

    settings = TeamsDigestSettings.from_env()

    assert settings.enabled is True
    assert settings.dry_run is False
    assert settings.run_hour == 9
    assert settings.window_days == 14


@pytest.mark.parametrize("literowka", ["ture", "flase", "nie", "tak", ""])
def test_literowka_w_DRY_RUN_wywala_start_zamiast_uzbrajac_wysylke(monkeypatch, literowka: str):
    """Jedyna flaga o ODWRÓCONEJ polaryzacji — zwykły parser czynił z literówki realną wysyłkę.

    ``_bool_from_env`` mapuje wszystko spoza listy prawdy na ``False``. Dla bramek ``enable_*`` to
    kierunek bezpieczny, bo literówka zostawia zdolność wyłączoną. Tutaj ``False`` znaczy „to nie
    jest próba", więc ``DRY_RUN=ture`` przy ``ENABLED=true`` puszczało DM do CAŁEJ listy odbiorców.
    Pole nie nazywa się ``enable_*``, więc golden-test bramek go nie obejmuje.
    """
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_DRY_RUN", literowka)

    with pytest.raises(ValueError, match="SUFLER_TEAMS_DIGEST_DRY_RUN"):
        TeamsDigestSettings.from_env()


@pytest.mark.parametrize(("wartosc", "oczekiwane"), [("off", False), ("0", False), ("on", True)])
def test_rozpoznane_wartosci_DRY_RUN_dzialaja_jak_dotad(monkeypatch, wartosc, oczekiwane):
    """Kontrast: ścisły parser ZAWĘŻA wejście, nie zmienia znaczenia wartości poprawnych."""
    monkeypatch.setenv("SUFLER_TEAMS_DIGEST_DRY_RUN", wartosc)

    assert TeamsDigestSettings.from_env().dry_run is oczekiwane
