"""Testy licznika SQLite: inkrementacja UPSERT + zwijanie (wywołania/unikalni/powracający).

Sonda plikowa na końcu sprawdza to, czego ``:memory:`` sprawdzić nie może: docstring adaptera
obiecuje jeden licznik dzielony przez OSOBNE PROCESY drzwi (teams/cli), więc UPSERT musi trafiać
w ten sam wiersz także wtedy, gdy inkrementy przychodzą z różnych połączeń.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sufler.adapters.outbound.sqlite_metrics import SqliteMetricsStore


def _at(day: int) -> datetime:
    return datetime(2026, 7, day, 10, 0, tzinfo=UTC)


def test_record_call_increments_same_bucket():
    store = SqliteMetricsStore(":memory:")
    store.record_call("teams", "u1", "2026-W31", _at(29))
    store.record_call("teams", "u1", "2026-W31", _at(29))
    (door,) = store.summary().by_door
    assert door.door == "teams"
    assert door.calls == 2
    assert door.unique_users == 1


def test_summary_counts_unique_users_per_door():
    store = SqliteMetricsStore(":memory:")
    store.record_call("teams", "u1", "2026-W31", _at(29))
    store.record_call("teams", "u2", "2026-W31", _at(29))
    store.record_call("telegram", "u1", "2026-W31", _at(29))
    by_door = {d.door: d for d in store.summary().by_door}
    assert by_door["teams"].unique_users == 2
    assert by_door["teams"].calls == 2
    assert by_door["telegram"].unique_users == 1


def test_returning_requires_two_distinct_weeks():
    store = SqliteMetricsStore(":memory:")
    # u1 w dwóch tygodniach → powracający; u2 tylko w jednym → nie.
    store.record_call("teams", "u1", "2026-W31", _at(29))
    store.record_call("teams", "u1", "2026-W32", _at(29))
    store.record_call("teams", "u2", "2026-W31", _at(29))
    (door,) = store.summary().by_door
    assert door.unique_users == 2
    assert door.returning_users == 1


def test_summary_empty_store():
    assert SqliteMetricsStore(":memory:").summary().by_door == ()


def test_dwa_polaczenia_do_jednego_pliku_licza_w_tym_samym_wierszu(tmp_path: Path):
    """Drzwi to osobne procesy: ``(door, user_key, week)`` musi zostać JEDNYM ziarnem.

    Gdyby klucz główny nie trzymał (albo zapis szedł bez commitu), ten sam użytkownik zliczyłby
    się dwa razy jako „unikalny", a raport użycia zawyżałby zasięg narzędzia.
    """
    db = tmp_path / "stan" / "metrics.db"
    SqliteMetricsStore(db).record_call("teams", "u1", "2026-W31", _at(29))
    drugi_proces = SqliteMetricsStore(db)
    drugi_proces.record_call("teams", "u1", "2026-W31", _at(30))

    (door,) = drugi_proces.summary().by_door

    assert (door.calls, door.unique_users) == (2, 1)
    assert db.parent.is_dir()  # katalog stanu powstaje przy pierwszym starcie


def test_summary_sorted_by_calls_desc():
    store = SqliteMetricsStore(":memory:")
    store.record_call("telegram", "u1", "2026-W31", _at(29))
    for _ in range(3):
        store.record_call("teams", "u1", "2026-W31", _at(29))
    doors = [d.door for d in store.summary().by_door]
    assert doors == ["teams", "telegram"]
