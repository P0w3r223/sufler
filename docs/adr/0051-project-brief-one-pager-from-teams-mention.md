# 0051 — Project one-pager on demand from a Teams @mention ("ogarnij mnie na <projekt>")

Date: 2026-07-29
Status: accepted (implemented — `brief_command.py`, `core/domain/project_brief.py`)
Author: P0w3r223
Related to: [ADR 0048](0048-thread-note-capture-from-teams-mention.md) (mention seam + gated router pattern),
[ADR 0029](0029-branch-pr-state-transitions-and-project-activity.md) (activity facts folded into project status),
[ADR 0026](0026-agent-file-reply-in-thread.md) (render + file reply in a thread),
[ADR 0017](0017-read-only-command-dispatcher.md) (read-only dispatcher),
[ADR 0009](0009-meeting-note-flow-and-write-surface.md) (§3 caller-controls-location),
[ADR 0006](0006-write-capability-gate-2.md) (write gate — why a read-only feature needs none),
[ADR 0008](0008-agent-runtime-and-tool-catalog.md) (single-source tool catalog / frozen MCP surface)

---

## Context

Capture (F1–F2) fills the corpus; the corpus is only worth filling if a user can *pull* a fast, honest
picture of a project without reading every note. Today the only way to get "where does project X stand" is to
chat with the agent turn by turn (`search_notes` → read → `get_project_status` → read) — several LLM round
trips for what is fundamentally a **deterministic composition of already-synthesized reads**.

Two structural facts make the one-pager cheap and safe:

1. **The status synthesis already exists.** `ProjectsService.get_project_status(key)` returns a `ProjectStatus`
   that already folds the declared registry status with note-derived facts (`notes_count`, `latest_note_date`,
   `open_action_items`) and GitHub activity (`recent_activity_count`, `latest_activity_at`, `failing_ci_count`,
   ADR 0029). A one-pager is that object plus the few most recent notes for the project — no new synthesis.
2. **The mention seam already exists.** ADR 0048 added `mentions_bot` to `ChannelMessage` and the
   `ThreadNoteContext` seam through the responder. A second gated router reuses it verbatim.

Hard constraints (hexagonal + governance): `core/` never imports adapters; the read-only dispatcher
(ADR 0017) stays read-only; the frozen MCP surface (golden test) and `NoteMetadata` (Gate 1) are untouched;
the project key comes from a TRUSTED argument (ADR 0009 §3), never from thread content; note/status content
is DATA, never commands.

## Decision

1. **Trigger = an explicit @mention of the bot carrying an `ogarnij mnie na <projekt>` directive**, mirroring
   ADR 0048. A new `BriefRouter` (`adapters/inbound/brief_command.py`) handles messages that mention the bot
   AND whose text carries the directive; the `<projekt>` key is parsed from the explicit argument, never from
   surrounding text. An optional `| pdf` flag requests file delivery. Non-mention or non-directive messages
   return `None` → the normal agent turn handles them.

2. **Composition is DETERMINISTIC — no LLM.** A new read-only core service
   `ProjectBriefService.brief(project)` composes `ProjectsService.get_project_status` (the full synthesis,
   incl. ADR 0029 activity) with the most recent notes from `NotesService.search_notes("", project=…)` into a
   frozen `ProjectBrief` domain value. `ProjectBrief.to_text()` renders the one-pager. No summarizer, no
   token cost, no hallucination surface (note/status content is DATA): the brief is a projection of stored
   facts. An LLM narrative variant is explicitly deferred, not chosen.

3. **Read-only ⇒ NO write gate, but a staged feature flag.** The brief only reads data the door already
   exposes, so it needs neither `save_note`, nor a new MCP tool (it is a router over existing core reads —
   `build_tool_catalog` and the golden surface stay untouched), nor the identity map (there is nothing to
   authorize beyond what the pion already shares). A single flag `enable_project_brief`
   (`SUFLER_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF`, default OFF) gates the router for staged rollout, consistent
   with the gated-build discipline — but `validate` adds no fail-fast precondition (nothing extra is required).

4. **PDF is an optional projection of the SAME text, reusing the file-reply pipeline.** When `| pdf` is
   requested, delivery calls the single-source `build_file_reply_catalog` pipeline (ADR 0026) — the same
   render → `_safe_doc_name` → upload → post sequence, filename hardening, and size cap the agent's
   `reply_with_file` uses — rather than re-implementing it. PDF delivery is available only when
   `enable_file_reply` is ALSO on (it reuses that pipeline and its `Files.ReadWrite.All` scope). Absent that —
   or on any expected delivery error — the brief degrades to the inline text answer with a one-line note. The
   delivery target (`team/channel/root`) is bound from the trusted `external_id`, never from the model.

## Consequences

- **Positive.** One round-trip, deterministic, testable on in-memory fakes; zero LLM cost; reuses the ADR 0048
  seam and the ADR 0026 file channel; no new MCP tool, no `NoteMetadata` change, no new admin scope. A read-only
  feature carries no write risk, so activation is a single flag flip (no RW mount, no identity map).
- **Negative / trade-offs.** The one-pager is a fixed projection, not a free-form narrative — a user who wants
  "explain the risk in prose" still uses a normal agent turn. The directive parse is a loose `find` (ADR
  0048 precedent): it degrades cleanly (no directive → normal turn; no project → usage hint) and the project
  never comes from thread content, so the loose match cannot redirect the read to another project's data.
- **Follow-on.** `ProjectBrief`/`to_text` is the shared render skeleton that F5 ("co się zmieniło od <data>")
  and F6 (Monday DM digest) build on; F6 additionally needs its own ADR (proactive push + scheduler).

## Alternatives considered

- **LLM-composed narrative brief.** Richer prose, but adds token cost, latency, and a hallucination surface
  over stored facts for a view that is inherently structured. Rejected as the default; left as a future option.
- **A new `project_brief` MCP tool.** Would put the one-pager on the frozen MCP surface and let the agent call
  it mid-turn. Rejected: the frozen surface stays frozen (rule 6), and the feature is a door-level directive,
  not a catalog tool. A router composing existing reads keeps the surface and golden test untouched.
- **Read-only command `/ogarnij <projekt>` via the read-only dispatcher.** Viable, but the natural-language
  "ogarnij mnie na <projekt>" was the chosen trigger; a mention directive matches F2 and keeps the read-only
  dispatcher (ADR 0017) read-only rather than teaching it a new composed action.
