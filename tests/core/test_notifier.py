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
            e
            for e in self._events
            if e.id > after_id and (source is None or e.source == source)
        ][:limit]


class _FakeSender:
    """Atrapa ``TeamsNotifier`` — notuje wywołania; opcjonalnie rzuca na ``post_channel``."""

    def __init__(self, *, fail_channel: bool = False) -> None:
        self.chats: list[tuple[str, str]] = []
        self.channels: list[tuple[str, str, str]] = []
        self._fail_channel = fail_channel

    async def send_chat(self, target_user_id, text):
        self.chats.append((target_user_id, text))

    async def post_channel(self, team_id, channel_id, text):
        if self._fail_channel:
            raise RuntimeError("Graph 503")
        self.channels.append((team_id, channel_id, text))


def _notifier(events, sender, *, targets, cursor=0, saved=None):
    return EventNotifier(
        events,
        sender,
        targets=targets,
        save_cursor=(saved.append if saved is not None else lambda _cid: None),
        cursor=cursor,
    )


def test_pumps_to_both_targets_when_enabled():
    sender = _FakeSender()
    targets = NotifyTargets(
        chat_user_id="u1", team_id="t1", channel_id="c1",
        enable_chat=True, enable_channel=True,
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
    sender = _FakeSender(fail_channel=True)
    saved: list[int] = []
    targets = NotifyTargets(
        chat_user_id="u1", team_id="t1", channel_id="c1",
        enable_chat=True, enable_channel=True,
    )
    with pytest.raises(RuntimeError):
        asyncio.run(
            _notifier(_FakeEvents([_event(1)]), sender, targets=targets, saved=saved).pump_once()
        )
    assert saved == []  # kursor NIE przesunięty → następna runda ponowi (at-least-once)


def test_default_render_includes_key_fields():
    text = default_event_render(_event(7, title="Awaria API", actor="alice"))
    assert "**[GitHub] Nowe issue**" in text
    assert "Awaria API" in text
    assert "alice" in text
    assert "http://gh/7" in text
