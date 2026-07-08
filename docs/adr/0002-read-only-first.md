# 0002. Read-only tools first (Phase 1)

Date: 2026-07-07
Status: accepted (amended by ADR 0006 — the save_note write tool passed Gate 2)
Author: P0w3r223
Related to: roadmap_workmate.pdf (cross-cutting principles, Gate 2), docs/adr/0006-write-capability-gate-2.md

---

## Context

The roadmap scopes Phase 1 deliberately narrow and read-mostly: four tools
(`search_notes`, `get_note`, `list_projects`, `get_project_status`) over a
well-structured note store, no RAG. Cross-cutting principles require narrow,
typed tools — no "read any file", no shell — and treating fetched content as
data, never commands. The reference skeleton (`teams-ai-file-agent`) instead
grants full write/rename/delete access; that shape belongs to Phase 2 and would
contradict Phase 1 if copied.

## Options considered

1. **Read-only tools only.** Matches the roadmap scope; smallest permission
   surface; defers the hard permission/untrusted-content questions to Gate 2.
2. **Include write tools now** (as in the skeleton). Faster path to a "does
   things" demo, but pulls Gate 2 (permission boundary, untrusted content)
   forward before it is designed, and widens attack surface prematurely.

## Decision

**Option 1.** Phase 1 exposes only read-only tools. Any tool that writes or
mutates external state requires a new ADR and team sign-off (Gate 2) before
implementation.

## Consequences

- Minimal, auditable surface; content is consumed strictly as data.
- The MCP boundary still degrades gracefully on data errors (returns
  `{"error": ...}`) without a write path existing.
- When Phase 2 introduces writes (e.g. "save a meeting note"), it arrives with
  an explicit permission profile per door, as the roadmap intends — not by
  accident.
