# 0005. Company/project note layout and note-id evolution

Date: 2026-07-07
Status: accepted
Author: P0w3r223
Related to: docs/adr/0003-note-schema.md (Gate 1), docs/adr/0006-write-capability-gate-2.md
  (Gate 2), docs/reference/note-schema.md

---

## Context

Notes lived flat at `data/notes/<project>/<slug>.md`. The team requires a new,
top-level dimension above project: FIRMA/KLIENT -> PROJEKT -> NOTATKI, so a
company can hold several projects and no single folder collects every note. The
note id is the note's path without extension, and `get()` reconstructs the path
from the id directly, so the directory layout and the id format are the same
decision. Scale is small (hundreds of notes); an index/DB is explicitly out of
scope — the current in-memory `all()` + `rglob` is accepted. This ADR fixes the
layout and the id evolution; the programmatic write path that fills it is a
separate, Gate-2 decision (ADR 0006), because it changes the read-only invariant
of ADR 0002.

## Options considered

### Layout

1. **`firma/projekt/` + flat notes** (`notes/<company>/<project>/<date>-<slug>.md`).
   Matches the required hierarchy exactly; most human-scannable; shallowest tree;
   at hundreds of notes no single project folder grows unwieldy. The read side
   (`rglob`, id-is-path) needs no change.
2. **`firma/projekt/rok/` date-sharded.** Even chronological distribution, but the
   year is redundant with the date already in filename and frontmatter, adds a
   level the reader must guess, and — because id encodes depth — would have to be
   locked in now "just in case". No real benefit at the stated scale.
3. **Hash/prefix bucket sharding.** Even distribution at the cost of readability,
   which is the explicit goal. Rejected.

### Company source & id derivation

A. **Company derived from the project registry; id = full relative path.** One
   project maps to exactly one company; the registry already is a required
   dependency of a valid note (`project` must exist in it). Note frontmatter is
   unchanged, so `NoteMetadata` (Gate 1 / ADR 0003) stays frozen. The id becomes
   `<company>/<project>/<date>-<slug>`; `get()` stays O(1); `all()` unchanged.
B. **Company as a new `NoteMetadata` field; id = opaque stable token.** Note is
   self-describing and id survives re-org, but it reopens Gate 1, denormalizes a
   1:1 project→company relation (drift risk), and breaks O(1) `get()` (needs a
   scan or an id→path map the team ruled out).

## Decision

Adopt **Layout 1 + Derivation A**. Notes live at
`data/notes/<company>/<project>/<YYYY-MM-DD>-<slug>.md`. Company is an attribute
of the project, stored once in `registry.yaml` (a `company` key on each project),
never on the note — so `NoteMetadata` stays a frozen Gate-1 contract. The note id
evolves additively from `<project>/<slug>` to `<company>/<project>/<slug>`;
because the id is still the relative path, `get()` and `all()` need no code
change. The write path (ADR 0006) resolves company via the registry, slugifies
the title into a `[a-z0-9-]` whitelist (path-traversal safe), names the file
`<date>-<slug>.md`, and appends `-2/-3` on collision (create-only, never clobber).

The seed data's `mpwik`/`biap` keys were company-level; migration introduces
distinct project keys: company `mpwik` → project `scada-integration`, company
`biap` → project `workmate`.

## Consequences

- Read layer untouched: the whole change is the new write path (ADR 0006) plus a
  one-off relocation of existing notes into `company/project/` folders.
- Single source of truth for company (registry); no per-note denormalization.
- The id format is a tool-contract change (Gate 1 territory): every hard-coded id
  example in `docs/reference/tools.md`, `docs/reference/note-schema.md` and the
  `get_note` docstring is updated to the three-segment form.
- `registry.yaml` and the `Project` model gain a `company` field; `ProjectStatus`
  surfaces it too. `NoteMetadata` is unchanged.
- The 6 seed notes were relocated and their `project:` frontmatter + registry keys
  updated (`mpwik`→`scada-integration`, `biap`→`workmate`).
- Depth is baked into the id; revisit only if a *single* project ever accumulates
  hundreds of notes, at which point that project adopts a year shard with a
  documented re-id migration.
