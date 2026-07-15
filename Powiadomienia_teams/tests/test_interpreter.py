from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.agent.interpreter import ReplyDecision, build_schedule, interpret_reply
from powiadomienia_teams.domain.models import Shift, WeekSchedule

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")


class _FakeLlm:
    def __init__(self, response: str) -> None:
        self._response = response

    def complete(self, system: str, user: str) -> str:
        return self._response


def _proposal() -> WeekSchedule:
    # poniedziałek 08:00–16:00 lokalnie (06:00Z–14:00Z latem)
    shift = Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC))
    return WeekSchedule("u1", date(2026, 7, 20), (shift,))


def test_confirm_returns_schedule():
    llm = _FakeLlm('{"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]}')
    decision = interpret_reply(_proposal(), "ok", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "confirm"
    assert decision.schedule is not None
    assert len(decision.schedule.shifts) == 1
    assert decision.schedule.shifts[0].start.astimezone(WAW).hour == 8
    assert decision.schedule.shifts[0].scheduling_group_id == "TAG"


def test_modify_returns_new_schedule():
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":4,"start":"10:00","end":"20:00"}],'
        '"note":"tylko piątek"}'
    )
    decision = interpret_reply(_proposal(), "tylko piątek 10-20", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].start.astimezone(WAW).weekday() == 4
    assert decision.schedule.shifts[0].start.astimezone(WAW).hour == 10


def test_decline_has_no_schedule():
    decision = interpret_reply(
        _proposal(), "nie pracuję", tz=WAW, group_id=None, llm=_FakeLlm('{"action":"decline"}')
    )
    assert decision.action == "decline"
    assert decision.schedule is None


def test_invalid_interval_falls_back_to_unclear():
    # koniec przed początkiem → interwał odrzucony → pusty grafik → unclear
    llm = _FakeLlm('{"action":"modify","shifts":[{"weekday":0,"start":"20:00","end":"08:00"}]}')
    decision = interpret_reply(_proposal(), "bez sensu", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "unclear"
    assert decision.schedule is None


def test_json_wrapped_in_prose_is_extracted():
    llm = _FakeLlm(
        'Jasne! {"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]} gotowe'
    )
    decision = interpret_reply(_proposal(), "ok", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "confirm"


def test_build_schedule_respects_local_time():
    schedule = build_schedule(
        "u1", date(2026, 7, 20), [{"weekday": 0, "start": "08:00", "end": "16:00"}], WAW, "TAG"
    )
    assert schedule.shifts[0].start.astimezone(WAW).hour == 8
    assert schedule.shifts[0].end.astimezone(WAW).hour == 16


def test_confirm_copies_theme_per_weekday_from_proposal():
    # gotowiec: poniedziałek z kolorem green; model zwraca shifts BEZ theme → kolor kopiowany
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm('{"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]}')
    decision = interpret_reply(proposal, "ok", tz=WAW, group_id="TAG", llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


def test_reply_sets_mode_zdalnie_to_blue():
    shift = Shift(
        "u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00","tryb":"zdalnie"}]}'
    )
    decision = interpret_reply(proposal, "w pon zdalnie", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "blue"


def test_reply_sets_mode_stacjonarnie_to_green():
    shift = Shift(
        "u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="blue",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00",'
        '"tryb":"stacjonarnie"}]}'
    )
    decision = interpret_reply(proposal, "w pon do biura", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


# Testy kontraktowe (dokumentują mapowanie action → decyzja/grafik). LLM jest atrapą, więc NIE
# weryfikują samego promptu — czy realny model faktycznie zwróci decline/modify/unclear dla tych
# zdań sprawdza smoke na żywym modelu (patrz PLAN.md / docs live-smoke). Chronią przed regresją
# logiki interpret_reply/build_schedule dla nowo obsłużonych scenariuszy nieobecności.
def test_whole_week_vacation_declines():
    # „jestem na urlopie w tym tygodniu" → decline → brak grafiku, nic do zapisu
    llm = _FakeLlm('{"action":"decline","shifts":[],"note":"urlop"}')
    decision = interpret_reply(
        _proposal(), "jestem na urlopie w tym tygodniu", tz=WAW, group_id="TAG", llm=llm
    )
    assert decision.action == "decline"
    assert decision.schedule is None


def test_partial_absence_modifies_removing_day():
    # „w piątek mnie nie będzie, reszta tak samo" → modify z pozostałymi dniami (bez piątku)
    llm = _FakeLlm(
        '{"action":"modify","shifts":['
        '{"weekday":0,"start":"08:00","end":"16:00"},'
        '{"weekday":1,"start":"08:00","end":"16:00"},'
        '{"weekday":2,"start":"08:00","end":"16:00"},'
        '{"weekday":3,"start":"08:00","end":"16:00"}]}'
    )
    decision = interpret_reply(
        _proposal(), "w piątek mnie nie będzie, reszta tak samo", tz=WAW, group_id="TAG", llm=llm
    )
    assert decision.action == "modify"
    assert decision.schedule is not None
    weekdays = {s.start.astimezone(WAW).weekday() for s in decision.schedule.shifts}
    assert weekdays == {0, 1, 2, 3}  # piątek (4) usunięty


def test_ambiguous_absence_is_unclear():
    # „nie będzie mnie kilka dni" bez wskazania których → unclear (model nie zgaduje dni)
    llm = _FakeLlm('{"action":"unclear","shifts":[],"note":""}')
    decision = interpret_reply(
        _proposal(), "nie będzie mnie kilka dni", tz=WAW, group_id="TAG", llm=llm
    )
    assert decision.action == "unclear"
    assert decision.schedule is None


def test_reply_decision_defaults():
    d = ReplyDecision("unclear")
    assert d.schedule is None
    assert d.note == ""
