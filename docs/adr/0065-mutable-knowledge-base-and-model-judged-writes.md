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
   - **Path/schema confinement** — `_resolve_within` (anti-traversal), the frozen note-id/company/
     project derivation (`paths.py`, ADR 0003/0005), and `reject_dangerous_content`. A mutation cannot
     land outside `notes_dir` or fabricate a company/project the registry does not know.
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
   confirmation counts **only** if it arrives as a new turn from a sender whose `sender_id` resolves
   to the *same* `Person` who requested the mutation (ADR 0042/0054 resolution, T1 in ADR 0066 terms) —
   so a confirmation can never be satisfied by content the model read, by another participant, or by
   the model quoting itself. It expires with the request (no standing consent), and an expired or
   absent confirmation is a refusal. This is the Rule-of-Two checkpoint: when the flow reaches
   untrusted content, sensitive data and a mutating effect at once, a person decides.

7. **The judge's verdict is recorded in the audit row at append time** — closing the seam question
   this ADR raised against ADR 0067. The judge runs *before* the mutation, so its verdict is already
   known when the tool call's audit row is written: the recorder gains an explicit fourth argument
   (`judge_verdict`), and the shipped append-only `AuditStore` (`core/ports/audit.py`) needs **no**
   update verb. What is recorded is the verdict, the reason, and the trust class of the turn — never
   the note content (ADR 0067 redaction rule holds unchanged).

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
| R9 | Extending the `NotesWriter` port with overwrite touches ~6 assembly sites + every fake (`server.py`, `agent_wiring.py`, `teams_graph/app.py`, `cli/meeting.py`, `seed_corpus.py`, golden/fakes) — not "localized in `os.link`". | A separate, explicitly-counted port method (`overwrite`/`mutate`), not a change to `write`; all implementors + fakes in one PR; a negative probe per consumer. |

## Options considered

- **Keep create-only, no judge** (the safe default): build only `File(read)` (ADR 0064); the knowledge
  base stays append/create-only. Rejected by the owner in favor of mutation + judge (2026-08-12), with
  the risks above accepted.
- **Hard deny-list on destructive commands** (rejected by the owner): the thing the owner explicitly
  does not want — and on the shell it is redundant with the `ro` mount anyway (ADR 0057).
- **Generic `File(write/edit)` straight to notes** (the owner's literal ask): carried, but with the
  schema validator forced in front (R3); the open question below asks whether a typed `Notes(action)`
  is the better channel for the same effect.

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
  already exists** (infra `systemd/workmate-backup.timer`, 03:00 daily, off-host destination
  mandatory, 30 kept). No new cadence is invented here; the question was answered by shipped infra.
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
