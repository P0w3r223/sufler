# ADR 0036 — Weekly worklog integration: identity bridge, week/date contract, composite hours source

Date: 2026-07-23
Status: accepted (assembled S1–S5: identity, Shifts hours, commit issue-attribution, claude_summary
comments, wired into the `workmate-worklogi` door and covered by an end-to-end dry-run test; go-live
still gated on operator confirming `WORKLOGPRO_HEADERS` — S5 § go-live)
Author: Patryk
Related to: 0034 (worklog from commits, write path removed), 0035 (WorklogPRO sheets + Teams DM)

---

## Context

The goal is a weekly, per-person Jira timesheet that combines three signals for a **closed** week
(previous Mon–Sun): worked hours/days from the schedule, and what tasks the person did each day, with
day labels that match Jira's calendar dates exactly. Three tools hold the pieces: `Powiadomienia_teams`
(Microsoft Shifts schedule), `claude_summary` (per-day task descriptions from Claude Code prompts +
commits), and WorkMate's Jira bridge.

The existing `workmate-worklogi` door (ADR 0035) already implements the honest output path — one
WorklogPRO `.xlsx` per person that the **employee imports** (so the worklog carries a real author) —
and already solves date correctness (`timesheet_sheet.format_started`: full ISO-8601 + explicit
offset, DST-safe) and closed-week selection (`week.reported_week`, half-open `[start, end)`). Its
hours source is a placeholder (`JsonHoursSource`). This ADR sets up the integration; Session 1
delivers the identity bridge, the week/date contract, and the config seam **without changing behavior**
(the JSON source stays the default; `shifts` is allowed but not yet wired).

## Decisions

**Output stays the WorklogPRO sheet, not an API write.** Jira attributes `POST .../worklog` to the
token account and ignores any author field (verified live 2026-07-20; ADR 0034 removed the write
path). Per-person attribution is only honest via the human-imported sheet. So the integration feeds
the existing sheet pipeline; it does not add a Jira worklog write.

**Composite hours source (`hours_source=shifts`).** The single insertion point is a new `HoursSource`
that, per person and day for the closed week, assembles: real worked minutes from **Microsoft Shifts
(Graph)**; Jira issue keys from that person's **commits** (`worklog.extract_issue_keys`); and the
day's task description from **`claude_summary`** as the `comment`. Everything downstream
(`WeeklyTimesheetService`, sheet projection, Teams DM, identity, scheduling, idempotency, single
instance, output safety) is reused unchanged. Session 1 only registers `shifts` as a valid source and
its config; the door raises a clear "not yet wired" error until later sessions implement it.

**Honesty distinction vs ADR 0035.** ADR 0035 forbids feeding *estimated* commit hours into the sheet
(a human imports it as fact). Here the hours are **real** (Shifts is the schedule system of record);
commits only **distribute** those known hours across issues, and a human still reviews the sheet
before importing. Estimating the *quantity* from commits remains forbidden; using commits to decide
*which issue* the real hours belong to is allowed.

**Identity bridge — `git_email` as an optional fourth identifier.** `Person` and `identities.yaml`
gain `git_email`, bridging commits/`claude_summary` (keyed by git email) to the person keyed by
`source_id`/`aad_user_id`/`jira_user`. It is optional: an empty `git_email` means "no per-commit
attribution" and that person's hours all go to the fallback issue. The directory gains reverse
lookups `resolve_by_aad_user_id` (input from Shifts) and `resolve_by_git_email` (input from
commits/`claude_summary`), both fail-closed and, in the Graph variant, gated by current team
membership via a shared `_gate`. `_reject_shared_identifiers` now also rejects a shared non-empty
`git_email` (a copied YAML block would attribute one person's commits to another).

**Week/date contract (single source of truth).** Every future source and the sheet MUST use the same
closed-week window `week.reported_week(now, tz)` and bucket each event by its **local date in `tz`**
(the one `WorklogiSettings.tz_name`), and the sheet MUST stamp dates via `timesheet_sheet.format_started`.
This is what guarantees the user's requirement that day labels match Jira dates and no entry lands on
the wrong day across DST.

**Fallback issue is required for `shifts`.** A configured catch-all Jira key
(`WORKMATE_WORKLOGI_FALLBACK_ISSUE`, validated to the `PROJ-123` shape) receives time on days with no
issue key in commits, so no sheet row is left without an `issue_key` (WorklogPRO would reject it).
`WORKMATE_WORKLOGI_SUMMARY_DIR` points at the collected `claude_summary` output.

## Consequences

- Session 1 is behavior-neutral for the JSON source; `shifts` is validated but the door fail-fasts
  until wired, so no silent wrong data.
- The `claude_summary` **collection** mechanism (asking each user to run it on their machine) is
  deliberately deferred; the integration reads a collected store, whose ingestion seam is designed later.
  Trust boundary for that seam (S6): the store attributes a file to the person named in its top-level
  `person` field, so today it leans on the operator hand-placing files in `summary_dir`. When collection
  is automated, the seam MUST bind a file's actual origin to its claimed `person` — otherwise one user
  could attribute fabricated day text to a colleague's sheet. Until then, `summary_dir` is trusted input.
- The reverse lookups live on the concrete directory classes, not the `IdentityDirectory` protocol, so
  existing fakes are untouched; the composite source (later session) depends on the concrete directory.
- `git_email` is **case-folded** (canonicalized to lowercase at load and at lookup, incl. collision
  detection) — done in S3, when commit attribution was wired, so a mixed-case commit email still
  resolves the person instead of falling to the bucket issue. (S1 originally matched it case-sensitively;
  that was corrected in S3.)

## Multi-session roadmap

S1 identity bridge + week/date contract + config (this ADR). S2 real hours from Shifts (Graph). S3
issue attribution from commits + fallback. S4 `claude_summary` comments. S5 assembly + header
confirmation + dry-run→live. S6 (later) collection from user machines + scale — designed in
[[0037-claude-summary-collection-authenticated-teams-dm]] (authenticated Teams DM; manual for pilot,
build deferred to post go-live). First tester: the author, single person, dry-run.
