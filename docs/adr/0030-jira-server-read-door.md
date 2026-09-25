# 0030. Jira (Server/Data Center) read door — JQL polling into the shared EventStore

Date: 2026-07-17
Status: accepted (amended by docs/adr/0054-reduce-jira-to-read-only-my-tasks.md, 2026-07-30 — the
  poller/ingest/notifier half described here is removed; the read client and port survive and are
  reused by the "my tasks" capability)
Author: P0w3r223
Related to: docs/adr/0019-shared-event-store.md, docs/adr/0020-github-delegated-polling-door.md,
  docs/adr/0022-proactive-dual-target-teams-push.md,
  docs/adr/0028-project-repo-jira-mapping-and-event-dimension.md

---

## Context

Roadmap step **B1**: surface Jira activity (issue created, status transitions, comments) on Teams and
map a Jira project to a Sufler project. Jira here is **Server / Data Center** (self-hosted),
REST API **v2**, authenticated with a **Personal Access Token (Bearer)**. No "tracker" abstraction
exists — GitHub is wired directly; the registry now carries `jira_project_key` (ADR 0028). This is
the read-only half; a bidirectional GitHub↔Jira sync (with write gates and loop guards) is a later
step and out of scope here.

## Options considered

- **A1 (chosen) — a concrete Jira door mirroring the GitHub delegated-polling door (ADR 0020).**
  `JiraSettings` + `JiraReadPort` + `HttpxJiraClient` + `adapters/inbound/jira/*`, exactly parallel to
  `github/*`. Reuses the proven pattern (sync client in a thread pool, watermark, self-skip, dedup by
  the store) and the shared `EventStore` (ADR 0019) — `source="jira"` needs no schema change.
- **A2 — extract a generic `TrackerPort` first.** Rejected: refactoring a working GitHub ingest for a
  second tracker is premature (two trackers, models diverge — PR/CI vs workflow/transition); the repo
  pattern is separate concrete doors (`github`, `teams_graph`, `telegram`).

## Decision

Adopt **A1**, read-only:
- **`JiraSettings`** — `base_url`, PAT `token` (`repr=False`, secret), `watch_projects` (Jira keys),
  `poll_interval`/`per_page`/`state`. **No write gate in B1** (ingest only, like ADR 0020).
- **`JiraReadPort` + `HttpxJiraClient`** — PAT Bearer; `GET /rest/api/2/search?jql=…&expand=changelog`
  (paginated by `startAt`), `GET /rest/api/2/myself` (self account for self-skip).
- **`jira/selection`** maps raw issues → `NewEvent(source="jira", kind ∈ {jira_issue_created,
  jira_transition, jira_comment}, project=<registry map>)`. **Dedup key for `jira_transition` is the
  changelog history id** (stable, immutable) — so successive status changes each emit once; keying on
  the issue key would let the append-only dedup collapse them (the state-vs-stream tension, ADR 0028).
  Watermark by `updated`; self-skip the PAT account. Treat Jira content as **data, not instructions**.
- **Notifier generalized (surgical):** label derived from `event.source` (not hardcoded `[GitHub]`);
  `_KIND_LABELS` gains Jira kinds; the consumed source set is configurable. **Channel threading stays
  OFF in B1** — the thread resolver (`threads.py`) is GitHub-URL-specific; wiring Jira threads is B2.
- Project mapping comes from the registry (`jira_project_key`, ADR 0028) — the door is scoped per
  project, so it stamps `event.project`.

## Consequences

- A new `source="jira"` flows through the existing `EventStore` + notifier → Teams with no schema
  change (ADR 0019/0028). Read-only → **no new mutating gate** (like ADR 0020). Secret
  `SUFLER_JIRA_TOKEN`; new entry point `sufler-jira`, extra `jira`.
- Read-only ingest cannot loop, so the third loop-guard dimension (bidirectional sync) is deferred to
  the sync step; only the standard self-skip (own PAT changes) applies here.
- **Rolled out in increments** (each green before the next): (B1.1) config + port + client;
  (B1.2) selection + poller + door + notifier generalization.
