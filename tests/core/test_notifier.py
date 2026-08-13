"""Testy notifiera zdarzeń → Teams (EventNotifier, ADR 0022) — cele, kursor, at-least-once."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from workmate.core.application.notifier import (
    EventNotifier,
    NotifyTargets,
    default_event_render,
)
from workmate.core.domain.events import Event
from workmate.core.errors import ThreadRootGone

_WHEN = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)


def _event(event_id: int, *, source: str = "github", kind: str = "issue_opened", **kw) -> Event:
    base = {
        "id": event_id,
        "source": source,
        "kind": kind,
        "external_id": str(event_id),
        "title": f"Issue {event_id}",
        "url": f"http://gh/{event_id}",
        "occurred_at": _WHEN,
        "ingested_at": _WHEN,
    }
    base.update(kw)
    return Event(**base)


class _FakeEvents:
    """Atrapa ``EventService`` — oddaje zaskryptowaną listę filtrowaną kursorem i źródłem."""

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def read_since(self, after_id, *, source=None, limit=50):
        return [
            e for e in self._events if e.id > after_id and (source is None or e.source == source)
        ][:limit]


class _FakeSender:
    """Atrapa ``TeamsNotifier`` — notuje wywołania; opcjonalnie rzuca na ``post_channel``.

    ``post_channel`` ZWRACA id nowego roota (jak realny adapter po ADR 0024) — notifier zapisuje
    je do ``ThreadLinkStore``; ``reply_channel`` notuje dołożenie do istniejącego wątku.
    """

    def __init__(
        self, *, fail_channel: bool = False, gone_roots: frozenset[str] = frozenset()
    ) -> None:
        self.chats: list[tuple[str, str]] = []
        self.channels: list[tuple[str, str, str]] = []
        self.replies: list[tuple[str, str, str, str]] = []
        self._fail_channel = fail_channel
        self._gone_roots = gone_roots
        self._root_seq = 0

    async def send_chat(self, target_user_id, text):
        self.chats.append((target_user_id, text))

    async def post_channel(self, team_id, channel_id, text) -> str:
        if self._fail_channel:
            raise RuntimeError("Graph 503")
        self._root_seq += 1
        root_id = f"root-{self._root_seq}"
        self.channels.append((team_id, channel_id, text))
        return root_id

    async def reply_channel(self, team_id, channel_id, root_id, text):
        self.replies.append((team_id, channel_id, root_id, text))
        if root_id in self._gone_roots:
            raise ThreadRootGone(f"root {root_id} usunięty")  # jak adapter na Graph 404


class _FakeThreadLinks:
    """Atrapa ``ThreadLinkStore`` w pamięci — ``link`` NADPISUJE (upsert) jak realny SQLite."""

    def __init__(self) -> None:
        self.links: dict[tuple[str, str, str, str], str] = {}

    def get_root(self, team_id, channel_id, target_kind, target_number):
        return self.links.get((team_id, channel_id, target_kind, target_number))

    def get_target(self, team_id, channel_id, root_id):
        for (team, chan, kind, number), root in self.links.items():
            if team == team_id and chan == channel_id and root == root_id:
                return (kind, number)
        return None

    def link(self, team_id, channel_id, target_kind, target_number, root_id):
        # Nadpisanie (upsert): przełączenie wątku na nowy root po usunięciu starego.
        self.links[(team_id, channel_id, target_kind, target_number)] = root_id


def _notifier(
    events,
    sender,
    *,
    targets,
    cursor=0,
    saved=None,
    thread_links=None,
    dead_letters=None,
    heartbeat=None,
    max_attempts=5,
):
    return EventNotifier(
        events,
        sender,
        targets=targets,
        save_cursor=(saved.append if saved is not None else lambda _cid: None),
        cursor=cursor,
        thread_links=thread_links,
        dead_letters=dead_letters,
        heartbeat=heartbeat,
        max_attempts=max_attempts,
    )


def test_pumps_to_both_targets_when_enabled():
    sender = _FakeSender()
    targets = NotifyTargets(
        chat_user_id="u1",
        team_id="t1",
        channel_id="c1",
        enable_chat=True,
        enable_channel=True,
    )
    sent = asyncio.run(_notifier(_FakeEvents([_event(1)]), sender, targets=targets).pump_once())
    assert sent == 1
    assert len(sender.chats) == 1 and len(sender.channels) == 1


def test_only_enabled_target_receives():
    sender = _FakeSender()
    targets = NotifyTargets(chat_user_id="u1", enable_chat=True, enable_channel=False)
    asyncio.run(_notifier(_FakeEvents([_event(1)]), sender, targets=targets).pump_once())
    assert len(sender.chats) == 1
    assert sender.channels == []  # kanał wyłączony


def test_only_notifies_github_source():
    sender = _FakeSender()
    targets = NotifyTargets(chat_user_id="u1", enable_chat=True)
    events = _FakeEvents([_event(1, source="teams"), _event(2, source="github")])
    asyncio.run(_notifier(events, sender, targets=targets).pump_once())
    # Zdarzenie source="teams" pominięte (strażnik pętli) — tylko github wypchnięte.
    assert [c[0] for c in sender.chats] == ["u1"]
    assert sender.chats[0][1].startswith("**[GitHub]")


def test_cursor_advances_after_send():
    sender = _FakeSender()
    saved: list[int] = []
    targets = NotifyTargets(chat_user_id="u1", enable_chat=True)
    events = _FakeEvents([_event(1), _event(2), _event(3)])
    asyncio.run(_notifier(events, sender, targets=targets, saved=saved).pump_once())
    assert saved == [1, 2, 3]  # kursor zapisany po każdym udanym wysłaniu


def test_cursor_not_advanced_on_send_failure():
    # Bez magazynu kwarantanny (dawne zachowanie, ADR 0067): porażka wysyłki NIE przesuwa kursora
    # i NIE rzuca — runda przerywa się, następna ponowi (at-least-once). Poison message blokuje.
    sender = _FakeSender(fail_channel=True)
    saved: list[int] = []
    targets = NotifyTargets(
        chat_user_id="u1",
        team_id="t1",
        channel_id="c1",
        enable_chat=True,
        enable_channel=True,
    )
    sent = asyncio.run(
        _notifier(_FakeEvents([_event(1)]), sender, targets=targets, saved=saved).pump_once()
    )
    assert sent == 0
    assert saved == []  # kursor NIE przesunięty → następna runda ponowi (at-least-once)


def test_default_render_includes_key_fields():
    text = default_event_render(_event(7, title="Awaria API", actor="alice"))
    assert "**[GitHub] Nowe issue**" in text
    assert "Awaria API" in text
    assert "alice" in text
    assert "http://gh/7" in text


@pytest.mark.parametrize(
    "kind,label",
    [
        ("pr_opened", "Nowy PR"),
        ("pr_comment", "Nowy komentarz w PR"),
        ("pr_review", "Recenzja PR"),
        ("ci_success", "CI: sukces"),
        ("ci_failure", "CI: porażka"),
    ],
)
def test_default_render_labels_new_event_kinds(kind, label):
    text = default_event_render(_event(7, kind=kind))
    assert f"**[GitHub] {label}**" in text


def test_default_render_falls_back_to_raw_kind_when_unknown():
    text = default_event_render(_event(7, kind="mystery_kind"))
    assert "mystery_kind" in text  # brak etykiety → surowy kind (nie wywala renderu)


@pytest.mark.parametrize(
    "kind,label",
    [
        ("jira_issue_created", "Nowe zgłoszenie"),
        ("jira_transition", "Zmiana statusu"),
        ("jira_comment", "Nowy komentarz"),
    ],
)
def test_default_render_labels_jira_kinds_with_jira_source(kind, label):
    # Etykieta źródła wyprowadzona z event.source (ADR 0030) — nie zaszyta [GitHub].
    text = default_event_render(_event(7, source="jira", kind=kind))
    assert f"**[Jira] {label}**" in text


def test_default_render_unknown_source_falls_back_to_raw_source():
    text = default_event_render(_event(7, source="gitlab", kind="issue_opened"))
    assert "**[gitlab] Nowe issue**" in text


def test_notifier_source_configurable_to_jira():
    sender = _FakeSender()
    targets = NotifyTargets(chat_user_id="u1", enable_chat=True)
    events = _FakeEvents([_event(1, source="github"), _event(2, source="jira")])
    notifier = EventNotifier(
        events,
        sender,
        targets=targets,
        save_cursor=lambda _cid: None,
        source="jira",
    )
    asyncio.run(notifier.pump_once())
    # source="jira" → tylko zdarzenie Jiry wypchnięte (github pominięty przez filtr źródła).
    assert len(sender.chats) == 1
    assert sender.chats[0][1].startswith("**[Jira]")


# --- ADR 0024 Faza 3a: wątkowanie kanału (GitHub → jeden wątek na issue/PR) -----------

_CHANNEL_TARGETS = NotifyTargets(
    team_id="t1",
    channel_id="c1",
    enable_channel=True,
)


def test_threading_off_always_new_root_never_reply():
    """Wątkowanie OFF (thread_links=None): każde zdarzenie to nowy root — wsteczna zgodność."""
    sender = _FakeSender()
    events = _FakeEvents(
        [
            _event(1, kind="issue_opened", url="https://github.com/o/r/issues/7"),
            _event(2, kind="issue_comment", url="https://github.com/o/r/issues/7"),
        ]
    )
    asyncio.run(_notifier(events, sender, targets=_CHANNEL_TARGETS, thread_links=None).pump_once())
    # Oba zdarzenia tego samego issue → DWA osobne rooty; nigdy reply (jak przed ADR 0024).
    assert len(sender.channels) == 2
    assert sender.replies == []


def test_threading_on_first_event_creates_and_persists_root():
    """Wątkowanie ON, brak roota: post_channel raz, zwrócone id zapisane w ThreadLinkStore."""
    sender = _FakeSender()
    links = _FakeThreadLinks()
    events = _FakeEvents([_event(1, url="https://github.com/o/r/pull/12")])
    asyncio.run(_notifier(events, sender, targets=_CHANNEL_TARGETS, thread_links=links).pump_once())
    assert len(sender.channels) == 1  # nowy root
    assert sender.replies == []
    # Link zapisany: kolejne zdarzenie tego PR trafi do tego roota.
    assert links.get_root("t1", "c1", "pr", "12") == "root-1"


def test_threading_on_second_event_of_same_target_replies_to_root():
    """Wątkowanie ON, istniejący root: drugie zdarzenie tego samego CELU → reply, bez nowego roota.

    Klucz to CEL (issue #7), nie rodzaj zdarzenia: ``issue_opened`` tworzy root, a ``issue_comment``
    tego samego issue dołącza do tego wątku.
    """
    sender = _FakeSender()
    links = _FakeThreadLinks()
    events = _FakeEvents(
        [
            _event(1, kind="issue_opened", url="https://github.com/o/r/issues/7"),
            _event(2, kind="issue_comment", url="https://github.com/o/r/issues/7"),
        ]
    )
    asyncio.run(_notifier(events, sender, targets=_CHANNEL_TARGETS, thread_links=links).pump_once())
    assert len(sender.channels) == 1  # tylko pierwsze zdarzenie utworzyło root
    assert len(sender.replies) == 1  # drugie dołączone jako odpowiedź
    team_id, channel_id, root_id, _text = sender.replies[0]
    assert (team_id, channel_id) == ("t1", "c1")
    assert root_id == "root-1"  # reply do zapamiętanego roota pierwszego zdarzenia


def test_threading_on_event_without_target_is_new_root_without_link():
    """Wątkowanie ON, zdarzenie bez celu (CI bez PR): nowy root, BEZ zapisu linku."""
    sender = _FakeSender()
    links = _FakeThreadLinks()
    events = _FakeEvents(
        [
            _event(1, kind="ci_failure", url="https://github.com/o/r/actions/runs/9"),
        ]
    )
    asyncio.run(_notifier(events, sender, targets=_CHANNEL_TARGETS, thread_links=links).pump_once())
    assert len(sender.channels) == 1  # osobny root
    assert sender.replies == []
    assert links.links == {}  # brak celu → nic do zmapowania


def test_threading_on_reply_to_deleted_root_creates_new_root_and_relinks():
    """Root wątku usunięty w Teams (reply → ThreadRootGone): notifier NIE blokuje strumienia —
    tworzy nowy root i PRZEŁĄCZA link, a kursor idzie dalej (naprawa MAJOR z review 3a)."""
    links = _FakeThreadLinks()
    links.link("t1", "c1", "pr", "12", "root-old")  # istniejący link do usuniętego roota
    sender = _FakeSender(gone_roots=frozenset({"root-old"}))
    events = _FakeEvents([_event(1, url="https://github.com/o/r/pull/12")])
    saved: list[int] = []

    sent = asyncio.run(
        _notifier(
            events, sender, targets=_CHANNEL_TARGETS, thread_links=links, saved=saved
        ).pump_once()
    )

    assert sent == 1  # zdarzenie obsłużone — brak head-of-line blocking
    assert len(sender.replies) == 1  # próbowaliśmy odpowiedzieć do martwego roota
    assert len(sender.channels) == 1  # utworzono NOWY root po ThreadRootGone
    assert links.get_root("t1", "c1", "pr", "12") == "root-1"  # link przełączony na nowy root
    assert saved == [1]  # kursor przesunięty (postęp zachowany)


def test_threading_on_chat_branch_stays_independent():
    """Czat 1:1 działa jak dotąd (send_chat) — wątkowanie dotyczy WYŁĄCZNIE kanału."""
    sender = _FakeSender()
    links = _FakeThreadLinks()
    targets = NotifyTargets(
        chat_user_id="u1",
        team_id="t1",
        channel_id="c1",
        enable_chat=True,
        enable_channel=True,
    )
    events = _FakeEvents([_event(1, url="https://github.com/o/r/pull/12")])
    asyncio.run(_notifier(events, sender, targets=targets, thread_links=links).pump_once())
    assert len(sender.chats) == 1  # czat 1:1 dostał wiadomość niezależnie od wątkowania kanału
    assert len(sender.channels) == 1  # kanał: nowy root przez ścieżkę wątkowania
    assert sender.replies == []


# --- ADR 0024 B2: wątkowanie kanału dla źródła Jira (url /browse/{KEY}) ----------------


def _jira_notifier(events, sender, *, thread_links):
    return EventNotifier(
        events,
        sender,
        targets=_CHANNEL_TARGETS,
        save_cursor=lambda _cid: None,
        source="jira",
        thread_links=thread_links,
    )


def test_jira_threading_same_issue_replies_to_root():
    """Utworzenie i komentarz JEDNEGO zgłoszenia (te same /browse/WM-5, różny sufiks) → jeden wątek.

    Drugie zdarzenie niesie ``?focusedCommentId=`` — cel wątku to nadal klucz WM-5, więc dokłada
    się jako odpowiedź do roota pierwszego, a nie nowy root.
    """
    sender = _FakeSender()
    links = _FakeThreadLinks()
    events = _FakeEvents(
        [
            _event(1, source="jira", kind="jira_issue_created", url="https://j/browse/WM-5"),
            _event(
                2,
                source="jira",
                kind="jira_comment",
                url="https://j/browse/WM-5?focusedCommentId=99",
            ),
        ]
    )
    asyncio.run(_jira_notifier(events, sender, thread_links=links).pump_once())
    assert len(sender.channels) == 1  # tylko pierwsze zdarzenie utworzyło root
    assert len(sender.replies) == 1  # komentarz dołączony jako odpowiedź
    assert sender.replies[0][2] == "root-1"  # reply do roota WM-5
    assert links.get_root("t1", "c1", "jira", "WM-5") == "root-1"


def test_jira_threading_different_issue_starts_new_root():
    """Inny klucz zgłoszenia = inny cel → osobny root (wątki zgłoszeń się nie mieszają)."""
    sender = _FakeSender()
    links = _FakeThreadLinks()
    events = _FakeEvents(
        [
            _event(1, source="jira", kind="jira_issue_created", url="https://j/browse/WM-5"),
            _event(2, source="jira", kind="jira_issue_created", url="https://j/browse/OPS-9"),
        ]
    )
    asyncio.run(_jira_notifier(events, sender, thread_links=links).pump_once())
    assert len(sender.channels) == 2  # dwa różne cele → dwa rooty
    assert sender.replies == []
    assert links.get_root("t1", "c1", "jira", "WM-5") == "root-1"
    assert links.get_root("t1", "c1", "jira", "OPS-9") == "root-2"


# --- Dead-letter + puls notifiera (ADR 0067 §2) -------------------------------------------------


class _FakeDeadLetters:
    """Atrapa ``DeadLetterStore`` w pamięci — notuje przeniesienia do kwarantanny."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, *, source, event_id, reason, attempts):
        self.records.append(
            {"source": source, "event_id": event_id, "reason": reason, "attempts": attempts}
        )

    def recent(self, limit=200):
        return list(reversed(self.records))[:limit]


def _channel_targets() -> NotifyTargets:
    return NotifyTargets(team_id="t1", channel_id="c1", enable_channel=True)


def test_dead_letter_after_max_attempts_advances_cursor_and_beats():
    sender = _FakeSender(fail_channel=True)
    dl = _FakeDeadLetters()
    beats: list[int] = []
    saved: list[int] = []
    notifier = _notifier(
        _FakeEvents([_event(1)]),
        sender,
        targets=_channel_targets(),
        saved=saved,
        dead_letters=dl,
        heartbeat=lambda: beats.append(1),
        max_attempts=3,
    )
    # Rundy 1 i 2: poniżej progu — kursor stoi, brak dead-letter, brak pulsu (sygnał zatoru).
    for _ in range(2):
        assert asyncio.run(notifier.pump_once()) == 0
    assert saved == [] and dl.records == [] and beats == []
    # Runda 3: próg osiągnięty — zdarzenie do kwarantanny, DOPIERO POTEM kursor rusza, puls bije.
    assert asyncio.run(notifier.pump_once()) == 1
    assert [r["event_id"] for r in dl.records] == [1]
    assert saved == [1]  # kursor przesunięty PO dead-letter (zapis-przed-ruchem)
    assert beats == [1]  # runda produktywna


def test_below_threshold_leaves_cursor_and_does_not_beat():
    sender = _FakeSender(fail_channel=True)
    dl = _FakeDeadLetters()
    beats: list[int] = []
    saved: list[int] = []
    notifier = _notifier(
        _FakeEvents([_event(1)]),
        sender,
        targets=_channel_targets(),
        saved=saved,
        dead_letters=dl,
        heartbeat=lambda: beats.append(1),
        max_attempts=5,
    )
    assert asyncio.run(notifier.pump_once()) == 0
    assert saved == [] and dl.records == [] and beats == []


def test_successful_round_beats_and_advances():
    sender = _FakeSender()  # post_channel zwraca root (sukces)
    beats: list[int] = []
    saved: list[int] = []
    notifier = _notifier(
        _FakeEvents([_event(1)]),
        sender,
        targets=_channel_targets(),
        saved=saved,
        dead_letters=_FakeDeadLetters(),
        heartbeat=lambda: beats.append(1),
    )
    assert asyncio.run(notifier.pump_once()) == 1
    assert saved == [1] and beats == [1]


def test_empty_batch_still_beats():
    beats: list[int] = []
    notifier = _notifier(
        _FakeEvents([]),
        _FakeSender(),
        targets=_channel_targets(),
        dead_letters=_FakeDeadLetters(),
        heartbeat=lambda: beats.append(1),
    )
    assert asyncio.run(notifier.pump_once()) == 0
    assert beats == [1]  # pusta kolejka = zdrowo, puls bije


def test_dead_letter_records_source_and_reason():
    sender = _FakeSender(fail_channel=True)
    dl = _FakeDeadLetters()
    notifier = _notifier(
        _FakeEvents([_event(1)]),
        sender,
        targets=_channel_targets(),
        dead_letters=dl,
        max_attempts=1,  # pierwsza porażka od razu dead-letteruje
    )
    asyncio.run(notifier.pump_once())
    assert dl.records[0]["source"] == "github"
    assert "Graph 503" in dl.records[0]["reason"]
    assert dl.records[0]["attempts"] == 1
