from powiadomienia_teams.reminders.replies import (
    MEMORY_CAP,
    MEMORY_WINDOW,
    advance_memory,
    history_for_llm,
    is_pure_affirmation,
    message_text,
    newest_incoming,
)


def _msg(sender: str, created: str, content: str = "") -> dict:
    return {
        "from": {"user": {"id": sender}},
        "createdDateTime": created,
        "body": {"content": content, "contentType": "html"},
    }


def test_message_text_strips_html():
    assert message_text(_msg("u1", "t", "<p>w piątek <b>10-20</b></p>")) == "w piątek  10-20"


def test_newest_incoming_ignores_own_and_old():
    me = "me"
    messages = [
        _msg("me", "2026-07-19T16:00:00Z"),  # nasze — pominięte
        _msg("u1", "2026-07-19T17:00:00Z", "stara"),  # przed watermarkiem
        _msg("u1", "2026-07-19T18:00:00Z", "nowa"),
    ]
    picked = newest_incoming(messages, me, after_iso="2026-07-19T17:30:00Z")
    assert picked is not None
    assert message_text(picked) == "nowa"


def test_newest_incoming_none_when_only_own():
    picked = newest_incoming([_msg("me", "2026-07-19T18:00:00Z")], "me")
    assert picked is None


def test_newest_incoming_compares_parsed_time_not_string():
    # '.' (0x2E) < 'Z' (0x5A) leksykograficznie przestawiłby te dwie w tej samej sekundzie
    msgs = [
        _msg("u1", "2026-07-19T18:00:00Z", "starsza"),
        _msg("u1", "2026-07-19T18:00:00.500Z", "nowsza"),
    ]
    picked = newest_incoming(msgs, "me", after_iso="2026-07-19T18:00:00Z")
    assert picked is not None
    assert message_text(picked) == "nowsza"


def test_is_pure_affirmation_accepts_clean_yes():
    assert is_pure_affirmation("tak")
    assert is_pure_affirmation("Ok!")
    assert is_pure_affirmation("ok, dzięki")
    assert is_pure_affirmation("no dokładnie")


def test_is_pure_affirmation_rejects_correction_even_without_digits():
    # Regresja live: „Ok, ale nie będzie mnie w czwartek" NIE jest czystym potwierdzeniem —
    # zawiera poprawkę (brak cyfr), więc musi trafić do reinterpretacji.
    assert not is_pure_affirmation("Ok, ale nie będzie mnie w czwartek")
    assert not is_pure_affirmation("tak ale w piątek 10-20")
    assert not is_pure_affirmation("ok tylko środa wolne")
    assert not is_pure_affirmation("w piątek mnie nie będzie")


def test_advance_memory_starts_fresh_and_anchors_on_first_message():
    memory, started_at = advance_memory([], "", "2026-07-19T18:00:00Z", "pon-pt 8-16")
    assert memory == ["pon-pt 8-16"]
    assert started_at == "2026-07-19T18:00:00Z"


def test_advance_memory_appends_without_moving_anchor():
    memory, started_at = advance_memory(
        ["pon-pt 8-16"], "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "a w piątek zdalnie"
    )
    assert memory == ["pon-pt 8-16", "a w piątek zdalnie"]
    assert started_at == "2026-07-19T18:00:00Z"  # kotwica NIE przesuwa się przy każdej wiadomości


def test_advance_memory_caps_at_ten_dropping_oldest():
    memory = [f"msg{i}" for i in range(MEMORY_CAP)]
    new_memory, started_at = advance_memory(
        memory, "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "nowa"
    )
    assert len(new_memory) == MEMORY_CAP
    assert new_memory[0] == "msg1"  # najstarsza (msg0) odpadła
    assert new_memory[-1] == "nowa"
    assert started_at == "2026-07-19T18:00:00Z"  # kotwica przycięciem się nie rusza


def test_advance_memory_resets_after_window_expires():
    started = "2026-07-19T18:00:00Z"
    after_window = "2026-07-19T19:01:00Z"  # > 1h po kotwicy
    memory, started_at = advance_memory(["stare"], started, after_window, "nowe od zera")
    assert memory == ["nowe od zera"]
    assert started_at == after_window


def test_advance_memory_is_idempotent_for_same_inputs():
    args = (["a"], "2026-07-19T18:00:00Z", "2026-07-19T18:05:00Z", "b")
    assert advance_memory(*args) == advance_memory(*args)


def test_history_for_llm_returns_copy_of_memory_within_window():
    memory = ["a", "b"]
    history = history_for_llm(memory, "2026-07-19T18:00:00Z", "2026-07-19T18:30:00Z")
    assert history == ["a", "b"]
    assert history is not memory


def test_history_for_llm_empty_when_window_expired():
    history = history_for_llm(["a", "b"], "2026-07-19T18:00:00Z", "2026-07-19T19:01:00Z")
    assert history == []


def test_history_for_llm_empty_when_no_anchor():
    assert history_for_llm([], "", "2026-07-19T18:00:00Z") == []


def test_memory_window_is_one_hour():
    from datetime import timedelta

    assert timedelta(hours=1) == MEMORY_WINDOW
