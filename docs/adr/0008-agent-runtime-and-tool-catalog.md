# 0008. Agent runtime and single-source tool catalog (Phase 2, M1)

Date: 2026-07-08
Status: accepted
Author: P0w3r223
Related to: docs/adr/0001-src-layout-and-hexagonal.md,
  docs/adr/0003-note-schema.md, docs/adr/0006-write-capability-gate-2.md,
  docs/adr/0007-gate-3-http-auth-deployment.md,
  src/sufler/adapters/inbound/teams/responder.py (M1 seam), roadmap Phase 2 / M1

---

## Context

Phase 2 milestone M1 needs an agent runtime: a module that takes a
natural-language query, decides in a loop which of the frozen Phase-1 tools to
call (via the Claude API), and composes an answer — runnable locally without
Teams or Azure. Three existing contracts constrain the design. (1) The note
schema (`NoteMetadata`, Gate 1) and the 4+1 tools' names/inputs/outputs are
frozen; the runtime may not change them. (2) The dependency rule `core ↛
adapters` (ADR 0001) stands: network I/O to Claude belongs in an outbound
adapter, while the loop may live in the core behind a `Protocol` port, like the
repositories. (3) Tools must be single-sourced: today FastMCP generates each
tool's schema from the typed function signature in
`adapters/inbound/mcp/tools.py`; the agent and the MCP door must share one tool
definition over one service layer (`core/application/services.py`), without
forking descriptions or schemas. The Teams seam already anticipates a
`core/agent/runtime.py` wrapped by a `Responder` (see `responder.py`).

## Options considered

1. **Shared typed-function catalog in the core, both doors derive from it.**
   One `ToolSpec` per tool in `core/application/tools.py`, each carrying a typed
   handler `fn` (exactly today's signature and body, lifted out of `tools.py`
   and closed over the services) plus name and description. The MCP door is
   rebuilt to a trivial loop that registers each `fn` on FastMCP — identical
   schema, because it is the same generator over the same signature. The agent's
   outbound adapter derives the Anthropic `input_schema` from the same `fn`
   (reusing `mcp`'s `func_metadata`, an already-present base dependency), so the
   agent schema is byte-identical to the MCP schema. Per-door write gating is
   reused: `build_tool_catalog(..., write_service=None)` omits `save_note`,
   mirroring `register_tools(write_service=None)`. The loop lives in
   `core/agent/runtime.py` behind an `LLMClient` port (`core/ports/llm.py`) with
   a domain-shaped request/response vocabulary (anti-corruption); the concrete
   `AnthropicLLMClient` lives in `adapters/outbound/`. A new inbound CLI door
   (`adapters/inbound/cli/app.py`, console script `sufler-agent`) runs the
   runtime locally with the trusted (read+write) catalog; Teams later wraps the
   same runtime in a read-only catalog via a `RuntimeResponder`.

2. **Explicit Pydantic args-model catalog.** Express each tool's parameters as a
   first-class Pydantic model; `input_schema = model.model_json_schema()`. Clean
   and dependency-light for the agent, but FastMCP (pinned `mcp>=1.2`) registers
   from a function signature, not a model — a single model argument would nest
   the parameters under one object and change the frozen MCP input surface.
   Preserving the surface requires typed wrapper functions in the MCP door that
   mirror the model fields, i.e. a partial re-fork of the parameter list that
   must be pinned by a schema-equality test.

3. **Project the catalog out of FastMCP.** Keep the FastMCP registrations as the
   authority and build the agent's tool list by reading back
   `mcp._tool_manager.list_tools()`. Least new code, but it depends on a private
   API and inverts the layering — the core runtime would consume an
   MCP-adapter artifact, so the catalog is not transport-neutral and the goal of
   rebuilding the MCP door *on* the catalog is not met.

## Decision

**Option 1.** It is the only option that makes the catalog the transport-neutral
source of truth, keeps the frozen 4+1 surface provably identical (same generator
over the same lifted signatures), reuses the ADR 0006 per-door write gate for
the agent as well, and avoids private APIs. The single fork it costs is one
adapter-level import of `func_metadata` from `mcp`, which is already a base
dependency and lives in an adapter, not the core.

Envelope:

- **Loop in the core behind a port.** `AgentRuntime` in `core/agent/runtime.py`
  depends only on the `LLMClient` port and the tool catalog. The port speaks a
  domain-shaped vocabulary (`LLMRequest`/`LLMTurn`/`ToolInvocation`/
  `ToolResult`); Anthropic content-blocks, `tool_use`, and `stop_reason` never
  enter the core. The runtime is testable against a scripted fake `LLMClient`,
  no network — mirroring the in-memory-fakes convention.
- **Outbound Claude adapter owns the network and the secret.**
  `AnthropicLLMClient` (lazy `import anthropic`) owns the model id, `max_tokens`,
  and the API key, and translates Anthropic ⇆ core vocabulary. The key is read
  only here — the core and the runtime never see it.
- **Single-source catalog.** `core/application/tools.py` exposes `ToolSpec`
  (name, description, `fn`) and `build_tool_catalog(notes, projects,
  write_service=None)`. The composition root builds it once and passes the same
  object to both the MCP registrar and the agent runtime.
- **Bounded loop.** `max_tool_iterations` and `max_tokens` guard cost and
  latency; tool errors return `{"error": ...}` to the model as a tool result;
  unknown exceptions still propagate as defects.
- **Secret and model config.** A new `AgentSettings` (mirroring `TeamsSettings`)
  reads `ANTHROPIC_API_KEY` (optional `SUFLER_AGENT_API_KEY` override),
  `SUFLER_AGENT_MODEL`, `SUFLER_AGENT_MAX_TOKENS`,
  `SUFLER_AGENT_MAX_TOOL_ITERATIONS`; `api_key` uses `field(repr=False)` and
  `validate()` fails fast when empty. The key never lands in the repo or under
  `data/` (the tool-indexed folder), consistent with ADR 0007. The default model
  is `claude-sonnet-5` — chosen for the project's stronger synthesis requirements —
  and is overridable via `SUFLER_AGENT_MODEL` (e.g. a Haiku id for cheaper
  dispatch, or an Opus id for harder synthesis). The default lives in config, not
  logic. Extended thinking is disabled explicitly in the LLM adapter, because the
  tool-dispatch loop replays assistant turns without `thinking` blocks.
- **Per-door trust profile.** The local CLI door (trusted, like the stdio dev
  door) builds a read+write catalog; the Teams door (less trusted, ADR 0006)
  builds a read-only catalog, so the agent over Teams cannot `save_note` until a
  future ADR grants it. The runtime is identical across doors; only the injected
  catalog differs.
- **New optional dependency.** An `agent` extra pins `anthropic`; the base MCP
  server stays lightweight. Missing extra yields a clean message, like `teams`.

## Consequences

- `adapters/inbound/mcp/tools.py` is rebuilt from five decorated functions into a
  thin loop over the catalog. This must not change the MCP-visible surface: a
  characterization test snapshots each tool's FastMCP `parameters`, name, and
  description before the refactor and asserts equality after (tool output shapes
  are pinned separately by the read-tool and gating tests, since both doors share
  one `fn` and cannot diverge).
  If the pinned `mcp` version cannot reproduce an identical schema from the
  lifted signatures, the MCP rebuild is a blocker and is deferred (agent-only
  catalog, MCP left as-is) until resolved — the frozen contract wins.
- The core gains an `agent/` package and an `LLMClient` port; the dependency rule
  is unchanged and could be enforced later by import-linter (already noted in
  ADR 0001).
- Adding a *further* mutating tool still needs its own ADR (ADR 0006 unchanged);
  the note schema stays frozen (ADR 0003) and the agent uses only existing
  fields. Editing/deleting notes remain out of scope.
- Prompt injection from note content is bounded structurally: the less-trusted
  doors get a read-only catalog, so a successful injection cannot mutate state.
  The system prompt reiterates that note content is data, not commands.
- New env surface (`ANTHROPIC_API_KEY`, `SUFLER_AGENT_*`), a new console script
  (`sufler-agent`), and the `agent` extra are documented in `.env.example` and
  `pyproject.toml`.
- Revisit when: a second LLM provider is needed (the port already allows it); the
  agent needs write over Teams (new ADR); or `mcp`/FastMCP changes how schemas
  are generated (re-run the characterization test).
