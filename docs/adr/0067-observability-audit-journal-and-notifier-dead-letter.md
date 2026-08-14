# 0067 — Observability: per-tool audit journal + notifier dead-letter and cursor-progress health

Date: 2026-08-13
Status: accepted (implemented — audit journal + notifier dead-letter, PR #48)
Author: P0w3r223
Related to: [ADR 0049](0049-usage-metrics-pseudonymized-counter.md) (pseudonymized-store pattern — copied 1:1),
  [ADR 0022](0022-proactive-dual-target-teams-push.md) (the at-least-once cursor invariant this must not break),
  [ADR 0040](0040-eventstore-to-mcp-session-cursor-read.md) (id-cursor as the only "since last look" mechanism),
  [ADR 0045](0045-poller-state-durability-and-graceful-shutdown.md) (atomic-write durability + "beat only after a good round"),
  [ADR 0066](0066-content-trust-classes-and-sticky-conversation-taint.md) (the trust-class field this journal records);
  upstream: `infra-docker-workmate/plan-workmate-2.0.md` §Faza 0

---

## Context

Faza 0 of the WorkMate 2.0 plan is **observability before capability**: four new capabilities (File,
websearch, note mutation, judge) are being added across ADR 0064–0066, and today we cannot answer
"how many times was tool X called last week, and how many times did it fail," nor reconstruct a turn
after an incident. This ADR builds that floor. Two **independent** gaps, both measured in code:

**Gap A — no per-tool audit journal.** `metrics.db` (ADR 0049) records a pseudonymized per-door call
*counter*, incremented once per turn in `ConversationalResponder._respond_sync`
(`responder.py:308-316`). It does not record *which tool* ran, with *what arguments*, to *what
result*. The judge (ADR 0065) and the trust-class escalation (ADR 0066) both require a per-operation
record; there is none.

**Gap B — a poison event blocks the whole notification stream, invisibly.** `EventNotifier.pump_once`
(`notifier.py:131-141`) reads a batch via `read_since(cursor)`, delivers each event, then advances
`self._cursor = event.id` and persists it via the injected `save_cursor` callback — **cursor advances
only after a successful send** (at-least-once, ADR 0022). On a send failure the exception propagates
out of `pump_once`, is caught and logged in `pump` (`:125-128`), and the cursor is **not** advanced —
so a permanently-undeliverable event re-delivers forever and every later event waits behind it. The
only event already handled locally is `ThreadRootGone` (`:172-180`, caught precisely so it "does not
block the whole stream"). Meanwhile the healthcheck is blind to this: `workmate-heartbeat-check`
(`heartbeat.py:main`) checks the age of a `.heartbeat` file written by the **poller** after a good
round (`poller.py:103-105`, R5) — the poller keeps beating while the notifier's cursor is stuck.

The design must honor two settled invariants: the **at-least-once cursor** (ADR 0022 §45-46: advance
only after a successful send — never lose an event) and the **pure runtime contract**
(`AgentRuntime` depends only on `LLMClient` + a `ToolSpec` catalog; "Anthropic and FastMCP do not
enter here"). Neither may be relaxed to get observability.

## Decision

Two parts at two concerns. §1 (audit) and §2 (dead-letter + health) are independent and ship
together only because both are Faza 0.

### §1 — Per-tool audit journal (app, buildable now, OFF by default)

1. **Copy the ADR 0049 store pattern exactly.** New `AuditStore` port (`core/ports/audit.py`),
   `SqliteAuditStore` adapter (own file at `WORKMATE_AUDIT_DB`, `WAL` + `busy_timeout` + a `Lock`,
   DDL in the constructor, atomic per ADR 0045), and an `AuditService` in the core that pseudonymizes
   before writing (**reuse `core/domain/metrics.pseudonymize`**, `sha256[:16]`). **OFF by default:**
   no `WORKMATE_AUDIT_DB` → no store built (wired in `agent_wiring.py` beside metrics, `:644-648`),
   and every write is **best-effort, never fatal** (a failed audit write logs a warning and the turn
   proceeds — the ADR 0049 §4 rule).

2. **The seam is an optional audit callback threaded through `run_turn`, consulted once in
   `_dispatch`** *(revised in place during implementation — see "Execution correction"; the original
   proposal below was a per-turn wrapper on `ToolSpec.fn`, rejected because it breaks argument
   coercion)*. The responder has the turn's identity (`message.sender_id`, `conversation_id`) and —
   once ADR 0066 lands — its trust class, but does not see individual tool calls; `runtime._dispatch`
   sees each call (name, arguments, result/error) but is deliberately context-free. The runtime gains
   **one optional parameter** `audit: Callable[[str, Mapping, str], None]` on `run_turn`, consulted
   once in `_dispatch` at the single point where name/args/status exist — additive exactly like
   `session_header`/`extra_tools`, covering base **and** per-turn tools. The turn context (pseudonym,
   conversation-ref, door, trust class) is closed **at the door**: the responder builds the recorder
   via `AuditService.turn_recorder`, which records `(pseudonym, conversation-ref, door, tool name,
   redacted-arg-projection, result status, trust class, judge verdict)` and pseudonymizes/stores;
   the runtime sees only an opaque callback and stays free of storage. **Why not the wrapper:**
   wrapping `ToolSpec.fn` breaks `AgentRuntime._coerce_arguments`, which introspects each tool's `fn`
   via `inspect.signature`/`get_type_hints` over string annotations, and the base catalog lives in the
   once-built shared runtime so it cannot carry per-turn context. `AgentRuntime` gains one optional
   param, not a dependency.

3. **Record actions and paths, never content — redact by default.** The recorded arguments are a
   **per-tool projection**: `action`, target `path`/note-id/issue-number and similar structural
   fields pass; every other field is reduced to `type+length`. A note body, a materialized file's
   bytes, and a raw `Bash` command **never** enter the journal — the same "we store the pseudonym and
   the door, not the message" stance as metrics. The projection is allowlist-based (record only named
   safe fields) so a new tool defaults to fully-redacted until its projection is written.

4. **The trust-class column dovetails ADR 0066.** The journal has a `trust_class` field; until ADR
   0066 lands it is written `"unknown"`, and the ADR 0065 judge writes its verdict into the same row.
   This is the field ADR 0066 §5 names as "the foundation for the Faza 0 `audit.db`."

### §2 — Notifier dead-letter and cursor-progress health (app + infra)

1. **Dead-letter is an addition to at-least-once, never a switch to at-most-once.** New
   `DeadLetterStore` port + `SqliteDeadLetterStore` — a `dead_letters` sibling table in the events DB
   (same `workmate-state` volume, same connection discipline), idempotent on `(source, event_id)`.
   `EventNotifier` counts delivery attempts per event; after **N** failed attempts it writes the event
   to `dead_letters` **and only then** advances the cursor. Ordering is load-bearing and mirrors
   compaction's "save summary *before* archive": the event is durably in `dead_letters` before the
   cursor moves past it, so a crash between the two re-delivers rather than loses (R1). This preserves
   ADR 0022 — nothing is dropped; a poison event is *quarantined with its reason*, not skipped.

2. **A notifier-owned heartbeat, beaten only on a productive round.** Today only the poller beats. Add
   a second heartbeat file written by the notifier after any `pump_once` that made progress —
   delivered ≥1, dead-lettered, or found the batch empty — mirroring the poller's R5 pattern
   (`poller.py:103-105`). A notifier stuck on a genuinely failing transport (backlog non-empty, zero
   progress, not yet at the dead-letter threshold) stops beating; its heartbeat goes stale. This is
   the plan's "cursor progress, not just pulse; no progress with a non-empty queue = unhealthy,"
   realized statelessly (no inter-check bookkeeping, no `MAX(id)` query).

3. **Infra follow-up (its own small change/ADR):** the compose healthcheck checks **both** heartbeat
   files; `preflight.sh` gains the missing `WORKMATE_METRICS_DB` gate and a new `WORKMATE_AUDIT_DB`
   gate, symmetric to the existing `EVENTS_DB`/`CONVERSATIONS_DB` gates. (The disk-space gate the plan
   groups here is Faza 1.)

## Risk register

| # | Risk | Mitigation |
|---|---|---|
| R1 | Dead-letter reads as silent event loss (at-most-once by the back door). | Write-to-`dead_letters` **before** advancing the cursor (crash re-delivers); the quarantined event is durable, carries its failure reason, and is surfaced — not skipped. ADR 0022 invariant intact. |
| R2 | Per-event attempt counter in memory resets on restart → a poison event is retried N times again every restart. | Persist `first_failed_at`/`attempts` (or accept bounded extra retries) — **open question**; either way the stream is unblocked after N within a run. |
| R3 | The audit wrapper captures content by accident (note body, `Bash` command). | Allowlist projection — unnamed fields redacted to `type+length`; a test asserts no raw body/command string appears in any audit row. |
| R4 | Audit erodes the pure runtime contract. | The runtime gains **one optional** `audit` callback param on `run_turn` (additive, like `session_header`), consulted once in `_dispatch`, defaulting to `None`. Turn context is closed at the door (responder/wiring); pseudonymization and storage stay outside. The runtime holds no audit dependency. *(Revised from the wrapper design — see Execution correction.)* |
| R5 | The notifier heartbeat beats on an empty queue, masking a stall. | Beating on empty is correct (nothing to do = healthy); a stall is non-empty **and** zero progress, which does not beat. |
| R6 | `audit.db` / `dead_letters` grow unbounded. | Retention deferred to Faza 7 (audit retention > conversations); `dead_letters` is small by construction. |
| R7 | MCP-door tool calls are not audited (they bypass the responder/per-turn-catalog seam, ADR 0049 §Alternatives). | Recorded as out-of-scope residual, like ADR 0049's deferred per-tool metrics; the agent (Teams) runtime is covered. A second seam for MCP is a later ADR if measured to matter. |

## Alternatives considered

- **Audit at the responder only (one row per turn, like metrics).** Rejected: loses the per-tool
  granularity the judge and incident-reconstruction need — the whole point of the journal.
- **Thread an audit sink into `run_turn`/`_dispatch`.** **Chosen (after implementation).** Originally
  rejected here in favor of a per-turn `ToolSpec.fn` wrapper — but that wrapper breaks
  `_coerce_arguments` (signature introspection over string annotations) and cannot carry per-turn
  context into the once-built base catalog. One **optional** `run_turn` param — additive like
  `session_header`, consulted once in `_dispatch` — records at the single point where name/args/status
  exist, with the turn context still closed at the door. See "Execution correction".
- **A per-turn wrapper on `ToolSpec.fn`.** Rejected during implementation: it breaks
  `AgentRuntime._coerce_arguments` and cannot be applied per-turn to the shared base catalog.
- **Dead-letter by advancing the cursor on any failure (at-most-once).** Rejected: breaks ADR 0022 —
  a transient transport blip would silently drop events.
- **Healthcheck compares `notify_cursor` (state.json) against `MAX(id)` in events.db.** Workable but
  needs inter-check state and timestamps to distinguish "backlog draining" from "backlog stuck"; the
  notifier heartbeat is stateless and reuses the proven poller pattern.

## Consequences

- **Buildable now, safe by default.** Audit is OFF without `WORKMATE_AUDIT_DB` and best-effort when
  on; with it off the transcript and behavior are byte-for-byte as today. Dead-letter is intrinsic to
  the notifier but only fires after N failures — a strict improvement over today's infinite block. The
  golden MCP surface is untouched (audit wraps the agent-runtime catalog, a Teams-door concern).
- **Foundation for Faza 4–6.** The audit row is where the ADR 0065 judge verdict and the ADR 0066
  trust class are recorded; per-tool call counts feed the File/websearch "measure first" gates.
- **Two invariants explicitly preserved:** at-least-once (ADR 0022) and the pure runtime (ADR 0008).
- **Infra follow-up tracked:** compose two-heartbeat check + `preflight` `WORKMATE_METRICS_DB` /
  `WORKMATE_AUDIT_DB` gates.

## Open questions (to close before code, as in ADR 0064–0066)

- **`Bash` argument redaction:** action-only, a salted hash of the command, or a redacted form? (The
  command *is* the action for `Bash`, so "paths and actions, not content" needs a concrete rule.)
- **Attempt-count persistence across restart (R2):** persist, or accept bounded extra retries?
- **Dead-letter threshold N** default (and per-source override?).
- **Audit the MCP door** (second seam) or leave out-of-scope (R7)?
- **Conversation reference in the journal:** pseudonymize `channel/external_id`, or store raw for
  incident correlation? (Faza 7 privacy leans pseudonymized.)
- **Salt for the actor/conversation pseudonym (review Faza 0):** `pseudonymize` is `sha256[:16]`
  *without* salt (1:1 with ADR 0049), so it is dictionary-reversible over the known pion AAD id set
  (tens of people). No new exposure today — `conversations.db` already holds verbatim transcripts
  beside it. But when Faza 7 gives `audit.db` a **longer** retention than conversations, the audit
  outlives the transcripts while the mapping stays trivially reversible. Should the audit pseudonym
  then take a deployment-secret salt (so the journal survives conversations but the mapping does not)?

## Follow-ups

- On acceptance: implement §1 and §2 in a follow-up PR (ADR-first, per the 0062–0065 precedent), on a
  branch off `Main` (this is independent of the ADR 0064/0065 File-tool work).
- Infra ADR/change for the compose two-heartbeat healthcheck and the `preflight` metrics/audit gates.
  **Timing invariant to encode there (review Faza 0):** with defaults `max_attempts=5 × poll_interval
  =60s = 300s` to quarantine, but the notifier heartbeat may go stale at `--max-age=180s`, so a single
  permanently-undeliverable event yields ~2 min of `unhealthy` before self-heal. Worse, if anything
  restarts on `unhealthy` (operator, a future autoheal), the in-memory `_attempts` resets to zero and
  the event never reaches N — the exact block §2 removes. The infra change must satisfy
  `max_attempts × poll_interval < notifier max_age` (or give the notifier heartbeat its own larger
  `--max-age`), and should consider a dead-letter **volume** threshold as an escalation signal rather
  than only `logger.error`. Today compose has no autoheal, so this is tuning, not an outage.
- Wire `trust_class` from real values when ADR 0066 lands; wire `judge_verdict` when ADR 0065 lands.
- Retention for `audit.db` (longer than conversations) in Faza 7 — see the salt open question above.
- **Read paths for the write-only stores (review Faza 0):** `AuditService.recent` and
  `DeadLetterStore.recent` have no caller yet; without a follow-up surface (an `/audyt` command or a
  CLI) the journal and quarantine sit in a file nobody opens. Acceptable as a Faza 0 floor, tracked here.

## Execution correction (2026-08-13) — implemented, two premises adjusted against the code

§1 and §2 were implemented on `feat/adr-0067-faza0-observability`; two design statements above did
not survive contact with the code and were changed in place (house rule: measure, then correct).

1. **The audit seam is a recorder threaded through `run_turn`, not a wrapper on `ToolSpec.fn`
   (Decision §1.2 / R4 revised).** Wrapping `fn` breaks `AgentRuntime._coerce_arguments`, which
   introspects each tool's `fn` via `inspect.signature` / `get_type_hints` over string annotations
   (`from __future__ import annotations`); a wrapper's signature/globals do not resolve `date`,
   `Literal`, etc., so argument coercion fails. Independently, the **base** catalog lives inside the
   shared, once-built runtime, so it cannot be wrapped with *per-turn* context. The buildable seam is
   an optional `audit: Callable[[str, Mapping, str], None]` parameter on `run_turn`, consulted once in
   `_dispatch` — **additive, exactly like `session_header`/`extra_tools`**, covering base + per-turn
   tools at the single point where name/args/status exist. The turn context (pseudonym, conversation,
   trust class) is still closed at the door (responder builds the recorder via
   `AuditService.turn_recorder`); the runtime sees only an opaque callback and stays free of
   pseudonymization and storage. R4 holds — the runtime gained one optional param, not a dependency.
   The redaction still lives in `core.domain.audit.project_arguments` (allowlist, R3 intact).

2. **Dead-letter absence now returns instead of raising (§2.1 clarified).** When no `DeadLetterStore`
   is wired, `pump_once` retains the old net behavior (leave the cursor, retry next round — poison
   blocks) but **returns early rather than propagating the exception**, so `pump_once` owns the
   heartbeat decision (a stuck round does not beat). One existing test that asserted `pump_once`
   *raises* on send failure was updated to assert the return-based contract; the at-least-once
   invariant is unchanged. `first_failed_at` (R2) shipped as `failed_at` = quarantine time, since the
   in-memory attempt counter does not know the first-failure moment.

**Gates green (local, POSIX — the production platform):** `ruff check src/ tests/` clean;
`mypy src/workmate` 173 files clean; full `pytest -n auto` green (1 skip); §1 targeted 100 passed,
§2 targeted 46 passed. New probes verify each claim against pre-fix code (no raw content in audit
rows; cursor held below threshold and no beat; dead-letter after N then cursor advances and beats;
two-heartbeat health unhealthy when the notifier heartbeat is stale). **Infra follow-up still owed:**
compose two-heartbeat healthcheck + `preflight` `WORKMATE_METRICS_DB`/`WORKMATE_AUDIT_DB` gates.

## Review corrections (2026-08-13) — code-review of the Faza 0 branch, fixes applied

A structural code review of `feat/adr-0067-faza0-observability` confirmed the at-least-once invariant
holds (record → cursor → save, exception-safe) and that redaction keeps content out. Findings applied
on the branch:

1. **(HIGH) The notifier heartbeat now beats on a *partially* productive round.** The two early
   `return` paths (no dead-letter store; below-threshold retry) skipped the end-of-round beat even
   when the round had already advanced the cursor on an earlier event — so a catch-up round that
   delivered N events but hit a snag on the next one reported `unhealthy` despite real progress
   (contradicting §2.2 "delivered ≥1"). Fix: `_beat_if_progress(sent)` beats before both early returns
   iff `sent > 0`; a true stall (`sent == 0`, first event stuck) still does not beat. Regression test
   `test_partial_progress_round_beats_despite_later_retry`.
2. **(MED) Rejected tool calls are now audited.** `_dispatch` returned before the audit seam for an
   unknown tool and for argument-validation failures, so an attempt to reach a tool behind a gate
   (shell OFF, `notes_read` after ADR 0062) left no trace — exactly the "how often did tool X fail"
   signal Faza 0 wants. Both paths now call `audit(name, raw_args, "rejected")`; raw args are still
   redacted by `project_arguments`.
3. **(MED) The redaction allowlist was reconciled with the real tool catalog.** `name`, `file_format`,
   `image_format`, `week`, `since`, `until` are real structural parameters that were falling to
   `<str:N>`; added. Content fields (`content`/`body`/`command`/`image_base64`/`title`/`query`) stay
   off the list. (Direction of the old miss was safe — over-redaction — but it defeated §1.3's "paths.")
4. **(MED) Coverage added for the two seams that had none:** responder→runtime
   (`test_audit_recorder_built_per_turn_and_passed_to_runtime` — the recorder is built on
   door/raw_user/conversation and threaded into `run_turn`) and dead-letter **ordering**
   (`test_dead_letter_recorded_before_cursor_moves` asserts the `["dead_letter", "cursor"]` sequence,
   not just end state; `test_cursor_stays_when_dead_letter_record_raises` proves a failed quarantine
   leaves the cursor put).
5. **(LOW)** `pump_once` docstring clarified ("events that moved the cursor", not "pushed"); dead
   `if TYPE_CHECKING: pass` removed from `core/ports/dead_letters.py`.

Deferred (tracked above, not code on this branch): the `max_attempts × poll_interval < max_age`
timing invariant (Follow-ups, an infra change), the pseudonym-salt question (Open questions, Faza 7),
and read surfaces for the write-only stores (Follow-ups).
