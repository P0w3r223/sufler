from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from ceidg_tool.errors import StoreError, StoreLockedError
from ceidg_tool.store import Store
from tests.conftest import FakeClock, detail_record, list_record


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> Iterator[Store]:
    s = Store(tmp_path / "store-test.sqlite", environment="test", clock=clock)
    yield s
    s.close()


def start(store: Store, run_id: str = "run-1", mode: str = "szczegoly") -> str:
    return store.start_run(
        run_id=run_id,
        criteria_json='{"wojewodztwo":["podlaskie"]}',
        criteria_hash="abc",
        profile_hash="prof",
        mode=mode,
        tool_version="0.1.0",
        cursor_mode="links",
    )


def test_page_and_checkpoint_are_saved_together(store: Store) -> None:
    run_id = start(store)
    store.save_page(
        run_id,
        page_index=0,
        records=[list_record(1), list_record(2)],
        next_cursor="https://test-dane.biznes.gov.pl/api/ceidg/v3/firmy?page=1",
    )
    cp = store.get_checkpoint(run_id)
    assert cp is not None
    assert cp.page_index == 1
    assert cp.cursor is not None and cp.cursor.endswith("page=1")
    assert cp.stage == "lista"
    run = store.get_run(run_id)
    assert run.pages_done == 1
    assert run.records_seen == 2
    assert store.count_run_records(run_id) == 2


def test_resume_finds_interrupted_run_and_cursor(store: Store) -> None:
    run_id = start(store)
    store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor="p1")
    store.save_page(run_id, page_index=1, records=[list_record(2)], next_cursor="p2")
    store.update_run_status(run_id, "przerwany", error="timeout")

    found = store.find_resumable_run("abc", profile_hash="prof")
    assert found is not None and found.run_id == run_id
    cp = store.get_checkpoint(run_id)
    assert cp is not None and cp.cursor == "p2" and cp.page_index == 2

    # wznowienie zapisuje kolejną stronę, wcześniejsze rekordy zostają nietknięte
    store.save_page(run_id, page_index=2, records=[list_record(3)], next_cursor=None)
    ids = [r.id for r in store.iter_run_records(run_id)]
    assert ids == [list_record(1)["id"], list_record(2)["id"], list_record(3)["id"]]
    assert store.get_run(run_id).pages_done == 3


def test_repeated_record_is_harmless(store: Store) -> None:
    run_id = start(store)
    store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor="p1")
    store.save_page(run_id, page_index=1, records=[list_record(1)], next_cursor=None)
    assert store.count_run_records(run_id) == 1


def test_crash_mid_transaction_leaves_no_partial_page(store: Store, clock: FakeClock) -> None:
    run_id = start(store)
    bad = [list_record(1), {"nazwa": "bez id"}]
    with pytest.raises(StoreError):
        store.save_page(run_id, page_index=0, records=bad, next_cursor="p1")
    assert store.count_run_records(run_id) == 0
    cp = store.get_checkpoint(run_id)
    assert cp is not None and cp.page_index == 0 and cp.cursor is None


def test_pending_details_skip_fetched_and_not_found(store: Store, clock: FakeClock) -> None:
    run_id = start(store)
    store.save_page(
        run_id, page_index=0, records=[list_record(i) for i in (1, 2, 3, 4)], next_cursor=None
    )
    ids = store.pending_detail_ids(run_id, ttl_days=7)
    assert len(ids) == 4

    store.save_details(
        details=[detail_record(1)],
        missing_ids=[list_record(2)["id"]],
        failed_ids=[list_record(3)["id"]],
    )
    pending = store.pending_detail_ids(run_id, ttl_days=7)
    assert pending == [list_record(3)["id"], list_record(4)["id"]]  # błąd ponawiamy, 404 nie
    assert store.count_run_details(run_id) == 1

    clock.advance(8 * 86_400)  # cache przeterminowany po TTL
    assert list_record(1)["id"] in store.pending_detail_ids(run_id, ttl_days=7)


def test_iter_run_records_merges_list_and_detail(store: Store) -> None:
    run_id = start(store)
    store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor=None)
    store.save_details(details=[detail_record(1)])
    rec = next(store.iter_run_records(run_id))
    assert rec.list_json is not None and rec.detail_json is not None
    assert rec.detail_state == "pobrany"
    assert rec.detail_json["pkdGlowny"]["kod"] == "3031Z"
    assert rec.list_utc is not None and rec.detail_utc is not None


def test_details_for_unknown_id_create_record(store: Store) -> None:
    # tryb /zmiana: szczegóły bez wcześniejszej listy
    store.save_details(details=[detail_record(9)])
    run_id = start(store)
    assert store.count_run_records(run_id) == 0  # nie należy do runu, ale jest w cache


def test_lock_blocks_second_process_unless_stale(
    store: Store, clock: FakeClock, tmp_path: Path
) -> None:
    store.acquire_lock()
    store.touch_lock()
    # symulacja innego procesu: ten sam plik, inny PID
    conn = sqlite3.connect(tmp_path / "store-test.sqlite")
    conn.execute("UPDATE run_lock SET pid = 999999")
    conn.commit()
    conn.close()
    with pytest.raises(StoreLockedError):
        store.acquire_lock()
    store.acquire_lock(force=True)
    clock.advance(601)
    store.acquire_lock()  # heartbeat stary → blokada wygasła
    store.release_lock()


def test_watermark_roundtrip(store: Store) -> None:
    assert store.get_watermark("zmiana:test") is None
    store.set_watermark("zmiana:test", "2026-09-01T00:00:00Z")
    store.set_watermark("zmiana:test", "2026-09-05T00:00:00Z")
    assert store.get_watermark("zmiana:test") == "2026-09-05T00:00:00Z"


def test_request_history_persists_and_marks_status(store: Store, clock: FakeClock) -> None:
    hist = store.history("tokenfp")
    hist.record(clock.wall(), "firmy")
    hist.mark(clock.wall(), 429)
    other = store.history("inny-token")
    assert other.recent(0) == []
    stamps = hist.recent(0)
    assert len(stamps) == 1 and stamps[0].status == 429


def test_purge_removes_old_finished_runs_and_orphans(store: Store, clock: FakeClock) -> None:
    run_id = start(store)
    store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor=None)
    store.update_run_status(run_id, "zakonczony")
    clock.advance(31 * 86_400)
    runs, records = store.purge_older_than(30)
    assert runs == 1 and records == 1
    assert store.list_runs() == []


def test_schema_version_guard(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "future.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(StoreError, match="schemat"):
        Store(path, environment="test", clock=clock)
