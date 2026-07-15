# 0022. Proactive dual-target Teams push (1:1 + channel)

Date: 2026-07-15
Status: accepted
Author: P0w3r223
Related to: docs/adr/0015-teams-delegated-graph-polling.md, docs/adr/0019-shared-event-store.md,
  docs/adr/0020-github-delegated-polling-door.md

---

## Context

To close the GitHub → Teams direction, events collected in the EventStore (ADR 0019) must be
pushed proactively into Teams. The existing `teams_graph` door is purely reactive: it can only
`post_reply` into an existing channel thread — it cannot start a new post and cannot message a
person 1:1. A proactive push (a "ping") is a new outbound capability. The user chose to deliver
to **both** targets, configurably: a 1:1 chat with a person **and** a new post on a team channel.

## Options considered

1. **A dual-target `TeamsNotifier` outbound adapter** (chosen). One adapter that can (a) find/
   create a 1:1 chat and send a message (the proven `create_or_get_chat` + `send_chat_message`
   pattern from the `Powiadomienia_teams` sub-project) and (b) post a new root message to a
   channel. Both targets are independent flags; a core `EventNotifier` reads the store by cursor
   and delivers to whichever targets are enabled.
2. **1:1 only** or **channel only.** Simpler, but the user explicitly wants both configurable —
   urgent pings 1:1, status on a channel.
3. **Extend the existing `teams_graph` client with proactive sends.** Couples the reactive
   poller's client with a different concern and different Graph scopes; a separate outbound
   adapter keeps the poller untouched.

## Decision

**Option 1.** A proactive, dual-target notifier:

- **Port + core service.** `TeamsNotifier` port (async `send_chat` / `post_channel`) and a
  core `EventNotifier` that reads `read_since(cursor, source="github")` and delivers to enabled
  targets. Core depends only on ports (`core ↛ adapters`); Markdown→HTML happens in the adapter.
- **Delegated identity, silent refresh.** Same MSAL device-code login as `teams_graph`, reusing
  `build_token_provider` (generalized to a structural settings Protocol) and optionally the same
  token cache. Scopes add `Chat.Create`/`Chat.ReadWrite`/`ChatMessage.Send` and
  `ChannelMessage.Send`. The token cache is the secret, kept outside repo/`data/`.
- **Both targets configurable.** `TeamsPushSettings` enables chat and/or channel independently;
  if neither is enabled the notifier does not start (the GitHub door runs ingest-only).
- **At-least-once cursor.** The cursor advances only after a successful send, so a transient
  transport error re-delivers (at the cost of a possible duplicate) rather than losing an event.
- **Loop guard + injection safety.** The notifier pushes only `source="github"` events, so
  Teams-originated echoes (ADR 0021) are never re-sent; posting under the bot's own identity is
  skipped by the `teams_graph` poller's `me_id` self-skip. Event text (from GitHub) is rendered
  with `to_teams_html` (`html=False`), so injected HTML is escaped, not executed.

## Consequences

- The GitHub door process (`workmate-github`) runs the poller and the notifier concurrently
  (`asyncio.gather`) over the shared EventStore, closing the GitHub → base → Teams loop.
- Messages are attributed to the logged-in user (the bot's "voice"), as in ADR 0015.
- Unattended runs depend on a warm MSAL token cache; a silent-only provider should be used in
  headless mode so an expired cache logs/alerts instead of blocking on device-code.
