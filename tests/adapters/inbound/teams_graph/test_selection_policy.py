"""Testy bramki ReplyPolicy (`selection.py`) — czysta logika, bez sieci/Graph.

Pokrywają: tryb `all` (regresja — zachowanie sprzed bramki), tryb `mention` (wzmianka,
przyklejenie wątku, nowy wątek), `always_reply` oraz `policy=None` (stara ścieżka).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from workmate.adapters.inbound.teams_graph.selection import ReplyPolicy, plan_channel
from workmate.config import TeamsGraphSettings

ME_ID = "bot-aad-id"
OTHER_ID = "user-aad-id"
CHANNEL = ("team-1", "channel-1")
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
ACTIVE_IDLE = timedelta(hours=24)


def _raw(
    msg_id: str,
    *,
    root_id: str | None = None,
    sender_id: str | None = OTHER_ID,
    text: str = "hej",
    created: str = "2026-08-04T10:00:00Z",
    mention_ids: tuple[str, ...] = (),
) -> dict:
    return {
        "id": msg_id,
        "replyToId": root_id,
        "messageType": "message",
        "createdDateTime": created,
        "from": {"user": {"id": sender_id, "displayName": "Ktoś"}} if sender_id else {},
        "body": {"content": text},
        "mentions": [{"mentioned": {"user": {"id": uid}}} for uid in mention_ids],
    }


def _plan(roots, replies_by_root, *, policy=None, channel=CHANNEL, state=None):
    return plan_channel(
        roots,
        replies_by_root,
        state or {"since_roots": "", "threads": {}},
        me_id=ME_ID,
        replied=set(),
        now=NOW,
        active_idle=ACTIVE_IDLE,
        policy=policy,
        channel=channel,
    )


def test_policy_none_behaves_like_before_the_gate():
    root = _raw("r1", text="siema bez wzmianki")
    messages, _ = _plan([root], {"r1": []}, policy=None)
    assert [m.id for m in messages] == ["r1"]


def test_mode_all_ignores_mentions_regression():
    root = _raw("r1", text="siema bez wzmianki")
    messages, _ = _plan([root], {"r1": []}, policy=ReplyPolicy(mode="all"))
    assert [m.id for m in messages] == ["r1"]


def test_mode_mention_rejects_message_without_mention():
    root = _raw("r1", text="siema bez wzmianki")
    messages, _ = _plan([root], {"r1": []}, policy=ReplyPolicy(mode="mention"))
    assert messages == []


def test_mode_mention_accepts_message_with_mention():
    root = _raw("r1", text="@workmate pomóż", mention_ids=(ME_ID,))
    messages, _ = _plan([root], {"r1": []}, policy=ReplyPolicy(mode="mention"))
    assert [m.id for m in messages] == ["r1"]


def test_mode_mention_sticks_to_thread_after_bot_already_replied():
    bot_reply = _raw(
        "r1-bot", root_id="r1", sender_id=ME_ID, text="jasne", created="2026-08-04T09:05:00Z"
    )
    follow_up = _raw("r1-f2", root_id="r1", text="a jeszcze to", created="2026-08-04T09:10:00Z")
    state = {
        "since_roots": "2026-08-04T08:00:00Z",
        "threads": {
            "r1": {"watermark": "2026-08-04T09:05:00Z", "last_seen": "2026-08-04T09:05:00Z"}
        },
    }
    messages, _ = _plan(
        [], {"r1": [bot_reply, follow_up]}, policy=ReplyPolicy(mode="mention"), state=state
    )
    assert [m.id for m in messages] == ["r1-f2"]


def test_mode_mention_new_thread_without_mention_is_rejected():
    root = _raw("r2", text="nowy wątek bez wzmianki", created="2026-08-04T09:00:00Z")
    messages, _ = _plan([root], {"r2": []}, policy=ReplyPolicy(mode="mention"))
    assert messages == []


def test_always_reply_bypasses_mention_mode_for_listed_channel():
    root = _raw("r1", text="siema bez wzmianki")
    policy = ReplyPolicy(mode="mention", always_reply=frozenset({CHANNEL}))
    messages, _ = _plan([root], {"r1": []}, policy=policy, channel=CHANNEL)
    assert [m.id for m in messages] == ["r1"]


def test_always_reply_is_scoped_to_its_own_channel():
    root = _raw("r1", text="siema bez wzmianki")
    other_channel = ("team-1", "channel-2")
    policy = ReplyPolicy(mode="mention", always_reply=frozenset({CHANNEL}))
    messages, _ = _plan([root], {"r1": []}, policy=policy, channel=other_channel)
    assert messages == []


def test_from_settings_reads_reply_policy_and_always_reply():
    settings = TeamsGraphSettings(
        client_id="x",
        tenant_id="y",
        watch=(CHANNEL,),
        reply_policy="mention",
        always_reply=(CHANNEL,),
    )
    policy = ReplyPolicy.from_settings(settings)
    assert policy.mode == "mention"
    assert policy.always_reply == frozenset({CHANNEL})
