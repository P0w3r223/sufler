# 0017 — Generalized read-only command dispatcher in the door seam

Date: 2026-07-12
Status: accepted
Author: P0w3r223
Related to: [ADR 0006](0006-write-capability-gate-2.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md), [ADR 0010](0010-conversation-threading-and-context-limit.md), [ADR 0012](0012-thread-integrity-and-idle-boundary.md)

---

## Context

The bot understood exactly one command — `/new` (start a thread, ADR 0012) — implemented as a
single `if _is_new_thread_command(...)` in `ConversationalResponder._respond_sync`, intercepted
before the agent loop with an early return (the command is not persisted as a conversation turn).
There was no `/help`, no onboarding, and no cheap way to reach the knowledge base without paying a
full agent round-trip. The owner asked for a small set of Claude-Code-style commands to make the
bot easier to use.

Two facts shape the design. First, **all four doors** (CLI, Telegram, Teams Bot Framework, Teams
delegated Graph) run through the same `ConversationalResponder`, so a dispatcher added there covers
every door for free. Second, the read paths already exist: the tool catalog (`search_notes`,
`list_projects`, `get_project_status`) and `ConversationService` (`start_new_thread`,
`list_conversations`, `active_summary`) can be called directly, bypassing the LLM.

Hard constraints: `core ↛ adapters` (command handling stays in `adapters/inbound/`); read-only on
less-trusted doors (ADR 0006) — no command may bypass the write gate; a command is **not** a
conversation turn (no `record_run`, no FTS, no context-limit accounting) — preserving the `/new`
property; the frozen 4+1 MCP tool surface (ADR 0008, golden test) must not change — commands live
in the door seam, not the catalog.

## Options Considered

- **Option A (chosen): a `CommandRouter` in a shared seam module** (`adapters/inbound/commands.py`),
  injected into `ConversationalResponder` (`commands: CommandRouter | None = None`). A single
  registry (`COMMAND_SPECS`) is the one source of tokens/aliases/summaries — it drives `/pomoc`,
  the token→handler binding, and the Telegram command-name list. **Pros:** single responsibility
  (responder = memory, router = parse/dispatch), unit-testable in isolation, one registry, and
  `None` preserves the old constructor behavior. **Cons:** one new module and a new (generic,
  additive) `ConversationService.active_conversation` delegation for the current-thread `/status`.
- **Option B (rejected): methods on `ConversationalResponder`.** Bloats the memory class and mixes
  two concerns; harder to test the parser in isolation.
- **Option C (rejected): per-door command parsing.** Duplicates the parser across four doors and
  splits the single source of command names.

## Decision

Adopt **Option A**. A `CommandRouter.dispatch(text, ctx) -> str | None` parses the first token
(stripping an `@bot` suffix, lowercasing, **keeping arguments** so `/szukaj <fraza>` works), and
returns the command reply or `None` ("not a command → normal turn"; an unknown `/slash` returns
`None` and flows to the LLM, so pasted paths aren't hijacked). The intercept sits exactly where
`/new` used to, in `_respond_sync` before the agent loop, under the store lock, with an early
return — so a command never becomes a turn. The router is wired by a new consolidated builder
`build_conversational_responder(...)` in `agent_wiring.py`, which hands it a **read-only** catalog
(`build_read_catalog` = `build_tool_catalog(..., write_service=None)`) — a structural guarantee that
commands never touch the write gate, even on the trusted CLI door. Command set: `/pomoc` (`/help`),
`/nowa` (`/nowy`/`/new`), `/szukaj <fraza>`, `/projekty`, `/status [projekt]`, `/historia`.

## Consequences

- **New seam module** `adapters/inbound/commands.py` (`CommandRouter`, `CommandSpec`/`COMMAND_SPECS`,
  `CommandContext`, `telegram_command_names`, formatting helpers; the `_NEW_THREAD_*` strings move
  here from `responder.py`). Dispatch reply formatting (dict → text) is an adapter concern.
- **Covers all four doors** through the shared responder; each door only builds the responder via
  the new builder. Telegram additionally registers command names via `telegram_command_names()`
  in `CommandHandler` (python-telegram-bot filters `~COMMAND`), keeping one source of truth; a test
  guards against drift.
- **Realizes the deferred alternative in ADR 0012** ("explicit `/new` command as the boundary") and
  generalizes it into a command surface.
- **Small additive core change**: `ConversationService.active_conversation` — a generic read
  delegation (the core stays unaware of commands; the adapter router calls it) for the
  current-thread `/status`. `/status` without an argument reports turn count + compaction state, not
  a token sum (deliberately, so it stays consistent with the hot-path optimization that dropped the
  usage SUM from `active_conversation`).
- **Read-only preserved (ADR 0006)**: no `/zapisz`; commands use the read-only catalog. Command
  arguments are data, not instructions (e.g. `/szukaj` → parameterized FTS).
- **MCP 4+1 surface untouched (ADR 0008)**: commands are a door-seam feature, not catalog tools; the
  golden test `test_mcp_tool_surface` stays green.
- **Wiring consolidation (bundled, internal)**: the `SafeResponder(ConversationalResponder(...))`
  recipe — previously copied in four `app.py` files — is now a single `build_conversational_responder`,
  and `.env` loading is unified in a shared `adapters/inbound/env.py` (fixing the Teams door, which
  did not load `.env`). No contract change → no separate ADR.
- **Revisit when** a command needs write capability (would require its own ADR + gate per ADR 0006),
  or when per-door command availability should differ.
