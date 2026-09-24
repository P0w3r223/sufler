"""Zachowania dodane wg uzupelnienie-01.md: kwarantanna bazy, link_ceidg, odstęp po wznowieniu."""

from __future__ import annotations

from pathlib import Path

import pytest

from ceidg_tool.normalizer import normalize
from ceidg_tool.ratelimit import REASON_RESUME, InMemoryHistory, RateLimiter, RequestStamp
from ceidg_tool.records import RawRecord, RowContext
from ceidg_tool.store import Store
from tests.conftest import FakeClock, list_record


def test_corrupted_database_is_quarantined_and_recreated(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "store-test.sqlite"
    path.write_bytes(b"to nie jest baza sqlite" * 100)
    with Store(path, environment="test", clock=clock) as store:
        assert store.quarantined is not None
        assert store.quarantined.name.startswith("store-test.sqlite.uszkodzony-")
        assert store.list_runs() == []
    assert path.exists() and store.quarantined.exists()


def test_healthy_database_is_not_quarantined(tmp_path: Path, clock: FakeClock) -> None:
    path = tmp_path / "ok.sqlite"
    with Store(path, environment="test", clock=clock) as store:
        assert store.quarantined is None
    with Store(path, environment="test", clock=clock) as again:
        assert again.quarantined is None


def test_link_ceidg_points_to_public_search() -> None:
    raw = RawRecord(
        id=list_record(1)["id"],
        list_json=list_record(1),
        detail_json=None,
        list_utc=None,
        detail_utc=None,
        detail_state="brak",
        zrodlo="CEIDG_API",
    )
    row = normalize(raw, RowContext(srodowisko="test", pobrano_utc="x")).firmy
    assert row["link_ceidg"] == (
        "https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/SearchDetails.aspx?Id="
        + list_record(1)["id"].lower()
    )
    report_row = normalize(
        RawRecord("NIP:1234567890", {"nazwa": "x"}, None, None, None, "brak", "CEIDG_RAPORT"),
        RowContext(srodowisko="prod", pobrano_utc="x"),
    ).firmy
    assert report_row["link_ceidg"] is None and report_row["id"] == "NIP:1234567890"


@pytest.mark.parametrize(
    ("age_s", "expected_wait"),
    [(30.0, 150.0), (200.0, 0.0)],
)
def test_resume_gap_counts_from_last_recorded_request(
    clock: FakeClock, age_s: float, expected_wait: float
) -> None:
    history = InMemoryHistory([RequestStamp(clock.wall() - age_s, "firmy", 200)])
    waits: list[str] = []

    class Rec:
        def on_request(self, *a: object) -> None: ...

        def on_wait(self, seconds: float, reason: str, resume: float) -> None:
            waits.append(reason)

        def on_page(self, *a: object) -> None: ...

        def on_details(self, *a: object) -> None: ...

        def on_export(self, *a: object) -> None: ...

        def on_download(self, *a: object) -> None: ...

        def on_model(self, *a: object) -> None: ...

        def on_message(self, *a: object) -> None: ...

        def close(self) -> None: ...

    limiter = RateLimiter(
        windows=((48, 180.0),),
        min_spacing_s=0.0,
        cooldown_s=185.0,
        clock=clock,
        history=history,
        events=Rec(),
    )
    assert limiter.enforce_resume_gap(180.0) == pytest.approx(expected_wait)
    limiter.acquire("firmy")
    if expected_wait:
        assert clock.sleeps[-1] == pytest.approx(expected_wait)
        assert waits[-1] == REASON_RESUME
    else:
        assert clock.sleeps == []
