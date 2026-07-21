# 0035. Weekly per-person WorklogPRO sheet generation and Teams 1:1 delivery

Date: 2026-07-20
Status: accepted
Author: P0w3r223
Related to: docs/adr/0006-write-capability-gate-2.md,
  docs/adr/0018-agent-working-directory.md,
  docs/adr/0022-proactive-dual-target-teams-push.md,
  docs/adr/0026-agent-file-reply-in-thread.md,
  docs/adr/0027-agent-outbound-file-push-to-user.md,
  docs/adr/0034-jira-worklog-from-github-commits.md

---

## Context

ADR 0034 attempted to write worklogs directly, and ran into a hard wall: Jira Cloud attributes every
worklog to the token's account and ignores any `author` field. Logging time "for Mikołaj" from
Piotr's token was verified live on 2026-07-20 — the entry appears in Jira as Piotr's, with the real
person named only in free text. Attribution was lossy by construction.

WorklogPRO (The Starware) is now installed on the instance, which changes the shape of the problem.
Its bulk import reads a spreadsheet with a `User` column, so **the employee imports their own
hours** and the worklog carries a real author. We no longer need to write on anyone's behalf; we
need to hand each person a correct file and tell them about it.

Hours will come from an external system, not from commit history. That source is not yet chosen.

## Decision

A new inbound door, `workmate-worklogi`, runs every Friday: it reads the past week's hours, builds
one WorklogPRO import sheet per person, and sends that person a private Teams message containing a
table of their hours and the path to their file. Only people who actually worked are messaged.

**The reported week is the one that has closed** — the previous Monday–Sunday, not the one the run
falls in. See § Consequences: the original "current week" choice silently lost every weekend.

### Excel, not the WorklogPRO GraphQL API

WorklogPRO exposes `addWorklog(authorAccountId: …)` behind a "Log work for others" permission, which
would give true attribution without employee tokens. We chose the spreadsheet path anyway, because
it keeps a **human between the data and the mutation**: the employee sees their hours before
anything reaches Jira. Given that hours arrive from a system we do not yet control, that review step
is worth more than the automation it costs. The API remains the obvious next step if review becomes
a bottleneck.

### Table in the message body, not an attachment

Sending the file itself requires `Files.ReadWrite.All`, blocked on admin consent since 2026-07-17
(ADR 0026/0027). Rather than wait, the message carries the hours as an HTML table and names the file
path as plain text.

This required a **new port method**, `TeamsNotifier.send_chat_html`. The existing `send_chat` routes
through `to_teams_html`, which renders CommonMark with `html=False` and deliberately does not enable
tables — a table passed to it arrives as `&lt;table&gt;`. The new method posts HTML unchanged, and
that is only safe because of a contract: the HTML is built by `core/domain/timesheet_message.py`, a
pure function that `html.escape`s every interpolated value into a fixed skeleton. Untrusted content
(GitHub, Jira, model output) must keep using `send_chat`. A regression test pins that `send_chat`
still escapes raw HTML.

### Identity: Graph for Teams, explicit config for Jira

Three systems, three identifiers, none derivable from the others. `TeamMember.Read.All` was added to
`_DEFAULT_TEAMS_PUSH_SCOPES` — **no new admin consent**, because WorkMate shares the app
registration, tenant and MSAL cache with `Powiadomienia_teams`, where the scope has been consented
since 2026-07-14. WorkMate simply was not asking for it.

Graph supplies `aad_user_id`, the display name, and validates current team membership. It cannot
supply the Jira account: on this instance Jira identities include private addresses outside the
tenant. That mapping is an explicit YAML file.

The directory is **fail-closed**. An unknown `source_id`, a missing `jira_user`, or a person no
longer in the team yields no file and no message, plus an entry in the run report. Matching by
display name is explicitly rejected: a wrong `User` value imports someone's hours into another
person's Jira account, and ADR 0034 established those worklogs are create-only and unremovable by
tooling. A missing message is recoverable; someone else's time in your timesheet is not.

### Sheet projection in core, writer in the adapter

Core produces `Sheet(headers, rows)`; the openpyxl adapter only dumps it. Everything that can
actually be wrong — column names, duration notation, ISO offset, person filtering — is pure and
tested without the SDK. Since the schema is unconfirmed it *will* churn, and churn should land in a
tested constant rather than in an adapter tested only by file round-trip.

Two format decisions worth recording. Duration never uses `1d`, because a Jira "day" is a
configurable number of hours and the file must be portable across instances. `Start Date & Time`
carries a full ISO 8601 timestamp with an explicit offset, because a date alone makes WorklogPRO
apply the *importing browser's* timezone — the same file would produce different results for
different people.

### Ported, not shared

`week.py` and `single_instance.py` are ports of `Powiadomienia_teams` code, not imports.
`Powiadomienia_teams` is a separate uv project with its own venv, so sharing means a path dependency
and coupled CI to own ~150 lines of finished, third-party-free date math. Copies can drift; the
mitigation is a "keep in sync" note in each module docstring plus ported tests. Deliberately
**not** copied: `github/state.py`'s non-atomic `write_text` — worklogi state uses temp + `os.replace`,
because a truncated state file means everyone gets a duplicate message.

### Ordering and isolation

Sheet is written **before** the message. A crash between them leaves a file nobody was told about
(harmless, retried next run) rather than a message pointing at a file that does not exist. Each
person is processed in isolation and state is saved after every success, so a crash at person seven
of twenty does not re-message the first six.

## Consequences

- **Re-import duplicates worklogs.** The human performs the import, so our idempotency cannot cover
  it; WorklogPRO's own duplicate behaviour is undocumented and unverified. Mitigated only by the
  week label in the filename and message, plus an explicit warning in the message footer.
- **The schema is a hypothesis.** `WORKLOGPRO_HEADERS` comes from vendor documentation whose exact
  casing could not be confirmed; matching is by column name, so a typo invalidates every file. The
  authoritative source is the template downloaded from the import wizard **on this instance**.
  **Since 2026-07-21 a live run refuses to start** without `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`
  — the hypothesis is now a deliberate operator decision instead of a comment nobody reads. Dry runs
  are unaffected, because a dry run is exactly how you produce the file to compare. The test that
  pins the tuple is a *change detector*, not a correctness gate: it cannot know what the real
  headers are, only that nobody edited ours by accident.
- **Import may be admin-only.** Vendor docs describe the wizard as available to administrators. If
  confirmed, the per-person model collapses into a single consolidated file for an admin — a
  materially different application. Unverified at time of writing.
- **No file attachment** until `Files.ReadWrite.All` is granted. The path is text, on a share the
  employee must be able to reach.
- **Ported code can drift** from `Powiadomienia_teams`.
- **Reporting the current week lost every weekend — corrected 2026-07-21.** The original text
  claimed hours after the Friday deadline "fall into next week's report". They did not: the next run
  reported *its own* current week, so Saturday, Sunday and Friday afternoon never reached any sheet,
  ever. `reported_week` now returns the closed week, which is complete by definition. The cost is a
  report that is 3–12 days old — cheap for a document that must be reviewed and imported by hand.
- **Two processes now message the same six people weekly** (Shifts reminders and timesheets). No
  coordination between them; if this becomes noise, merging the schedules is the follow-up.
- **Formula injection is blocked by the writer, not by mangling the text.** A comment beginning with
  `=` becomes a formula when a spreadsheet library infers the cell type, and we explicitly tell
  people to open this file. `SheetWriter` now carries a contract — every cell written as text — and
  the openpyxl adapter forces the string type. The core strips control characters (the sanitization
  `WorkEntry` had promised and nobody implemented) but leaves content otherwise untouched, so the
  comment reaches Jira as written.
- **Sheet filenames now include `source_id`.** The name alone was not injective: two people sharing
  a display name overwrote each other's sheet, and the first person's message then pointed at
  someone else's hours. `_slug` narrowed it further — a name with no ASCII characters collapsed to
  a constant.
- **This ADR made most of ADR 0034 dead code**, and the write path was removed on 2026-07-21 (see
  the superseding note there). What survives is `propose_worklog` — read-only, GitHub-side.
  Its estimator fits `HoursSource` structurally (`read(since, until) -> list[WorkEntry]`), and
  wiring it would be a few lines — **do not**. This ADR deliberately left estimation behind for
  measured data; feeding guessed hours into a sheet a human imports into Jira as fact would
  reintroduce, one layer lower, exactly the dishonesty the WorklogPRO route was chosen to avoid.

## Rejected alternatives

- **WorklogPRO GraphQL API.** Removes the human review step that motivated the Excel path.
- **A third uv package for shared date math.** Three lockfiles and three CI jobs to own 150 lines.
- **Main project depending on `Powiadomienia_teams`.** Inverts the dependency and drags
  `Schedule.ReadWrite.All`, MSAL and Anthropic into WorkMate's resolution.
- **Deriving Jira identity from display name.** Cheap and wrong; see fail-closed above.
- **Living in `Powiadomienia_teams`.** Would have reused more, but the user chose the main project
  for one core and one set of conventions.

## Non-goals

File attachments in Teams, writing to Jira directly, an agent tool ("generate my sheet now" would
need `extra_catalog` and its own ADR), multi-team support, e-mail as a fallback channel.
