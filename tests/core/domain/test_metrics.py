"""Testy czystej domeny metryk: tydzień ISO (z granicą + strefą) i pseudonimizacja PII."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from workmate.core.domain.metrics import REPORT_TZ, iso_week, pseudonymize


def test_iso_week_format_and_value():
    # 2026-07-29 to środa 31. tygodnia ISO.
    assert iso_week(datetime(2026, 7, 29, 12, 0, tzinfo=REPORT_TZ)) == "2026-W31"


def test_iso_week_treats_naive_as_utc():
    """Ścieżka produkcyjna: szew podaje NAIWNY UTC — musi być liczony jako UTC, nie czas systemowy.

    23:30 UTC ndz 2026-01-04 (naiwny) → 00:30 pon. w Warszawie → tydzień W02, niezależnie od TZ
    kontenera. Bez guardu naiwny byłby interpretowany jako lokalny i przy TZ≠UTC wpadłby do W01.
    """
    assert iso_week(datetime(2026, 1, 4, 23, 30)) == "2026-W02"


def test_iso_week_uses_local_date_not_utc():
    """23:30 UTC 2026-01-04 (ndz) to już 00:30 pon. 2026-01-05 w Warszawie → inny tydzień ISO."""
    utc_late_sunday = datetime(2026, 1, 4, 23, 30, tzinfo=UTC)
    assert iso_week(utc_late_sunday, ZoneInfo("Europe/Warsaw")) == "2026-W02"
    # Ta sama chwila w UTC to wciąż niedziela 1. tygodnia — potwierdza, że strefa decyduje.
    assert iso_week(utc_late_sunday, ZoneInfo("UTC")) == "2026-W01"


def test_pseudonymize_is_deterministic_and_hides_raw():
    raw = "aad-user-1234-guid"
    out = pseudonymize(raw)
    assert out == pseudonymize(raw)  # deterministyczny (ten sam pseudonim/tydzień → jeden wiersz)
    assert raw not in out  # surowej tożsamości nie widać
    assert len(out) == 16


def test_pseudonymize_distinguishes_users():
    assert pseudonymize("user-a") != pseudonymize("user-b")


def test_pseudonymize_empty_is_anon():
    assert pseudonymize("") == "anon"
    assert pseudonymize("   ") == "anon"
