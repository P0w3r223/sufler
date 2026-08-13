# 0065 — Mutable knowledge base, judged by a model instead of blocked by a rule

Date: 2026-08-12
Status: proposed
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
   I/O and covers only the meeting path (`can_write_meeting_note`).

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

4. **Mutation is create-only-safe where determinism is load-bearing.** `edit`/overwrite is offered
   only on the title-derived, suffixed `save_note` path. The deterministic meeting/thread paths
   (`-mtg-`/`-thr-` ids) **stay create-only** — they rely on the `os.link` collision
   (`FileExists → NoteExistsError`) as an idempotency guarantee (ADR 0043/0048); a probabilistic judge
   plus write-new+swap must not weaken that (R8). `edit` is odczyt→podmiana→atomic-write-new+swap with
   the pre-mutation snapshot retained — never in-place without a snapshot.

## Risk register — this is the conscious-consent content

| # | Risk introduced | Mitigation |
|---|---|---|
| R1 | The judge becomes the *only* real boundary, and it is probabilistic — the exact failure mode ADR 0057 avoids. | The judge is defense-in-depth **on top of** AAD authz (hard, pre-judge), path/schema confinement, and pre-mutation backup. It can only narrow, never widen. |
| R2 | `edit`/overwrite kills the create-only invariant (`os.link`); reversibility stops being structural. | Snapshot before every destructive write + cadenced full-volume backup (infra ADR 0008); overwrite is write-new+swap, never in-place-without-snapshot. |
| R3 | A generic `File(write)` bypasses the note schema/provenance/id derivation and could break the `company/project` layout that authz and retrieval depend on. | Mutation routes through the `NotesWriteService` validator (metadata/path enforced, body free). **Open:** prefer a typed `Notes(action=edit)` over generic `File` (below). |
| R4 | Prompt-injection drives a destructive write; the second model can be injected too. | Structural, not just the judge: AAD authz before the judge, content-as-data boundaries on both the agent and the judge, backups, single-file ops. |
| R8 | Probabilistic judge + write-new+swap collide with create-only-as-race-guard on deterministic ids (ADR 0043/0048). | Mutation only on `save_note`; `-mtg-`/`-thr-` stay create-only. |
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

## Open questions (to close before code, as in ADR 0012)

- **`confirm` semantics.** What does a `confirm` verdict do — refuse with reason, warn-and-proceed, or
  require an explicit human confirmation in the Teams thread? This is the core UX+security fork.
- **Mutation channel: generic `File(write/edit)` vs typed `Notes(action=edit/delete)`.** The typed
  action preserves schema/provenance/authz and keeps the judge on one narrow surface (R3); the generic
  `File` is closer to the literal ask but broader. Owner's call.
- **Does `delete` exist at all?** No delete exists today. Decide whether the mutation surface is
  `edit`/overwrite only, or includes `delete` (which raises the R2/R4 blast radius).
- **Backup cadence and trigger** before a destructive write (per-op snapshot always; full-volume how
  often?).
- **Is the security reversal accepted as written?** This ADR flips ADR 0003's "create-only,
  secure-by-default" and ADR 0057's "security by lack, not by filtering". Merging it is the conscious
  consent; the risk register above is what is being consented to.
- **Where does the judge verdict get recorded? (counted seam against Faza 0).** ADR 0067 §1.4 says the
  judge "writes its verdict into the same audit row", but the shipped `AuditStore` is **append-only**
  (`core/ports/audit.py`, no update verb) and the recorder signature is `(tool_name, arguments,
  status)` with `judge_verdict` hard-`None`. So this ADR must add the seam explicitly — a fourth
  recorder argument, or a separate `AuditStore` verb keyed on the row id — rather than assume the
  column is writable today. Decide the shape before implementation.
