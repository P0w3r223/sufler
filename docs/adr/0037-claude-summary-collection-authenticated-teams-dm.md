# ADR 0037 — Collecting `claude_summary` output: authenticated Teams DM (design), manual for pilot

Date: 2026-07-23
Status: proposed (build deferred to post go-live; manual collection for the pilot)
Author: P0w3r223
Related to: [[0036-shift-worklog-integration-identity-and-week-contract]], 0035 (WorklogPRO sheets),
0015/0016 (Teams delegated door + attachments), `Powiadomienia_teams` (1:1 chat lifecycle)

---

## Context

The `shifts` hours source (ADR 0036) fills each WorklogPRO sheet's Comment column from per-person
`claude_summary` output — JSON produced on each employee's own machine (they run `claude_summary`
with consent; its top-level `person` field is that machine's `git config user.email`). WorkMate reads
those files from a configured `summary_dir` and indexes them by the `person` field
(`ClaudeSummaryStore`). ADR 0036 deliberately deferred the **collection** mechanism (how files travel
from user machines into `summary_dir`), and the S4/S5 security review flagged the load-bearing risk:

**The `person` field is self-asserted.** Under manual, operator-placed collection this is fine — the
operator vouches for each file's origin (trusted input). But if collection is automated with files
arriving straight from user machines, a user who set their local git email to a colleague's could
submit a file claiming `person: colleague@example.org`, and their prose would land in the colleague's
sheet. Attributing a person's work text to someone else is exactly the class of error the pipeline is
otherwise fail-closed against (ADR 0036 identity binding). Hours and issue split are unaffected (they
come from Shifts/AAD and the GitHub author filter), so the blast radius is comment misattribution —
still unacceptable at scale.

The core (S1–S5) is not yet live — go-live is gated on the operator confirming `WORKLOGPRO_HEADERS`
(ADR 0035). Building an automated collector before the core proves out in production would be
premature and risks building for the wrong operational shape.

## Decision

**Target design: authenticated collection via Teams 1:1 chat. Build it later; use manual collection
for the pilot now.**

**Authenticated collection (the target).** A collector reads `claude_summary` JSON that each employee
sends into their existing 1:1 Teams chat with the bot (the same chat `Powiadomienia_teams` already
creates and reads replies from). The person a file is attributed to is the **authenticated Teams
sender** (`from.user.id` on the Graph message — Teams verifies who sent it), resolved to a `Person`
via the identities directory (`aad_user_id`), **never** the self-declared JSON `person` field. The
collector then writes the file into `summary_dir` named authoritatively by that identity (so the
worklogi store reads it under the correct person). The JSON's own `person` field is, at most,
cross-checked against the authenticated sender's `git_email` and the submission **rejected on
mismatch** (fail-closed) — it is never the source of attribution. This is the only mechanism that
binds file origin to person cryptographically (Graph auth), closing the security note.

Reused infrastructure: `Powiadomienia_teams` 1:1 chat lifecycle (`create_or_get_chat`,
`list_chat_messages`), the Graph attachment path from ADR 0016 (download + extract, size limits) for
files sent as attachments rather than pasted text, and the worklogi `GraphIdentityDirectory`
(`aad_user_id → Person`, team-membership gated, fail-closed). Content stays DATA: `claude_summary`
redacts on the sender's machine; the collector executes nothing from it and the sheet writer forces
text (ADR 0035).

**Manual collection (the pilot, now).** For the pilot (the author, then a few people), the operator
collects each person's `claude_summary` JSON and places it in `summary_dir`. The operator vouches for
origin, so the `person`-field indexing is acceptable — `summary_dir` is trusted operator-controlled
input, exactly the model the S5 security review validated. This is what ships today; no code changes.

**Multi-person rollout is already config-ready** (no build): `identities.yaml` scales to the whole
team (each with `git_email`), `WORKMATE_WORKLOGI_ONLY_SOURCE_IDS` gates the pilot to a subset,
catch-up (`max_catchup_days`) covers missed Fridays, and `RunReport` (sent/skipped/failed) is already
structured for monitoring.

## Security requirements for the future collector

1. Attribution = authenticated sender (Graph `from.user.id`), never the JSON `person` field.
2. Fail-closed: sender not in `identities` / not in the team → reject, do not store.
3. Reject on `person`-vs-authenticated-sender mismatch (do not silently re-key).
4. Enforce attachment/message size limits (reuse ADR 0016 caps); treat content as data.
5. Idempotency: one current file per person per closed week; later submission overwrites, keyed by a
   server-time watermark (same discipline as `Powiadomienia_teams`).
6. Redaction remains the sender's responsibility (`claude_summary`); the collector must not surface
   raw content anywhere except `summary_dir` (outside repo and `data/`).

## Consequences

- No code this session; the pilot runs on manual collection, which the security model already permits.
- When built, the collector is a new inbound door (or an extension of the worklogi/teams_graph door)
  and inherits the fail-closed identity + attachment machinery — modest new surface.
- Until then, do NOT expose `summary_dir` to untrusted writers; ADR 0036's "trusted input" note holds.

## Alternatives considered

- **Filename-binding only** (store keys by an operator/collector-chosen filename, `person` field just
  verifies): cheap and channel-independent, but only moves trust to whoever names the file — no
  authentication. Fine as an incremental hardening, insufficient as the answer to the security note.
- **HTTP upload endpoint with per-user tokens**: new user-facing auth infrastructure WorkMate does not
  have; Teams already provides authenticated per-user identity for free.
- **Git-based submission** (users commit their JSON, commit author = identity): puts per-person
  (even redacted) prompt/task text into a git repo — unacceptable data exposure; rejected.
- **WorklogPRO "Log work for others" API instead of sheets**: orthogonal to collection (it changes the
  *write* side, not how summaries arrive); deferred separately (ADR 0035 non-goal).
