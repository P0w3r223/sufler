# 0053 — Proactive weekly change digest (Monday DM)

Date: 2026-07-29
Status: proposed
Author: P0w3r223
Related to: [ADR 0052](0052-change-digest-from-teams-mention.md) (F5 pull digest — reused composition),
[ADR 0035](0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md) (weekly scheduler door pattern — `worklogi`),
[ADR 0022](0022-proactive-dual-target-teams-push.md) (proactive Teams push / 1:1 chat),
[ADR 0019](0019-shared-event-store.md) (shared EventStore),
[ADR 0006](0006-write-capability-gate-2.md) (gated, off-by-default capability)

---

## Context

F4/F5 answer questions the user *asks*. The standup ritual is the inverse: a short, unprompted "here is what
changed in the pion last week", pushed on Monday morning without anyone having to remember to ask. That is the
last Tor C item and the only PROACTIVE one — a scheduler that pushes, not a router that answers.

Everything it needs already exists:

1. **Composition** — `ChangeDigestService.since(day)` (ADR 0052) folds bridge events into a deterministic,
   per-project digest with no LLM. F6 reuses it verbatim with `since = run_day − 7`.
2. **Scheduler** — the `worklogi` door (ADR 0035) is a long-running entry-point that computes the next
   `weekday`/`hour`, sleeps with a heartbeat, catches up a missed deadline, survives a failed run, and dedups
   "who already got it this week" in an atomic state file. F6 is the same shape with a different payload.
3. **Delivery** — `HttpxTeamsNotifier.send_chat(user_id, text)` (ADR 0022) sends a 1:1 DM; markdown is rendered
   with links disabled and every value escaped (event content is DATA).

Hard constraints: `core/` never imports adapters (F6 reuses the core `ChangeDigestService` unchanged); the
frozen MCP surface and `NoteMetadata` are untouched (this is a scheduler door, not a tool); a proactive push to
real people is outward-facing, so it is gated off-by-default AND ships in dry-run first; event content is DATA.

## Decision

1. **A new scheduler door `workmate-teams-digest`**, structurally mirroring `worklogi`: `--once` / `--login` /
   loop modes; `next_run(weekday=Mon, hour, minute)` + heartbeat + single-instance lock; a missed-deadline
   catch-up bounded by `max_catchup_days`; `_safe_run_once` so one failure never kills the loop. It reuses the
   generic `worklogi.state` dedup store (`(week_label, recipient)` → timestamp, atomic write, tolerant read)
   and `core/domain/week` — no scheduler logic is re-implemented.

2. **Recipients are an explicit env allow-list**, `WORKMATE_TEAMS_DIGEST_RECIPIENTS` (AAD user ids). A proactive
   DM to a person requires deliberate audience selection, not "everyone in the identity map" — an env list makes
   the audience an explicit operator choice and cannot silently grow. `validate` rejects `enabled` with no
   recipients (a door that can send to nobody is a mistake).

3. **Payload = the F5 digest, events only.** `deliver_weekly_digest` (a pure, injected-`send`/`mark` function)
   builds `ChangeDigestService.since(run_day − window_days)`, and for each recipient not already marked for the
   week, sends the rendered digest and marks them — at-least-once with no double-send across restarts. An empty
   week is NOT DM'd (no "0 changes" spam) but the week is still marked handled, so catch-up detection stays
   consistent. Notes are intentionally out of scope for v1 (kept a pure events fold; a notes section is a
   future extension).

4. **Two-stage gate.** `enabled` (default OFF) builds the door; `dry_run` (default ON) makes a run render and
   log the digest without sending or persisting state, so the operator can preview before the first real DM.
   Real sending needs `enabled=true` AND `dry_run=false`. Delivery reuses the `worklogi` sync→async bridge (an
   `AsyncClient` created and closed inside one `asyncio.run` per recipient — avoids the closed-loop reuse bug).

## Consequences

- **Positive.** Reuses the F5 composition, the ADR 0035 scheduler, the ADR 0022 DM channel, and the generic
  state/week helpers — the new code is thin glue plus a pure, unit-tested delivery function. No new MCP tool, no
  `NoteMetadata` change, no calendar dependency (EventStore only). Idempotent per (week, recipient); a failed
  recipient is retried next run, never double-sent.
- **Negative / trade-offs.** A new long-running process to deploy (systemd/timer), like `worklogi`. The digest
  is events-only for now (no notes). The reporting window (`run_day − window_days`) is a rolling 7 days, not a
  strict Mon–Sun closed week; for a Monday run these nearly coincide, and F5's `truncated` flag already surfaces
  scan-cap incompleteness.
- **Security.** Outward-facing push, so off-by-default + dry-run-first + an explicit recipient allow-list;
  event content is sanitized on ingest and escaped again by `send_chat` (no injection into the DM).

## Alternatives considered

- **In-process timer inside the `teams_graph` poller.** Avoids a second process, but couples an unrelated
  weekly cadence to the interactive door's lifecycle and complicates its loop. Rejected: the `worklogi`
  precedent (a dedicated scheduler door) is cleaner and already battle-tested.
- **Recipients from the identity map** (`meeting_note_identities`). Reuses the roster, but sends a proactive DM
  to everyone in it — too blunt for a first rollout. Rejected in favour of an explicit allow-list.
- **Include a notes section (EventStore + notes).** Richer, but adds a second fold and render surface. Deferred;
  v1 stays a pure reuse of the F5 events digest.
