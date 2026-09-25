"""Testy raportu metryk: czyste formatowanie + kod wyjścia CLI bez skonfigurowanej bazy."""

from __future__ import annotations

from datetime import UTC, datetime

from sufler.adapters.inbound.metrics_report import format_summary, main
from sufler.adapters.outbound.sqlite_metrics import SqliteMetricsStore
from sufler.core.domain.metrics import DoorUsage, MetricsSummary


def test_format_summary_empty():
    assert "pusty" in format_summary(MetricsSummary()).lower()


def test_format_summary_lists_doors():
    summary = MetricsSummary(
        by_door=(DoorUsage(door="teams", calls=5, unique_users=3, returning_users=1),)
    )
    out = format_summary(summary)
    assert "teams" in out
    assert "5" in out and "3" in out and "1" in out


def test_main_without_db_returns_1(capsys, monkeypatch):
    monkeypatch.delenv("SUFLER_METRICS_DB", raising=False)
    assert main([]) == 1
    assert "SUFLER_METRICS_DB" in capsys.readouterr().out


def test_main_with_missing_db_path_returns_1(tmp_path, capsys):
    """Zła ścieżka w trybie odczytu → błąd, NIE ciche utworzenie pustego magazynu."""
    missing = tmp_path / "nie_ma.db"
    assert main(["--db", str(missing)]) == 1
    assert "Nie znaleziono" in capsys.readouterr().out
    assert not missing.exists()  # nic nie zmaterializowano


def test_main_reports_from_db(tmp_path, capsys):
    db = tmp_path / "metrics.db"
    store = SqliteMetricsStore(db)
    store.record_call("teams", "u1", "2026-W31", datetime(2026, 7, 29, tzinfo=UTC))
    assert main(["--db", str(db)]) == 0
    assert "teams" in capsys.readouterr().out
