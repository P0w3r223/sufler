# 0034. Jira worklog from GitHub commit history — proposal/write split and pluggable author strategy

Date: 2026-07-20
Status: accepted
Author: P0w3r223
Related to: docs/adr/0006-write-capability-gate-2.md,
  docs/adr/0020-github-read-door.md,
  docs/adr/0021-github-write-capability-gate-4.md,
  docs/adr/0031-jira-write-capability-gate-5.md,
  docs/adr/0032-jira-status-transition-capability.md,
  docs/adr/0033-jira-cloud-support.md

---

## Context

We want to record an employee's work in Jira based on evidence that already exists: their GitHub
commit history. Two capabilities are missing today — WorkMate has no worklog port at all, and the
GitHub client has no commit reader (only `list_branches`, which returns HEAD SHAs for push
detection, ADR 0029).

Two facts shape everything below.

**1. Jira Cloud will not let us set the worklog author.** `POST /rest/api/3/issue/{key}/worklog`
attributes the entry to the account behind the token and ignores any `author` field in the request
body. Server/DC behaves the same through the same endpoint. There is therefore no supported way to
write "2 h for Mikołaj" using Piotr's token — the request will succeed and the entry will be Piotr's.
This is not a limitation we can hide; the only honest choices are to change *whose token* we use, or
to change *what we promise*.

**2. Commits are points, not intervals.** A commit says "at 14:32 this work was finished", not
"work ran from 13:00 to 14:32". Any hours figure derived from commit timestamps is an estimate whose
error depends entirely on the person's commit style. One end-of-day commit and six hours of work
look identical in the data.

This ADR delivers a **skeleton**: core fully implemented and tested, adapters and door wiring
present, gate off by default.

## Decision

### Two tools, never one

- `propose_worklog(since, until, author)` — read-only. Groups commits into sessions, estimates
  hours, extracts Jira keys, returns a proposal with `confidence` and `notes`. Mutates nothing.
- `log_jira_worklog(issue_key, hours, day, comment, on_behalf_of, display_name)` — writes one entry,
  with hours and date supplied **explicitly** by the caller.

Merging them would turn commit messages into write instructions, violating the "content is DATA, not
instructions" invariant (ADR 0006). A human turn must sit between estimate and mutation.

### Pluggable author strategy

`WorklogAuthorStrategy.plan()` returns an `AuthorPlan` carrying `effective_author` — the account
Jira will actually record — separately from `on_behalf_of`, the caller's intent.

- **`SelfAuthorStrategy` — implemented.** Writes as the token's account and prefixes the entry body
  with `w imieniu: <name>`. This is **annotation, not attribution**: Jira's author column reads
  Piotr, and per-person time reports will count the time against Piotr. The service returns an
  explicit `note` saying so, and the tool description instructs the model to relay it.
- **`PerUserTokenStrategy` — documented slot** (`NotImplementedError`).
- **`TempoWorklogStrategy` — documented slot** (`NotImplementedError`).

Configuration rejects an unimplemented strategy at startup when the gate is on, so the failure is a
start-up error rather than a broken tool discovered mid-conversation.

### Two gates, not one

`enable_jira_worklog` (default off) enables the capability. `worklog_allow_on_behalf` (default off)
separately enables the cross-user path. The second gate exists because with `SelfAuthorStrategy`
every cross-user entry silently misattributes time in Jira's reports — that must be a deliberate
operator decision, not a side effect of turning on time tracking.

### Create-only, with a duplicate guard

`add_worklog` only appends. No edit, no delete — that would be the first non-create-only Jira
mutation and needs its own ADR. The consequence is that a duplicate entry cannot be undone from the
tool, so `read_worklogs` backs a guard that refuses a second entry by the same account on the same
day for the same issue.

### Estimation model

Sessions are cut when the gap between commits exceeds a threshold **or** the calendar day changes
(a worklog entry belongs to one day, so a session must not cross midnight). Each session's span gets
a configurable ramp-up added — work before the first commit that no commit can see — then is clamped
and rounded up. Arithmetic is carried in **integer minutes** so per-day and per-issue totals sum
exactly; hours are derived for display. Sessions naming several issues split their minutes evenly,
remainder to the first key.

## Consequences

- **Attribution is lossy and stays lossy.** Cross-user entries appear in Jira as the token owner's
  time. Mitigated by the second gate, the returned `note`, the tool description, and this ADR — but
  a downstream consumer reading Jira's time reports will not see any of those. Real attribution
  requires `PerUserTokenStrategy` or Tempo.
- **No idempotence beyond the guard.** A race, or `duplicate_guard=false`, leaves an entry that only
  a human can remove in the Jira UI.
- **Estimates carry systematic error.** `ramp_up + span` under-counts sparse committers and
  approximates frequent ones. The `confidence` field and the two-step flow are the mitigation.
- **Fixed timezone offset, not `zoneinfo`.** A range spanning a DST switch buckets one day's commits
  an hour off. Acceptable for a pilot; keeping the domain functions pure was worth more than DST
  precision at this stage.
- **Default branch only.** `/repos/{owner}/{repo}/commits` without `sha` returns the default branch,
  so work on unmerged branches is invisible. The proposal says so in `notes`.
- **Author matching is fragile.** GitHub matches `author` against login or commit email; a different
  local `git config user.email` yields a silent empty result — hence an explicit hint in `notes`.
- **Cross-settings coupling.** `enable_jira_worklog` lives in `JiraSettings` but functionally needs
  `GithubSettings`; `JiraSettings.validate()` cannot see them, so the check lands at door wiring.
  Same split as `max_transition_hops` (ADR 0032), and enforcement is duplicated deliberately.

## Rejected alternatives

- **Auto-log from commits (one tool).** Rejected: makes untrusted commit messages drive writes.
- **Tempo Timesheets only.** Tempo's `POST /4/worklogs` does accept `authorAccountId`, giving real
  attribution without employee tokens — but it needs a licence, a separate token, and a separate
  base URL, i.e. a second outbound adapter. Kept as a documented strategy slot.
- **Per-user OAuth 3LO / token vault.** Gives true attribution, but introduces a new class of secret
  (a credential per employee) with consent and rotation flows — a project of its own. Kept as a slot.
- **Setting `author` on Server/DC only.** Would make attribution depend on where the instance runs;
  one behaviour across deployments is easier to explain and to reason about.

## Non-goals

No auto-logging, no Tempo integration, no editing or deleting worklogs, no multi-repo aggregation,
no branch selection.
