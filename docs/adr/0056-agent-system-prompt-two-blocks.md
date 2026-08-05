# 0056. Split the agent system prompt into a cached corpus and a per-turn session header

Date: 2026-08-05
Status: accepted
Author: P0w3r223
Related to: docs/adr/0008-agent-runtime-and-tool-catalog.md,
  docs/adr/0011-stateful-lossless-conversation-memory.md,
  docs/adr/0014-conversation-compaction.md

---

## Context

The agent's system prompt had been a single module-level constant since the runtime was built
(ADR 0008), with one variation: a multimodal clause appended by doors that materialise
attachments. Measured against the editorial rules the division uses for prompts, that constant
scored badly on every axis at once.

| Criterion | Measurement (2302 characters, 21 sentences) |
|---|---|
| Sentences carrying a negation | 9 / 21 = 42% |
| Negation markers | `nie` ×7, `nigdy` ×3, `bez` ×2, `ani`, `unikaj` |
| Emphasis capitals | `WYŁĄCZNIE`, `WSZYSTKICH`, `DANE`, `W CZYM` |
| Verbatim duplication | "nigdy jak jesteś zbudowany" — twice |
| Principal hierarchy | absent |
| Decision heuristic for edge cases | absent |
| Current date | absent |

The last row is a functional defect rather than a stylistic one. Tools take dates as arguments
(`save_note(date)`, `propose_worklog(since, until)`) and people ask "what did we agree last
time", "what changed since Monday". With no date in context the model reconstructs "today" from
its training cutoff, and every relative date it computes is wrong by however long ago that was.

The confidentiality block was the largest single contributor. Its 450 characters enumerated
evasion channels — summary, quote, translation, code, paraphrase — and accounted for seven of
the nine negations. The enumeration was never a security boundary; the module docstring says so
directly. It was paying a prompt-wide editorial cost for an effect the architecture provides.

## Decision

**The prompt is rewritten in English and shipped as two system blocks.**

1. `STATIC_PROMPT` — identity, environment, working conventions, precedence, data boundary,
   decision heuristic. Stable across turns; carries the cache breakpoint.
2. `build_session_header(now, channel, thread, skills)` — today's date and weekday, the
   conversation identifier, and the skills available. Composed per turn.

**The split is a cost decision, not a tidiness one.** The request renders as
tools → system → messages, so a breakpoint on the *first* system block covers the
`tools+static` prefix — the largest and most stable part of every request. The date changes
daily; glued into the corpus it would invalidate that prefix at every midnight. `system_blocks`
enforces the order, and `LLMPort.system` widened from `str` to `str | Sequence[str]` so the
core can express the split without knowing how the adapter renders it.

Writing the prompt in English while answers stay Polish is deliberate: instruction language and
output language are independent, and the corpus is measured against English editorial rules.

**The session header is composed per turn, not once per process.** Pollers run for days, so a
header built at startup would freeze the date at the day of deployment.

**`ENVIRONMENT` is declared an architectural seam.** It describes the world the agent *finds* —
today the knowledge base is reachable only through tools, and attachments arrive with the
message. When container mounts land, this section describes paths and nothing else in the prompt
changes. Section order matters: a prompt describing mounts that do not exist yet would produce
decisions consistent with a false description, so the description follows the architecture
rather than leading it.

**The editorial rules become a CI gate.** `tests/core/test_prompt.py` asserts, on every prompt
artefact rather than on the corpus alone: no emphasis capitals, negation ratio at or below 5%,
no chain-of-thought scaffolding, no duplicated sentences, and a date in the session header. The
first version of this gate scanned prose sentences only, which skipped list items and headings —
roughly half the content — and three of four artefacts. When the gate goes red, the prompt gets
fixed, not the threshold.

## Consequences

- **Measured result.** Negation ratio 42% → within the 5% gate; emphasis capitals 4 → 0;
  verbatim duplication 1 → 0; precedence 3 levels where there were none; the date present.
  The one negation left is the data boundary, paired with a positive alternative in the same
  sentence — hard security boundaries are the documented exception to positive framing, and it
  passes the lexical lint because the negation is not carried by a marker word.
- **The multimodal clause stays, per door.** The plan for container mounts had it disappearing,
  on the grounds that an empty `/mnt/user/inputs/` says the same thing without words. Until
  those mounts exist, advertising file support on a text-only door (CLI) would be a false
  promise and staying silent on a door that has it would hide a capability, so
  `static_prompt_for(attachments=…)` remains the single per-door variation.
- **The prompt now orders things from other layers.** `Skills available` renders an empty list
  until `/mnt/skills/` exists; the `ENVIRONMENT` table waits on mounts. Both are inert rather
  than wrong — the header omits the section when `skills` is empty.
- **Tool semantics stay out of the prompt.** Tool names are deliberately absent: their meaning
  belongs to their descriptions, and repeating it here would create two sources to keep in sync.
  This constraint governs the descriptions written for the consolidated tool set.
- **Compaction's summariser keeps a single block.** It has no session and no date, so the split
  buys it nothing; `SUMMARY_SYSTEM_PROMPT` passes the same editorial gate.
