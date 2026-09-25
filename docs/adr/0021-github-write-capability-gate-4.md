# 0021. GitHub write capability: create issue / comment (Gate 4)

Date: 2026-07-15
Status: accepted
Author: P0w3r223
Related to: docs/adr/0006-write-capability-gate-2.md, docs/adr/0020-github-delegated-polling-door.md,
  docs/adr/0019-shared-event-store.md

---

## Context

The user wants the interaction to be bidirectional: not only GitHub → Teams notifications,
but also Teams → GitHub actions ("create an issue from Teams"). Writing to an external system
is a new mutating capability. ADR 0006 established that any tool which mutates state needs its
own ADR and a per-door permission gate (Gate 2 was `save_note`). This is that decision for
GitHub — Gate 4 — and it deliberately mirrors ADR 0006's envelope.

## Options considered

1. **Narrow, create-only write tools behind a per-door gate** (chosen). Two tools —
   `create_github_issue`, `comment_github_issue` — that only *create*, never edit or delete;
   a separate `GithubWritePort` (distinct from the read port); exposed only when a write
   service is injected, controlled by `enable_github_write` (default off). Smallest mutation
   surface, matches `save_note`'s shape.
2. **Full issue CRUD (edit/close/delete) now.** Faster to a rich demo, but widens the
   mutation and attack surface far beyond the stated need and pulls edit/delete permission
   questions forward before they are needed.
3. **No write; keep GitHub read-only.** Zero new surface, but fails the explicit "vice versa"
   requirement.

## Decision

**Option 1.** Add gated GitHub write with this envelope:

- **Separate write port.** `GithubWritePort` distinct from `GithubReadPort` — the read side
  stays visibly read-only; `GithubWriteService` depends only on ports (`core ↛ adapters`).
- **Per-door gating (default off).** `SUFLER_GITHUB_ENABLE_WRITE` controls whether
  `build_github_write_catalog` is built and injected (via `extra_catalog`) into a door's
  agent. When off, the write client is `None`, so the model never sees a mutating tool —
  a structural guarantee, exactly like `save_note`. Wired into the `teams_graph` door.
- **Create-only, config-scoped.** Never edits or deletes (those would need another ADR). The
  target `owner`/`repo` comes from configuration, not the request text, so untrusted content
  cannot redirect the write to another repository.
- **Sanitized, bounded input.** `reject_dangerous_content` on title/body/labels; a hard
  length cap (raise, never silently truncate). Transport errors from the adapter are
  translated to `WriteError` so the tool returns `{"error": …}` instead of crashing the turn.
- **Echo into the spine.** On success the service ingests a `source="teams"` event
  (`github_issue_created` / `github_comment_created`) into the EventStore, so the GitHub side
  and any agent can see it. Because the notifier pushes only `source="github"`, this echo is
  **not** sent back to Teams — the second half of the loop guard (the first being the PAT
  self-skip of ADR 0020).

## Consequences

- The "read-only GitHub door" of ADR 0020 gains an explicit, gated exception — the notes
  base and the GitHub door keep independent write gates.
- Editing/closing/deleting issues remains out of scope and would need another ADR.
- Identity is the PAT account; issues/comments are attributed to it. Acceptable for the
  pilot; revisit if a bot identity is required.
