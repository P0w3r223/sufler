# 0058. Clear stale tool results in-API; make the compaction threshold absolute

Date: 2026-08-05
Status: accepted
Author: P0w3r223
Related to: docs/adr/0011-stateful-lossless-conversation-memory.md,
  docs/adr/0014-conversation-compaction.md
Amends: ADR 0014 (compaction threshold: fraction of the model window → absolute token count)

---

## Context

A tool result enters the conversation once and is then re-sent on every subsequent turn until the
thread ends. Nothing removes it. A conversation that calls tools heavily therefore grows by content
the model has already consumed and will not read again — and pays for it on each turn.

Two thresholds govern how large that context is allowed to get, and both were mis-set.

**Compaction fired at 700k tokens.** ADR 0014 derives the trigger as a fraction of the model's
context window: `0.70 × 1_000_000`. That conflates two different quantities. The window size states
how many tokens the API will *accept*; the compaction threshold should state after how many the
answer stops being reliable. Published measurements put the second number far below the first —
retrieval accuracy degrades well before a window is full, and the degradation is gradual rather than
a cliff at the boundary. Deriving one from the other also meant that raising the window — a routine
consequence of a model upgrade — silently moved the agent deeper into degraded territory.

**Nothing cleared tool results at all.** The only mechanism that shrank a conversation was
compaction, which summarises *everything* older than the last N exchanges with a separate model
call. Using it to shed stale tool output is the wrong instrument: it is expensive, it is lossy about
the parts worth keeping, and at a 700k trigger it effectively never ran.

This matters now rather than later because the `Bash` tool is next in the rebuild. Command output is
capped at 64 KB per call (ADR 0057), which is roughly 16–20k tokens returning on every later turn,
several times per turn at eight tool iterations. Adding the largest producer of context bytes to a
harness with no mechanism for removing them would compound a problem that already exists.

## Decision

**Clear stale tool results through the Claude API rather than in our own code.** Claude API's context
editing (`clear_tool_uses_20250919`, beta `context-management-2025-06-27`) removes the *body* of old
tool results while leaving the `tool_use` call in place. `AgentSettings` carries the three thresholds;
`_context_management` in the Anthropic adapter builds the parameter, and the request moves to
`client.beta.messages.stream` only when clearing is enabled.

Building this ourselves was the alternative — a persistent flag on tool rows, following the
`archive_through` pattern from ADR 0014. It was rejected because it is scaffolding the vendor already
provides: an equivalent mechanism in our core would be code to maintain, a port to extend, and a
behaviour to keep aligned with an API that is moving in the same direction anyway.

Three choices inside the configuration are load-bearing:

- **The call is cleared, the result is not.** `clear_tool_inputs` stays at its default `false`. The
  record "I asked for X" is what stops the model from asking again; without it, clearing context
  would *generate* traffic instead of removing it.
- **`keep` never drops below 1.** Clearing every pair would take the result the model asked for in
  the current tool-use loop, handing the loop an empty answer to its own question.
- **`clear_at_least` is set, not omitted.** Clearing invalidates the cached prefix, so each
  activation costs a cache write. A minimum batch size converts frequent small clears into rare
  large ones.

**Make the compaction threshold absolute.** `compaction_threshold_tokens` (default 150 000) replaces
the `context_window_tokens × compaction_threshold_fraction` pair. The two thresholds form a cascade:
clearing tool results at 100k is cheap and precise, summarising the conversation at 150k is expensive
and lossy, so the cheap instrument runs first.

## Consequences

- **Two environment variables stop being read.** `SUFLER_CONTEXT_WINDOW_TOKENS` and
  `SUFLER_COMPACTION_THRESHOLD_FRACTION` are gone, replaced by
  `SUFLER_COMPACTION_THRESHOLD_TOKENS`. Both were commented out in the shipped `.env`, so a
  deployment that never uncommented them is unaffected — but a deployment that *did* set them will
  find its override silently ignored, since an unknown variable is not an error. Check the server's
  `.env` before rolling out.
- **The agent request now runs on a beta surface.** Clearing lives behind
  `context-management-2025-06-27`. The failure mode is narrow: setting
  `SUFLER_CONTEXT_EDITING_ENABLED=false` returns the request to exactly its previous shape, header
  and all, because the beta path is selected by a parameter rather than by a separate code branch.
- **The replay invariant is untouched.** Clearing happens API-side, on the request. Our store keeps
  every turn verbatim, so ADR 0011's guarantee — memory holds only complete, replayable exchanges —
  holds unchanged, and so does the audit trail.
- **Compaction never sees the cleared bytes.** Because clearing triggers first and lowers
  `input_tokens`, the value that gates compaction, summarisation now runs less often and on less
  material. That is the intended ordering, not a side effect.
- **Clearing is invisible without a log.** It happens remotely and leaves no trace in our transcript,
  so "the threshold is too high and nothing is ever cleared" and "it clears every turn and we pay a
  cache write each time" look identical from outside. `_log_applied_edits` records the reported
  `cleared_tool_uses` and `cleared_input_tokens` at INFO; those two numbers are how the thresholds get
  tuned against real conversations rather than guessed.
- **Compaction's own summariser opts out.** It receives a single flattened message with no tool calls,
  so clearing has nothing to act on; the wiring disables it there rather than sending a beta header on
  a request that does not use the feature.
