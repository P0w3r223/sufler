# 0048 — Gated note capture from a Teams-thread @mention ("zapisz to")

Date: 2026-07-29
Status: accepted
Author: P0w3r223
Related to: [ADR 0041](0041-production-m3-meeting-note-write-from-teams-door.md) (production `/notatka`),
[ADR 0042](0042-meeting-note-sender-authorization.md) (sender authorization),
[ADR 0043](0043-async-meeting-note-with-thread-callback.md) (idempotency + async),
[ADR 0009](0009-meeting-note-flow-and-write-surface.md) (M3, §3 caller-controls-location),
[ADR 0006](0006-write-capability-gate-2.md) (write gate, create-only),
[ADR 0017](0017-read-only-command-dispatcher.md) (read-only dispatcher),
[ADR 0008](0008-agent-runtime-and-tool-catalog.md) (single-source tool catalog)

---

## Context

The shared knowledge base only grows if capture is effortless. `/notatka` (ADR 0041–0043) captures a
*meeting* but needs a real meeting with a processed transcript. The far more common event — a decision or
commitment reached in an ad-hoc Teams *thread* — has no capture path today, so users must remember to write
a note by hand. That is exactly the friction that keeps the corpus empty and the synthesis features a demo.

Two structural facts of the current door:

1. **No @mention seam exists.** `teams_graph/selection.normalize` never reads Graph `mentions`; the only
   inbound gate is `_from_other_human` (other human, not self, not-yet-replied). The bot answers every human
   message. A capture that must trigger *only* on an explicit mention needs a new, additive parse step.
2. **Write from a less-trusted door is a deliberate gate** (ADR 0006). The Teams door runs the agent
   read-only (`enable_write=False`); the only DB write anywhere is create-only `save_note`. Any new write is
   a per-door gated capability with its own ADR and team consent.

Hard constraints (hexagonal + governance): `core/` never imports adapters; the read-only dispatcher
(ADR 0017) stays read-only; the frozen MCP surface (golden test) and `NoteMetadata` (Gate 1) are untouched;
authorization is a pure core decision over a port (ADR 0042); thread content is DATA, never commands.

## Decision

1. **Trigger = an explicit @mention of the bot carrying a `zapisz to` directive.** `selection.normalize`
   gains additive extraction of `mentions` (bot AAD id) into `ChannelMessage`; a new `ThreadNoteRouter`
   (`adapters/inbound/thread_note_command.py`) handles messages whose mention targets the bot AND whose text
   matches the capture directive. The read-only `CommandRouter` (ADR 0017) is untouched. Syntax:
   `@WorkMate zapisz to | <projekt>` — the project is the trusted arg.

2. **Consulted next to (not inside) the read-only router.** `ConversationalResponder` gains an additive
   `thread_note: ThreadNoteRouter | None = None` (default `None` → every existing caller unchanged), checked
   right after the read-only dispatch and the meeting-note router, **outside `_store_lock`** (the summary is
   slow; the write is create-only and safe under its own concurrency). Mirrors the `meeting_notes` slot.

3. **Trusted project, never thread content** (ADR 0009 §3). `project` comes from the mention directive the
   authorized human typed. The thread body (root + already-materialized replies) is the untrusted *source
   material* to summarize — it can never redirect the note into another project.

4. **Thread → structured note by cloning the meeting pipeline.** A `ThreadNoteService` (core/application)
   feeds the concatenated thread text to the existing `MeetingSummarizer` port → `MeetingSummary`
   (title/decisions/action_items/open_questions/tags/body). `participants` come from the thread's real
   senders (deterministic roster, anti-hallucination — like `parse_speaker_roster`), never the model. The
   summarizer contract and the two-pass grounded verifier (ADR 0047) are reused as-is.

5. **Source-message-id idempotency** (clone ADR 0043). New pure `thread_note_id(company, project, date,
   source_message_id)` in `core/domain/paths.py`: `<firma>/<projekt>/<data>-thr-<sha256(id)[:12]>`. Written
   via a `save_meeting_note`-style create-only path (`os.link` → `NoteExistsError` on re-`zapisz`). A crash
   or a double-mention on the same source message collides on the same id → "już zapisane", **not** a `-2`
   duplicate. `NoteMetadata` is **not** touched (Gate 1): provenance rides the id + a body line
   ("Źródło: wątek Teams …") + a `src:teams-thread` tag.

6. **Authorization reuses ADR 0042.** `MeetingNoteAuthorizer` (AAD `sender_id` → membership gate via the
   `AadIdentityLookup` port) authorizes **synchronously, fail-closed, before** any summary work. An
   unknown/empty sender → readable refusal, zero LLM cost. The pure `can_write_meeting_note(actor, project)`
   decision is reused (or a sibling `can_capture_thread_note`), so the per-project policy (ADR 0042 B2-B)
   drops in later unchanged.

7. **No MCP surface change.** The router + `ThreadNoteService` write live only on the Teams-door path via the
   door wiring — never through `build_tool_catalog`. Golden test `tests/adapters/test_mcp_tool_surface.py`
   is untouched; `save_note` stays absent from HTTP doors (ADR 0007).

8. **Gated OFF by default** — new `TeamsGraphSettings.enable_thread_note_capture`, which **requires**
   `WORKMATE_TEAMS_GRAPH_IDENTITIES` (authorization is intrinsic to the write gate, not a second toggle —
   ADR 0042) and the RW mount of `data/notes` (same as `/notatka`). It does **not** require
   `enable_meeting_transcript` — the source material is thread text, not a WebVTT transcript, so no
   transcript scopes are needed. The **inline** router is self-sufficient; the **async** mode reuses the
   `/notatka` scheduler/poster (`enable_meeting_note_async`), which by its own fail-fast chain pulls in the
   full meeting-note write path — so async thread capture presently implies enabling `/notatka` too. Inline
   is the standalone default; a dedicated async toggle is a later, additive option (§Follow-ups). Validated
   fail-fast: gate on without a target is a hard startup error, never a dead gate. **This ADR stays
   `proposed` until the team accepts enabling thread capture.**

## Alternatives considered

- **`/zapisz` slash-command** (clone the existing `MeetingNoteRouter` seam directly; cheapest, no mention
  parsing). Rejected per product decision: an @mention is the natural "act on this thread" UX. Kept as the
  documented fallback trigger if mention parsing proves brittle on real Graph payloads.
- **Agent tool `note_from_thread` in the tool-use loop** (the model calls it). Rejected as the default by the
  same reasoning as ADR 0009: the write location would then be chosen by the model over untrusted content,
  and the flow is harder to test deterministically. Still viable later on the same ports.
- **Reuse `MeetingNoteService` verbatim.** Rejected: the source shape differs (thread messages vs a WebVTT
  transcript). A thin `ThreadNoteService` over the same `MeetingSummarizer` port is cleaner than overloading
  the meeting use case with a second source type.

## Consequences

- **Buildable/verifiable now (gated OFF):** mention extraction, `ThreadNoteRouter`, `ThreadNoteService`,
  `thread_note_id`, authorization reuse, the responder seam, config gate + fail-fast validation. Unit-tested
  on fakes / `MockTransport`. Golden MCP surface unchanged; `NoteMetadata` unchanged.
- **Not verified live until the gate is flipped** (RW mount + identities + a real thread) — the same posture
  as every write feature in this repo (ships gated, operator enables and live-smokes).
- **Corpus capture path for ad-hoc decisions opened**, closing the cold-start "zapisz ustalenia z tego
  wątku" gap and feeding cross-project search, onboarding one-pagers, and "co się zmieniło" digests.

## Follow-ups

- On acceptance: flip to `accepted`; populate `WORKMATE_TEAMS_GRAPH_IDENTITIES`; live-smoke an allowed and a
  refused sender, plus a forced-retry idempotency check on the same source message.
- Optional, additive: a `/zapisz` command alias; per-project capture policy (ADR 0042 B2-B seam); async
  execution reusing the ADR 0043 scheduler/callback if summary latency stalls the door.

## Update (2026-07-30)

Status flipped to `accepted`; `enable_thread_note_capture` set `true` in `deploy/docker/env` (shares
the ADR 0042 identities file). Live-smoke of an allowed/refused sender and idempotency on a real
Teams thread is still pending — no real thread capture run yet, same "config-enabled, not
operationally proven" caveat as ADR 0041.
