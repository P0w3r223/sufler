# 0047 — Two-pass grounded meeting note: speaker anchoring + verifier critic

Date: 2026-07-29
Status: accepted
Author: P0w3r223
Related to: [ADR 0009](0009-meeting-note-flow-and-write-surface.md) (M3 summarizer),
[ADR 0041](0041-production-m3-meeting-note-write-from-teams-door.md) (production `/notatka`),
[ADR 0042](0042-meeting-note-sender-authorization.md), [ADR 0043](0043-async-meeting-note-with-thread-callback.md)

---

## Context

The M3 summarizer (`AnthropicMeetingSummarizer`) ran a **single Claude pass** with a minimal system
prompt ("summarize, return JSON with these fields, absent field = empty"). Run against a real Teams
recruitment transcript (~57k chars, a raw export where the whole hour is glued into one paragraph and
**only the organizer "Mikołaj Anonimowicz" carries a speaker label**), the note exhibited two independent
failure modes:

1. **Over-generation / identity hallucination.** The model emitted a participant
   `"Dyrektor (…prawdopodobnie ta sama osoba co Mikołaj…)"` — speculative prose dropped into a structured
   field, actively misleading. It also produced a synthesized bio ("na magisterce, informatyka
   techniczna") — individual tokens exist in the transcript, but the *claim* was assembled, not stated.
   The real participant names (candidate surname, director "Zastepski") are **physically absent** from the
   transcript — they live only in human knowledge. The model was, in effect, asked to know the unknowable.

2. **Under-extraction.** The note dropped hard facts that *were* present (a 490-page tender, a
   163–170 mln PLN value, a wastewater-treatment OPZ, ERD, rector's scholarship), while over-committing
   on identity. Both stem from one root: no fidelity rules, single-shot generation, no speaker anchoring,
   no coverage verification, and a schema tolerant of hedge-prose in fields.

Two owner decisions frame the fix: **(a)** participant identity comes **only from the transcript, no
external roster, zero guessing**; **(b)** a **two-pass generate→verify** design is acceptable (~2× cost).

Hard invariants hold: `core/` never imports adapters; transcript is DATA not commands; `NoteMetadata`
is the frozen Gate-1 contract; the golden MCP surface is frozen (M3 adds no tool); the write stays
gated + create-only; deterministic `note_id` remains the idempotency key (ADR 0043).

## Decision

**Variant B — deterministic speaker parser in the core + optional verifier port + rewritten pass-1
prompt, with participants set deterministically (never by the LLM).**

1. **Speaker anchoring (`core/domain/transcript.py`).** A pure, I/O-free `parse_speaker_roster()` extracts
   speaker labels format-aware: the `Name: text` form produced by `vtt_to_text` (the production Graph/VTT
   path) and the `Name   M:SS` turn markers of raw Teams exports (with a `(?!\d)` guard so a timestamp
   glued to the next word — as in the real export — is still recovered, without matching longer numbers).
   Single-word colon artifacts are filtered (kept only if multi-word or repeated ≥2×). When diarization is
   **degenerate** (≤1 distinct speaker, or turns too sparse for the text volume), the roster honestly emits
   the known labels **plus one generic** ("Pozostali uczestnicy nierozpoznani…") — it never invents a count
   or a role. This `SpeakerRoster` is the single source of participant names.

2. **Participants are deterministic, not model output.** `MeetingNoteService` sets
   `NoteMetadata.participants = roster.participants()` and **ignores** whatever the model returns for that
   field. Identity hallucination is eliminated at the source, not merely discouraged by the prompt.

3. **Rewritten pass-1 prompt (draft).** Explicit fidelity rules (every claim must be grounded; leave
   `participants` empty — the system fills it; refer to people only by allowlisted speaker labels or a
   role that follows directly from the content; never combine separate mentions into a new "bio"; leave
   empty rather than guess) plus an extraction checklist that restores richness (exact numbers/amounts,
   named documents/tools, decisions vs open-questions split, short quotes). The roster's allowlist is
   injected into the **trusted system prompt**; the transcript stays the sole untrusted user turn.

4. **Verifier critic (pass 2), optional and gated.** A new `MeetingNoteVerifier` port; the same Claude
   adapter implements both protocols. It confronts the draft with the transcript and **removes/corrects
   unsupported claims** (verifying a claim *as a whole*, not token presence), never enriches, and may
   demote a genuinely-unsettled item to `open_questions` with a "do potwierdzenia:" prefix.
   `MeetingNoteService(verifier=None)` keeps the ADR 0041 single-pass behavior — additive and reversible,
   exactly like the existing `authorizer=None`/`scheduler=None` seams.

5. **Gating.** The verifier is controlled by `SUFLER_AGENT_VERIFY_MEETING_NOTE` (`AgentSettings`,
   default OFF, ~2× cost when ON). This is a **quality toggle, not a write-capability gate** — flipping it
   is an operator decision, not a team-consensus write flip. The `sufler-meeting` harness enables it by
   default (`--verify`, `--no-verify` to disable), since the harness exists to verify note quality.

**No `NoteMetadata` change.** Uncertainty is represented in existing fields: unrecognized speakers as a
generic string in `participants`, unsupported claims dropped or demoted to `open_questions`, caveats in
`body`. Gate 1 and the golden MCP surface are untouched. Any structural provenance would extend the
non-frozen `MeetingSummary` DTO, never `NoteMetadata`.

## Alternatives considered

- **Trusted attendee roster from the caller.** The only honest way to get the real names that are absent
  from the transcript. Rejected by owner decision (a) for now — kept as the documented upgrade path if the
  generic "unrecognized" participant proves too weak.
- **Single pass, harder prompt only.** Cheaper, but leaves plausible-synthesis unchecked; the owner
  accepted the ~2× cost for an independent critic.
- **Two-pass inside the adapter, port unchanged.** Smallest blast radius, but buries the highest-value,
  highest-risk logic (anchoring + verification) in the one layer the repo deliberately leaves without unit
  tests. Rejected for testability.

## Consequences

- **Built and verified now.** Full gate green (423 passed, ruff/mypy clean). A live run on the real
  export with verify ON produced `participants: [Mikołaj Anonimowicz, Pozostali uczestnicy nierozpoznani…]`
  — no invented names, people referenced by role in prose — while restoring grounded facts (490 pages,
  163–170 mln, OPZ, ERD, scholarship, 2-July exam). Spot-checked claims all trace to the transcript.
- **Notes may be sparser on identity by design.** With no roster and degenerate diarization, the system
  will not name or count unnamed participants. This is correct under zero-guessing, and weaker than a human
  note that knows names externally.
- **Cost/latency.** ~2× Claude per note when verify is ON; the idempotency pre-check (ADR 0043) means it is
  paid at most once per meeting. Inline `/notatka` (0041) deepens the poller stall; async (0043) absorbs it.
- **Golden MCP surface and Gate 1 unchanged.**

## Follow-ups

- **Prompt allowlist nuance (safe):** the "no name outside the allowlist" rule is aimed at participant
  identity; a name literally present in the transcript (e.g. a third party mentioned in passing) is
  grounded and correctly retained. Sharpen the wording to say "don't *invent/guess* identities" so the
  intent is unambiguous, without reopening the hallucination door.
- **Roster is a transcript-grounded superset, not a curated person list.** The speaker parser promotes a
  multi-word title-case colon label even on a single occurrence, so a pasted heading like
  `"Podsumowanie Spotkania: …"` can flow into `participants`. This is **grounded text, never an invented
  name**, so the core anti-hallucination invariant holds — but it can seat a non-person as an "attendee".
  Structurally a heading is indistinguishable from a one-utterance real speaker (requiring a repeat would
  drop both the once-speaking organizer recovered via `Name  M:SS` and legitimate single-turn colon
  speakers), so this is accepted as a heuristic trade-off rather than tightened with a brittle stop-word
  list. Documented so it is a conscious boundary, not a surprise.
- **Allowlist is transcript-derived yet reaches the trusted system prompt.** `_allowlist(roster)` is
  interpolated into both passes' system prompt, so up to a few capitalized name-tokens from the untrusted
  transcript sit in the trusted position. Contained: tokens are constrained to the name char-class (no
  newline/colon/sentence punctuation) and the summarizer has **no tools, no retrieval, no egress and
  touches no secrets** — the worst case is a degraded summary, further bounded by the verifier and the
  deterministic participants. Accepted; noted so the posture is explicit rather than implied.
- **Structured outputs.** Move both passes to Claude structured outputs (`output_config.format`) to retire
  the `_extract_json`/`_loads_lenient` robustness layer (pre-existing follow-up from ADR 0009).
- **Diarization dependency.** Anchoring quality tracks the transcript's diarization; VTT from Graph carries
  clean `<v Name>` cues, raw exports may not. Documented, not a regression.
- **On acceptance:** flip to `accepted`; decide whether `SUFLER_AGENT_VERIFY_MEETING_NOTE` defaults ON in
  production (operator call, given the ~2× cost).

## Update (2026-07-30)

Status flipped to `accepted`; `SUFLER_AGENT_VERIFY_MEETING_NOTE=true` set in `deploy/docker/env`
per team decision — the ~2× Claude call cost per meeting note is accepted in exchange for the
verifier catching unsupported claims. Live-verified only insofar as unit tests on fakes cover it;
still riding on the same parked live-smoke as ADR 0041 (no real transcript run yet).
