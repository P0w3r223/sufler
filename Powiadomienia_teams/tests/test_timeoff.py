from powiadomienia_teams.reminders.timeoff import (
    TeamReasons,
    display_name,
    normalize,
    resolve_time_off,
)


def _reasons() -> TeamReasons:
    return TeamReasons(
        by_name={"urlop": "TOR_U", "zwolnienie lekarskie": "TOR_L4", "nieobecność": "TOR_N"},
        names={"TOR_U": "Urlop", "TOR_L4": "Zwolnienie lekarskie", "TOR_N": "Nieobecność"},
    )


def test_display_name_maps_keywords():
    assert display_name("urlop") == "Urlop"
    assert display_name("chorobowe") == "Zwolnienie lekarskie"
    assert display_name("nieobecność") == "Nieobecność"


def test_display_name_unknown_falls_back_to_nieobecnosc():
    assert display_name("cokolwiek innego") == "Nieobecność"


def test_resolve_matches_specific_reason():
    r = _reasons()
    assert r.resolve("urlop") == ("TOR_U", "Urlop")
    assert r.resolve("chorobowe") == ("TOR_L4", "Zwolnienie lekarskie")


def test_resolve_falls_back_to_generic_when_specific_missing():
    # Zespół nie ma „Urlop bezpłatny" → fallback do istniejącej „Nieobecność" (dzień nie ginie).
    r = _reasons()
    assert r.resolve("urlop bezpłatny") == ("TOR_N", "Nieobecność")


def test_resolve_none_when_team_has_no_reasons():
    empty = TeamReasons(by_name={}, names={})
    assert empty.resolve("urlop") is None


def test_resolve_time_off_produces_resolved_entries():
    out = resolve_time_off([{"weekday": 4, "powod": "chorobowe"}], _reasons())
    assert out == [{"weekday": 4, "reason_id": "TOR_L4", "reason_name": "Zwolnienie lekarskie"}]


def test_resolve_time_off_skips_when_no_reasons():
    empty = TeamReasons(by_name={}, names={})
    assert resolve_time_off([{"weekday": 4, "powod": "urlop"}], empty) == []


def test_normalize_lowercases_and_collapses_spaces():
    assert normalize("  Zwolnienie   Lekarskie ") == "zwolnienie lekarskie"
