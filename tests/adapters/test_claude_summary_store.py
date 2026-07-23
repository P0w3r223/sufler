"""Store claude_summary: indeks po osobie (case-insensitive), okno, degradacja (ADR 0036)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from workmate.adapters.outbound.claude_summary_store import ClaudeSummaryStore

SINCE = date(2026, 7, 13)
UNTIL = date(2026, 7, 20)


def _write(store_dir: Path, name: str, report: dict[str, Any]) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / name).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


def _report(person: str, days: list[dict[str, Any]]) -> dict[str, Any]:
    return {"person": person, "days": days}


def _day(
    date_str: str,
    *,
    prose: str | None = None,
    commits: tuple[str, ...] = (),
    prompts: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "date": date_str,
        "llm_prose": prose,
        "commits": [{"message": m} for m in commits],
        "prompts": [{"text": t} for t in prompts],
    }


def test_indexes_by_person_and_builds_comment(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "mikolaj.json",
        _report(
            "mikolaj@example.org",
            [
                _day("2026-07-15", prose="Pracował nad filtrem."),
                _day("2026-07-16", commits=["feat: X"]),  # brak prozy → z commitu
            ],
        ),
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("mikolaj@example.org", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "Pracował nad filtrem."
    assert result[date(2026, 7, 16)] == "feat: X"


def test_person_match_is_case_insensitive(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(store_dir, "a.json", _report("Mikolaj@EXAMPLE.org", [_day("2026-07-15", prose="X")]))
    result = ClaudeSummaryStore(store_dir).comments_by_day("mikolaj@example.org", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "X"


def test_window_filters_days(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "a.json",
        _report(
            "me@x.pl",
            [_day("2026-07-12", prose="stary"), _day("2026-07-15", prose="w oknie")],
        ),
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert list(result) == [date(2026, 7, 15)]


def test_unknown_person_yields_no_comments(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(store_dir, "a.json", _report("me@x.pl", [_day("2026-07-15", prose="X")]))
    assert ClaudeSummaryStore(store_dir).comments_by_day("obcy@x.pl", SINCE, UNTIL) == {}


def test_missing_directory_degrades_to_empty(tmp_path: Path) -> None:
    assert ClaudeSummaryStore(tmp_path / "nie-ma").comments_by_day("me@x.pl", SINCE, UNTIL) == {}


def test_corrupt_file_is_skipped_others_still_read(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    store_dir.mkdir()
    (store_dir / "bad.json").write_text("{nie json", encoding="utf-8")
    _write(store_dir, "good.json", _report("me@x.pl", [_day("2026-07-15", prose="X")]))
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "X"


def test_window_is_half_open_since_inclusive_until_exclusive(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "a.json",
        _report(
            "me@x.pl",
            [
                _day("2026-07-13", prose="dzień SINCE"),  # == SINCE → w oknie
                _day("2026-07-20", prose="dzień UNTIL"),  # == UNTIL → poza (półotwarte)
            ],
        ),
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert list(result) == [date(2026, 7, 13)]


def test_non_dict_day_entry_is_skipped_others_read(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "a.json",
        {"person": "me@x.pl", "days": ["nie-slownik", _day("2026-07-15", prose="realny")]},
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "realny"


def test_days_not_a_list_degrades_to_empty(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(store_dir, "a.json", {"person": "me@x.pl", "days": {"date": "2026-07-15"}})
    assert ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL) == {}


def test_report_without_person_is_skipped_others_read(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(store_dir, "ghost.json", {"days": [_day("2026-07-15", prose="bez osoby")]})
    _write(store_dir, "good.json", _report("me@x.pl", [_day("2026-07-15", prose="realny")]))
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "realny"


def test_top_level_not_a_dict_is_skipped_others_read(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    store_dir.mkdir()
    (store_dir / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    _write(store_dir, "good.json", _report("me@x.pl", [_day("2026-07-15", prose="realny")]))
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "realny"


def test_day_without_any_content_yields_no_comment(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    # dzień bez prozy/commitów/promptów → build_comment == "" → wpis pominięty w indeksie
    _write(store_dir, "a.json", _report("me@x.pl", [_day("2026-07-15")]))
    assert ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL) == {}


def test_null_message_is_treated_as_blank_not_the_string_none(tmp_path: Path) -> None:
    """Jawny ``null`` w commit/prompt nie może stać się literałem ``"None"`` w komentarzu (L1)."""
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "a.json",
        {
            "person": "me@x.pl",
            "days": [
                {
                    "date": "2026-07-15",
                    "llm_prose": None,
                    "commits": [{"message": None}, {"message": "feat: realny"}],
                    "prompts": [],
                }
            ],
        },
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert result[date(2026, 7, 15)] == "feat: realny"  # None pominięty, nie "None; feat: realny"


def test_day_with_unparsable_date_is_skipped_others_read(tmp_path: Path) -> None:
    store_dir = tmp_path / "sum"
    _write(
        store_dir,
        "a.json",
        _report(
            "me@x.pl",
            [
                {"date": "nie-data", "llm_prose": "zła data"},
                _day("2026-07-15", prose="realny"),
            ],
        ),
    )
    result = ClaudeSummaryStore(store_dir).comments_by_day("me@x.pl", SINCE, UNTIL)
    assert list(result) == [date(2026, 7, 15)]
