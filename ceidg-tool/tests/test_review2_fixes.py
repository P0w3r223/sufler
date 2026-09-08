"""Testy dla poprawek z drugiego przeglądu kodu (2026-09-05)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ceidg_tool.config import Settings
from ceidg_tool.errors import ConfigError, ProdWithoutConsentError, ProfileMismatchError
from ceidg_tool.pipeline import build_deps, purge_report_files, run_fetch
from ceidg_tool.store import SCHEMA_VERSION, Store
from tests.conftest import FakeClock, list_record
from tests.support import FakeApi


def test_profile_pointing_to_prod_under_test_environment_is_refused(
    tmp_path: Path, clock: FakeClock
) -> None:
    profile = tmp_path / "prod.yaml"
    profile.write_text('base_url: "https://dane.biznes.gov.pl/api/ceidg/v3"\n', encoding="utf-8")
    settings = Settings(
        token="tok", environment="test", data_dir=tmp_path / "dane", profile_path=profile
    )
    with pytest.raises(ProdWithoutConsentError, match="dane.biznes.gov.pl"):
        build_deps(settings, clock=clock, http=FakeApi().client())


def test_v1_database_gets_kind_column_on_open(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "v1.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE run (
          run_id TEXT PRIMARY KEY, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
          environment TEXT NOT NULL, tool_version TEXT NOT NULL, profile_hash TEXT NOT NULL,
          criteria_json TEXT NOT NULL, criteria_hash TEXT NOT NULL, mode TEXT NOT NULL,
          status TEXT NOT NULL, count_api INTEGER, pages_done INTEGER NOT NULL DEFAULT 0,
          records_seen INTEGER NOT NULL DEFAULT 0, error TEXT
        );
        INSERT INTO run VALUES ('a','2026-09-05T10:00:00Z','2026-09-05T10:00:00Z','test','0','p',
          '{"wojewodztwo":["podlaskie"]}','h','lista','przerwany',NULL,1,5,NULL);
        INSERT INTO run VALUES ('b','2026-09-05T10:00:01Z','2026-09-05T10:00:01Z','test','0','p',
          '{"zmiana_od": "2026-09-01T00:00:00+00:00"}','zmiana:x','szczegoly',
          'zakonczony',NULL,0,0,NULL);
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()
    with Store(path, environment="test", clock=clock) as store:
        runs = {r.run_id: r for r in store.list_runs()}
        assert runs["a"].kind == "firmy" and runs["b"].kind == "zmiana"
        assert store.find_resumable_run("h", profile_hash="p") is not None
        assert store.find_resumable_run("zmiana:x", profile_hash="p") is None  # inny rodzaj
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        # Oczekiwana wartość idzie ze stałej, nie z liczby wpisanej w test: migracja v1 ma
        # dowieźć bazę do **bieżącego** schematu, a nie do tego, który był aktualny w dniu
        # pisania testu — wpisane `2` zrobiło z podbicia wersji czerwony test zamiast
        # potwierdzić, że łańcuch migracji dochodzi do końca.
        assert version == SCHEMA_VERSION


def test_report_run_is_not_resumable_through_api(tmp_path: Path, clock: FakeClock) -> None:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=FakeApi().client())
    run_id = deps.store.start_run(
        run_id="r",
        criteria_json='{"wojewodztwo":["podlaskie"]}',
        criteria_hash="h",
        profile_hash=deps.profile.profile_hash(),
        mode="lista",
        tool_version="0",
        cursor_mode="numeric",
        kind="raport",
    )
    deps.store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor="1")
    with pytest.raises(ConfigError, match="raport"):
        run_fetch(None, deps, resume_run_id=run_id)  # type: ignore[arg-type]
    deps.store.close()


def test_non_numeric_cursor_gives_clear_error(clock: FakeClock) -> None:
    from ceidg_tool.apiprofile import ApiProfile
    from ceidg_tool.client import CeidgClient, Cursor
    from ceidg_tool.criteria import Criteria
    from ceidg_tool.ratelimit import InMemoryHistory, RateLimiter

    profile = ApiProfile(base_url="https://test-dane.biznes.gov.pl/api/ceidg/v3")
    limiter = RateLimiter(
        windows=((48, 180.0),),
        min_spacing_s=0,
        cooldown_s=185,
        clock=clock,
        history=InMemoryHistory(),
    )
    client = CeidgClient(
        http=FakeApi().client(), profile=profile, limiter=limiter, token="t", clock=clock
    )
    with pytest.raises(ProfileMismatchError):
        list(client.iter_pages(Criteria(), start=Cursor("numeric", "dalej")))


def test_records_seen_counts_only_new_rows(tmp_path: Path, clock: FakeClock) -> None:
    with Store(tmp_path / "s.sqlite", environment="test", clock=clock) as store:
        run_id = store.start_run(
            run_id="r",
            criteria_json="{}",
            criteria_hash="h",
            profile_hash="p",
            mode="lista",
            tool_version="0",
            cursor_mode="links",
        )
        store.save_page(
            run_id, page_index=0, records=[list_record(1), list_record(2)], next_cursor="x"
        )
        store.save_page(
            run_id, page_index=1, records=[list_record(2), list_record(3)], next_cursor=None
        )
        assert store.get_run(run_id).records_seen == 3 == store.count_run_records(run_id)


def test_purge_report_files_by_age(tmp_path: Path) -> None:
    reports = tmp_path / "raporty"
    reports.mkdir()
    old = reports / "stary.zip"
    old.write_bytes(b"PK")
    import os

    os.utime(old, (1_000_000, 1_000_000))
    fresh = reports / "nowy.zip"
    fresh.write_bytes(b"PK")
    assert purge_report_files(reports, days=30, now_epoch=1_000_000 + 40 * 86_400) == 1
    assert fresh.exists() and not old.exists()
    assert purge_report_files(tmp_path / "brak", days=1, now_epoch=0) == 0
