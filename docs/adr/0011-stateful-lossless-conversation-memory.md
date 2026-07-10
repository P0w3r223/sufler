# 0011 — Stateful, lossless conversation memory with extended thinking round-trip

Date: 2026-07-09
Status: accepted
Author: P0w3r223
Related to: [ADR 0010](0010-conversation-threading-and-context-limit.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md)
Amends: ADR 0010 (message storage model, token accounting, responder mapping)

---

## Context

ADR 0010 gave the async doors conversation memory, but the store is **lossy**:
each exchange persists only flat `text` for the final user and assistant messages
(`ConversationService.record_turn`), and history replay
(`responder._to_transcript`) rebuilds `AssistantTurn(text, ())`, dropping the
`tool_use`/`tool_result` interleaving. Extended thinking was disabled outright
(`thinking={"type": "disabled"}` in `anthropic_llm.py`), so thinking blocks never
existed.

We want **stateful / lossless** memory: store the full content-block sequence of
every turn so history round-trips to the Claude API without losing or modifying
blocks — including `thinking` / `redacted_thinking` blocks and their `signature` —
and enable adaptive thinking end to end.

Hard constraints (owner-decided, not reopened):

1. Content blocks are stored **verbatim** as opaque JSON and resent to the API
   byte-for-byte (thinking text + `signature` unmodified). Blocks are never edited
   or dropped.
2. Enable adaptive thinking: drop `disabled`, capture thinking blocks in
   `_from_message`, resend them in `_to_messages`, inspect `stop_reason`.
3. `core ↛ adapters` must hold: blocks are provider-shaped and the core must not
   interpret them.
4. Evolve the SQLite schema safely (dev DB) and keep FTS5 search working via a
   derived flat-text projection.

Load-bearing Claude API facts (verified against the `claude-api` skill), which
drive the design:

- Sonnet 5 uses adaptive thinking (`thinking={"type": "adaptive"}`);
  `budget_tokens` returns 400. `display` defaults to `"omitted"` → thinking blocks
  come back with **empty text but a present `signature`**.
- Continuing on the **same model**, thinking blocks must be echoed back **exactly as
  received** (including empty-text blocks). The API rejects *modified* blocks, not
  read ones — so a dict round-trip (`block.model_dump(mode="json")` → JSON → resend)
  is safe.
- In a turn that ends in `tool_use`, thinking blocks must be present and precede the
  `tool_use` block. Order is preserved by storing the whole `content` array.
- A turn ending in `tool_use` with no matching `tool_result`, or truncated on
  `max_tokens`, must **not** be replayed as history (400).

## Decision

1. **Per-turn opaque block blob (storage).** `messages` gains `blocks_json TEXT`
   (the verbatim `content` array) and `stop_reason TEXT`; `text` becomes a derived
   flat-text projection used for FTS/snippets and legacy compatibility. The
   round-trip unit is the message, matching the API. Normalized per-block rows were
   rejected — they would leak the provider block `type` vocabulary into the storage
   schema.

2. **Opaque passthrough in the core (dependency rule).** The block payload is a
   plain JSON structure (`tuple[Mapping[str, Any], ...]`), **not** an Anthropic SDK
   type. `LLMResponse` gains opaque `blocks` + `stop_reason`; `AssistantTurn` gains
   opaque `blocks`; a new `RawTurn(role, blocks)` transcript entry carries replayed
   history; the runtime returns an `AgentResult(reply, entries, stop_reason)`.
   SDK⇆dict conversion (`block.model_dump(mode="json")`) lives only in the adapter.
   The anti-corruption invariant is **refined, not broken**: the core does not
   *interpret* provider data, it only *transports* it (opaque-cursor pattern),
   contained to one labelled field. The flat-text projection needs no block
   inspection — `LLMResponse.text` is already the concatenation of text blocks
   (thinking excluded), so the core computes `token_estimate` from `.text`.

3. **Thinking is ephemeral for accounting, verbatim for storage (tokens).**
   `token_estimate` per message is computed over the flat-text projection (which
   excludes thinking and signatures). Prior-turn thinking is server-stripped/
   unbilled by the API, and counting large base64 signatures would trigger premature
   rollover. Rollover logic (ADR 0010) is otherwise unchanged. `display` is left at
   the default `"omitted"` — the signature (needed for round-trip) is present
   regardless, and thinking text is never indexed or shown.

4. **`tool_result` stored as role `tool` (roles).** `role ∈ {user, assistant, tool}`;
   the adapter renders a `tool` message to an API user-role message with
   `tool_result` blocks. `tool` rows store the **domain** form
   (`[{call_id, content, is_error}]`) — deterministically reconstructable, no
   signature — so they need no provider blocks. `text=""` keeps them out of FTS.
   Only `assistant` rows hold opaque, signature-bearing provider blocks.

5. **`stop_reason` handling; persist only complete exchanges (loop).** The runtime
   inspects `stop_reason`: `tool_use` → dispatch and continue; `end_turn` /
   `stop_sequence` → done, persist the full turn. A turn that does **not** reach a
   clean final assistant answer — truncated on `max_tokens`, or the iteration cap is
   reached — persists **nothing** (`AgentResult.entries == ()`); the user still gets
   the partial/fallback reply (with a truncation notice from the door). This gives a
   strong invariant: **persisted history contains only complete exchanges ending on
   an assistant turn**, so replayed history always ends on an assistant turn and the
   next user message alternates cleanly — no two consecutive user-role messages, and
   no truncated/incomplete turn (partial thinking, unmatched `tool_use`) ever reaches
   the API. A non-clean turn is treated like a transient failure: the reply is shown,
   memory stays consistent (no orphan). `AgentRuntime.run_turn()` returns
   `AgentResult`; `run()` stays a thin wrapper returning `.reply` for stateless doors.
   Default `max_tokens` is 128000 (the model's full output ceiling; thinking shares the
   budget) and the adapter calls the API via `messages.stream()` + `get_final_message()` —
   non-streaming (`messages.create`) is rejected by the SDK at that size. (Originally 16000
   non-streaming with streaming deferred; revised when large single-turn outputs were needed.)

6. **Backward compatibility (legacy).** Additive `ADD COLUMN` migration. Rows with
   `blocks_json IS NULL` degrade on read to text-only entries (today's behaviour).
   Text-only assistant turns are always valid to replay, so mixing legacy and new
   turns carries no 400 risk.

## Alternatives considered

- **Normalized per-block rows** — rejected: the storage schema would mirror provider
  block types, leaking the vocabulary the core must not interpret.
- **Counting thinking in `token_estimate`** — rejected as default: erratic rollover
  and double-counting of server-stripped context.
- **`role ∈ {user, assistant}` with `tool_result` as user-role blocks** — viable but
  needs block inspection to project FTS/tokens; the explicit `tool` marker is
  simpler and keeps block knowledge in the adapter.
- **Persisting truncated turns** — rejected: replaying incomplete thinking → 400.

## Consequences

- `AgentRuntime` gains `run_turn()`; storage granularity changes from 2 rows per
  exchange to N rows per run (user + every assistant/tool turn).
- The `LLMClient` port carries an opaque provider payload (a documented, contained
  exception to the anti-corruption boundary).
- Signature round-trip correctness is only verifiable against the **real API** —
  in-memory fakes cannot validate signatures. Needs a live smoke test.
- Revisit when: switching models mid-conversation (thinking blocks from another
  model are dropped by the API — plan a re-baseline), or when moving to streaming for
  large outputs.
