# 0006. Write capability: the save_note tool (Gate 2)

Date: 2026-07-07
Status: accepted
Author: P0w3r223
Related to: docs/adr/0002-read-only-first.md (amends), docs/adr/0005-company-project-note-layout.md,
  roadmap_workmate.pdf (Gate 2)

---

## Context

ADR 0002 made Phase 1 read-only and stated that *any* tool which writes or
mutates state requires a new ADR and team sign-off (Gate 2) before
implementation. The team now wants a programmatic "save note" that places a new
meeting note into the correct `company/project` folder (layout fixed in ADR 0005)
instead of hand-placing files. This ADR is that Gate-2 decision: it introduces
the first mutating tool and defines the permission and safety envelope it ships
with. This amends — does not discard — ADR 0002: read-only remains the default
posture; write is an explicit, gated exception.

## Options considered

1. **One narrow write tool (`save_note`) behind a per-door flag, create-only.**
   Smallest mutation surface: a single tool that only *adds* notes, never edits
   or deletes; exposed only to doors whose permission profile enables it; title
   sanitized before it touches the filesystem. Matches the roadmap's "permission
   profile per door" and "untrusted content = data" principles.
2. **General write/edit/delete tools now** (as in the `teams-ai-file-agent`
   skeleton). Faster to a full CRUD demo, but widens the mutation and attack
   surface far beyond the stated need and pulls edit/delete permission questions
   forward before they are needed.
3. **Defer write; keep placing notes by hand.** Zero new surface, but fails the
   explicit requirement for a programmatic save path.

## Decision

**Option 1.** Add a single mutating tool `save_note` with this envelope:

- **Separate write port.** A new `NotesWriter` port (distinct from the read-only
  `NotesRepository`); the read side stays visibly read-only. The `save_note`
  use case (`NotesWriteService`) depends only on ports, preserving the
  `core ↛ adapters` dependency rule.
- **Per-door gating.** `save_note` is registered only when a write service is
  injected. `Settings.enable_write` (env `WORKMATE_ENABLE_WRITE`, default `true`
  for the trusted local dev door) controls this; less-trusted future doors
  (Teams, GitHub) set it `false`.
- **Untrusted content / path-traversal safety.** The caller-supplied `title`
  reaches a file path, so it is slugified to a `[a-z0-9-]` whitelist; `/`, `..`
  and absolute paths cannot survive. `company`/`project` come from the registry
  whitelist. Fetched/authored content is still treated as data, never commands.
- **Create-only, atomic.** Never overwrites an existing note (collision appends
  `-2/-3`). Writes are atomic (temp file + `os.replace`) so a partial file can
  never break the greedy `all()` read.

## Consequences

- The "all tools read-only" statement is superseded for this one tool. `CLAUDE.md`,
  the server `INSTRUCTIONS`, and `docs/how-to/add-a-tool.md` are updated to say:
  read-only is the default; adding a *further* mutating tool still needs its own
  ADR + sign-off.
- Editing or deleting notes remains out of scope and would need another ADR.
- The permission profile is now a real, per-door knob rather than an implicit
  global guarantee — a foundation Gate 3 (auth per person) builds on.
- Layout ADR 0005 is no longer inert: `save_note` is what fills the
  `company/project` tree it defines.
