# 0059. Extended Jira read + read-only Teams Shifts schedule

Date: 2026-08-04
Status: accepted
Author: P0w3r223
Related to: docs/adr/0030-jira-server-read-door.md, docs/adr/0033-jira-cloud-support.md,
  docs/adr/0054-reduce-jira-to-read-only-my-tasks.md, docs/adr/0055-withdraw-worklogpro-timesheets.md,
  docs/adr/0015-teams-delegated-graph-polling.md, docs/adr/0022-proactive-dual-target-teams-push.md

---

## Context

ADR 0054 reduced Jira to a single read capability: "my open tasks", scoped server-side to the
caller's own account, with zero mutation and zero parameters. That reduction was deliberately
minimal — it left no way to look up a specific ticket by key, search across a project, or ask
about work someone else on the pion is doing (open or historical). Those are all read-only
questions people actually ask the bot in practice, and none of them require the mutation surface
that ADR 0054 removed.

Separately, the team's operating rhythm depends on Microsoft Teams Shifts (the "grafik") — who is
working today, who is remote vs. in the office, who is out. Today that lives only in the Shifts
UI; getting the bot to answer "kto dziś pracuje zdalnie?" requires no new bot registration and no
new admin consent if it borrows the identity already granted to the `powiadomienia-teams` bot
(ADR 0015/0022's delegated device-code identity, `TeamMember.Read.All` already consented for
roster lookups). Requesting a fresh `Schedule.Read.All` consent on that SAME app registration is a
smaller ask than standing up a new one.

## Decision

**Extended Jira read** (`core/domain/jira_tasks.py`, `core/application/jira_read.py`,
`core/application/tools.py`):

- `JiraTask` gains `assignee`/`resolved` fields; `JiraComment`/`JiraTaskDetails` are new,
  whitelisted-field models for a single issue's detail view (description + up to 5 recent
  comments, both trimmed and control-character-stripped via the new
  `core/domain/sanitize.strip_control_chars` — issue content is DATA fed to the model, not
  instructions).
- `escape_jql_string` properly escapes caller-controlled JQL literals (backslash then quote);
  `build_search_jql`/`build_history_jql` compose JQL from caller-supplied text/project/status/date
  filters, each validated or whitelisted in the domain layer before reaching JQL (project key
  regex, status-category whitelist, strict ISO-date parsing via `InvalidRequestError`) — unlike
  `build_my_tasks_jql`'s trusted config value, these inputs are NOT trusted and get the stricter
  treatment.
- `split_by_assignment` separates "assigned to me" from "reported by me, still unassigned" (an
  existing bug fix carried over from ADR 0054's follow-up), reused by both `get_my_jira_tasks` and
  the new `get_member_jira_tasks`.
- `JiraReadService` (new) adds `task_details`, `search_tasks`, `member_open_tasks`,
  `member_history` — all read-only, all translating transport errors to `JiraReadError` the same
  way `MyJiraTasksService` already does.
- `MyJiraTasksService.my_history` (new) — the caller's own resolved/closed tickets, optionally
  windowed by resolution date, capped at `_MAX_HISTORY_RESULTS` with an explicit `truncated` flag
  rather than a silent cut.
- `tools.build_my_jira_tasks_catalog` now returns TWO `ToolSpec`s (`get_my_jira_tasks`,
  `get_my_jira_history`) instead of one; `tools.build_jira_read_catalog` (new) adds
  `get_jira_task`, `search_jira_tasks`, `get_member_jira_tasks`, `get_member_jira_history`.
  `get_member_*` resolves the target person's Jira account EXCLUSIVELY through the trusted
  identity map (`resolve_member`, name → `jira_user`) — never a caller-supplied account string —
  so an unknown or ambiguous name degrades to a readable refusal instead of guessing.
- `adapters/outbound/jira_api.py`/`jira_cloud_api.py` add `get_issue`/`list_comments` (with
  `_validate_key` guarding the URL path against traversal); `jira_cloud_api.py`'s `_SEARCH_FIELDS`
  gains `assignee`/`resolutiondate` to feed the new fields. `core/ports/jira.py` extends
  `JiraReadPort` with both methods. Both clients stay SYNCHRONOUS, read-only, and behind the same
  `jira_http`/transport retry as before — no new write surface, no new HTTP verb.
- `adapters/outbound/graph_identity_directory.py` adds `resolve_by_display_name` — matching a
  human-typed "Imię Nazwisko" against the trusted identity map (`core/domain/names.py`:
  `normalize_name` folds Polish diacritics and case; `match_name` requires an exact or
  unambiguous-token match, else returns the ambiguous candidates for a readable refusal). This is
  the ONLY place name-matching is allowed, because the candidate set is already a trusted,
  curated roster — never a guess against an external system's account list.

**Read-only Teams Shifts schedule** (`core/domain/schedule.py`, `core/application/team_schedule.py`,
`core/ports/schedule.py`, `adapters/outbound/graph_schedule_api.py`,
`adapters/outbound/msal_silent_token.py`, `config.ScheduleSettings`,
`teams_graph/app.py:_build_team_schedule_catalog`):

- `ScheduleReadPort`/`HttpxGraphScheduleClient` — a synchronous Graph client for
  `/teams/{id}/schedule` (members, shifts, times-off, time-off reasons), mirroring the shape of the
  Jira read clients: GET-only, retried transiently, translated to `ScheduleReadError` at the
  adapter boundary.
- `TeamScheduleService.schedule(week, date_from, date_to, person)` composes the raw Graph data into
  a single response: shifts and times-off in the requested window, rendered in `Europe/Warsaw` (the
  grafik is read by humans; "8:00" must mean local 8:00 across DST), optionally narrowed to one
  person via the same trusted-roster name matching as `resolve_by_display_name` (`core/domain/
  names.py`, shared module — the schedule's candidate set is the Graph team roster, not the Jira
  identity map, but the matching rule is identical). Shift color (`theme`) is translated to
  `work_mode` ("stacjonarnie"/"zdalnie") by a fixed, documented color convention; unknown colors
  pass through as the raw `theme` rather than a guessed label.
- **Authentication is the load-bearing decision here.** `ScheduleSettings` deliberately does NOT
  provision a new identity. It borrows the **existing** `powiadomienia-teams` bot's MSAL token
  cache — mounted **read-only** into the sufler container — and exchanges the cached
  refresh-token for a Graph access token via `acquire_token_silent`
  (`adapters/outbound/msal_silent_token.py`). Three invariants enforced in that module and worth
  restating because they are the entire safety argument:
  1. The cache is **never written** — `cache.serialize()` is not called; the mount is read-only and,
     even if it weren't, this process has no business mutating another bot's refresh-token.
  2. The cache is **deserialized on every call**, not cached in memory at startup — the owning
     process rotates the refresh-token, and a stale in-memory copy would eventually fail silently.
  3. A failed silent acquisition (no account, missing consent, expired session) raises
     `ScheduleReadError` with a readable, actionable message — never a device-code prompt (this
     process has no interactive channel) and never a bare traceback.
- `ScheduleSettings.enabled` defaults to `"auto"`: the tool is wired up only when
  `client_id`/`tenant_id` are set AND the cache file actually exists on disk
  (`is_enabled()`) — so a host without the `powiadomienia-teams` volume mounted silently gets no
  `get_team_schedule` tool rather than a startup failure. `client_id`/`tenant_id` themselves fall
  back to `SUFLER_TEAMS_PUSH_*` (the same app registration `TeamsPushSettings` already uses) so
  the operator does not have to duplicate the app registration into a third set of env vars.
- `tools.build_team_schedule_catalog` exposes exactly one tool, `get_team_schedule` — no gate,
  because after the above there is nothing to mutate; the tool degrades to `{"error": ...}` on any
  Graph/consent failure rather than crashing the poller.

## Consequences

- Both extensions are strictly additive on top of ADR 0054's read-only baseline: no new mutation
  path exists anywhere in this ADR, and the "my tasks" tool's guarantee (assignee is always
  injected server-side, never a request parameter) is preserved — `get_member_*` and
  `get_team_schedule`'s `person` parameter both resolve through a TRUSTED roster, never a raw
  caller-supplied account/ID.
- The schedule capability's blast radius if misconfigured is bounded: worst case is
  `ScheduleReadError` (no schedule shown) or `is_enabled()` staying `False` (tool absent) — there is
  no code path where a broken mount or expired consent surfaces as anything other than a readable
  degradation.
- `sufler-teams-graph` now depends at runtime on the `powiadomienia-teams` bot's token cache
  volume being mounted read-only when the schedule feature is desired; `docker compose config` must
  keep that volume mapping consistent between the two services. Losing that mount does not break
  `sufler-teams-graph` (auto-disable), but does silently remove the schedule tool — this is
  intentional (fail-closed on a nice-to-have) and documented here for anyone chasing "why did the
  grafik tool disappear".
- `JiraReadService`'s new caller-controlled inputs (search text, project key, status category, ISO
  dates) required promoting the escaping/validation discipline from "trusted config value, simple
  strip" (`build_my_tasks_jql`) to "untrusted input, escape or whitelist"
  (`escape_jql_string`/`build_search_jql`/`build_history_jql`) — this is a stricter bar than ADR
  0054 needed, because ADR 0054's only JQL input was server-side configuration.
- Tests for the new domain/application logic are added alongside (`tests/core/domain/
  test_jira_tasks.py` extensions and new schedule/jira_read coverage) — see the accompanying test
  commit.
