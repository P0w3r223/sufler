# 0019. Shared append-only EventStore (the cross-door spine)

Date: 2026-07-15
Status: accepted
Author: P0w3r223
Related to: docs/adr/0010-conversation-threading-and-context-limit.md, docs/adr/0020-github-delegated-polling-door.md,
  docs/adr/0022-proactive-dual-target-teams-push.md

---

## Context

Phase 3 extends the "one core, many doors" star with a requirement the roadmap did not
have: the layers (GitHub, Teams, local base) must "see each other" and interact. A GitHub
event should be able to reach Teams; a Teams action should be visible to the GitHub side;
and any door's agent should be able to read what happened elsewhere. The knowledge base
(notes) is deliberately *not* a mirror of GitHub, so it cannot be that shared channel. We
need a small shared substrate that every door can write to and read from.

## Options considered

1. **A dedicated append-only EventStore (new SQLite file `~/.sufler/events.db`).**
   A narrow domain (`source, kind, external_id, actor, title, summary, url, occurred_at`)
   behind an `EventStore` port, mirroring the proven `SqliteConversationStore` concurrency
   pattern (WAL + `busy_timeout` + `Lock`), append-only with `UNIQUE(source, external_id,
   kind)` for cross-process at-least-once. A single read tool (`read_recent_events`) exposes
   it to every door's agent.
2. **Reuse `conversations.db` (add an `events` table there).** One fewer file, but that
   store rebuilds its `messages` table under `foreign_keys=OFF` during FK migration
   (ADR 0012); a foreign table complicates that migration and blocks writes while it runs.
   Different lifecycle, retention and schema evolution than chat history.
3. **No store; push GitHub events straight to Teams in-process.** Simplest, but the layers
   would not "see each other" (no shared memory, no cross-door read), no at-least-once
   replay across restarts, and Teams→GitHub echo would have nowhere to land.

## Decision

**Option 1.** Add a shared, append-only `EventStore`:

- **Separate SQLite file**, outside `data/` and outside repo (operational data, not the
  knowledge base — a poisoned event must never reach the indexed notes). Separate from
  `conversations.db` for the migration/lifecycle reasons above.
- **Ports/services in core** (`ports/events.py`, `application/events.py`), adapter in
  `adapters/outbound/sqlite_events.py` — preserves `core ↛ adapters`. Timestamps/ids come
  from the store, never the core.
- **Append-only + dedup at the DB** (`UNIQUE(source, external_id, kind)` + `INSERT … ON
  CONFLICT DO NOTHING`): concurrent doors cannot create duplicates, so pollers get safe
  at-least-once and cursors (`read_since(after_id)`) never lose or double an entry.
- **Untrusted content = data.** `EventService.ingest` runs `reject_dangerous_content`
  before writing; readers treat event text as data, never commands.
- **One read tool for all doors** (`read_recent_events`, injected via `extra_catalog`,
  not through `build_tool_catalog`) — the MCP surface golden test stays frozen.

## Consequences

- The EventStore is the load-bearing "spine": GitHub writes events, the notifier reads them
  (ADR 0022), Teams-originated writes echo into it (ADR 0021), and every agent can read it.
- A new operational data file joins `conversations.db`/workspace outside `data/`; its
  retention is independent and can be pruned later without touching chat history.
- Sharing one file across processes relies on the same WAL/`busy_timeout` guarantees already
  trusted for conversations.
