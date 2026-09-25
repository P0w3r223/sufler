# 0009 — Meeting-note flow (M3) and the write surface

Date: 2026-07-09
Status: accepted
Author: P0w3r223
Related to: [ADR 0006](0006-write-capability-gate-2.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md), roadmap §6 (Phase 2, M3–M4)

---

## Context

Phase 2 milestone M3 is the roadmap's "original dream": `@Sufler new note` →
fetch the meeting transcript → summarize into the frozen note schema → save it to
the right project folder. M4 adds the hard asynchronous topics: background jobs with
callbacks, identity mapping (Entra/AD → the core permission model), and the gated
write path from a less-trusted door.

Two constraints shape this decision:

1. **No Azure/M365 access right now.** Fetching a Teams meeting transcript requires
   Microsoft Graph (Entra app registration, `OnlineMeetingTranscript.Read.All`, a
   tenant identity). Identity mapping and delivering an async callback into a Teams
   thread likewise require live Microsoft infrastructure. None of it can be built or
   verified today. The roadmap already scopes M3–M4 as continuation work.
2. **Team approval to proceed with the practitioner's decisions.** This unblocks the
   Gate-2 write decision (ADR 0006) enough to design and build the write flow — but
   the frozen note schema (Gate 1) and the deliberately narrow write surface still
   stand.

## Decision

1. **M3 core is a use case, not a new tool.** `MeetingNoteService.note_from_meeting`
   (`core/application/meeting_notes.py`) orchestrates two narrow ports —
   `TranscriptSource.fetch` and `MeetingSummarizer.summarize` (`core/ports/meeting.py`)
   — then writes through the **existing** gated `NotesWriteService.save_note`. The MCP
   tool surface stays frozen at 4+1 (golden test unchanged); M3 adds no tool.

2. **The frozen note schema is untouched.** The summarizer returns a `MeetingSummary`
   (a *new* intermediate DTO — transcript-derived fields only), from which the service
   assembles a `NoteMetadata` in the frozen schema (Gate 1). `NoteMetadata` fields are
   not changed.

3. **The caller controls the write location, not the transcript.** `project` and
   `date` are passed by the flow (door/registry), never taken from the summarized
   content. Meeting transcripts are untrusted data; a transcript must not be able to
   redirect a note into another project via injected fields.

4. **Write stays gated and append-only.** M3 reuses the single existing write tool
   (`save_note`): it never overwrites (collision → `-2`, `-3`, …) and narrows titles
   to `[a-z0-9-]`, per ADR 0006. Enabling this write path from the (less-trusted)
   Teams door is the Gate-2 trust decision; it is exposed by building the flow with a
   write-enabled `NotesWriteService`, gated per ADR 0006 — not by loosening the tool.

5. **No destructive CRUD.** Edit/delete/overwrite tools are explicitly **out of
   scope**. They are not in any roadmap phase, and destructive operations on a shared
   knowledge base — especially through a less-trusted async door — invert the per-door
   trust model and risk irreversible data loss. If ever needed, they require their own
   ADR with per-door gating; team approval to "continue" does not extend to them.

6. **Azure-dependent parts are deferred, as honest stubs.**
   - `GraphTranscriptSource` (`adapters/outbound/transcript_sources.py`) raises a clear
     `NotImplementedError` naming what Azure/Graph access it needs. `InMemoryTranscriptSource`
     lets the whole flow run locally without Azure.
   - M4 identity mapping (Entra/AD) and async background-job + Teams callback are **not**
     built — they cannot be designed correctly or verified without the real infrastructure.

## Alternatives considered

- **Agent-driven M3** — give the agent runtime (ADR 0008) `save_note` plus the
  transcript in the prompt and let the tool-use loop summarize and save. Rejected as
  the default because the write location would then be decided by the model over
  untrusted content, and the flow is harder to test deterministically. It remains a
  viable later option on the same ports.
- **Deterministic (non-LLM) summarizer** — rejected: the roadmap's value is a genuine
  summary; the LLM summarizer sits behind a port and the core is tested with a fake.

## Consequences

- **Done and verifiable today:** the M3 core flow (ports + `MeetingNoteService`),
  tested end-to-end on in-memory fakes (`tests/core/test_meeting_notes.py`); the
  `MeetingSummary` DTO; the Graph stub + in-memory transcript source; an Anthropic
  summarizer adapter following the `anthropic_llm.py` pattern (thinking disabled).
- **Not verified today (Azure-gated):** real Graph transcript fetch; the LLM
  summarizer's actual output quality/JSON validity (verifies against Claude, exactly
  as `AnthropicLLMClient` does); M4 identity + async callback.
- **Not "Phase 2 complete."** Phase 2 is code-complete for everything that does not
  require live Microsoft infrastructure; M3-Graph and all of M4 remain continuation
  work, consistent with roadmap §6/§8.

## Follow-ups

- A small local harness (`InMemoryTranscriptSource` + `AnthropicMeetingSummarizer` +
  `MeetingNoteService`) to run M3 end-to-end against Claude with a pasted transcript.
- When Azure lands: implement `GraphTranscriptSource`, wire the write-enabled flow into
  the Teams door, and take on M4 (identity mapping, async jobs/callbacks).
