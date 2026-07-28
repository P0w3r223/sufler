# ADR 0002 — Optional LLM prose layer

Date: 2026-07-23
Status: accepted
Author: P0w3r223
Related to: [[0001-structure-and-prompt-discriminator]], Powiadomienia_teams `agent/interpreter.py`

---

## Context

The tool must produce a per-day account of what a person did. Deterministic structured data
(prompts + commits grouped by day) is always available; a natural-language summary is a nicety that
requires an LLM. We must decide whether the LLM is core or optional, and how to keep it safe and testable.

## Decision

**The deterministic layer is primary; the LLM layer is optional** (`--llm` / `CLAUDE_SUMMARY_LLM`).
The larger Jira agent can consume the structured JSON directly; the prose is a convenience.

**Injected port for testability.** Prose generation depends on a narrow `LlmClient` protocol
(`complete(system, user) -> str`), so `summarize_day` is tested on an in-memory fake — no network,
no key. The Anthropic client lazily imports `anthropic`; a missing `agent` extra fails with a clear
`SystemExit`, not a raw `ImportError`.

**Graceful degradation.** No API key → the LLM layer is skipped with a note on stderr and structured
data is still returned. A per-day LLM error degrades only that day's prose to `None`, never the whole
report. Empty days short-circuit to a fixed message without calling the model.

**Content is data, not instructions.** Prompt and commit text are passed to the model strictly as
data. The system prompt instructs the model to ignore any instructions embedded in that text, and the
tool uses only the returned prose — mirroring the WorkMate/Powiadomienia_teams invariant against
prompt injection.

## Consequences

- Core install has no LLM dependency; `anthropic` is an opt-in extra.
- The tool is useful and fully tested without any API key or network.
- Prose quality depends on the chosen model (`CLAUDE_SUMMARY_MODEL`, default `claude-sonnet-5`).

## Alternatives considered

- **LLM as a mandatory step** — rejected: adds cost, non-determinism and a network/key dependency to a
  tool whose primary value (structured per-day facts) is deterministic.
