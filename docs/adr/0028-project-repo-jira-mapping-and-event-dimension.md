# 0028. Project ↔ repo ↔ Jira mapping and a project/repo dimension on events

Date: 2026-07-17
Status: accepted
Author: P0w3r223
Related to: docs/adr/0005-company-project-note-layout.md, docs/adr/0019-shared-event-store.md,
  docs/adr/0020-github-delegated-polling-door.md, docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md

---

## Context

Two upcoming capabilities — the agent reporting on real branch/PR/work state, and a Jira
integration — both need something the system does not have today: a way to say *which project a
GitHub repo (or Jira project) belongs to*, and *which project an event is about*.

Current gaps (verified):
- The project registry (`data/projects/registry.yaml`, `core/domain/models.py::Project`) has no
  link to any external system. `owner`/`repo` is a single **global** env value on `GithubSettings`
  — one door, one repo, no per-project mapping.
- The shared `EventStore` event (`core/domain/events.py::NewEvent`) carries no `repo`/`project`
  field. `read_recent_events` filters only by `source`. So "what's happening on project X" is not
  answerable — events cannot be attributed to a project.
- Dedup is `UNIQUE(source, external_id, kind)` (`adapters/outbound/sqlite_events.py:39`). The store
  deliberately lives in its own DB file to **avoid table rebuilds** on migration.

The load-bearing tension of the whole roadmap: the EventStore is append-only with dedup — it models
**immutable facts** well and **current state** poorly. Any project/repo dimension must respect that,
and must not force a rebuild of the `UNIQUE` constraint (which would contradict the file's design).

## Options considered

### A — Where the project→repo/Jira map lives
- **A1 (chosen).** Extend `Project` + registry with optional `github_repos: list[str]` and
  `jira_project_key: str | None`. The registry **is** the source of truth about a project (ADR 0005);
  `core` already depends on `Project`; a reverse index (repo → project) is built in memory. `Project`
  is not a frozen contract like `NoteMetadata`, so the ceremony is low. Pydantic ignores unknown YAML
  keys, so adding fields is backward compatible.
- **A2.** A separate correlation file/table. Rejected — a second source of truth about a project
  drifts from the registry and forces joins everywhere; the repo/Jira map is project metadata.

### B — How to attribute an event to a project/repo without breaking dedup
- **B1 (chosen — hybrid).** Add `repo` and `project` as **columns** (and model fields) for
  attribution and querying, via `ALTER TABLE … ADD COLUMN … DEFAULT ''` (safe, backfills existing
  rows). Solve cross-repo **dedup** by **composing the repo into `external_id`** at the producer
  (`owner/repo#<n>`) — a domain helper — rather than changing the `UNIQUE` constraint. This keeps
  append-only intact and avoids the table rebuild the store deliberately avoids.
- **B2.** Change `UNIQUE(source, external_id, kind)` to include `repo`. Rejected — SQLite cannot add
  a column to a `UNIQUE` in place; it needs a table rebuild, exactly what `sqlite_events.py` avoids.
- **B3.** Encode repo only in `external_id`, no columns. Rejected — not queryable without string
  parsing; `read_recent_events(project=…)` and status enrichment need a real column.

## Decision

Adopt **A1 + B1**:
- `Project` gains optional `github_repos: list[str]` and `jira_project_key: str | None`; the registry
  and YAML repo read them; `ProjectsService` exposes a reverse lookup `project_for_repo(repo)`.
- `NewEvent`/`Event` gain `repo: str = ""` and `project: str = ""`; the `events` table gains matching
  columns (idempotent `ADD COLUMN` migration for existing DBs, in the `CREATE TABLE` for fresh ones).
  `read_recent_events` / `EventStore.recent` / `read_since` gain an optional `project` filter.
- Dedup **stays** `UNIQUE(source, external_id, kind)`. A domain helper `composite_external_id(repo, n)`
  produces `"<repo>#<n>"` so multi-repo streams do not collide. **Producers adopt it when multi-repo
  ingest lands (Capability A / ADR 0029)** — this ADR ships the mechanism + columns + registry map;
  the existing single-repo GitHub door is unchanged and keeps working (fields default to `""`).

This is a read/foundation change: no new mutating capability, no new gate, the frozen MCP 4+1 surface
is untouched (`read_recent_events` is already an `extra_catalog` tool; only its filter grows).

## Consequences

- The `events` table grows two nullable-by-default columns; existing rows backfill to `''`. No rebuild,
  no dedup change — the append-only guarantee holds.
- Events can now be attributed to a project (once producers stamp `project`/`repo`), enabling
  `read_recent_events(project=…)`, project-scoped activity, and status enrichment (ADR 0029).
- The registry becomes the per-project home of `github_repos`/`jira_project_key`, replacing the single
  global `owner/repo` as the mapping source of truth (the global env stays for the single-repo door
  until callers migrate).
- `composite_external_id` is available but **not yet adopted by the live GitHub mapper** — that arrives
  with multi-repo/branch ingest (ADR 0029), so this change is additive and non-breaking to the running
  bridge.
- Blocks and enables the rest of the roadmap: Capability A (branch/PR state) and Jira B1 both depend on
  this dimension.
