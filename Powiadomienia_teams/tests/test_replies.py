from powiadomienia_teams.reminders.replies import (
    is_affirmative,
    is_pure_affirmation,
    looks_like_schedule,
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


def test_is_affirmative():
    assert is_affirmative("ok")
    assert is_affirmative("Tak, potwierdzam!")
    assert is_affirmative("no dokładnie")
    assert not is_affirmative("w piątek mnie nie będzie")


def test_newest_incoming_compares_parsed_time_not_string():
    # '.' (0x2E) < 'Z' (0x5A) leksykograficznie przestawiłby te dwie w tej samej sekundzie
    msgs = [
        _msg("u1", "2026-07-19T18:00:00Z", "starsza"),
        _msg("u1", "2026-07-19T18:00:00.500Z", "nowsza"),
    ]
    picked = newest_incoming(msgs, "me", after_iso="2026-07-19T18:00:00Z")
    assert picked is not None
    assert message_text(picked) == "nowsza"


def test_looks_like_schedule():
    assert looks_like_schedule("tak ale w piątek 10-20")
    assert not looks_like_schedule("tak, potwierdzam")


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
