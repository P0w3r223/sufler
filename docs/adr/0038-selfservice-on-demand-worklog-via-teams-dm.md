# 0038. Self-service on-demand worklog: authenticated Teams DM submission → WorklogPRO sheet reply

Date: 2026-07-24
Status: superseded by docs/adr/0055-withdraw-worklogpro-timesheets.md (2026-07-30 scope decision:
  WorklogPRO / timesheets module withdrawn from the project). Formerly: proposed (built: submission
  parse + single-person compose + operator-pilot door; file-attachment reply delivered A′4 2026-07-28;
  live 1:1 intake pending — M5)
Author: P0w3r223
Related to: [[0035-weekly-per-person-worklogpro-sheets-and-teams-dm]],
  [[0036-shift-worklog-integration-identity-and-week-contract]],
  [[0037-claude-summary-collection-authenticated-teams-dm]],
  [[0026-agent-file-reply-in-thread]], [[0027-agent-outbound-file-push-to-user]],
  0015/0016 (Teams delegated door + attachments)

> **Update 2026-07-27:** `Files.ReadWrite.All` admin consent has been granted — the file-attachment
> reply (M4) is no longer scope-blocked; only the build (`TeamsFileSender`, ADR 0026/0027) remains.
> The live 1:1 intake (M5) is unaffected by this update.
>
> **Delivery note (A′4, 2026-07-28):** the file-attachment reply is now **built**, gated behind
> `SUFLER_WORKLOGI_ENABLE_ATTACHMENT` (default OFF, shared with the batch door ADR 0035). When on,
> `handle_submission(deliver_as_attachment=True)` renders the reply in **attachment mode** (no server
> path) and the door delivers the `.xlsx` via `UserDocSender.send_document_to_user` (OneDrive upload
> → `invite` → 1:1 chat → `reference` attachment), passing the rich reply HTML as `caption_html`.
> `file_path` is still returned — the door needs it to read the bytes to upload. Path-fallback stays
> the default when the gate is off. Real upload = operator (device-code re-consent for the file scope).

---

## Context

ADR 0035/0036 built a **batch** timesheet pipeline: on Friday an operator runs `sufler-worklogi`,
it pulls the whole team's Shifts hours + GitHub commits + `claude_summary` output, generates one
WorklogPRO `.xlsx` per person, and DMs each person a **text pointer** to the file on a share (the
human imports it — the worklog then has a real author). Two things make that shape heavy for the
"I need my weekly work certificate now" use case:

- **Collection is unsolved.** The `claude_summary` output lives on each person's own machine and is
  self-asserted by a `person` field; ADR 0037 deferred the authenticated collector and runs the
  pilot on manual operator file-drops into `summary_dir`.
- **It is a scheduled fan-out**, not a request the individual can trigger for their own closed week.

This ADR reframes the same machinery as a **self-service, on-demand, request→response** flow in the
existing 1:1 Teams chat: the person runs `claude_summary` on their machine (consent + always-on
redaction), sends the resulting JSON to the bot, and the bot replies with a ready-to-import `.xlsx`
that the person downloads and imports themselves. It fuses three already-designed pieces into one
interactive turn — ADR 0037 (authenticated-DM submission, sender-based attribution) + ADR 0035 (the
WorklogPRO sheet writer) + ADR 0026/0027 (replying with a file).

The key simplification over the batch pipeline: **attribution is the authenticated sender**, so there
is no central collection step and no self-asserted-`person` trust problem — the person a submission
is credited to is whoever is in the 1:1 chat (Graph `from.user.id`), resolved to a `Person` via the
identities directory. The submitted JSON's `person` field is used **only** as a cross-check against
the sender's `git_email`; a mismatch is rejected (never re-keyed) — ADR 0037 rule 3.

Hard constraints unchanged: `core ↛ adapters`; frozen MCP surface untouched; per-door write gate
(ADR 0006); OUTPUT_DIR outside `data/` and outside the repo (ADR 0035); hours are **real** Shifts
minutes, never estimated (ADR 0036).

## Decision

Adopt a **new inbound door in the `sufler` package** (`adapters/inbound/worklog_selfservice/`),
not an extension of `Powiadomienia_teams`. Rationale: the worklog domain, the xlsx writer, the
identity bridge, and the inbound-attachment path (ADR 0016) all live in `sufler`; `Powiadomienia_teams`
has no attachment handling, no identity resolver, and no sheet generation, and should stay a narrow
Shifts-nudge bot (the two share only the MSAL cache / Graph identity). The flow is **deterministic
and attribution-sensitive** — a handler, not a free-form agent conversation; the LLM plays no role in
who gets credited or how many hours are logged.

**Data flow (one request):**

1. Resolve the authenticated sender (`from.user.id`) → `Person` via `GraphIdentityDirectory.resolve_by_aad_user_id` (fail-closed; unknown sender / not-in-team → reject).
2. Parse the submitted `claude_summary` JSON → per-day issue keys (from commit messages) + per-day comments + declared `person` email + window (`since`/`until`). Cross-check declared `person` against the sender's `git_email`; **reject on mismatch or missing `git_email`** (fail-closed).
3. Pull the **sender's** Shifts for the submission's window (real minutes per local day), isolating to the sender's `aad_user_id`.
4. Compose the timesheet: split each day's real minutes across that day's submitted issue keys (basket issue for keyless days), attach that day's comment; build the single-person `PersonTimesheet`.
5. Render the WorklogPRO `.xlsx` (text cells) and **reply with the file in the 1:1**.
6. The person downloads and imports it in Jira (real author = themselves; re-import duplicates, as warned by ADR 0035).

**Window contract.** The window comes from the **submission** (`since`/`until`), not `reported_week`
— the user decides which week they need. `claude_summary` reports `until` **inclusive**, so the
service converts to the domain's half-open `[since, until+1day)`, otherwise the last day (Sunday)
would drop out of both Shifts and the sheet.

**Delivery / the one hard dependency.** Replying with the actual `.xlsx` attachment is the single
un-built, admin-scoped piece: it needs Graph `Files.ReadWrite.All` (admin consent + one-time
device-code re-consent) and the outbound-attachment primitive from ADR 0026/0027 (`TeamsFileSender`
+ upload/reference). Until that scope is granted, the door ships the **path/link fallback** (today's
`sufler-worklogi` behavior, ADR 0035 how-to §7): identical flow, minus the attachment. The file
reply is gated behind `enable_user_file_push` (default OFF), promoting ADR 0026/0027 from proposed →
accepted when it lands.

## Options considered

- **A1 (chosen).** New inbound door in `sufler`, single-person on-demand handler, reusing the
  worklog domain + identity bridge + Shifts source unchanged; attribution = authenticated sender;
  file reply gated on the ADR 0026/0027 scope with a path/link fallback.
- **A2 (rejected).** Extend `Powiadomienia_teams`. It has none of the needed machinery (attachments,
  identity resolver, sheet writer) and would duplicate `sufler`; bloats a deliberately narrow bot.
- **A3 (rejected for this decision).** Fully local CLI that generates the xlsx on the user's machine
  with no bot round-trip. Simplest and zero new scope, but **incompatible with Shifts hours** — only
  the bot (delegated Graph) can authoritatively read published shifts, and a work certificate needs
  real hours. Kept as a possible offline dry-run harness, not the product.

## What is already built (this ADR's core)

- `core/domain/submitted_summary.py` — pure parse of the submitted `claude_summary` JSON (issue keys
  + comments + window) and `verify_submission_owner` (ADR 0037 rule 3); `SubmissionRejected` fail-closed.
- `core/application/selfservice_worklog.py` — single-person on-demand compose: Shifts minutes ×
  submitted keys/comments → `PersonTimesheet` → WorklogPRO sheet, with sender isolation (aad +
  source_id) and the inclusive→half-open window conversion. Reuses `minutes_by_person_day`,
  `split_day_minutes`, `build_timesheet`, `project_sheet`, `sheet_filename`, `week_label` unchanged.
- Tests mirror both (`tests/core/domain/test_submitted_summary.py`, `tests/core/test_selfservice_worklog.py`).

Also built (M3): the operator-pilot door `adapters/inbound/worklog_selfservice/` — a
`sufler-worklog-selfservice` CLI that takes one submitted JSON + a `source_id`, resolves the
person via the identity bridge, runs `handle_submission` (parse → verify owner → compose sheet →
render reply), and with `--send` delivers a **path-fallback** DM via the existing `HttpxTeamsNotifier`
(no new Graph scope). Submission idempotency (`state.py`, keyed `<source_id>:<week>`) marks only on
success. Reuses `WorklogiSettings`; `--send` is gated on `SUFLER_WORKLOGI_HEADERS_CONFIRMED`. See
`docs/how-to/worklog-selfservice.md` — **plik usunięty razem z modułem przy wycofaniu
WorklogPRO (ADR 0055); wskazanie zostaje jako zapis tego, co wtedy istniało**.

Pending (M4/M5): live 1:1 intake (submission via ADR 0016 attachment or pasted text; sender-bound
attribution replacing the operator's `--source-id`), inbound discovery of unsolicited DMs (Graph
subscription or `/me/chats` poll), per-`message.id` idempotency, and the file-attachment reply
(ADR 0026/0027 `Files.ReadWrite.All` scope, `enable_user_file_push` gate).

## Threat model

- **Comment/hours misattribution.** Closed structurally: everything is keyed by the authenticated
  sender; a crafted or mistaken submission can only ever affect the sender's own sheet, never a
  colleague's. The `person`-vs-`git_email` cross-check catches an accidentally-forwarded foreign
  export (reject, never re-key).
- **Prompt-injection via submitted content.** Content is DATA: `claude_summary` redacts on the
  sender's machine, the handler executes nothing from it, and the sheet writer forces text cells
  (formula-injection neutralized, ADR 0035).
- **Outward file push to a person.** Inherits ADR 0027's control: the destination is bound to the
  current sender (the model never names a recipient), gate OFF by default.
- **Scope escalation.** The delegated token gains `Files.ReadWrite.All` only when the attachment
  reply is enabled — documented as the deploy blocker; the path/link fallback carries no new scope.

## Consequences

- Medium blast radius when the door lands (`adapters/inbound/worklog_selfservice/`, `core/ports/`
  + `adapters/outbound/` for `TeamsFileSender`, `config.py`); the core composed here is additive and
  already green.
- Reuses ~90% of the ADR 0035/0036 machinery; the genuinely new surface is intake + delivery + discovery.
- Reversible: `enable_user_file_push` OFF keeps the path/link behavior; the door is opt-in via config.
- Relationship to the batch pipeline: the two coexist. Batch remains the scheduled team-wide safety
  net; self-service is the individual's on-demand path. Both preserve human-does-the-import.
