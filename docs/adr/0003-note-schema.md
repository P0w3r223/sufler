# 0003. Note schema and Markdown + YAML frontmatter

Date: 2026-07-07
Status: proposed
Author: P0w3r223
Related to: docs/reference/note-schema.md, roadmap_workmate.pdf (Gate 1)

---

## Context

The note schema is Gate 1 in the roadmap — a team-owned decision that, once
locked, turns the rest of the work into extension rather than redesign. The
roadmap lists the fields: project, date, participants, decisions, action items,
open questions. The core principle is "structure at write time, not at read
time": a fixed template and folder layout are what make querying and status
tracking simple. This ADR proposes a concrete schema and storage format for
team ratification.

## Options considered

1. **Markdown + YAML frontmatter.** Human-readable and hand-editable; body holds
   the prose, frontmatter holds queryable metadata; trivial to diff and review
   in git. Parsing needs a small frontmatter split + YAML.
2. **JSON files.** Easiest to parse and validate; poor for human authoring and
   for reading prose; noisy diffs.
3. **Database rows now.** Powerful querying, but premature — adds infrastructure
   before the schema itself is stable, and hurts human editability.

## Decision

**Option 1.** Notes are Markdown files with a YAML frontmatter carrying:
`title, project, date, participants, decisions, action_items, open_questions,
tags`. `title` and `tags` extend the roadmap's list to aid search; `project`,
`title`, `date` are required. Files live at `data/notes/<project>/<file>.md`;
the note id is the path without extension. Schema is enforced by
`core/domain/models.py::NoteMetadata` (Pydantic). `action_items` are plain
strings in Phase 1.

## Consequences

- Search works over metadata + body without RAG, as Phase 1 requires.
- The schema is a frozen contract: changing fields requires a superseding ADR.
- Structured `action_items` (owner, due date) is a known future extension and is
  intentionally deferred to keep Gate 1 small.
- **Pending team ratification at Gate 1.** Until then, treat as the working
  proposal that the seed data and models already follow.
