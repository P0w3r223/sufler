"""Testy notifiera zdarzeń → Teams (EventNotifier, ADR 0022) — cele, kursor, at-least-once."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from workmate.core.application import notifier as notifier_module
from workmate.core.application.notifier import (
    EventNotifier,
    NotifyTargets,
    default_event_render,
)
from workmate.core.domain.events import Event
from workmate.core.errors import ThreadRootGone

_WHEN = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


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


def test_a_failing_cursor_save_leaves_the_event_redeliverable_after_restart():
    """Utrwalenie kursora też bywa niemożliwe (ENOSPC, wolumen stanu zamontowany read-only).

    Wszystkie dotychczasowe sondy kursora ćwiczą awarię WYSYŁKI; awaria samego ZAPISU kursora to
    druga strona at-least-once i nie ćwiczył jej nikt. Niezmiennik jest taki: zdarzenie może
    pójść do Teams DRUGI raz, ale nie ma prawa przepaść — po restarcie kursor wraca z dysku, więc
    runda rusza od miejsca ostatniego UDANEGO zapisu, a nie od stanu z pamięci procesu.
    """
    sender = _FakeSender()
    targets = NotifyTargets(chat_user_id="u1", enable_chat=True)
    events = _FakeEvents([_event(1), _event(2)])

    def zapis_ktory_pada(_cursor_id: int) -> None:
        raise OSError("wolumen stanu tylko do odczytu")

    zablokowany = EventNotifier(
        events, sender, targets=targets, save_cursor=zapis_ktory_pada, cursor=0
    )
    with pytest.raises(OSError, match="tylko do odczytu"):
        asyncio.run(zablokowany.pump_once())
    assert len(sender.chats) == 1  # pierwsze zdarzenie POSZŁO, ale kursor nie ma jak przeżyć

    po_restarcie = _FakeSender()
    utrwalone: list[int] = []
    asyncio.run(
        EventNotifier(
            events, po_restarcie, targets=targets, save_cursor=utrwalone.append, cursor=0
        ).pump_once()
    )

    assert utrwalone == [1, 2]  # kursor z DYSKU (0) — oba zdarzenia znów w strumieniu
    assert len(po_restarcie.chats) == 2


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

    def recent_by_time(self, *, source=None, project=None, limit=20):
        """Jak ``recent``, ale po CZASIE ZDARZENIA — wiernie wobec magazynu (ADR 0071).

        Nie alias: alias ukryłby różnicę, o którą w tej zmianie chodzi, a atrapa odpowiadałaby
        na pytanie o czas kolejnością przyjęcia.
        """
        okno = self.recent(source=source, project=project, limit=limit)
        return sorted(okno, key=lambda e: e.occurred_at, reverse=True)


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


def test_partial_progress_round_beats_despite_later_retry():
    """Runda dostarcza zdarzenie 1, a 2 pada poniżej progu → puls MIMO wczesnego wyjścia.

    Regresja review Fazy 0: wczesny ``return`` omijał puls nawet gdy runda RUSZYŁA kursor —
    healthcheck raportował ``unhealthy`` przy nadrabianiu zaległości mimo realnego postępu
    (sprzeczne z docstringiem i ADR 0067 §2.2 „delivered ≥1").
    """

    class _FailSecond(_FakeSender):
        async def post_channel(self, team_id, channel_id, text) -> str:
            if "Issue 2" in text:
                raise RuntimeError("Graph 503")
            return await super().post_channel(team_id, channel_id, text)

    beats: list[int] = []
    saved: list[int] = []
    notifier = _notifier(
        _FakeEvents([_event(1), _event(2)]),
        _FailSecond(),
        targets=_channel_targets(),
        saved=saved,
        dead_letters=_FakeDeadLetters(),
        heartbeat=lambda: beats.append(1),
        max_attempts=5,
    )
    assert asyncio.run(notifier.pump_once()) == 1  # tylko zdarzenie 1 ruszyło kursor
    assert saved == [1]  # kursor stoi na 1 — zdarzenie 2 ponowi się w następnej rundzie
    assert beats == [1]  # POSTĘP (dostarczono 1) → puls bije mimo zatoru na zdarzeniu 2


def test_dead_letter_recorded_before_cursor_moves():
    """Kolejność zapis-przed-ruchem: dead-letter TRWAŁY zanim kursor je minie (ADR 0022).

    Sonda KOLEJNOŚCI, nie stanu końcowego: zamiana ``record()`` ↔ ``save_cursor`` przeszłaby test
    stanu (``test_dead_letter_after_max_attempts_...``), a inwariant at-least-once padłby po cichu.
    """
    seq: list[str] = []

    class _SeqDeadLetters(_FakeDeadLetters):
        def record(self, *, source, event_id, reason, attempts):
            seq.append("dead_letter")
            super().record(source=source, event_id=event_id, reason=reason, attempts=attempts)

    notifier = EventNotifier(
        _FakeEvents([_event(1)]),
        _FakeSender(fail_channel=True),
        targets=_channel_targets(),
        save_cursor=lambda _cid: seq.append("cursor"),
        dead_letters=_SeqDeadLetters(),
        max_attempts=1,
    )
    asyncio.run(notifier.pump_once())
    assert seq == ["dead_letter", "cursor"]  # trwałość PRZED przesunięciem kursora


def test_cursor_stays_when_dead_letter_record_raises():
    """Gdy zapis do kwarantanny rzuci, kursor NIE rusza — zdarzenie ponowi się (at-least-once)."""
    saved: list[int] = []

    class _BrokenDeadLetters(_FakeDeadLetters):
        def record(self, *, source, event_id, reason, attempts):
            raise RuntimeError("dysk pełny")

    notifier = EventNotifier(
        _FakeEvents([_event(1)]),
        _FakeSender(fail_channel=True),
        targets=_channel_targets(),
        save_cursor=saved.append,
        dead_letters=_BrokenDeadLetters(),
        max_attempts=1,
    )
    with pytest.raises(RuntimeError, match="dysk pełny"):
        asyncio.run(notifier.pump_once())
    assert saved == []  # kursor nietknięty gdy kwarantanna zawiodła — brak utraty zdarzenia


# --- Pętla ``pump``: awaria rundy nie kładzie notifiera -------------------------
# ``pump_once`` ma gęste pokrycie; sama PĘTLA nie miała żadnego. To ona decyduje o tym,
# czy jeden wyjątek gasi most zdarzeń na resztę życia procesu — a to najdroższa awaria
# tego komponentu: cicha, bo nikt nie dostaje powiadomień o braku powiadomień.


def test_pump_survives_a_failing_round_and_keeps_polling():
    """Wyjątek W RUNDZIE ma być zalogowany i przełknięty — pętla rusza dalej po interwale."""
    rundy: list[str] = []

    class _NotifierZAwariami(EventNotifier):
        async def pump_once(self) -> int:
            rundy.append("runda")
            if len(rundy) == 1:
                raise RuntimeError("Graph zwrócił 503")
            if len(rundy) >= 3:
                raise asyncio.CancelledError  # sposób na wyjście z nieskończonej pętli
            return 0

    notifier = _NotifierZAwariami(
        _FakeEvents([]),
        _FakeSender(),
        targets=_channel_targets(),
        save_cursor=lambda _cid: None,
        poll_interval=0,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notifier.pump())

    # Trzy rundy mimo wyjątku w pierwszej: 503 nie zabił mostu.
    assert len(rundy) == 3


def test_pump_waits_the_configured_interval_between_rounds(monkeypatch):
    """Bez odczekania pętla zjadałaby procesor i zalewała Graph — interwał jest częścią umowy.

    Zegar podmieniamy (``monkeypatch`` przywraca go sam), zamiast czekać naprawdę: test na
    realnym ``sleep`` byłby wolny i zależny od obciążenia maszyny.
    """
    czekania: list[float] = []
    rundy: list[int] = []
    prawdziwy_sleep = asyncio.sleep

    async def _zapisz_sleep(delay, *args, **kwargs):
        czekania.append(delay)
        return await prawdziwy_sleep(0)

    monkeypatch.setattr(notifier_module.asyncio, "sleep", _zapisz_sleep)

    class _NotifierZLicznikiem(EventNotifier):
        async def pump_once(self) -> int:
            rundy.append(1)
            if len(rundy) >= 2:
                raise asyncio.CancelledError
            return 0

    notifier = _NotifierZLicznikiem(
        _FakeEvents([]),
        _FakeSender(),
        targets=_channel_targets(),
        save_cursor=lambda _cid: None,
        poll_interval=37,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(notifier.pump())

    assert czekania == [37]
