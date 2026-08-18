# 0054. Reduce Jira integration to a single read-only capability: "my tasks"

Date: 2026-07-30
Status: accepted
Author: P0w3r223
Related to: docs/adr/0028-project-repo-jira-mapping-and-event-dimension.md,
  docs/adr/0030-jira-server-read-door.md, docs/adr/0031-jira-write-capability-gate-5.md,
  docs/adr/0032-jira-status-transition-capability.md, docs/adr/0033-jira-cloud-provider-variant.md,
  docs/adr/0042-meeting-note-sender-authorization.md

---

## Context

ADR 0030/0031/0032 built a bidirectional Teams↔Jira bridge: a JQL poller ingesting into the shared
`EventStore` (0030), gated create/comment writes (0031), and a gated best-effort multi-hop status
transition walk (0032). All three are implemented, tested and reviewed.

A binding scope decision (2026-07-30) narrows this to a single, deliberately small capability: a user
asks the bot (Teams / CLI / Claude Code) for **their own** open Jira tasks and gets a list. Nothing
else. Out of scope as of this ADR: push of Jira events to Teams, the poller feeding the EventStore,
every mutating write (create/comment/transition), and bidirectionality altogether. The reduction is a
deliberate scope decision, not a technical failure — nothing in 0030/0031/0032 was broken or unsafe;
the team decided the mutation surface and event-push surface are not worth maintaining for the pilot.

## Options considered

- **A1 (chosen) — remove the poller, push and write paths from the tree; keep only the read
  primitives already used by them.** `HttpxJiraClient`/`HttpxJiraCloudClient.search_issues` /
  `authenticated_account`, `JiraReadPort`, and the retry/backoff transport (`jira_http.py`) already
  exist read-only and are exactly what "my tasks" needs — reused as-is, no new HTTP surface. Everything
  mutating or poller-shaped is deleted (lives on in git history). Smallest maintained surface, no dead
  code to keep passing CI, no flags that look live but are structurally inert.
- **A2 — keep the mutating/poller code, flip every gate to a hardcoded-off, remove entry points from
  compose.** Rejected (operator decision, this session): keeps ~1500 lines and a dozen tests
  exercising a bridge nobody is allowed to turn on, with no path back except re-reading old code
  anyway. Higher maintenance cost for a reversibility benefit git history already provides.

## Decision

Adopt **A1**. Concretely:

- **Removed:** `adapters/inbound/jira/{poller,selection,state,app}.py` (poller + entrypoint
  `workmate-jira`), `core/application/jira.py` (`JiraWriteService`), `JiraWritePort`
  (`core/ports/jira.py`), `build_jira_write_catalog` / `build_jira_transition_catalog`
  (`application/tools.py`), the Jira wiring in `teams_graph/app.py` (`_build_jira_catalog`, the
  `source="jira"` notifier), the `jira_*` entries in `notifier._KIND_LABELS`, the `jira` service from
  the `bridge` compose profile, `deploy/jira/preflight.py` (poller-shaped), and every
  write/transition/poller field on `JiraSettings` (`enable_write`, `enable_transition`,
  `write_project`, `self_account`, `max_transition_hops`, `watch_projects`, `state`).
  Write/transition methods are dropped from `HttpxJiraClient`/`HttpxJiraCloudClient`.
- **Kept:** `JiraReadPort`, `search_issues`/`authenticated_account` on both clients,
  `jira_http.request_with_retry`, and `JiraSettings.{deployment, base_url, token, email}`. A new,
  narrow `core/application/my_jira_tasks.py` service builds a JQL scoped to one assignee and calls
  `search_issues` — no new HTTP surface, no new client method.
- **New capability** ("my tasks") is specified and implemented separately from this ADR's removal;
  see the accompanying implementation (`core/application/my_jira_tasks.py`, the `/moje-zadania` Teams
  command, and the `extra_catalog`/conditional-MCP tool). It is strictly read-only, scoped server-side
  to the resolved caller's own `Person.jira_user` (never a request parameter), and requires only
  browse/read grants on the PAT/API token — no create, edit, transition or admin scope.
- **This ADR supersedes ADR 0031 and ADR 0032** in full — every capability they added is removed.
- **This ADR amends ADR 0028 and ADR 0030** rather than superseding them: 0028's project/repo/Jira
  registry mapping (`Project.jira_project_key`) and event-attribution columns are foundation-level and
  harmless to keep even though Jira no longer produces events through them (GitHub still can); 0030's
  read door (`JiraReadPort`/`HttpxJiraClient` read methods, dual server/cloud provider from ADR 0033)
  is largely **retained**, only its poller/ingest/notifier half is removed. Both are marked with a
  pointer to this ADR rather than rewritten.

## Consequences

- Jira can no longer post to Teams, and nothing in WorkMate can create, comment on, or transition a
  Jira issue. The only Jira-shaped effect on the system is a read query scoped to the caller.
- The `bridge` compose profile now runs `github` + `teams-graph` only; `docker compose config` must
  still resolve cleanly for that profile.
- `WORKMATE_JIRA_TOKEN` can be issued with browse/read-only grants going forward — the write/transition
  scope requirement from ADR 0031/0032 no longer applies.
- Re-adding push, ingest or mutation later means re-implementing against this ADR's baseline, not
  reverting it — the code is recoverable from git history but this ADR's removal is treated as a
  considered decision, not a placeholder.
- Tests exercising the poller/write/transition paths are removed together with the code; tests for
  `search_issues`/`authenticated_account` and the new "my tasks" service remain and grow.

---

## Amendment (2026-08-09, release 1.6.0): the guarantee changed kind

The sentence in this ADR that says *no parameter can express "whose tasks"* was true
**structurally** while `get_my_jira_tasks` had no parameters at all. Consolidation replaced it
with `Jira(action=…)`, whose schema carries a `member` field alongside the `my_*` actions; only a
dispatcher branch separates them. Reading this ADR against today's code without this note leaves
a reader with an invariant the code appears to break.

What did **not** change is the invariant worth citing: the Jira account is never taken from the
model. `my_*` closes over the account resolved at build time, and `member_*` accepts a *name*
which is translated through the trusted identity map — an unknown name is a refusal, not a query.
`tests/core/test_jira_catalog.py` probes both halves, including that the member-read service is
not called for `my_*`.

The general lesson, recorded in
[ADR 0061](0061-consolidated-tool-surface-upstream-pointer.md): **consolidation can quietly
exchange a structural guarantee for a procedural one**, and nothing fails when it does.
