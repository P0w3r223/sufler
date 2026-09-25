"""Testy renderu digestu zmian (``ChangeDigest.to_text``, F5, ADR 0052).

Render jest CZYSTY, więc testujemy go bez atrap I/O: składamy ``ChangeDigest``/``ProjectChanges``
wprost i sprawdzamy nagłówek, wiersz łączny + wg źródła, sekcje per projekt, etykietę
nieprzypisanych, przypadek pusty oraz notkę ucięcia.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sufler.core.domain.change_digest import ChangeDigest, ProjectChanges


def _dt(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, 0, 0, tzinfo=UTC)


def test_to_text_renders_header_totals_and_project_sections():
    digest = ChangeDigest(
        since=date(2026, 7, 1),
        total=5,
        by_source=(("github", 4), ("jira", 1)),
        projects=(
            ProjectChanges(
                project="workmate",
                total=4,
                by_kind=(("pr_merged", 2), ("issue_opened", 2)),
                latest=_dt(19, 14),
            ),
            ProjectChanges(
                project="scada-integration", total=1, by_kind=(("issue_opened", 1),), latest=_dt(10)
            ),
        ),
        truncated=False,
    )

    text = digest.to_text()

    assert "# Co się zmieniło od 2026-07-01" in text
    assert "Zdarzeń łącznie:** 5 · github: 4 · jira: 1" in text
    assert "## workmate — 4" in text
    assert "pr_merged: 2 · issue_opened: 2 (ostatnia: 2026-07-19T14:00:00)" in text
    assert "## scada-integration — 1" in text
    assert "_(okno ucięte" not in text  # truncated=False → brak notki


def test_to_text_labels_unassigned_project_section():
    digest = ChangeDigest(
        since=date(2026, 7, 1),
        total=1,
        by_source=(("teams", 1),),
        projects=(ProjectChanges(project="", total=1, by_kind=(("mention", 1),), latest=_dt(5)),),
        truncated=False,
    )

    assert "## (nieprzypisane) — 1" in digest.to_text()


def test_to_text_empty_window_has_no_project_sections():
    digest = ChangeDigest(
        since=date(2026, 7, 1), total=0, by_source=(), projects=(), truncated=False
    )

    text = digest.to_text()

    assert "_(brak zdarzeń w tym oknie)_" in text
    assert "##" not in text  # brak sekcji projektów
    # Wiersz łączny nadal jest, z kreską zamiast pustej listy źródeł.
    assert "Zdarzeń łącznie:** 0 · —" in text


def test_to_text_marks_truncated_window():
    digest = ChangeDigest(
        since=date(2026, 7, 1),
        total=3,
        by_source=(("github", 3),),
        projects=(
            ProjectChanges(project="workmate", total=3, by_kind=(("ci_failed", 3),), latest=_dt(9)),
        ),
        truncated=True,
    )

    assert "_(okno ucięte do najnowszych zdarzeń — mogło ich być więcej.)_" in digest.to_text()
