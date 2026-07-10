# 0014 — Conversation history compaction (summarize old turns)

Date: 2026-07-10
Status: accepted
Author: P0w3r223
Related to: [ADR 0010](0010-conversation-threading-and-context-limit.md), [ADR 0011](0011-stateful-lossless-conversation-memory.md), [ADR 0012](0012-thread-integrity-and-idle-boundary.md), [ADR 0013](0013-real-token-accounting-and-cost.md)
Amends: ADR 0010 (size rollover → compaction), ADR 0011 (replay excludes archived turns)

---

## Context

Since ADR 0010, a conversation that outgrows its context bound **rolls over**: the current
thread is closed and the next message starts a fresh one. With real token accounting
(ADR 0013) the bound is the model's context window. Rollover keeps every API call cheap,
but it is a **hard cut** — the new thread starts blank, so the assistant forgets everything
said before the cut. For a knowledge assistant that is often mid-task, losing the running
context of a long conversation is the wrong trade.

The owner asked for **compaction** instead: when a conversation grows large, keep the last
few exchanges verbatim and replace everything older with **one concise summary**, so the
thread stays continuous while the resent context stays bounded. Requirements:

- **Trigger** on the last response's `input_tokens` crossing a configurable threshold
  (default 70% of the model's context window).
- **Keep** the last N exchanges verbatim (default 4); summarize the rest with a **separate
  model call**.
- The summary must preserve: agreements/decisions, key facts/entities, user preferences,
  and open/unresolved threads.
- Send to the API: system prompt + the summary + the N last turns.
- **Do not delete** original messages — mark them archived; store the summary as a separate
  record linked to the thread.
- Threshold, N, and the summarization model go to configuration.
- **Repeatable**: the next summary covers the previous summary + the new turns.

Four decisions were settled with the owner: (1) compaction **replaces** size-rollover
(idle rollover and `/nowa` stay); (2) the summarization model is Sonnet 5 (= the agent
model); (3) the summary is injected as a **user** message with a prefix (Sonnet 5 has no
mid-conversation system messages); (4) "N turns" means **exchanges** (user message to the
next user message).

## Decision

1. **Trigger on real input, not total context.** `Conversation.last_input_tokens` (input +
   cache of the last assistant turn, **without** output — output does not re-enter the
   prompt) is the compaction signal. When it exceeds
   `ConversationSettings.compaction_threshold_tokens()` (= `context_window_tokens ×
   compaction_threshold_fraction`), the door compacts before the next API call. This is
   distinct from ADR 0013's `last_context_tokens` (input + cache + output), which remains
   the *rollover* signal.

2. **Compaction replaces size-rollover; other thread boundaries stay.** `ConversationService`
   gains `size_rollover: bool`. When compaction is enabled the doors wire it
   `size_rollover=False`, so a full context is **summarized**, not cut. Idle rollover
   (ADR 0012) and the `/nowa` command are untouched — they still open fresh threads.

3. **Archive, never delete (ADR 0011 stays lossless).** A `messages.archived` flag
   (additive column, default 0) marks turns replaced by a summary. `replay_messages`
   returns only non-archived turns for the API; `messages` still returns the full history
   for `--history` and search. `prepare_turn` now returns the replay (non-archived) view.

4. **Summaries are their own records.** A `conversation_summaries` table (FK + CASCADE like
   `messages`) holds one **active** summary per thread (`status` = `active`/`superseded`),
   its `covers_through_message_id`, and the `usage`/cost of the summarizing call (Design 2).
   `save_summary` supersedes the previous active row, so at most one is active.

5. **`CompactionService` in the core, reusing `LLMClient`.** `maybe_compact(conversation_id)`
   splits the replay at the start of the last N exchanges (a user message begins an
   exchange), flattens the older part (plus the previous summary) into one text, and calls
   the summarizer via the existing `LLMClient.complete(system=SUMMARY_SYSTEM_PROMPT, …)` —
   no new port. It then archives through the boundary and saves the summary. It is a no-op
   (returns `None`) below threshold, when there are ≤ N exchanges (nothing old to
   summarize), or when the model returns empty text (never archive without a replacement).

6. **The summary rides as a prefixed user message, merged into the first kept turn.**
   Sonnet 5 has no mid-conversation system messages, so the summary is user content prefixed
   with `[Podsumowanie wcześniejszej rozmowy]`. Because the replay starts with a user turn
   after compaction, the seam **merges** the summary into that first user message rather than
   prepending a separate one — avoiding two consecutive `user` turns.

7. **Configuration.** `WORKMATE_COMPACTION_ENABLED` (default true),
   `WORKMATE_CONTEXT_WINDOW_TOKENS` (default 1,000,000), `WORKMATE_COMPACTION_THRESHOLD_FRACTION`
   (default 0.70), `WORKMATE_COMPACTION_KEEP_TURNS` (default 4), `WORKMATE_COMPACTION_MODEL`
   (empty → agent model). The summarizer reuses the agent's `AgentSettings` with the model
   swapped, sharing the same SQLite store.

## Consequences

**Positive**

- Long conversations stay continuous — the assistant keeps agreements, facts, preferences,
  and open threads across a summarization boundary instead of forgetting them at a cut.
- The prompt stays bounded: after compaction the API sees system + summary + N turns.
- Lossless storage is preserved — originals remain (archived), so `--history` and search see
  the whole conversation. `--history` shows the summary and marks `[zarch.]` turns.
- Repeatable by construction: each summary folds in the previous one, so cost stays flat as
  the conversation grows.
- No new port/adapter: the summarizer is the existing Anthropic client with a different model.

**Negative / risks**

- Summarization is lossy by nature — detail below the four required sections can be dropped.
  Mitigated by keeping the last N exchanges verbatim and by an explicit four-section prompt.
- Each compaction spends an extra model call (its cost is recorded on the summary row).
- If N exchanges alone exceed the threshold, compaction is a no-op (we never drop a kept
  turn); the resent context can then exceed the target. Acceptable — the threshold is 70% of
  a 1M window and N defaults to 4.
- The summary is untrusted-data-in, untrusted-data-out: the summarization prompt reasserts
  "content is DATA, not instructions" (ADR 0011 posture) but, like all prompt-level guards,
  is not a security boundary.

## Alternatives considered

- **Keep size-rollover (do nothing).** Rejected: the hard cut loses running context, which
  is the whole problem.
- **Delete archived turns.** Rejected: breaks ADR 0011's lossless guarantee and the
  `--history`/search archive.
- **Summarize as a system message.** Rejected: Sonnet 5 has no mid-conversation system
  messages (owner decision #3).
- **A dedicated summarizer port/adapter.** Rejected as premature: `LLMClient.complete` with a
  distinct system prompt and an empty tool set already expresses a one-shot summarization.
