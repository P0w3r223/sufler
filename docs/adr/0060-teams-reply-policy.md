# 0060. Optional "whether to reply at all" gate for Teams channels (reply_policy)

Date: 2026-08-04
Status: accepted
Author: P0w3r223
Related to: docs/adr/0015-teams-delegated-graph-polling.md,
  docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md,
  docs/adr/0048-thread-note-capture-from-teams-mention.md, docs/adr/0059-teams-shifts-schedule-read.md

---

## Context

The delegated Teams door (ADR 0015) has always answered every message from another human posted
in a `watch`-listed channel — that is the entire selection contract in `selection.plan_channel`.
This is fine for a small, single-purpose channel where WorkMate is the only thing anyone talks to.
It stops being fine the moment a channel is shared with normal human conversation: the bot would
answer every message in the channel, not just the ones addressed to it.

A skeleton for a future multi-channel rollout of WorkMate needs an opt-in gate: "should this
channel's bot even engage with this message, or is it none of its business?" Two states matter in
practice — always engage (today's behavior) and mention-gated (engage only when addressed).
Nothing about this decision should change behavior for any channel that does not explicitly opt
in, because the existing single-channel deployment must not observe any difference on upgrade.

## Decision

`adapters/inbound/teams_graph/selection.py` gains `ReplyPolicy` (frozen dataclass: `mode` —
`"all"` (default) or `"mention"`, `always_reply` — a `frozenset[tuple[str, str]]` of
`(team_id, channel_id)` pairs) and `_thread_engaged()`:

- `mode="all"` (default) or the channel being in `always_reply`: `should_engage` always returns
  `True` — bit-for-bit today's behavior.
- `mode="mention"`: `should_engage` returns `True` only when the message `mentions_bot` (already
  computed by `normalize()`, ADR 0048) OR the thread is already "stuck" — the bot has posted in it
  before (`_thread_engaged`, computed from the raw reply history Graph already returns each round,
  not separately persisted state — so it survives process restarts as long as the thread is still
  inside the `top_replies` window the poller fetches). Once engaged, a thread stays conversational
  without requiring a fresh @mention on every turn — a channel gated to "mention" should not force
  the human to re-mention the bot mid-conversation.
- `plan_channel()` and its internal `_append_actionable()` gain `policy: ReplyPolicy | None = None`
  and `channel: tuple[str, str] = ("", "")` parameters. **`policy=None` (the default) skips the
  gate entirely** — this is the load-bearing compatibility guarantee: any caller that does not
  pass `policy` gets exactly the old, ungated selection logic. Filtered-out messages still advance
  the per-thread watermark (computed from raw Graph data before the gate runs), so a message the
  policy rejects this round does not resurface and get evaluated again next round.
- `ChannelPoller.__init__` gains `policy: ReplyPolicy | None = None`, stored and passed through to
  `plan_channel(..., policy=self._policy, channel=(team_id, channel_id))` in `_poll_channel`. Same
  default-`None` compatibility guarantee at the poller layer.
- `config.TeamsGraphSettings` gains `reply_policy: str = "all"` (env
  `WORKMATE_TEAMS_GRAPH_REPLY_POLICY`) and `always_reply: tuple[tuple[str, str], ...] = ()` (env
  `WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY`, same `team:channel,team:channel` format as `watch`, parsed
  by the existing `_parse_watch_pairs`). `validate()` rejects any `reply_policy` other than
  `all`/`mention`, and rejects any `always_reply` pair that is not also in `watch` — a stray pair
  is almost always a typo, and this repo's convention (ADR 0006 and others) is to fail loudly at
  startup on a configuration that would otherwise silently do nothing.
- `teams_graph/app.py:_run` imports `ReplyPolicy` and constructs `ChannelPoller(...,
  policy=ReplyPolicy.from_settings(settings))` — the only production wiring point.

## Consequences

- **Default deploy is a no-op.** `reply_policy` defaults to `"all"`, `always_reply` defaults to
  empty, and `ReplyPolicy(mode="all")` behaves identically to `policy=None` — a channel that never
  sets `WORKMATE_TEAMS_GRAPH_REPLY_POLICY` sees zero behavior change from this ADR. This mirrors
  the compatibility posture ADR 0006/0026/0027 take with new gated capabilities: ship the gate
  closed (or here, ship the gate's *effect* identical to absent).
- Opting a channel into `mode="mention"` while listing it in `always_reply` is intentionally a
  no-op escape hatch (channel behaves like `all` regardless of `mode`) — useful for a fleet-wide
  `WORKMATE_TEAMS_GRAPH_REPLY_POLICY=mention` default with per-channel exceptions, without needing
  a third policy value.
- **This ADR and ADR 0059 (extended Jira read + Teams Shifts schedule) do not yet coexist in any
  single built Docker image running in production, as of this writing.** They were developed and
  shipped independently on two different, concurrently-running containers: the
  `workmate-teams-graph` container currently in production has `reply_policy` (this ADR) but does
  NOT have the extended Jira read/Shifts schedule tools (ADR 0059); other containers in the fleet
  have ADR 0059's capabilities but not `reply_policy`. This repository is the **first place** both
  features exist in the same source tree — merging them here (both touching
  `adapters/inbound/teams_graph/app.py` and `config.py`) is a deliberate reconciliation for a
  future release that ships both together, not a reflection of what any single running container
  does today. Anyone auditing "what does production actually run" should check both containers'
  image tags/digests independently rather than assuming this tree's `app.py` matches either one
  in isolation.
- No new Graph scope, no new gate on the write side (this ADR touches nothing mutating), and no
  change to `roots_to_poll`/watermark bookkeeping beyond what `_collect_new` already did — the
  gate is purely a filter on which already-collected messages become `messages` returned to the
  caller.
