# 0052 — Change digest on demand from a Teams @mention ("co się zmieniło od <data>")

Date: 2026-07-29
Status: proposed
Author: P0w3r223
Related to: [ADR 0051](0051-project-brief-one-pager-from-teams-mention.md) (F4 one-pager — same seam, render skeleton, PDF channel),
[ADR 0048](0048-thread-note-capture-from-teams-mention.md) (mention seam + gated router pattern),
[ADR 0019](0019-shared-event-store.md) (shared EventStore),
[ADR 0028](0028-project-repo-jira-mapping-and-event-dimension.md) (per-event project attribution),
[ADR 0040](0040-eventstore-to-mcp-session-cursor-read.md) (cursor `read_events_since`),
[ADR 0026](0026-agent-file-reply-in-thread.md) (render + file reply in a thread),
[ADR 0006](0006-write-capability-gate-2.md) (write gate — why a read-only feature needs none)

---

## Context

F4 (ADR 0051) gives a per-project snapshot. The complementary standup question — "what changed across the
pion since <date>" — has no pull path today: the agent can call `read_events_since` (cursor-based), but a
human wants a dated, grouped digest in one shot, not a raw event window they must fold themselves.

Two structural facts make the digest cheap and safe, exactly as for F4:

1. **Events are already stored and attributed.** The shared `EventStore` (ADR 0019) holds GitHub/Jira/Teams
   events with `occurred_at`, `source`, `kind`, and `project` (ADR 0028). A digest is a deterministic fold of
   those rows since a date — no new synthesis, no LLM.
2. **The mention seam and render skeleton already exist.** ADR 0048 added `mentions_bot`; ADR 0051 added the
   responder slot pattern, the shared `text_format` render helpers, and the shared PDF delivery closure. F5 is
   a second router reusing all three.

One mismatch to resolve: `EventStore` exposes `recent(limit)` and cursor `read_since(after_id)`, but no
date-range query. Rather than grow the port, the service scans the most-recent `scan_limit` events and filters
`occurred_at.date() >= since` in memory — deterministic, testable on fakes, and honest about truncation (see
Decision §3). This mirrors F4's "compose existing reads" ethos.

Hard constraints (unchanged from F4): `core/` never imports adapters; the frozen MCP surface (golden test) and
`NoteMetadata` are untouched; the digest is a router, not a catalog tool; the `<data>` comes from a TRUSTED
mention argument, never from thread content; event content is DATA, never commands.

## Decision

1. **Trigger = @mention of the bot carrying a `co się zmieniło od <data>` directive**, mirroring ADR 0051. A
   new `ChangeDigestRouter` (`adapters/inbound/change_command.py`) handles mentions whose text carries the
   directive; `<data>` is parsed as ISO `YYYY-MM-DD` from the explicit argument. An optional `| pdf` flag
   requests file delivery. Non-mention / non-directive / unparseable-date messages fall back (`None` → normal
   turn) or return the usage hint.

2. **Composition is DETERMINISTIC — no LLM.** `ChangeDigestService.since(day)` folds events since `day` into a
   frozen `ChangeDigest` domain value: total, counts by source, and per-project sections (counts by kind +
   latest timestamp), sorted by volume. `ChangeDigest.to_text()` renders it, reusing the F4 `text_format`
   helpers. The render is a pure projection of stored facts (event content is DATA), so no hallucination
   surface.

3. **Read-only ⇒ NO write gate; date-window truncation is surfaced, not silent.** The digest reads only
   bridge events, so it needs no `save_note`, no new MCP tool, and no identity map. A single flag
   `enable_change_digest` (`WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST`, default OFF) gates the router; no
   `validate` precondition. Because the store has no date query, the service scans the `scan_limit`
   most-recently-ingested events (`recent` is id-ordered; the poller ingests near-real-time, so id ≈
   `occurred_at`). The truncation flag is deliberately order-independent: if the scan cap is hit AND any
   in-window events were found, `ChangeDigest.truncated` is set and rendered as a note, so a capped window
   never reads as complete — it does not rely on the scanned slice being sorted by `occurred_at`. A large
   backfill of old events would weaken the id ≈ `occurred_at` assumption, but the pipeline does not backfill.

4. **PDF reuses the shared file-reply delivery.** `| pdf` calls the same `_build_thread_pdf_delivery` closure
   as F4 (single `build_file_reply_catalog` pipeline: render → `_safe_doc_name` → upload → post), available
   only when `enable_file_reply` is also on; absent that or on any expected error, the digest degrades to the
   inline text answer. The shared closure is built once and injected into both the F4 and F5 routers (one
   sender/client), tightening the F4 wiring.

## Consequences

- **Positive.** One round-trip, deterministic, testable on in-memory fakes; zero LLM cost; reuses the ADR 0048
  seam, the ADR 0051 render skeleton + PDF closure, and the ADR 0019/0028 event store; no new MCP tool, no
  `NoteMetadata` change, no new scope. Read-only ⇒ activation is a single flag flip.
- **Negative / trade-offs.** The date window is bounded by `scan_limit` (no store-side date query); very wide
  windows on a busy bridge are truncated (flagged, not silent). The digest is a fixed projection, not prose;
  and like F4/F2 the directive parse is a loose `find` (degrades cleanly; date from a trusted argument, never
  thread content). A store-side date query is a future option if truncation bites.
- **Follow-on.** F6 (Monday DM digest) reuses `ChangeDigestService` under a scheduler + its own ADR (proactive
  push is a distinct capability from pull-on-mention).

## Alternatives considered

- **Grow `EventStore` with a date-range query.** Cleaner for very wide windows, but adds a port method + SQLite
  adapter change + tests for a summary view that a bounded recent-scan already serves. Deferred until
  truncation is shown to matter.
- **A new `change_digest` MCP tool.** Rejected for the same reason as F4: the frozen surface stays frozen; the
  feature is a door-level directive, not a catalog tool.
- **LLM-narrated digest.** Adds cost, latency, and a hallucination surface over structured event facts.
  Rejected as default; the deterministic fold is the honest projection.
