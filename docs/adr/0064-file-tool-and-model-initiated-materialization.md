# 0064 — A first-class `File` tool and model-initiated materialization

Date: 2026-08-12
Status: accepted (owner decisions 2026-08-14 — built despite the measurement; actions `read`/`write`/`edit`/`delete`)
Author: P0w3r223
Related to: [ADR 0008](0008-agent-runtime-and-tool-catalog.md) (tool catalog),
  [ADR 0009](0009-meeting-note-flow-and-write-surface.md), [ADR 0016](0016-user-multimodal-attachments.md)
  (attachment materialization), [ADR 0056](0056-agent-system-prompt-two-blocks.md) (prompt),
  [ADR 0057](0057-shell-executor-container-without-network.md) (executor boundary),
  [ADR 0061](0061-consolidated-tool-surface-upstream-pointer.md) (five-tool criterion);
  upstream: `docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md`,
  `docs/przebudowa-harnessu.md` §6 stage 4 (`File(read)` conditional) in `infra-docker-workmate`
Sibling: ADR 0065 (mutable knowledge base + Sonnet judge) — the `File(write/edit)`-into-notes half.

---

## Context

The owner asks for a first-class `File(action=read/write/edit)` tool, because the shell in the
executor cannot read a PDF or other binary/foreign format a user hands over, and because the agent
today has no way to reach a user's file at all once its bytes leave the attachment path.

This proposal is bounded by a criterion this series already decided and cites in code
([ADR 0061](0061-consolidated-tool-surface-upstream-pointer.md)): **a typed tool exists only where
the shell in the executor cannot reach** — no network, no state volume, *no path into the model's
context*, or an effect outside the container. That same ADR records that `File(write/list)` and
`Skill(name)` were deliberately *not* built, because they are use cases of `Bash`. So "add a `File`
tool" cannot be taken at face value; each action must be shown to cross a barrier the shell does not.

Per-action analysis against the criterion:

| action | can the executor shell do it? | verdict |
|---|---|---|
| `read` a binary **into the model's context** as a native `image`/`document` block | **no** — `Bash` returns text, capped at 64 KB (ADR 0057), and cannot emit a multimodal block | **crosses the barrier — this ADR builds it** |
| `read` → **extracted text** (HTML→text, PDF→text, docx) | yes — `cat`/python in the shell | a shell command (`sufler-extract`), not a typed tool — see below |
| `write`/`edit` in the **scratchpad** | yes — the scratchpad is rw in the executor | a use case of `Bash` — not built here |
| `write`/`edit` targeting the **knowledge base** (notes are `ro` to the shell) | **no** | crosses the barrier, but reopens the mutation vector ADR 0057 closed → **deferred to ADR 0065** |

So this ADR builds exactly the half that is uncontested by the criterion: **model-initiated
materialization of a user file into the model's context**, plus the extraction plumbing that feeds it.
The `write`/`edit`-into-notes half — which reverses the create-only, secure-by-default posture of
[ADR 0003](0003-note-schema.md)/[0057](0057-shell-executor-container-without-network.md) — is a
separate security decision and lives in ADR 0065.

### Two facts the design must stand on (measured, not assumed)

- **Attachments have no filesystem representation.** `AttachmentMaterializer` (ADR 0016) produces
  neutral content blocks stored in the `blocks_json` column of `conversations.db`, which lives on the
  `workmate-state` volume — a volume the executor deliberately does not mount ([ADR 0057](0057-shell-executor-container-without-network.md)).
  For `File(read)` to have a file to read, the input must first be materialized onto a path the shell
  sees. With per-conversation executors (infra ADR 0012, merged), the application can stage the file
  into the conversation's scope subdirectory of the scratchpad volume before `ensure(scope)`, and the
  executor mounts exactly that subpath under `/home/scratchpad/<scope>`. **This requires the compose
  wiring that mounts the shared scratchpad volume into the application** (`SUFLER_WORKSPACE_DIR` =
  the scratchpad root) — an infra change tracked with the ADR 0012 deployment.

- **A tool result cannot carry the binary, and would not survive if it could.** The Anthropic
  `tool_result` block accepts text and `image` content, but **not `document` (PDF)** — document
  blocks are only legal in user-turn content. And `tool_result` content is cleared by context editing
  ([ADR 0058](0058-context-editing-and-absolute-compaction-threshold.md), `clear_tool_uses`), so a
  binary returned through a tool result would be non-durable across turns. Therefore `File(read)`
  **does not return the binary through its tool result.** It materializes the file into the **next
  user turn's content blocks** — the same neutral `Attachment` carrier the door already uses (ADR 0016,
  `_attachment_block`) — and its tool result carries only a short confirmation
  (`materialized <name> (<type>, N bytes); it will appear as an attachment`). User-turn attachment
  blocks are degraded only at compaction ([ADR 0014](0014-conversation-compaction.md),
  `_describe_attachment`) and are re-materializable, so the model can re-`read` after a loss.

## Decision

1. **Build a typed `File` tool.** Its action set is `read | write | edit | delete`
   (owner decision 2026-08-14 — see *Measurement* below). This ADR owns **`read`**: it materializes a
   file from the conversation's scratchpad scope into the model's context. `read` is scoped in a
   closure over the `WorkspaceScope` (mirroring `build_workspace_catalog`); the tool never takes an
   absolute path from the model. The three mutating actions target the **knowledge base** — their
   gate, judge, confirmation and blast-radius controls live in **ADR 0065**, which the owner chose as a
   generic `File` channel rather than a typed `Notes(action=…)` one. Scratchpad writes remain a `Bash`
   use case (ADR 0061); `File(write/edit/delete)` is not a second door to the scratchpad.
   One `ToolSpec`, one `Literal`, one runtime seam — split ownership across two ADRs, not two tools.

2. **Materialize into the next user turn, not the tool result** (see Context, fact 2). Images become
   `image` blocks, PDFs become `document` blocks, Word/HTML/other formats become extracted-text blocks
   — reusing the neutral `Attachment` carrier and `_attachment_block` from ADR 0016. base64 is a
   consequence of this path (the multimodal block *is* the base64), never a raw string in a tool result.

3. **Extend materialization coverage with an HTML extractor and anti-masking budgeting.** HTML gains a
   new `extract_html` in `BINARY_EXTS`/`_BINARY_EXTRACTORS` (`document_text.py`), using the stdlib
   `html.parser` (no heavy dependency) — **not** a `TEXT_EXTS` entry, which would decode raw markup as
   text. For the "a decorative image occupies ~95% of the page, the real content is small text" case,
   HTML text is extracted *and* embedded images are surfaced as data under a **surface budget**, so a
   dominant image cannot crowd the text out of the materialization budget. File content stays **data,
   never commands** ([CLAUDE.md](../../CLAUDE.md), ADR 0016) — anti-masking is an extraction-quality
   concern, not a gate.

4. **Text extraction that the shell *can* do stays a shell command.** A `sufler-extract` command in
   the image (patterned on `sufler-search`/`sufler-render`) exposes HTML→text and the existing
   `extract_pdf`/`extract_text_from_path` (today wired only for the seed corpus, ADR 0050) to
   model-initiated extraction from the shell. This keeps text extraction off the typed surface
   (criterion, ADR 0061) while spanning the gap the owner named.

## Measurement — run 2026-08-14, and what the owner decided against it

`przebudowa-harnessu.md` §6 stage 4 makes this stage begin with a measurement: *is there a real case
where the model must fetch a file itself, given the door already materializes up to 20 attachments per
message?* It was run twice on the live `conversations.db` (`workmate-state` volume) over the window
**2026-07-31 → 2026-08-14**. `teams_graph` is the only door that holds conversations at all (`github`
is ingest-only), so this is the whole population, not a sample:

| Signal | Measured | What it says about the tool |
|---|---|---|
| user turns / conversations | 45 / 13 | the denominator is small — a signal, not a proof |
| messages carrying an attachment | **4**, every one a **singleton** | the door's cap of 20 was never approached; the over-limit case has not occurred |
| formats seen | 1 `image/jpeg`, 1 `application/pdf`, 2 `text/plain` (one of them an in-band `_note` status block) | inside today's coverage; no HTML, no rejected format observed |
| compaction rounds (ADR 0014) | **0** | `_describe_attachment` — the degradation this ADR cites as the loss `File(read)` would repair — **has never fired in production** |
| materialization failures in the door log | **0** across 56k lines since 2026-08-13 | no user has yet handed the bot a file it could not take in |
| tool calls | 11 `Bash` (all from the 2026-08-12 shell probe), rest typed, **0 errors** | no shell-side file-handling friction either |

**The measurement does not meet the stage-4 condition.** Read literally, §6 stage 4 says the tool is
then not built and the surface stays at five.

**Owner decision, 2026-08-14: build `File` anyway, with `read`/`write`/`edit`/`delete`.** This is
recorded here the same way ADR 0065 records its security reversal — as conscious consent, not as a
finding. What is being accepted without a demonstrated case: one more tool description in the cached
`tools + system` prefix of every turn (ADR 0056), a new runtime seam (a tool turn injecting an
attachment into the *next* user turn), and the maintenance surface of four actions. The reasoning the
owner is acting on is forward-looking — the traffic measured above is from a fleet where the shell was
off, attachments are rare because the bot visibly cannot do much with them, and the capability is
wanted before the demand rather than after it.

**Follow-up that keeps this honest:** re-measure after the shell has lived a month on prod. The
reference date was left implicit while it was still obvious, so it is written down here before it
stops being: the shell and the per-conversation executor went live on **2026-08-20** (activation
card steps 4-5, image 1.12.1), which puts the re-measure at **~2026-09-20** (same query, plus
`File` call counts from `audit.db`, ADR 0067). If `File` is unused by then, the honest
move is removal, not silence — a tool that costs prefix bytes every turn and buys nothing is exactly
the scaffolding this project deletes.

### Interim reading, 2026-09-04 — the numbers exist, and they cannot yet falsify anything

`audit.db` (ADR 0067) now holds the tool-call counts the follow-up asks for. They are recorded here
early because the shape of the sample matters more than the counts, and that shape will not be
visible on 2026-09-20 unless it is written down now.

Window **2026-08-20 → 2026-09-02**, `teams_graph` (the only door that records tool calls),
**62 calls, 10 conversations, one actor, 0 errors, every status `ok`**:

| Tool | Calls |
|---|---|
| `Bash` | 40 |
| `Activity` | 10 |
| `Schedule` | 5 |
| `Jira` | 4 |
| `Project` | 2 |
| **`File`** | **1** |

Read naively this inverts the plan's thesis: the shell — the candidate for removal — dominates, and
`File` sits at the removal threshold this ADR set for itself. **That reading does not hold.** The
traffic is not spread across the window:

| Day | Calls | Conversations |
|---|---|---|
| 2026-08-20 | 7 | 3 |
| 2026-08-21 | 54 | 6 |
| 2026-09-02 | 1 | 1 |

61 of 62 calls fall on the two days the shell was activated and smoke-tested, followed by **twelve
days of near-silence**. The single `File` call is itself part of that testing — its target was
`biap/smoke-test/2026-07-31-smoke-test-wdrozenia`. So the counts measure **who exercised what during
activation**, not what the tool is worth in use. `Bash`'s 40 is an activation artifact for the same
reason `File`'s 1 is: nobody has yet used this fleet in anger.

**Consequence for the 2026-09-20 re-measure:** run as scheduled, it would produce these same numbers
with a longer denominator and invite the same false conclusion. The follow-up therefore gains a
precondition it did not have: **the re-measure is only decisive if the window contains real traffic
— at least 20 user turns from at least 2 distinct actors, outside activation and smoke-test
conversations.** Below that, the honest verdict is *not measured*, and the date moves; it is not
evidence for removal. A tool cannot be falsified by an absence of users.

## Options considered

- **Return the binary through the tool result** (rejected): the `document` block is illegal there and
  the content is cleared by ADR 0058 — non-durable by construction (this ADR's Context, fact 2).
- **Materialize text via the typed tool** (rejected): text extraction is a `Bash` use case under the
  ADR 0061 criterion; a `sufler-extract` command carries it without a typed-surface cost.
- **A generic `File` covering scratchpad write/edit** (rejected here): scratchpad writes are `Bash`;
  only notes-targeted mutation crosses the barrier, and that is the security reversal deferred to 0065.

## Consequences

- **Surface cost.** One `ToolSpec("File", action: Literal[...])` adds its description to the cached
  `tools + system` prefix every turn (ADR 0056). Capability gates encode in the `Literal`, not the
  function body (ADR 0009). The A–D surface golden (`test_mcp_tool_surface.py`) is untouched **only if
  `File` is an agent-only tool** (factory / `extra_catalog`, never a `register_*` on FastMCP); add a
  negative assertion that `File` is absent from the MCP surface.
- **New runtime seam.** A tool turn must be able to inject an `Attachment` into the *next* `UserText`.
  This is a smaller change than turning `ToolOutput.content` into blocks: the tool returns a
  confirmation string plus a queued attachment the responder/runtime attaches to the following user
  turn. `selection.py` and the core stay provider-neutral; the Anthropic block schema stays in the
  outbound adapter (ADR 0016).
- **Infra dependency — satisfied since the 1.10.0 compose (verified 2026-08-14).** `File(read)`
  end-to-end needs the shared scratchpad volume mounted into the application; the `x-sufler` anchor
  now sets `SUFLER_WORKSPACE_DIR: /home/scratchpad` and mounts `workmate-scratchpad` there, and the
  `teams-graph` service repeats both in its own `environment`/`volumes` (YAML merge replaces the map,
  it does not merge it). No separate infra ADR is needed for the mount; what remains is deploying the
  1.10.0 image, which is where ADR 0012 lands anyway.
- **Limits reuse `AttachmentLimits`** (ADR 0016): `max_bytes`, `max_total_bytes`, `max_image_edge`,
  `_MAX_IMAGE_PIXELS`, plus a format allowlist. HTML adds `html.parser` only (stdlib).
- **Gates.** Negative probes as in this series (a gate that passes everything looks like one that
  works, ADR 0009): `File` absent from the MCP golden; `sufler-extract` yields the same bytes as the
  in-process extractor; a materialized `File(read)` block survives a compaction round (ADR 0014) and is
  re-materializable; over-limit degrades to an in-band note, never a crash.

## Closed questions — decisions of 2026-08-14

- **Does the measurement justify the tool?** No — and the tool is built regardless, by owner decision.
  See *Measurement* above, including the re-measure follow-up that keeps the decision falsifiable. The interim reading of 2026-09-04 adds the precondition that
  makes the re-measure worth running: without real traffic in the window, the verdict is *not
  measured*, not *remove*.
- **Formats `read` materializes.** Images (png/jpeg/gif/webp), PDF, docx, xlsx/pptx (already extracted
  to text), plain text, and **HTML** (new `extract_html`). Ceilings **reuse the `AttachmentLimits`
  values** unchanged (`max_bytes`, `max_total_bytes`, `max_extract_bytes`, `max_image_edge`,
  `max_count`). Over-limit degrades to the same in-band `_note`, never a crash.
- **Which message's budget does a pull charge?** The door's budget is stateless: it is computed inside
  a single `materialize()` call over one `ChannelMessage`'s references, so there is no running counter
  to debit — and `File(read)` materializes into a *different* (later) user turn than the one the model
  was reading. So the rule is stated in terms that exist: **the pulled attachments are budgeted
  together with whatever the door is materializing into that same next user turn**, one budget
  computed once for that turn (door pushes first, model pulls second, the remainder is what the pull
  may spend). The limits themselves move out of the Teams-door settings into a place the tool can also
  reach — today `AttachmentLimits` is constructed from `teams_graph` settings (`teams_graph/app.py`),
  which is the door, not the agent. That relocation is part of building this tool, not a detail.
- **Anti-masking surface budget.** Extracted **text has absolute priority**: it is materialized first,
  up to `max_extract_bytes`. Embedded images from an HTML file are surfaced **only from the budget
  left over**, at most **3**, and never displace text; if the text alone exhausts the budget the
  images are dropped with an in-band note naming how many. This is what makes the owner's case — a
  decorative image occupying ~95% of the page — unable to crowd out the small real content. It is an
  extraction-quality rule, not a security gate (file content stays data either way).
- **Does `File` hide when the shell is present?** **No — it stays on the surface in both layouts.**
  `build_workspace_catalog` is built **only when `shell_factory is None`** and therefore **disappears
  once the shell is present** (`agent_wiring.py:639-643`) — scratchpad file operations are `Bash` use
  cases, so the typed workspace tools step aside for it. Neither `read`-into-context nor notes mutation
  is a shell use case (notes are `ro` to the executor, ADR 0057), so `File` does **not** follow that
  rule and stays on the surface in both layouts.
- **`write`/`edit`/`delete` scope.** Owner chose the **generic `File` channel** over a typed
  `Notes(action=…)` one (2026-08-14). The actions live in this tool's `Literal` and share this
  runtime seam; their gate, judge, confirmation and risk register are **ADR 0065**.
- **MCP surface.** `File` is **agent-only** (factory / `extra_catalog`, never `register_*` on
  FastMCP); the A–D golden `test_mcp_tool_surface.py` gains a negative assertion that `File` is absent.
