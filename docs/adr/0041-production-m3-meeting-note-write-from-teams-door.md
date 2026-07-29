# 0041 — Production M3: gated meeting-note write from the Teams door

Date: 2026-07-28
Status: accepted
Author: P0w3r223
Related to: [ADR 0009](0009-meeting-note-flow-and-write-surface.md) (M3, §4 Gate-2 deferral),
[ADR 0006](0006-write-capability-gate-2.md) (write gate), [ADR 0017](0017-read-only-command-dispatcher.md)
(read-only command dispatcher), `roadmap-v1-gap-analysis.md` (B1),
[how-to](../how-to/meeting-transcript-live-smoke.md)

---

## Context

ADR 0009 built the M3 core (`MeetingNoteService`: transcript → summary → gated `save_note`) and
**deferred** two things to "when Azure lands": the real `GraphTranscriptSource`, and *"wire the
write-enabled flow into the Teams door"* — the latter explicitly named the **Gate-2 trust decision**
(§4). B1 delivered the real transcript adapter (`HttpxGraphTranscriptSource`) plus an operator
live-smoke path (`workmate-meeting --source graph`). What remains is the production trigger: how a
person in a Teams channel asks WorkMate to file a meeting note.

Two hard constraints from the existing design:

1. **Write from a less-trusted door is a deliberate gate** (ADR 0006). The Teams door runs the agent
   **read-only** (`enable_write=False`); the only DB write tool anywhere is `save_note`, create-only.
2. **The command dispatcher is read-only by charter** (ADR 0017). `CommandRouter` (`/pomoc`,
   `/szukaj`, …) must not grow a write command.
3. **The caller — not the transcript — chooses the write location** (ADR 0009 §3). Transcript content
   is untrusted; `project`/`date` must come from a trusted source.

## Decision

1. **Trigger = an explicit, separately-gated `/notatka` command**, handled by a **new
   `MeetingNoteRouter`** (`adapters/inbound/meeting_command.py`) — *not* added to the read-only
   `CommandRouter`. ADR 0017's dispatcher stays read-only; the write command is a distinct component
   that only exists when its gate is on. Syntax:
   `​/notatka <meeting-ref> | <projekt> | <RRRR-MM-DD>`.

2. **The router is consulted by `ConversationalResponder` next to (not inside) the read-only router.**
   An additive optional `meeting_notes: MeetingNoteRouter | None` (default `None` → every existing
   door/caller unchanged) is checked right after the read-only command dispatch and before the agent
   loop. It runs **outside** `_store_lock` (transcript fetch + Claude summary are slow; `save_note` is
   create-only and safe under its own concurrency), whereas read-only commands stay under the lock.

3. **Trusted args, never transcript content** (ADR 0009 §3). `meeting-ref`, `project`, and `date` come
   from the command the human typed. The transcript can never redirect the note into another project.

4. **Reuse the gated, append-only write** (ADR 0006). The note is written by a write-enabled
   `NotesWriteService.save_note` to the real `data/notes/`: create-only, collision → `-2`/`-3`, titles
   narrowed. No new mutating tool, no MCP surface change (golden test untouched) — the MCP door still
   has no `save_note`; this write lives only on the Teams door path, exactly the per-door profile of
   ADR 0006.

5. **Two independent gates, both OFF by default.**
   - `enable_meeting_transcript` (B1) — read scopes `OnlineMeetingTranscript.Read.All` +
     `OnlineMeetings.Read` (admin consent), validated fail-fast.
   - `enable_meeting_note_write` (this ADR) — the Gate-2 write decision; requires
     `enable_meeting_transcript` (no transcript source otherwise), validated fail-fast.

   Nothing is enabled until the operator flips both env flags. **This ADR stays `proposed` until the
   team/operator accepts enabling the write path.**

## Alternatives considered

- **Add `/notatka` to the read-only `CommandRouter`.** Rejected: violates ADR 0017's read-only
  charter and blurs the audit boundary between read and write commands.
- **Expose `note_from_meeting` as an agent tool** (the model calls it in the tool-use loop). Rejected
  as the default by ADR 0009: the write location would then be chosen by the model over untrusted
  content, and the flow is harder to test deterministically. Still viable later on the same ports.
- **A dedicated separate door/process** for meeting notes. Rejected as premature: the Teams door
  already owns command dispatch, token, and the conversation seam; a new door is more moving parts for
  the same capability. Revisit if M4 async callbacks need it.

## Consequences

- **Buildable/verifiable now (gated OFF):** `MeetingNoteRouter` (parse, trusted args, graceful error
  text), the additive responder seam, config gate + fail-fast validation, Teams-door wiring. Unit-
  tested on fakes/`MockTransport`. Golden MCP surface unchanged.
- **Not verified live (parked 2026-07-28):** transcript scopes are now admin-consented and device-code
  succeeded, but the end-to-end run is blocked on the lack of a real meeting joinWebUrl/id with a
  processed transcript (a placeholder ref returned Graph HTTP 400 — not proof of a scope problem). Same
  posture as every other write feature in this repo (ships gated, operator enables and live-smokes).
- **Trust boundary made explicit:** enabling `enable_meeting_note_write` is the Gate-2 decision from
  ADR 0009 §4, now a single reversible env flag with an ADR of record.

## Follow-ups

- On acceptance: flip status to `accepted`, run the live-smoke (`docs/how-to/meeting-transcript-live-smoke.md`),
  confirm channel-member authorization expectations.
- M4 (B2/B3): identity mapping (Entra/AD → core permission model) and async "fire-and-forget" with a
  callback into the Teams thread — separate ADR(s), need `@architect`.
