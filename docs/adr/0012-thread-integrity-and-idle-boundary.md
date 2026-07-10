# 0012 — Enforced thread integrity (FK) and idle-based conversation boundary

Date: 2026-07-10
Status: accepted
Author: P0w3r223
Related to: [ADR 0010](0010-conversation-threading-and-context-limit.md), [ADR 0011](0011-stateful-lossless-conversation-memory.md)
Amends: ADR 0010 (adds a rollover criterion; `messages` schema gains a foreign key)

---

## Context

ADR 0010 already models conversations as **threads**: a `conversations` row (its own
`id` + `created_at`) groups `messages` rows, each carrying `conversation_id` / `role` /
`text` / `created_at`. So the requested "each conversation is a separate thread with an
id, a creation timestamp, and a set of messages, every message belonging to exactly one
thread" is ~90% present in the schema. Two real gaps remained:

- **A — integrity is not enforced.** `messages.conversation_id` had **no** `FOREIGN KEY`
  and `PRAGMA foreign_keys` was off, so "every message belongs to exactly one *existing*
  thread" held only by convention, not by the database.
- **B — a thread only ever ends on the token limit.** The single rollover trigger was
  `max_context_tokens` (ADR 0010). For one interlocutor there is one active conversation
  that keeps growing until ~6000 tokens, so separate-in-time chats blur into a single
  long thread — the user's complaint that "history fills up one list and you can't tell
  where a conversation was".

Hard constraints carried over: the core stays pure (no I/O, **no clock**, no randomness)
and depends only on ports; the existing conversation API (ports, `ConversationService`
public methods, domain models) must not break; existing data must migrate in place.

## Decision

1. **Enforced FK on the message → thread link (gap A).** `messages.conversation_id`
   becomes `TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE`, and every
   connection runs `PRAGMA foreign_keys = ON`. New databases get the FK from the single
   schema source (`_messages_ddl`). SQLite cannot add a FK via `ALTER`, so pre-existing
   databases are migrated by **table rebuild** (create `messages_new` with the FK → copy
   with an explicit column list → drop old → rename). The rebuild runs with
   `foreign_keys` off (the connect-time default; we enable it only *after* migrating) and
   deletes any orphan rows before the constraint is created. Because FTS5 (`messages_fts`)
   is an external-content index over `messages`, it is dropped before the rebuild and
   re-created + `'rebuild'`-ed afterwards. The migration is **idempotent** — detected via
   `PRAGMA foreign_key_list(messages)` — running once and never again.

2. **Idle timeout as a second rollover criterion (gap B).** `ConversationService.
   _should_roll_over` now also starts a new thread when the active conversation has been
   **idle** longer than `idle_timeout` (time since `updated_at`). Effect: separate-in-time
   conversations become separate threads instead of one endless thread. It is evaluated
   **only for a non-empty thread** (`token_estimate > 0`) — a freshly opened, empty
   conversation has no history to cut and rolling it would orphan an empty thread.

3. **The clock lives in the adapter, not the core.** `ConversationService.prepare_turn`
   takes an optional `now: datetime`; the core never reads a clock. `ConversationalResponder`
   supplies it via an injectable `clock` (default: **naive UTC**, matching SQLite's
   `CURRENT_TIMESTAMP` so `now - updated_at` is a valid same-kind subtraction). Injection
   keeps idle rollover testable without a real clock.

4. **Configurable, off-able boundary.** `WORKMATE_CONV_IDLE_MINUTES` (default **30**)
   feeds `ConversationSettings.idle_timeout()`, which maps `0 → None` (criterion disabled,
   token limit only) in one place so the three door wirings don't repeat the `> 0` guard.

5. **Backward-compatible API.** All additions are keyword parameters with defaults
   (`idle_timeout=None`, `now=None`, `clock=_utcnow`). With them omitted the behaviour is
   exactly ADR 0010/0011; existing ports, service methods, and domain models are untouched.
   `ON DELETE CASCADE` is dormant today (no code deletes threads) but fixes the semantics
   of "a message belongs to its thread" for any future deletion.

## Alternatives considered

- **Explicit `/new` command as the boundary** — viable and complementary, but it needs
  message parsing in each door (Telegram/Teams) and a command vocabulary. Deferred: idle
  rollover needs no protocol change and covers the stated pain. Can be added later.
- **FK without `ON DELETE CASCADE`** — rejected: leaves the deletion story half-defined;
  cascade is the natural meaning of "message belongs to thread".
- **Exact wall-clock inside the core** — rejected: violates the core-has-no-clock rule.
  The adapter passes `now`; the core only compares.
- **Counting idle in tokens / a background sweeper** — rejected: over-engineered. A
  per-turn comparison at `prepare_turn` is enough and stays deterministic in tests.

## Consequences

- One-time table rebuild on first open of a pre-0012 file DB (fast; dev-scale data).
  New/`:memory:` DBs skip it. Signature round-trip and legacy text-only rows are
  preserved (the rebuild copies `blocks_json`/`stop_reason` verbatim).
- Writing a message to a non-existent thread now raises `IntegrityError` (fail fast)
  instead of silently inserting an orphan; `SafeResponder` still shields the async doors.
- Rollover is now driven by **either** token limit **or** idle; the "new conversation"
  notice already shown to users covers both. Idle threshold is soft/approximate (checked
  at the next turn), consistent with the token bound.
- Revisit when: adding an explicit new-thread command, or surfacing per-thread titles for
  the history preview.
