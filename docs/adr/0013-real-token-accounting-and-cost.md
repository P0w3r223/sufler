# 0013 — Real token accounting and per-conversation cost (Design 2)

Date: 2026-07-10
Status: accepted
Author: P0w3r223
Related to: [ADR 0010](0010-conversation-threading-and-context-limit.md), [ADR 0011](0011-stateful-lossless-conversation-memory.md), [ADR 0012](0012-thread-integrity-and-idle-boundary.md)
Amends: ADR 0010 (rollover signal), ADR 0011 (token accounting: estimate → real `usage`)

---

## Context

ADR 0010/0011 accounted tokens with a deterministic **estimate** — `len(text) // 4`
over the stored flat text (`estimate_tokens`). It served two jobs at once: the rollover
context bound and the `~N tok` figure in `--history`. The estimate is cheap and
test-reproducible, but it is **not the real number** — it ignores the system prompt, the
tool schemas, and the full history that is resent to the API on **every** turn, and it
carries no notion of **cost**.

The owner asked to replace the estimate with **real tokens read from the API `usage`
field**, and to show **per-conversation cost** for `claude-sonnet-5` (input $2/M, output
$10/M as the intro price through 2026-08-31; $3/M and $15/M from 2026-09-01), with cache
priced as read = 10% of input and 5-minute write = 125%. Two options were weighed:
Design 1 (keep the estimate for rollover, add real usage for display only) and **Design 2
(chosen): remove the estimate entirely — rollover on real tokens too).**

`usage` was previously **discarded** in `_from_message`, so this is not a display-only
change: usage must be captured and threaded through the port, runtime, and storage.

## Decision

1. **Capture `usage` and carry it as opaque plain data.** `_from_message` reads
   `message.usage` into a pure-domain `TokenUsage` (`input_tokens`, `output_tokens`,
   `cache_read_input_tokens`, `cache_creation_input_tokens`). It flows through
   `LLMResponse.usage` and `AssistantTurn.usage` (per API call) and `AgentResult.usage`
   (summed over a run's tool-loop). The runtime sums usage across iterations; each
   `AssistantTurn` carries the usage of the call that produced it. Same anti-corruption
   posture as ADR 0011's `blocks`/`thinking_text`: the core transports numbers, doesn't
   interpret provider objects.

2. **Storage: four additive columns, no rebuild.** `messages` gains `input_tokens`,
   `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (nullable) via
   the existing additive-`ALTER` path (ADR 0011). The legacy `token_estimate` column is
   **retired in place** (written as `0`, never read) — kept only to avoid a table rebuild;
   legacy rows keep their old value, ignored. Usage lands only on assistant rows with real
   (`> 0`) usage; user/tool/legacy rows store `NULL` → read back as "no usage".

3. **Rollover on the real context of the last turn.** `_should_roll_over` no longer sums
   an estimate. It rolls when `last_context_tokens` (the newest assistant turn's
   `input + cache + output`, ≈ what the next turn resends) reaches `max_context_tokens`.
   The "non-empty thread" guard for idle rollover and the `/nowa` command now uses
   `message_count` (a plain COUNT), which is independent of usage — so a text-only
   (`record_turn`) thread is still correctly treated as non-empty.

4. **`max_context_tokens` default raised 6000 → 128000.** Real `input_tokens` are orders
   of magnitude larger than the old flat-text estimate (system prompt + tool schemas +
   full resent history), so `6000` would roll over after ~1 turn. `128000` (well under the
   1M window) bounds per-turn context/cost; tunable via `WORKMATE_CONV_MAX_TOKENS`.

5. **Cost as configuration constants (`core/domain/pricing.py`).** Rates and the
   switch date (`PRICING_SWITCH_DATE = 2026-09-01`) are module-level constants — one place
   to edit on a price change. `cost_usd(usage, on=date)` picks intro vs standard rates by
   the message/conversation date; `input_tokens` (uncached) at full input price, cache read
   at 10% and 5-minute write at 125% of input, output at output price — cache counted
   separately from `input_tokens` to avoid double-counting. `--history` shows real
   `total_tokens` and `$cost` per conversation (priced at the conversation's `created_at`
   date; conversations are short — rollover bounds them — so a single-day price is fine).

## Alternatives considered

- **Design 1 — keep the estimate for rollover, real usage for display only.** Simplest and
  lowest-risk, but leaves a fictitious number gating rollover and two token notions in the
  system. Rejected per the owner's choice of Design 2.
- **Drop `token_estimate` via table rebuild.** Cleaner schema, but a rebuild (like the FK
  migration) for a column we can simply stop using is unjustified. Retire-in-place chosen.
- **Sum `input_tokens` across turns for the context bound.** Wrong: input is resent every
  turn, so summing over-counts massively (that sum is *billing*, not *context size*). The
  last turn's input is the right context signal.
- **Per-message cost with exact per-day rates.** Rejected as overkill: a rolled-over
  conversation is short and same-day; pricing the summed usage at the conversation date is
  accurate enough and simpler.

## Consequences

- Rollover semantics change: the threshold now counts *real* tokens, so the number means
  something different (and larger) than before — hence the 6000 → 128000 default. Operators
  tuning `WORKMATE_CONV_MAX_TOKENS` must think in real tokens.
- `usage` is only populated on the real agent path (`record_run`); `record_turn` (text-only
  fallback) stores no usage → those turns show `$0` and don't drive rollover.
- Legacy conversations (pre-0013 rows without usage) show `0 tok`/`$0` — real figures apply
  from the first turn recorded after this change.
- **Verifiable offline** (261 tests): pricing math and the intro/standard switch,
  `usage` capture and summation, rollover on real context, `message_count`-based
  empty-guard, additive migration. **Needs a live smoke** for the actual `usage` numbers
  the API returns (fakes can't produce them) and to confirm the pricing policy is current
  (Sonnet 5 intro price runs through 2026-08-31).
- Revisit when: enabling prompt caching (cache columns start being populated — cost formula
  already handles them), or if the published price/dates change (edit the constants).
