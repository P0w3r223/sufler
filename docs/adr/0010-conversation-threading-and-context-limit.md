# 0010 — Conversation threading, history, and context limit

Date: 2026-07-09
Status: accepted
Author: P0w3r223
Related to: [ADR 0006](0006-write-capability-gate-2.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md)

---

## Context

Until now the async doors (Telegram, Teams) answered each message statelessly:
`RuntimeResponder.respond` called `AgentRuntime.run(text)` with no memory of prior
turns. Requirements: give the bot **conversation threading** (remember the exchange),
**persist history**, store **many conversations efficiently** with **easy search of
old ones**, and enforce a **context limit** per conversation that is *not too large*
and **rolls over to a new conversation** when reached.

Constraints from the existing architecture: the knowledge base (notes/registry) is
deliberately file-based and is *sources-of-truth*; conversations are **operational
data** (chat logs), a different concern. The core must stay pure (no I/O, no clock,
no randomness) and depend only on ports.

## Decision

1. **Conversations are a separate subsystem, behind a port.** New domain types
   (`Conversation`, `ConversationMessage`, `ConversationSearchHit` in
   `core/domain/conversation.py`), a `ConversationStore` port
   (`core/ports/conversations.py`), and a `ConversationService`
   (`core/application/conversations.py`). The frozen note schema (Gate 1) is
   untouched — conversations are not notes.

2. **Storage: SQLite + FTS5 (stdlib), swappable behind the port.** The requirement
   is efficient storage of many conversations and easy full-text search of old ones.
   `sqlite3` is in the standard library (no new dependency) and FTS5 gives fast
   full-text search with ranking and snippets. When a Python build lacks FTS5, the
   adapter (`adapters/outbound/sqlite_conversations.py`) degrades gracefully to a
   `LIKE` scan (detected once at startup). The store is an adapter — swap it for a
   different backend without touching the core.

3. **Context limit + rollover in the service.** Each conversation's context is bounded
   by `max_context_tokens` (default 6000, modest, configurable via
   `WORKMATE_CONV_MAX_TOKENS`). Tokens are **approximated deterministically**
   (`len(text) // 4`, no API call) — enough to bound context length and keeps tests
   reproducible. When appending the next turn would exceed the limit, the active
   conversation is **closed** and a **new one is opened** (rollover). Two benefits:
   every model call has a bounded, cheap context; old conversations stay archived and
   searchable.

4. **History as context, threaded per (channel, external_id).** `ConversationService.
   prepare_turn` returns the turns *before* the current message; the door maps them to
   the runtime's transcript and calls `AgentRuntime.run(text, history=...)`
   (a new, backward-compatible parameter). After the model answers, `record_reply`
   appends the assistant turn. The thread key is the door's conversation id (chat /
   Teams conversation), falling back to sender.

5. **Wired into both async doors.** `ConversationalResponder` (in the inbound seam)
   replaces `RuntimeResponder` in the Telegram and Teams entry points, tagged with a
   `channel` (`telegram` / `teams`). On rollover it prefixes a short notice so the user
   knows a new conversation started.

6. **Security / data placement.** The conversations DB lives **outside `data/`**
   (the tool-indexed knowledge base) and outside the repo — default
   `~/.workmate/conversations.db` (writable without admin for local doors). Notes tools
   cannot reach it (they scan `notes_dir` only, with path-traversal already blocked).
   No secrets are stored in conversations.

## Alternatives considered

- **File-per-conversation (JSON/markdown)** — consistent with the file-based knowledge
  base, but "easy search of many old conversations" degrades to a full directory scan.
  SQLite+FTS5 directly serves the efficiency requirement. Rejected as the default.
- **Exact token counting via the Anthropic tokenizer** — accurate but needs an API call
  per turn (latency, cost, non-deterministic tests). The `//4` approximation is
  sufficient for a *context bound* and keeps the core pure. Chosen.
- **Summarize-and-continue instead of rollover** — keep one long conversation, summarize
  when large. More complex, and the requirement is explicitly "start a new conversation
  when the limit is reached". Rollover is simpler and matches the ask; the old thread
  remains searchable.

## Consequences

- **Done and verifiable today** (169 tests, incl. service rollover on a fake store and
  the real SQLite store + FTS search on `:memory:`; the `ConversationalResponder` seam
  tested with a fake runtime): threading, persistence, efficient search, context-limit
  rollover, and door wiring.
- **Not covered here:** exact token accounting; cross-conversation summarization;
  surfacing search to the user as a tool/command (the store supports it — exposing it in
  the agent is a small follow-up). Rollover boundary uses an approximation, so it is
  intentionally soft (a turn may slightly exceed the limit before rolling over).
- **Operational note:** the doors now write to a SQLite file at startup; a non-writable
  `WORKMATE_CONVERSATIONS_DB` path fails fast at construction. Default is user-home.
