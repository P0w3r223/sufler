# 0025. Note creation from the Teams (delegated Graph) door — a per-door write gate

Date: 2026-07-17
Status: proposed
Author: P0w3r223
Related to: docs/adr/0006-write-capability-gate-2.md, docs/adr/0015-teams-delegated-graph-polling.md,
  docs/adr/0018-agent-working-directory.md, docs/adr/0008-agent-runtime-and-tool-catalog.md

---

## Context

The `teams_graph` door builds its conversational responder with `enable_write=False` hardcoded
(`adapters/inbound/teams_graph/app.py`), so the agent on a Teams channel can read notes and project
status but **cannot** create them. Product wants note creation from Teams: a user says "save a note
about today's meeting" and the agent persists it to the shared knowledge base.

The mechanism already exists and is unchanged by this decision: `save_note` is the single note-writing
tool (ADR 0006), it flows through `NotesWriteService`, which sanitizes input (`reject_dangerous_content`),
requires the project to exist in the registry, slugifies the title to `[a-z0-9-]`, and **never
overwrites** (append-only with a numeric suffix). The frozen `NoteMetadata` schema (Gate 1) is untouched.

What is new is **trust**: ADR 0006 makes write a per-door capability and states that enabling a mutating
tool on a *less-trusted* door needs its own ADR and team sign-off. The Teams channel is exactly that —
multi-user, and its message content is untrusted input (prompt-injection surface). ADR 0018 set the
precedent: it split the workspace write gate (`enable_workspace`) from `enable_write` precisely so a
door can opt into one capability without the other.

Hard constraints: `core ↛ adapters`; the frozen MCP tool surface
(`tests/adapters/test_mcp_tool_surface.py`) stays untouched — `save_note` is already part of the 4+1
surface, and this change touches the **runtime** catalog per door, not the MCP door; secrets never enter
a note; note content is data, not instructions.

## Options considered

- **A1 (chosen).** A dedicated per-door flag `WORKMATE_TEAMS_GRAPH_ENABLE_NOTES_WRITE` (default OFF) on
  `TeamsGraphSettings`, passed into `build_conversational_responder(enable_write=…)` in place of the
  hardcoded `False`. Reuses `save_note` / `NotesWriteService` 1:1. This is the `enable_workspace` pattern
  from ADR 0018 applied to notes: every mutating gate in this repo is its own env flag, default OFF.
- **A2.** A narrower, dedicated write responder that persists a note only after an explicit user "yes"
  and records the sender as author. Safer against injection (a confirmation turn stands between an
  injected instruction and a write) but introduces a second write path parallel to `save_note` and more
  code. Rejected for the pilot; kept as the hardening path if injection proves real (see Threat model).

## Decision

Adopt **A1**: gate note writing on the Teams door behind `enable_notes_write`, default OFF, reusing the
existing `save_note` tool and service. No new tool, no new port, no schema change. The MCP golden surface
is untouched (the change is in the per-door runtime catalog, injected by the door's wiring, not in
`build_tool_catalog`). This ADR amends ADR 0006 (write is enabled on a less-trusted door) and ADR 0015
(the delegated Teams door is no longer strictly read-only when the operator opts in).

## Threat model (why the gate is OFF by default)

- **Prompt-injection → false write.** On a channel, any user's message (or the content of an uploaded
  file, ADR 0016) is untrusted; a crafted message could induce a note that misattributes decisions or
  pollutes the knowledge base. Unlike a public GitHub comment, this write is inward (to our own base),
  so it is *not* an exfiltration vector — the blast radius is data-quality, not disclosure.
- **Compensating controls (real, but not a boundary):** the service sanitizes all fields, the project
  must already exist in the registry (a note cannot invent a company/project path), the title is
  slugified (path-traversal safe), writes never overwrite, and the tool description says "only on an
  explicit request". None of these stops a *plausible-looking* but injected note; the "content is data,
  not instructions" rule lives in the system prompt (bypassable at inference), not in an architectural
  boundary.
- **Enabling condition (operator decision):** turn `enable_notes_write` ON only for a trusted team.
  To remove the residual risk structurally, adopt A2 (a hard draft→"yes" confirmation anchored to a
  verified user turn) before enabling on a wide/less-trusted channel.

## Consequences

- Small, reversible blast radius: `teams_graph/app.py` (pass the flag instead of `False`),
  `config.py::TeamsGraphSettings` (new env flag + validation), and a wiring test. Default OFF means
  existing deployments do not change behavior.
- Author attribution is *not* added under A1 (the note has no "created by" beyond the writing service's
  defaults); if per-sender attribution is required, it arrives with A2.
- The knowledge base becomes writable from a multi-user surface — operationally, expect to periodically
  review notes created from Teams until injection resistance is validated in the field.
