# 0065 — Mutable knowledge base, judged by a model instead of blocked by a rule

Date: 2026-08-12
Status: accepted (owner decisions 2026-08-14 — generic `File` channel, `delete` in scope, `confirm` = human checkpoint in the thread)
Author: P0w3r223
Related to: [ADR 0003](0003-note-schema.md) (note schema, create-only),
  [ADR 0006](0006-write-capability-gate-2.md), [ADR 0025](0025-teams-notes-write-gate.md),
  [ADR 0042](0042-meeting-note-sender-authorization.md) (sender authorization),
  [ADR 0043](0043-async-meeting-note-with-thread-callback.md),
  [ADR 0047](0047-two-pass-grounded-meeting-note.md) (independent LLM critic pattern),
  [ADR 0048](0048-thread-note-capture-from-teams-mention.md),
  [ADR 0057](0057-shell-executor-container-without-network.md) (security by lack, not by filtering);
  upstream: `docs/decyzje/0008-odwracalnosc-bazy-wiedzy.md` (reversibility = volume backup)
Sibling: ADR 0064 (`File` tool + read-materialization). This ADR owns the `write`/`edit`-into-notes half.

---

## Context

The owner asks for `File(write/edit)` that can also target the knowledge base, and — instead of a
deny-list that hard-blocks destructive commands — a **separate Sonnet call that judges whether a
mutating operation is harmful**, an LLM-judge rather than a rule.

Two facts frame this as a deliberate reversal, not an incremental feature:

- **Today nothing can destroy the knowledge base.** The only writer is a create-only `os.link`
  (`markdown_notes_writer._atomic_create`): it publishes atomically only when the target does not
  exist, and raises on collision. There is **no delete and no overwrite path anywhere.** The executor
  mounts notes `ro` ([ADR 0057](0057-shell-executor-container-without-network.md)), so the shell
  cannot damage them by construction, and the Teams doors expose no `save_note`
  (`enable_write=False`). Reversibility is a volume copy (`backup-notes.sh`, infra ADR 0008).

- **ADR 0057 is the exact antagonist of a model-judge.** It states, and code cites 16×: *"The executor
  does not inspect command content. Judging a shell string is unreliable; the security here comes from
  what the container lacks."* A model-judge is the philosophical inverse of that. It therefore
  **cannot be the boundary for the shell** — the `ro` mount already is. The judge is only meaningful
  once we *introduce* a mutation vector, and the only vector that crosses a barrier the shell cannot
  (per ADR 0061's criterion) is `write`/`edit` **into notes**. So the owner's two asks —
  `File(write/edit)`-into-notes and the Sonnet judge — are **one linked decision**: introduce
  note mutation, and guard it with a model rather than a rule.

This ADR records that reversal with its risks stated as conscious consent, and specifies the judge as
**defense-in-depth on top of** the existing hard controls, never instead of them.

## Decision

**Introduce a bounded, judged note-mutation path; keep every existing hard control underneath it.**

1. **One application-layer choke-point.** All mutation flows through a single gate wrapping the
   `save_*` methods before the `NotesWriter.write` port (the real single choke-point — every
   `save_note`/`save_meeting_note`/`save_thread_note` calls it). The judge and the pre-write backup
   live in that application gate — **not** in `core/domain/authorization.py`, which is pure and has no
   I/O and covers only the meeting path (`can_write_meeting_note`). The **generic `File(write/edit/
   delete)`** surface (owner decision 2026-08-14, ADR 0064) enters through this same gate and no other
   — the tool is a caller of the choke-point, never a second writer beside it.

2. **Layers below the judge, in order, all fail-closed:**
   - **Sender authorization (AAD, ADR 0042)** — resolved before the judge; an unrecognized sender is
     refused with no model call. The judge never widens who may write.
   - **Path/schema confinement** — the frozen note-id/company/project derivation (`paths.py`,
     ADR 0003/0005) and `reject_dangerous_content` in the application gate, plus anti-traversal
     `_resolve_within` **in the adapter**, where it already lives (it is a private helper of
     `markdown_notes_writer`, duplicated in the workspace/outbox adapters; `core/` cannot import it and
     `lint-imports` enforces that). Consequence for implementation: **every new port verb re-uses
     `_resolve_within` inside the adapter** — the guard does not move up into the gate, so a `delete`
     verb that forgets it would be unprotected. A mutation cannot land outside `notes_dir` or fabricate
     a company/project the registry does not know.
   - **Pre-mutation snapshot** — a backup of the target (and cadence-based full-volume backups,
     infra ADR 0008) *before* any destructive write. Reversibility stops being structural (create-only)
     and becomes backup-dependent (see R2) — so the backup is load-bearing, not a safety net.
   - **Single-file, non-recursive** — one note per operation; no directory deletes.

3. **The judge is an independent Sonnet critic, modeled on the working `verify` pass** (ADR 0047,
   `AnthropicMeetingSummarizer.verify`): a separate call with a forced `tool_choice` verdict schema,
   `thinking` disabled, `model = claude-sonnet-5`. **Input:** the proposed operation
   (`write`/`edit`/`delete`), target note path, the diff / new content, the requester's intent, and the
   note metadata — **treated as data, in its own prompt-injection boundary** (the content being judged
   never becomes instructions to the judge). **Output:** `verdict ∈ {allow, refuse, confirm}` +
   `reason`. The judge **fails toward refusal** (any error or ambiguity → refuse).

4. **Mutation is create-only-safe where determinism is load-bearing.** `edit`/overwrite and `delete`
   are offered only on the title-derived, suffixed `save_note` path. The deterministic meeting/thread
   paths (`-mtg-`/`-thr-` ids) **stay create-only** — they rely on the `os.link` collision
   (`FileExists → NoteExistsError`) as an idempotency guarantee (ADR 0043/0048); a probabilistic judge
   plus write-new+swap must not weaken that (R8). `edit` is read→replace→atomic-write-new+swap with
   the pre-mutation snapshot retained — never in-place without a snapshot.

5. **`delete` exists, and is the narrowest thing that can be called deletion** (owner decision
   2026-08-14; the safer option of shipping `edit` only was offered and declined). One note per call,
   **never a directory, never a glob, never recursive**; a pre-deletion snapshot is written first and
   the operation fails closed if the snapshot cannot be written. The removed note is recoverable from
   that snapshot and from the nightly volume backup (below) — deletion is therefore *reversible by
   procedure*, which is the entire basis on which the create-only invariant is being given up (R2).

6. **`confirm` is a human checkpoint in the Teams thread** (owner decision 2026-08-14). On a `confirm`
   verdict the bot does not mutate: it states plainly what the operation would change and waits. The
   request is *announced* and refused; it succeeds only when the identical request (same resolved
   person, same note, same content) returns **from a different turn**.

   *Implementation note, 2026-08-14 — the mechanism needs a turn identity, and the first
   implementation lacked one.* Checking merely "has this request been seen before" is not a human
   checkpoint at all: the agent loop runs up to eight rounds per turn and each round may carry
   several tool calls, so the model announced and executed the same deletion by itself, without a
   human writing a word. The gate therefore carries a per-turn token minted where the tool catalogue
   is built (once per turn), and consent requires the announcement and the execution to come from
   **different** tokens. What this proves is that a person spoke after seeing what would change —
   **not** that they agreed. A stronger proof needs a channel outside the model (a button, a distinct
   command), which this deployment does not have; the announcement narrows the window rather than
   closing it. Consent is single-use and expires with the request. This is the Rule-of-Two checkpoint: when the flow reaches
   untrusted content, sensitive data and a mutating effect at once, a person decides.

7. **How `edit`/`delete` name their target.** The model addresses a note by the **note id it already
   received from `search_notes`/`get_note`** — never by a free-form filesystem path, and never by
   re-deriving one. This matters because `note_id(company, project, on, title)` (`paths.py`) is a pure
   function of title/project/date, while the collision suffix (`-2`, `-3`, …) is added *afterwards* by
   `_unique_id`: a suffixed note is therefore **not** addressable by "unchanged derivation" at all, and
   an implementation that tried would silently target the wrong file. The gate resolves the id to a
   path, refuses anything that does not resolve to an existing note under `notes_dir`, and refuses ids
   the requester may not read (ADR 0062 authz, so mutation can never be a read oracle). In the
   shell-on layout the model sees note *paths* under `/mnt/system/notes` while the typed
   `search_notes`/`get_note` tools step aside (`agent_wiring.py`) — there the gate accepts the path
   **only** by mapping it back to an id under `notes_dir`, applying the same refusals; the path is a
   lookup key, never an instruction to open a file.

8. **The judge's verdict is recorded in the audit row at append time** — closing the seam question
   this ADR raised against ADR 0067. The judge runs *before* the mutation, so its verdict is already
   known when the tool call's audit row is written: the recorder gains an explicit fourth argument
   (`judge_verdict`), and the shipped append-only `AuditStore` (`core/ports/audit.py`) needs **no**
   update verb. What is recorded is the verdict, the reason, and the trust class of the turn — never
   the note content (ADR 0067 redaction rule holds unchanged).

   *Amended at implementation, 2026-08-18 (this decision had shipped only as a schema column:
   `AuditService.record` wrote a hardcoded `None` and `NoteMutationService` never called audit, so
   the Phase 6 acceptance criterion was unreachable by any flag).* The verdict reaches the row
   through a **slot on the per-turn recorder** (`TurnAudit.record_verdict`), not through a fourth
   argument carried by the runtime. The reason is mechanical: the row is appended in
   `AgentRuntime._dispatch`, *after* the tool returns, while the verdict is born *inside* the `File`
   tool — a fourth argument would force `core/agent/runtime.py` to carry a value it cannot produce
   and does not understand, for every tool in the catalog. The slot keeps the runtime contract at
   three arguments, is filled by the mutation gate and **consumed** by the next appended row
   (cleared unconditionally, including on a failed write), so one verdict can never attach itself to
   a later call. What is recorded is unchanged — verdict plus reason — with the reason passed
   through `project_verdict`, which strips control characters and collapses whitespace before
   applying the ADR 0067 field ceiling (128 chars). Both halves matter: the reason is free text
   written by a model that has just read the note, the ceiling is the only mechanical guarantee
   available that a note fragment does not ride into the log inside it, and the stripping keeps a
   reason containing `\n` or an ANSI escape from rewriting the operator's audit listing — this is
   the one field of that log that would otherwise reach the database raw (`arg_summary` goes
   through `json.dumps`).

   The verdict is reported **at the moment it is made** (`NoteMutationService._decide`), not after
   a successful write. The write can still fail on the version check — a parallel turn inside the
   window `_require_mutable` describes — and reporting afterwards would lose exactly the `allow`
   that a destructive-but-refused-by-infrastructure attempt produced, leaving a row indistinguishable
   from "the judge never ran".

   Refusals that are **not** adjudications report no verdict at all: gate-level refusals before the
   judge (no `reason` given, unresolved requester, closed gate) and the two *technical* refusals
   inside `_decide` — a failed snapshot and an unavailable judge. A row saying `refuse` with an
   exception's text would claim someone adjudicated, precisely in the situation where the log is
   supposed to explain an infrastructure failure instead.

   Complementing this, the judge's system prompt asks for a reason written *in its own words*
   rather than quoting the note, with the reason given (the log is read without the ADR 0062 read
   gate). That sentence is **hygiene, not a boundary** — the judge is itself an injection target
   (R10) and no prompt closes that. The mechanical guarantee stays on the write side, where it does
   not depend on what the model decided to write.

## Risk register — this is the conscious-consent content

| # | Risk introduced | Mitigation |
|---|---|---|
| R1 | The judge becomes the *only* real boundary, and it is probabilistic — the exact failure mode ADR 0057 avoids. | The judge is defense-in-depth **on top of** AAD authz (hard, pre-judge), path/schema confinement, and pre-mutation backup. It can only narrow, never widen. |
| R2 | `edit`/overwrite **and now `delete`** kill the create-only invariant (`os.link`); reversibility stops being structural and becomes procedural. | Per-operation snapshot before every destructive write, written *first* and fail-closed; **nightly full-volume backup already shipped** (infra `systemd/workmate-backup.timer`, 03:00, `WORKMATE_BACKUP_DEST` mandatory, keep 30). Worst case is one restore, not a lost note. |
| R3 | The **chosen** generic `File(write/edit/delete)` channel bypasses the note schema/provenance/id derivation and could break the `company/project` layout that authz and retrieval depend on. | The owner chose the generic channel over the typed one, so the mitigation must be structural rather than a smaller surface: every mutating `File` call is **rewritten into a validated note operation** by the `NotesWriteService` validator before it reaches the port — company/project resolved against the registry, path through `_resolve_within`, id derivation unchanged, body free. A `File` path that does not resolve to an existing note under `notes_dir` is refused, not created ad hoc. The tool argument is a *request*, never a filesystem path taken at face value. |
| R4 | Prompt-injection drives a destructive write; the second model can be injected too. | Structural, not just the judge: AAD authz before the judge, content-as-data boundaries on both the agent and the judge, backups, single-file ops. |
| R8 | Probabilistic judge + write-new+swap collide with create-only-as-race-guard on deterministic ids (ADR 0043/0048). | Mutation only on `save_note`; `-mtg-`/`-thr-` stay create-only. |
| R10 | The judge is **not** a Dual-LLM boundary, though it looks like one: it branches on the very content that may be hostile (the diff, the intent), so data-flow becomes control-flow — the known limit of the pattern. An injected note body can therefore aim at the judge as well as at the agent. | Accepted as *defense in depth*, never as the boundary: the hard controls under it (AAD authz before any model call, registry-validated paths, snapshot, single-file, `confirm` requiring a real T1 turn) hold whatever the judge decides. The judge fails toward refusal, and its input is framed as data on its own injection boundary. The boundary is the architecture; the judge only narrows. |
| R11 | `delete` widens R2/R4 blast radius: a successful injection now removes knowledge instead of only corrupting it, and removal is quieter than corruption. | Single-file, non-recursive, snapshot-first-or-refuse, judge + `confirm` from a resolved requester, and an audit row per attempt (ADR 0067). Removal is recoverable from the snapshot and the nightly backup; the audit makes it noisy after the fact. |
| R9 | Extending the `NotesWriter` port touches ~6 assembly sites + every fake (`server.py`, `agent_wiring.py`, `teams_graph/app.py`, `cli/meeting.py`, `seed_corpus.py`, golden/fakes) — not "localized in `os.link`". **Recount 2026-08-14: the original figure was ~half the real delta.** It is now *three* port verbs, not one (`overwrite`, `delete`, and a way to *read* the note back), because Decision 4 defines `edit` as read→replace→write-new+swap while `NotesWriteService.__init__` takes only `(writer, projects)` — it has nothing to read with. | A separate, explicitly-counted set of port methods, never a change to `write`; all implementors + fakes in one PR; a negative probe per consumer. **The read dependency is resolved by injecting the existing `NotesRepository`** into the service rather than growing a read verb on `NotesWriter` — ADR 0006 split those ports deliberately, and merging them here would reverse that split as a side effect of an unrelated feature. This makes the service's constructor a third assembly-site change; count it. |

## Options considered

- **Keep create-only, no judge** (the safe default): build only `File(read)` (ADR 0064); the knowledge
  base stays append/create-only. Rejected by the owner in favor of mutation + judge (2026-08-12), with
  the risks above accepted.
- **Hard deny-list on destructive commands** (rejected by the owner): the thing the owner explicitly
  does not want — and on the shell it is redundant with the `ro` mount anyway (ADR 0057).
- **Generic `File(write/edit)` straight to notes** (the owner's literal ask): carried, but with the
  schema validator forced in front (R3); the open question below asks whether a typed `Notes(action)`
  is the better channel for the same effect.

## Consequences the reversal drags along (found in review, 2026-08-14)

- **Three model-facing texts currently say the opposite, and they ship in the frozen prefix.** The
  reversal is not complete until they change **in the same PR as the code** — otherwise the cached
  `tools + system` prefix (ADR 0056) instructs the model against the very capability being added, and
  the likely outcome is a tool that exists and goes unused:
  - `_PRECEDENCE` (`core/agent/prompt.py`): *"add notes, and leave existing ones as their authors
    wrote them"* — the sentence has to become the *judged-and-confirmed* rule, not a prohibition.
  - `_NOTES_SAVE` (`core/application/tools.py`): *"istniejąca notatka nigdy nie jest nadpisywana"* —
    true of `Notes(save)` and must stay true of it, while naming `File` as the mutation path.
  - **Hard rule 2 in `CLAUDE.md`** ("create-only via `os.link`", "the model has exactly one write
    tool") becomes false the moment the code merges. It changes in that same PR — this ADR is the
    "own ADR" its escape clause requires.
- **The nightly volume backup is shipped but NOT installed on prod (verified 2026-08-14:
  `systemctl is-enabled workmate-backup.timer` → `not-found`, no `/etc/workmate/backup.env`).** R2 and
  R11 rest their entire case on procedural reversibility, so this is a **precondition, not a
  follow-up**: `delete` does not ship until the timer is installed, enabled, **proven by one
  restore**, and has one successful run to show. Per-operation snapshots alone protect a single
  mistake, not a bad day.

  **Amended 2026-08-19 (infra ADR 0014): the destination no longer has to be off-host.** The
  original wording said "off-host destination" — inherited verbatim from `systemd/README.md`,
  written the day before this ADR, and adopted here under "the question was answered by shipped
  infra" rather than derived from what `delete` needs. It conflated two threats. Losing a note to
  an *operation* (`down -v`, an agent deleting it) is what `delete` opens, and a copy **outside the
  Docker volume** covers it — which is exactly what upstream ADR 0008 asks for. Losing the *medium*
  (disk failure, host loss) is unchanged by `delete` and is Phase 7's offsite work. The deployment
  host has a single volume and will not get a second one, so the inherited wording made Phase 6
  unreachable for a reason unrelated to its own risk register. The precondition is now a backup
  that is **working and restorable**, with the weaker medium consented to explicitly and re-stated
  in the log on every run.

## Closed questions — decisions of 2026-08-14

- **`confirm` semantics** → a **human checkpoint in the thread**, satisfiable only by a new turn from
  the same resolved requester (Decision 6). Warn-and-proceed was rejected: it would reduce the verdict
  to telemetry. `confirm ≡ refuse` was rejected: it leaves a legitimate operation with no way to
  complete except rephrasing, which trains users to argue with the judge.
- **Mutation channel** → the **generic `File(write/edit/delete)`** of ADR 0064, not a typed
  `Notes(action=…)`. The narrower typed channel was recommended (Action-Selector: the model picks an
  action and typed arguments, never a path) and declined in favour of the literal ask. The cost lands
  in **R3**, whose mitigation is correspondingly structural: a mutating `File` call is *rewritten* into
  a validated note operation, never executed as a raw path.
- **Does `delete` exist?** → **Yes** (Decision 5), single-file and snapshot-first. Consented risk
  **R11**.
- **Backup cadence** → per-operation snapshot **always**, plus the **nightly full-volume backup that
  already exists** (infra `systemd/workmate-backup.timer`, 03:00 daily, destination outside the
  Docker volume, 365 kept). No new cadence is invented here; the question was answered by shipped
  infra — which is also why the destination requirement needed amending later, see the precondition
  note above and infra ADR 0014.
- **Is the security reversal accepted as written?** → **Yes**, 2026-08-14. What is consented to is the
  register above: R1–R4, R8–R9 as written, plus **R10** (the judge is not a Dual-LLM boundary) and
  **R11** (`delete` blast radius). ADR 0003's create-only invariant and ADR 0057's "security by lack"
  remain true *of the executor*; they stop being true of the application's note path, and that is the
  deliberate change.
- **Where does the judge verdict get recorded?** → **In the audit row at append time**, via an explicit
  fourth recorder argument (Decision 7). The append-only `AuditStore` needs no update verb, and ADR
  0067's §1.4 wording ("writes its verdict into the same audit row") becomes accurate rather than
  aspirational.
- **Trust class of the turn** → until ADR 0066 supplies a real class, the judge treats **every turn as
  tainted** (confirmed 2026-08-14). With 0066's T3 labels shipping default-ON, the judge gets real
  provenance for read content immediately; the T1/T2 sender split arrives with 0062's flag.
