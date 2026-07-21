"""Testy renderu wiadomości z kartą czasu (ADR 0035).

Ten HTML idzie do Teams z POMINIĘCIEM escapującego renderera (`send_chat_html`), więc kontrakt
„każda wartość z zewnątrz jest escapowana" jest tu kontrolą bezpieczeństwa, nie kosmetyką.
"""

from __future__ import annotations

from datetime import date

from workmate.core.domain.timesheet import Person, WorkEntry, build_timesheet
from workmate.core.domain.timesheet_message import render_timesheet_message

_PERSON = Person(
    source_id="EMP-042",
    aad_user_id="8a1f",
    jira_user="mikolaj@example.org",
    display_name="Mikołaj Anonimowicz",
)


def _sheet(entries: list[WorkEntry], person: Person = _PERSON):
    return build_timesheet(
        person,
        entries,
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026-W29",
    )


def _entry(day: int, issue: str, minutes: int, **kw) -> WorkEntry:
    return WorkEntry(
        source_id="EMP-042", day=date(2026, 7, day), issue_key=issue, minutes=minutes, **kw
    )


def _rendered(entries: list[WorkEntry], **kw) -> str:
    return render_timesheet_message(_sheet(entries), **kw)


# --- treść ------------------------------------------------------------------------


def test_greets_by_display_name() -> None:
    assert "Mikołaj Anonimowicz" in _rendered([_entry(15, "WT-12", 180)])


def test_falls_back_to_source_id_without_a_display_name() -> None:
    person = _PERSON.model_copy(update={"display_name": ""})
    assert "EMP-042" in render_timesheet_message(_sheet([_entry(15, "WT-12", 60)], person))


def test_shows_week_label_and_date_span() -> None:
    html = _rendered([_entry(15, "WT-12", 180)])
    assert "2026-W29" in html
    assert "2026-07-13" in html
    assert "2026-07-19" in html  # ostatni dzień okna, nie półotwarty koniec


def test_renders_a_real_html_table() -> None:
    """Sedno decyzji o `send_chat_html` — musi to być tabela, nie tekst."""
    html = _rendered([_entry(15, "WT-12", 180)])
    assert "<table" in html and "<tr>" in html and "</table>" in html


def test_one_row_per_entry() -> None:
    entries = [_entry(15, "WT-12", 180), _entry(16, "WT-14", 90), _entry(17, "WT-12", 60)]
    assert _rendered(entries).count("<tr>") == 4  # nagłówek + 3 wpisy


def test_shows_hours_not_minutes() -> None:
    html = _rendered([_entry(15, "WT-12", 150)])
    assert "2.5" in html
    assert "150" not in html


def test_shows_the_grand_total() -> None:
    html = _rendered([_entry(15, "WT-12", 180), _entry(16, "WT-14", 90)])
    assert "4.5 h" in html


def test_includes_file_path_when_given() -> None:
    html = _rendered([_entry(15, "WT-12", 60)], file_path=r"D:\worklogi\2026-W29\mikolaj.xlsx")
    assert "mikolaj.xlsx" in html


def test_omits_file_paragraph_when_path_is_empty() -> None:
    assert "<code>" not in _rendered([_entry(15, "WT-12", 60)])


def test_path_is_plain_text_not_a_link() -> None:
    """Bot nie uczy ludzi klikać w odnośniki — ścieżka idzie jako tekst."""
    html = _rendered([_entry(15, "WT-12", 60)], file_path=r"\\serwer\worklogi\a.xlsx")
    assert "<a " not in html


def test_warns_that_reimport_duplicates_entries() -> None:
    """Jedyna obrona przed podwójnym importem — import robi człowiek, nie my."""
    assert "zdubluje" in _rendered([_entry(15, "WT-12", 60)])


# --- escapowanie (kontrola bezpieczeństwa) ----------------------------------------


def test_escapes_html_in_display_name() -> None:
    person = _PERSON.model_copy(update={"display_name": "<script>alert(1)</script>"})
    html = render_timesheet_message(_sheet([_entry(15, "WT-12", 60)], person))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_escapes_html_in_issue_key() -> None:
    html = _rendered([_entry(15, "<b>WT-12</b>", 60)])
    assert "<b>WT-12</b>" not in html
    assert "&lt;b&gt;" in html


def test_escapes_html_in_file_path() -> None:
    html = _rendered([_entry(15, "WT-12", 60)], file_path='D:\\<img src=x onerror="1">.xlsx')
    assert "<img" not in html


def test_escapes_ampersand_in_values() -> None:
    html = _rendered([_entry(15, "WT-12&14", 60)])
    assert "&amp;" in html
