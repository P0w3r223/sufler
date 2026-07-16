from datetime import datetime, timedelta, timezone

from powiadomienia_teams.reminders.lifecycle import is_expired, prune_terminal
from powiadomienia_teams.state import APPLIED, AWAITING_REPLY, EXPIRED, PendingReminder

UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pending(status=AWAITING_REPLY, watermark="", nudged_at=""):
    return PendingReminder(
        member_id="u1", member_name="Ala", chat_id="c", week_start="2026-07-20",
        status=status, watermark=watermark, nudged_at=nudged_at,
    )


def test_is_expired_past_window():
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=49)))
    assert is_expired(p, NOW, 48) is True


def test_not_expired_within_window():
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=47)))
    assert is_expired(p, NOW, 48) is False


def test_watermark_extends_window_over_nudge():
    # Rozmowa w toku: nudge dawno, ale ostatnia aktywność świeża → okno liczone od aktywności.
    p = _pending(
        watermark=_iso(NOW - timedelta(hours=1)),
        nudged_at=_iso(NOW - timedelta(hours=100)),
    )
    assert is_expired(p, NOW, 48) is False


def test_legacy_record_without_nudged_at_uses_watermark():
    p = _pending(watermark=_iso(NOW - timedelta(hours=49)))  # nudged_at="" (stary wpis)
    assert is_expired(p, NOW, 48) is True


def test_record_without_anchor_never_expires():
    assert is_expired(_pending(), NOW, 48) is False  # watermark="" i nudged_at=""


def test_prune_drops_old_terminal_keeps_fresh_and_open():
    state = {
        "old": _pending(status=APPLIED, watermark=_iso(NOW - timedelta(hours=200))),
        "fresh": _pending(status=EXPIRED, watermark=_iso(NOW - timedelta(hours=10))),
        "open": _pending(status=AWAITING_REPLY, watermark=_iso(NOW - timedelta(hours=500))),
    }
    kept = prune_terminal(state, NOW, retain_hours=48)
    # stary terminalny wyrzucony; otwarty (mimo wieku) i świeży terminalny zostają
    assert set(kept) == {"fresh", "open"}


def test_prune_keeps_terminal_without_anchor():
    state = {"noanchor": _pending(status=APPLIED)}  # brak kotwicy → nie znamy wieku
    assert prune_terminal(state, NOW, 48) == state


def test_prune_returns_new_dict_without_mutating_input():
    state = {"old": _pending(status=APPLIED, watermark=_iso(NOW - timedelta(hours=200)))}
    prune_terminal(state, NOW, 48)
    assert "old" in state  # wejście nietknięte
