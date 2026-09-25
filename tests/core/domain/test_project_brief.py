"""Testy renderu one-pagera projektu (``ProjectBrief.to_text``, F4, ADR 0051).

Render jest CZYSTY (same przechowane fakty), więc testujemy go bez atrap I/O: składamy
``ProjectStatus`` + ``NoteSummary`` wprost i sprawdzamy sekcje, formatowanie dat/znaczników czasu
oraz przypadki brzegowe (brak notatek, brak dat).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sufler.core.domain.models import NoteSummary, ProjectStatus
from sufler.core.domain.project_brief import ProjectBrief


def _status(**over: object) -> ProjectStatus:
    base: dict[str, object] = dict(
        key="workmate",
        company="biap",
        name="Sufler",
        status="w toku",
        health="zielony",
        phase="budowa",
        summary="Wspólna baza wiedzy pionu.",
        last_updated=date(2026, 7, 20),
        notes_count=3,
        latest_note_date=date(2026, 7, 18),
        open_action_items=2,
        recent_activity_count=5,
        latest_activity_at=datetime(2026, 7, 19, 14, 30, 0, tzinfo=UTC),
        failing_ci_count=1,
    )
    base.update(over)
    return ProjectStatus(**base)  # type: ignore[arg-type]


def _note(title: str, on: date, participants: list[str]) -> NoteSummary:
    return NoteSummary(
        id=f"biap/workmate/{on.isoformat()}-x",
        title=title,
        project="workmate",
        date=on,
        participants=participants,
        snippet="",
        score=1.0,
    )


def test_to_text_includes_header_status_summary_and_counts():
    brief = ProjectBrief(
        status=_status(),
        recent_notes=(_note("Schemat notatki", date(2026, 7, 18), ["Piotr Zieliński"]),),
    )

    text = brief.to_text()

    assert "One-pager: Sufler (biap/workmate)" in text
    assert "w toku" in text and "zielony" in text and "budowa" in text
    assert "Zaktualizowano:** 2026-07-20" in text
    assert "Wspólna baza wiedzy pionu." in text
    assert "3 (ostatnia: 2026-07-18)" in text
    assert "otwarte action items:** 2" in text


def test_to_text_renders_activity_with_truncated_timestamp():
    text = ProjectBrief(status=_status(), recent_notes=()).to_text()

    assert "5 zdarzeń" in text
    assert "nieudane CI:** 1" in text
    # Znacznik czasu przycięty do sekund (bez strefy/mikrosekund) — czytelny w one-pagerze.
    assert "2026-07-19T14:30:00" in text


def test_to_text_lists_recent_notes_with_dates_and_participants():
    brief = ProjectBrief(
        status=_status(),
        recent_notes=(
            _note("Przegląd API", date(2026, 7, 18), ["Anna Kowalska", "Marek Nowak"]),
            _note("Kickoff", date(2026, 7, 10), []),
        ),
    )

    text = brief.to_text()

    assert "- 2026-07-18 — Przegląd API (Anna Kowalska, Marek Nowak)" in text
    # Brak uczestników → jawna etykieta, nie pusty nawias.
    assert "- 2026-07-10 — Kickoff (brak uczestników)" in text


def test_to_text_handles_no_notes_and_missing_dates():
    brief = ProjectBrief(
        status=_status(latest_note_date=None, latest_activity_at=None),
        recent_notes=(),
    )

    text = brief.to_text()

    assert "_(brak notatek dla tego projektu)_" in text
    # Brak dat → kreska, nie „None" ani wyjątek.
    assert "ostatnia: —" in text
