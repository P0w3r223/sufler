# 0055. Withdraw WorklogPRO / weekly timesheets from the project

Date: 2026-07-30
Status: accepted
Author: P0w3r223
Related to: docs/adr/0034-jira-worklog-from-github-commits.md,
  docs/adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md,
  docs/adr/0036-shift-worklog-integration-identity-and-week-contract.md,
  docs/adr/0037-claude-summary-collection-authenticated-teams-dm.md,
  docs/adr/0038-selfservice-on-demand-worklog-via-teams-dm.md

---

## Context

ADR 0035 and 0036 built a weekly, per-person WorklogPRO timesheet: a Friday scheduler (`workmate-worklogi`)
that reads a closed week's hours (Microsoft Shifts) and commit-derived issue attribution, produces one
`.xlsx` sheet per person, and DMs it (or, since A′4, attaches it) so the employee can review and import
their own hours into Jira. ADR 0038 added an on-demand self-service variant of the same pipeline. All of
it is implemented, tested (unit + integration on fakes) and code-reviewed; the batch scheduler has never
gone live (`WORKMATE_WORKLOGI_HEADERS_CONFIRMED` — the "Step 0" UI comparison against WorklogPRO's real
import template — was never completed).

A binding scope decision (2026-07-30): the project will not log work on anyone's behalf, in any form —
including the human-reviewed, self-imported form this module was carefully designed around (ADR 0035's
whole rationale was keeping a human between the data and the Jira mutation). The stage is closed as
**never having shipped** — not paused, not deferred. It is withdrawn from deployment, from the fleet, and
from the list of open items.

## Decision

**Delete the module's files**, not merely disable them:
- Domain: `core/domain/{timesheet,timesheet_sheet,timesheet_message,shift_hours,issue_attribution,
  submitted_summary,day_comment}.py`.
- Application: `core/application/{weekly_timesheets,selfservice_worklog,shift_hours_source}.py`,
  `core/ports/timesheets.py`.
- Adapters: `adapters/outbound/{graph_shift_source,graph_identity_directory,github_commit_source,
  claude_summary_store,json_hours_source,openpyxl_sheet_writer}.py`, the `adapters/inbound/worklogi/`
  door (`app.py`, `attachment_delivery.py`) and the whole `adapters/inbound/worklog_selfservice/` door.
- Config: `WorklogiSettings` and its env vars off `config.py`.
- Packaging/deploy: the `worklogi` extra and the `workmate-worklogi` / `workmate-worklog-selfservice`
  entry points from `pyproject.toml`; the `worklogi` and `worklog-selfservice` services, the
  `worklogi-out` volume and the `worklogi` compose profile from `deploy/docker/docker-compose.yml`;
  `deploy/worklogi/`; the worklog how-tos.
- Tests mirroring all of the above.

**Kept — these are shared with capabilities that are not withdrawn:**
- `core/domain/worklog.py` + `core/application/worklog.py` + `tools.build_worklog_catalog` +
  `teams_graph:_build_worklog_catalog` — this is `propose_worklog` (ADR 0034), a read-only estimator
  the write-removal note in ADR 0034/0035 already carved out as surviving. It is unrelated to logging
  work for someone; it proposes hours for the *caller's own* review and writes nothing.
- `core/domain/week.py` — the closed-week contract, reused by `teams_digest` (ADR 0053) independent of
  worklogs.
- `adapters/outbound/graph_user_doc_push.py` + `core/ports/{user_doc_push,file_output}.py` — the
  outbound Teams attachment mechanism, shared with `teams_graph` (ADR 0027) for unrelated features.
- `adapters/outbound/graph_teams_notifier.py` — shared with the GitHub bridge and `teams_digest`; only
  the worklog-only `send_chat_html` method is removed (no other caller).
- `adapters/inbound/worklogi/state.py` is **relocated**, not deleted: `teams_digest/app.py` imports it
  for its own dedup bookkeeping (an incidental reuse, unrelated to worklogs). It moves to
  `adapters/inbound/teams_digest/state.py` before the `worklogi` package is removed, so the digest
  keeps working.

**This ADR supersedes ADR 0035 and ADR 0036 in full.** ADR 0037 (design-only, never built) and ADR 0038
(self-service variant of the same withdrawn pipeline) are marked superseded as a consequence — nothing in
either survives independently of the module being removed here. ADR 0034 is **not** superseded: its
surviving half (`propose_worklog`) is explicitly out of this ADR's scope and continues unchanged.

## Consequences

- No code path in WorkMate can produce, deliver or otherwise log a timesheet on behalf of another
  person. `propose_worklog` remains as a read-only, self-service estimate the caller reviews themselves.
- The Docker fleet loses the `worklogi` service, its `worklogi-out` volume and the `worklogi` profile;
  `docker compose config` must still resolve cleanly for the remaining profiles (`mcp`, `bridge`, `tools`
  minus `worklog-selfservice`).
- `deploy/worklogi/preflight.py`, the `HEADERS_CONFIRMED` gate, and the "Step 0" WorklogPRO import-wizard
  comparison step are removed as open items — there is no longer a live path they gate.
- `teams_digest` keeps functioning because its dependency on `worklogi/state.py` is relocated first,
  before the package is deleted (dependency-safe removal order).
- Reintroducing any form of logging-for-others later requires a new ADR from first principles, not a
  revival of this one — the decision here is a considered withdrawal, not a pause.

## Rejected alternative

- **Disable via flags, keep the files ("dead but present").** Rejected (operator decision, this
  session): keeps ~1500 lines and a dozen tests exercising a pipeline the team decided never to run,
  for a reversibility benefit git history already provides at lower ongoing cost.
