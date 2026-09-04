# 0029. Branch/PR state: transition events, a live snapshot, and a project activity view

Date: 2026-07-17
Status: accepted
Author: P0w3r223
Related to: docs/adr/0019-shared-event-store.md, docs/adr/0020-github-delegated-polling-door.md,
  docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md,
  docs/adr/0028-project-repo-jira-mapping-and-event-dimension.md,
  docs/adr/0071-issue-closures-and-what-self-skip-was-actually-skipping.md — applies this ADR's
  transition pattern to issues, and names the latent flaw in the `pr_closed` dedup key

---

## Context

The agent should report on the **real state of branches and work** in a project ("what's happening
on X", "state of the PRs and branches"). Today the GitHub door ingests only opened issues/PRs,
comments, PR reviews (decisions), and CI results — there are no branch/push events, no PR **state
changes** (merged / closed / ready-from-draft), and (pre-ADR 0028) events were not attributed to a
project. The `map_ci_run` mapper even drops `head_branch`.

The load-bearing tension (ADR 0028): the EventStore is append-only with dedup — it models
**immutable facts** well and **current state** poorly. "Which PRs are open right now" and
"ahead/behind" are snapshot questions the append-only stream does not answer directly.

## Options considered

### A — How to represent state under append-only + dedup
- **A1 (chosen — hybrid).**
  - **Transitions as append-only events** for the feed/history: `pr_ready`, `pr_merged`, `pr_closed`,
    `branch_pushed` (`external_id` = commit SHA), `branch_deleted`. Each is a distinct, immutable fact
    → a distinct dedup key → no collision, and it **reuses the whole notifier→Teams pipeline** (each
    transition is a legitimate "what's happening" notification). PR transitions come from discrete
    fields (`merged_at`, `state`, `draft`) via `list_pulls`; pushes from a per-round HEAD-SHA diff of
    branches (coarse — PAT polling has no clean push feed without webhooks; acceptable for the pilot).
  - **Live query for snapshot questions** (open PR list, branch HEADs, ahead/behind): a tool calls
    `list_pulls`/`list_branches`/`compare` live (sync ports dispatched in the thread pool — the
    existing pattern). No dedup/watermark machinery for a point-in-time answer.
- **A2 — mutable state projection (CQRS read-model).** A `repo_state` table upserted each round.
  Rejected as the *first* step: it adds a mutable store and full-list polling every round; revisit
  only if a proactive digest's volume justifies it.
- **A3 — live-only, no transitions.** Rejected: no feed/history, and latency-couples every "what
  happened" answer to a live API call.

### B — Which endpoints
Add read-only `list_pulls(state=all)` (the dedicated `/pulls` carries `merged_at`, `draft`,
`head`/`base`, `state` — richer than PRs from `/issues`), `list_branches` (name + HEAD SHA), and
optional `compare` (ahead/behind). Read-only → **no Gate-4**, opt-in via `watch_kinds` (backward
compatible, like ADR 0024 added pulls/reviews/ci).

### C — Attribution and reporting
- The GitHub door **adopts `composite_external_id`** (ADR 0028) and **stamps `repo`/`project`**
  (registry reverse-lookup `project_for_repo`) so multi-repo streams don't collide and events are
  project-attributed — making `read_recent_events(project=…)`, activity, and status enrichment work.
- A new `get_project_activity(project)` tool (recent transitions from the EventStore + a live
  PR/branch snapshot) via `extra_catalog` (**frozen MCP 4+1 untouched**), and `get_project_status`
  gains synthesized facts (`open_pr_count`, `latest_activity_at`, `failing_ci_count`) from an
  **optional** `EventService` — MCP doors without events keep working.

## Decision

Adopt **A1 + B + C**, respecting the **field-whitelist invariant (ADR 0024)**: branch/PR events carry
only branch name, SHA, state, time, url, number — **never diffs, patches, or CI logs** (a secret
vector). No new mutating capability, no new gate; the change is read-side.

## Consequences

- New read-only endpoints (`list_pulls`, `list_branches`, `compare`); new opt-in `watch_kinds`
  (`pulls-state`, `branches`); new event kinds + notifier labels. No Gate-4, no mutation.
- Multi-repo safe (composite `external_id`); events attributed to a project.
- Push detection is coarse (HEAD-SHA diff, no webhooks) — acceptable for the pilot; webhooks are the
  scale path if push fidelity matters.
- **Rolled out in increments** (each green before the next): (1) read endpoints, (2) repo/project
  attribution + transition events, (3) the activity tool + status enrichment.
