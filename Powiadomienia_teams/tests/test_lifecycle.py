from datetime import datetime, timedelta, timezone

from powiadomienia_teams.domain.models import TimeOff
from powiadomienia_teams.reminders.lifecycle import (
    ReadOutcome,
    is_expired,
    prune_terminal,
    ready_for_self_fill_check,
    should_expire,
    still_writable,
)
from powiadomienia_teams.state import (
    APPLIED,
    AWAITING_REPLY,
    EXPIRED,
    SELF_FILLED,
    PendingReminder,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pending(status=AWAITING_REPLY, watermark="", nudged_at=""):
    return PendingReminder(
        member_id="u1",
        member_name="Ala",
        chat_id="c",
        week_start="2026-07-20",
        status=status,
        watermark=watermark,
        nudged_at=nudged_at,
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


# --- should_expire: termin to za mało, potrzebny DOWÓD (ADR 0003) ---------------------------

_PO_TERMINIE = _iso(NOW - timedelta(hours=49))


def test_should_expire_only_on_successful_read_finding_nothing():
    p = _pending(nudged_at=_PO_TERMINIE)
    assert should_expire(p, NOW, 48, read=ReadOutcome.NOTHING_NEW) is True


def test_handled_reply_blocks_expiry_even_after_deadline():
    # Przestój dłuższy niż okno: odpowiedź czekała w czacie i właśnie została obsłużona. Wygaszenie
    # w tym samym przebiegu wysłałoby prośbę o potwierdzenie i zaraz po niej „brak odpowiedzi".
    p = _pending(nudged_at=_PO_TERMINIE)
    assert should_expire(p, NOW, 48, read=ReadOutcome.HANDLED) is False


def test_failed_read_blocks_expiry_even_after_deadline():
    # Brak dowodu to nie dowód braku — awaria odczytu nie może kosztować pracownika grafiku.
    p = _pending(nudged_at=_PO_TERMINIE)
    assert should_expire(p, NOW, 48, read=ReadOutcome.UNKNOWN) is False


def test_evidence_alone_does_not_expire_before_deadline():
    # Kontrola w drugą stronę: dowód bez upływu terminu też nie wygasza.
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=1)))
    assert should_expire(p, NOW, 48, read=ReadOutcome.NOTHING_NEW) is False


# --- still_writable: użyteczność zapisu ma własny termin (ADR 0003) -------------------------


def _zmiana(start_h: int, end_h: int) -> TimeOff:
    """Wpis z konkretnym oknem czasu. TimeOff wystarcza — filtr patrzy wyłącznie na `end`."""
    return TimeOff(
        "u1",
        NOW.replace(hour=0) + timedelta(hours=start_h),
        NOW.replace(hour=0) + timedelta(hours=end_h),
        "TOR_URLOP",
    )


def test_finished_entries_are_dropped():
    # NOW to 12:00. Wpis 8:00-10:00 już się skończył — w grafiku byłby fałszywym stanem faktycznym.
    assert still_writable([_zmiana(8, 10)], NOW) == ()


def test_entry_in_progress_is_kept():
    # 8:00-16:00 trwa w tej chwili: praca jest realna, więc wpis nadal wart zapisania. Kryterium
    # to KONIEC, nie początek — inaczej gubilibyśmy dzień, który właśnie się dzieje.
    wpis = _zmiana(8, 16)
    assert still_writable([wpis], NOW) == (wpis,)


def test_future_entries_are_kept():
    wpis = _zmiana(30, 38)  # jutro
    assert still_writable([wpis], NOW) == (wpis,)


def test_partially_past_week_keeps_only_the_rest():
    # Sedno decyzji: „tak" potwierdzone w środku tygodnia zapisuje RESZTĘ tygodnia, a nie nic
    # (jak przy zamykaniu całego tematu) i nie wszystko (jak przed tą zmianą).
    minione, trwajace, przyszle = _zmiana(0, 6), _zmiana(8, 16), _zmiana(30, 38)
    assert still_writable([minione, trwajace, przyszle], NOW) == (trwajace, przyszle)


def test_empty_input_gives_empty_result():
    # Pusty wynik jest sygnałem „nie ma czego zapisać" dla wołającego — nie może rzucać.
    assert still_writable([], NOW) == ()


# --- SELF_FILLED: nowy status terminalny --------------------------------------------------


def test_self_filled_is_terminal_and_pruned_like_others():
    state = {
        "old": _pending(status=SELF_FILLED, watermark=_iso(NOW - timedelta(hours=200))),
        "open": _pending(status=AWAITING_REPLY, watermark=_iso(NOW - timedelta(hours=500))),
    }
    kept = prune_terminal(state, NOW, retain_hours=48)
    assert set(kept) == {"open"}


def test_self_filled_kept_when_fresh():
    state = {"fresh": _pending(status=SELF_FILLED, watermark=_iso(NOW - timedelta(hours=10)))}
    assert prune_terminal(state, NOW, retain_hours=48) == state


# --- ready_for_self_fill_check --------------------------------------------------------------


def test_ready_for_self_fill_check_true_after_idle_threshold():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=3601)))
    assert ready_for_self_fill_check(p, NOW, 3600) is True


def test_ready_for_self_fill_check_false_before_idle_threshold():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=1000)))
    assert ready_for_self_fill_check(p, NOW, 3600) is False


def test_ready_for_self_fill_check_negative_min_idle_disables():
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=1000)))
    assert ready_for_self_fill_check(p, NOW, -1) is False


def test_ready_for_self_fill_check_zero_checks_every_silent_cycle():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=1)))
    assert ready_for_self_fill_check(p, NOW, 0) is True


def test_ready_for_self_fill_check_false_without_anchor():
    assert ready_for_self_fill_check(_pending(), NOW, 3600) is False


def test_ready_for_self_fill_check_watermark_extends_like_expiry():
    # Rozmowa w toku: nudge dawno, ostatnia aktywność świeża → liczone od aktywności (jak _anchor).
    p = _pending(
        watermark=_iso(NOW - timedelta(seconds=100)),
        nudged_at=_iso(NOW - timedelta(hours=100)),
    )
    assert ready_for_self_fill_check(p, NOW, 3600) is False
