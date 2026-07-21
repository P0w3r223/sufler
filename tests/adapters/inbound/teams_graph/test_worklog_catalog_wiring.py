"""Testy wiringu ``_build_worklog_catalog`` (ADR 0034 po cięciu) — odczyt, bez bramki.

Sedno zmiany: zdolność stała dawniej na DWÓCH nogach (Jira = zapis wpisu, GitHub = źródło
commitów) i miała własną bramkę. Po wycięciu ścieżki zapisu została sama noga GitHuba, a wraz
z mutacją zniknął powód do bramkowania — odczyt jest w tym repo domyślny (ADR 0006).

To, co zostaje warte przypięcia: powierzchnia jest JEDNONARZĘDZIOWA, konfiguracja przenosi się
do polityki sesji, a sufity estymacji egzekwują TE drzwi (``GithubSettings.validate`` woła tylko
poller GitHuba). Golden-test powierzchni MCP zostaje nietknięty — wchodzimy przez ``extra_catalog``.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from workmate.adapters.inbound.teams_graph.app import _build_worklog_catalog
from workmate.config import GithubSettings


class _FakeGithubClient:
    """Atrapa klienta — zapamiętuje okno zapytania, żeby dało się sprawdzić strefę z ustawień."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def list_commits(self, owner: str, repo: str, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        return []


def _github(**kw) -> GithubSettings:
    base: dict = {
        "token": "gh-pat",
        "owner": "BIAP-Inteligentne-Technologie",
        "repo": "PIWorkmate",
    }
    base.update(kw)
    return GithubSettings(**base)


def _build(settings: GithubSettings, client: _FakeGithubClient | None = None):
    return _build_worklog_catalog(client or _FakeGithubClient(), settings)  # type: ignore[arg-type]


def test_catalog_has_exactly_the_read_tool() -> None:
    assert {spec.name for spec in _build(_github())} == {"propose_worklog"}


def test_no_gate_is_required() -> None:
    """Zdolność wchodzi z samą konfiguracją GitHuba — bramka zniknęła razem z mutacją."""
    assert _build(_github(enable_github_write=False)) != []


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("worklog_idle_gap_minutes", 4, "IDLE_GAP_MINUTES"),
        ("worklog_ramp_up_minutes", 999, "RAMP_UP_MINUTES"),
        ("worklog_round_minutes", 7, "ROUND_MINUTES"),
        ("worklog_max_session_hours", 100.0, "MAX_SESSION_HOURS"),
        ("worklog_max_range_days", 400, "MAX_RANGE_DAYS"),
        ("worklog_tz", "Europe/Warszawa", "WORKLOG_TZ"),
    ],
)
def test_absurd_tuning_is_a_hard_start_error(field: str, value: object, expected: str) -> None:
    """Sufity muszą działać po stronie, która LICZY estymację, nie tylko w procesie pollera."""
    with pytest.raises(ValueError, match=expected):
        _build(_github(**{field: value}))


def test_ramp_up_longer_than_idle_gap_is_rejected() -> None:
    """Rozbieg dłuższy niż przerwa dawałby sesje nachodzące na siebie — cichy bezsens."""
    with pytest.raises(ValueError, match="nie może przekraczać"):
        _build(_github(worklog_idle_gap_minutes=30, worklog_ramp_up_minutes=60))


def test_timezone_from_settings_reaches_the_query_window() -> None:
    """Strefa z ustawień musi dojechać do granic okna — inaczej doba liczyłaby się gdzie indziej."""
    client = _FakeGithubClient()
    spec = _build(_github(worklog_tz="Europe/London"), client)[0]
    spec.fn(date(2026, 1, 5), date(2026, 1, 9))
    assert client.calls[0]["since"].tzinfo == ZoneInfo("Europe/London")
