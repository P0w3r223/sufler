from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, Shift, WeekSchedule
from powiadomienia_teams.messages import build_nudge_text, to_html

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")


def test_nudge_with_proposal_lists_days_in_local_time():
    member = Member("u1", "Mikołaj Anonimowicz")
    # 06:00Z–14:00Z latem = 08:00–16:00 czasu lokalnego
    proposal = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    text = build_nudge_text(member, proposal, "20.07–26.07", WAW)
    assert "Mikołaj" in text
    assert "poniedziałek" in text
    assert "08:00–16:00" in text
    assert "ok" in text.lower()


def test_nudge_without_proposal_asks_for_hours():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW)
    assert "Ala" in text
    assert "napisz" in text.lower()


def test_to_html_escapes_and_breaks_lines():
    html = to_html("linia 1\n<b>x</b>")
    assert "<br>" in html
    assert "&lt;b&gt;" in html
    assert "<b>" not in html
