from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, Shift, WeekSchedule
from powiadomienia_teams.messages import (
    build_confirm_text,
    build_nudge_text,
    build_self_filled_text,
    build_summary_text,
    describe_schedule,
    describe_time_off,
    to_html,
)

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


def test_describe_time_off_uses_actual_reason_name():
    entries = [
        {"weekday": 4, "reason_id": "TOR_U", "reason_name": "Urlop"},
        {"weekday": 1, "reason_id": "TOR_L4", "reason_name": "Zwolnienie lekarskie"},
    ]
    assert describe_time_off(entries) == "pt: Urlop, wt: Zwolnienie lekarskie"


def test_describe_schedule_uses_color_emoji_not_words():
    green = Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC))
    blue = Shift(
        "u1",
        datetime(2026, 7, 21, 6, tzinfo=UTC),
        datetime(2026, 7, 21, 14, tzinfo=UTC),
        theme="blue",
    )
    text = describe_schedule(WeekSchedule("u1", date(2026, 7, 20), (green, blue)), WAW)
    assert "🟢" in text  # stacjonarnie (green/None)
    assert "🔵" in text  # zdalnie (blue)
    assert "stacjonarnie" not in text and "zdalnie" not in text  # bez słów w nawiasach


def test_confirm_text_mentions_schedule_and_time_off():
    schedule = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    time_off = [{"weekday": 4, "reason_id": "TOR_U", "reason_name": "Urlop"}]
    text = build_confirm_text(schedule, time_off, WAW)
    assert "grafik:" in text
    assert "czas wolny: pt: Urlop" in text
    assert "Potwierdź" in text


def test_build_self_filled_text_mentions_week_and_thanks():
    text = build_self_filled_text("20.07–26.07")
    assert "20.07–26.07" in text
    assert "uzupełniony" in text.lower()
    assert "dzięku" in text.lower()


def test_nudge_mentions_known_off_weekdays_and_excludes_them_from_ask():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(
        member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW, off_weekdays=[4]
    )
    assert "piątek" in text
    assert "wolne" in text.lower()
    assert "pozostałe dni" in text.lower()


def test_nudge_without_off_weekdays_keeps_original_wording():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW)
    assert "wolne" not in text.lower()
    assert "napisz proszę, kiedy pracujesz (np" in text


def test_nudge_with_proposal_and_off_weekdays_still_lists_shifts():
    member = Member("u1", "Mikołaj Anonimowicz")
    proposal = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    text = build_nudge_text(member, proposal, "20.07–26.07", WAW, off_weekdays=[4])
    assert "poniedziałek" in text
    assert "piątek" in text  # wspomniane jako dzień wolny


def test_summary_text_includes_self_filled_count():
    text = build_summary_text(
        oczekuje=1,
        do_potwierdzenia=0,
        zapisane=2,
        odmowy=0,
        wygasle=0,
        nastepny_przebieg="2026-07-24 16:00",
        samodzielne=3,
    )
    assert "uzupełnione samodzielnie: 3" in text


def test_summary_text_self_filled_defaults_to_zero():
    text = build_summary_text(
        oczekuje=0,
        do_potwierdzenia=0,
        zapisane=0,
        odmowy=0,
        wygasle=0,
        nastepny_przebieg="2026-07-24 16:00",
    )
    assert "uzupełnione samodzielnie: 0" in text
