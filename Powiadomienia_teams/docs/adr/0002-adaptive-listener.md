# Adaptive reply listener: backoff, reply-window lifecycle, auth resilience

Date: 2026-07-16
Status: accepted
Author: P0w3r223
Related to: PLAN.md (weekly cycle), CLAUDE.md (safety invariants), ADR 0001 (multi-team seam)

---

## Context

The bot nudges employees (Friday 16:00) and then listens for their reply, which may arrive
minutes — or many days — later. Three problems in the original loop made long waits wasteful or
outright broken:

1. **Fixed 10s polling all week.** `run_forever` called `poll_replies` every `poll_interval_s`
   (10s) until the next weekly deadline. Each tick cost `1 + N` Graph GETs (`GET /me` + one
   `GET /chats/{id}/messages` per open pending). During long silence this burned hundreds of
   requests per hour for nothing.
2. **`reply_window_hours` was dead config.** Declared but never enforced; a pending with no reply
   stayed `AWAITING_REPLY` forever and was polled indefinitely.
3. **Auth could hang the service.** On refresh-token expiry (rolling ~90 days / Conditional
   Access), `get_token` fell into interactive `acquire_token_by_device_flow`, which blocks on
   stdin — in a headless service it froze the whole loop with no alert.

Hard constraint (from PLAN.md / ADR 0001): **one Python process, no new infrastructure**,
synchronous-by-design Graph client, safety invariants preserved (write only after explicit "tak",
`ensure_single_owner`, reply content is data), backward-compatible state file and config.

## Decision

### 1. Adaptive backoff polling

New pure function `scheduler/backoff.py::next_poll_delay(now, last_activity, base_s, max_s, factor)`
— returns `base_s` right after activity, doubling with silence up to `max_s`. `poll_replies` now
returns a `PollOutcome(open_count, last_activity)` (latest watermark over still-open pendings), and
`run_forever` derives the next sleep from it: base interval when there was an error, the max
interval when nothing is open, otherwise the backoff value. The weekly `next_run` timer is
untouched; the delay is always clamped to the remaining time before the deadline. `poll_interval_s`
(10s) is now the floor; new `poll_max_interval_s` (default 300s) is the ceiling.

Effect: polls fast in the window right after a nudge (when replies actually arrive), then widens to
~5 min during long silence — most of the responsiveness at a fraction of the requests.

### 2. Reply-window lifecycle

`reply_window_hours` is now enforced. New pure module `reminders/lifecycle.py`:
- `is_expired(pending, now, window_hours)` — anchored on **last activity** (`watermark`) with a
  fallback to the immutable nudge time (`nudged_at`, new field). Un-engaged pendings expire
  `window_hours` after the nudge; an in-progress conversation measures silence, so no one is
  expired mid-dialogue.
- `prune_terminal(state, now, retain_hours)` — weekly GC of terminal entries (applied/declined/
  expired) so the JSON state file does not grow unbounded.

`poll_replies` runs in two phases so a reply is never dropped at the window edge: **first** it reads
and processes replies for every open pending (a reply advances `watermark`), **then** it expires
only the pendings that are *still* open past the window. Because expiry is decided after the read, a
reply arriving anywhere inside the window — even in the last poll interval — is processed, not
closed. On expiry it **commits the `EXPIRED` state before** sending (at-most-once, mirroring the
confirmed-write path), then sends one polite closure (`EXPIRED_TEXT`, gated by `send_expiry_message`).
`AuthExpiredError` re-raises out of the per-pending loop (never swallowed as a routine per-person
failure), so token loss still stops the service cleanly.

### 3. Auth resilience

`graph/auth.py`: the service token provider is **silent-only** — it builds the MSAL app and cache
**once**, refreshes silently, and raises `AuthExpiredError` on token loss instead of blocking on
device-code. Interactive `login_interactive` runs only at startup (`--login`, or first start with a
TTY). `run_forever` catches `AuthExpiredError` (before the broad handler) and stops cleanly with a
CRITICAL log, so a supervisor/systemd can alert and restart rather than the loop hanging silently.

### 4. Weekly-run resilience & single-instance safety

A full-audit review surfaced that a single transient Graph error (5xx/timeout) or a start just after
16:00 silently lost a whole week of nudges. Hardened:
- **Idempotent, per-person-isolated `run_once`** — persists state **after each send** and **skips
  members who already have a fresh open pending for the target week**, so a re-run only contacts
  those not yet notified (no duplicate nudges, no lost state on partial send). A single member's
  send failure is logged and skipped (others are unaffected), so one bad recipient never blocks the
  rest, and a later run completes the tail.
- **Bounded retry** — `_run_once_with_retry` re-attempts `run_once` with growing backoff before
  giving up for the week; `AuthExpiredError` is never retried (propagates to stop the service).
- **Catch-up window** — on start/after failure, if the scheduled time just passed (within
  `catchup_grace_hours`, default 6) `run_forever` runs the pass immediately instead of waiting a
  week, **anchored on the term time** (not "now" — so a post-midnight restart doesn't shift the
  target week by 7 days), relying on `run_once` idempotency rather than a "did it run" flag
  (`previous_run` in `scheduler/weekly.py`).
- **Confirmation-send isolation** — the `APPLIED_TEXT` confirmation is sent **outside** the try that
  guards the Shifts write, so a failed confirmation no longer reports a false "write failed" (which
  had risked duplicate manual entry); `WRITE_FAILED_TEXT` is neutral about partial writes.
- **Single-instance lock** — `single_instance.py` takes an advisory OS lock on `<state>.lock` at
  startup (msvcrt/fcntl); a second process (e.g. `--poll-once` beside the service) refuses to start,
  closing the only cross-process gap in the "commit-before-write" idempotency (which is per-process).

## Rejected alternative — Graph change notifications (webhooks)

Microsoft Graph supports change-notification subscriptions on chat messages (true push, instant,
near-zero idle Graph calls). Rejected under the no-new-infrastructure constraint: it needs a
**publicly reachable HTTPS webhook** with a validation handshake, **short-TTL subscription renewal**
(~1h for chat resources), and **encryption certificates** to receive resource data — exactly the
server + moving parts the project avoids. Adaptive polling captures most of the responsiveness at
zero infra. The `PollOutcome` / `poll_replies` seam is where a push adapter would later plug in if
latency or scale ever demands it.

## Consequences

- Reply latency during silence is bounded by `poll_max_interval_s` (default 5 min), not instant.
- Safety invariants unchanged: expiry is per-pending, commit-before-send is at-most-once, auth
  errors stop rather than partially act. `EXPIRED` is a new terminal status; `nudged_at` is a new
  optional field (old state files load unchanged via the tolerant loader).
- New config: `poll_max_interval_s`, `send_expiry_message`; `reply_window_hours` now validated `>0`.
- Tested purely: `test_backoff.py`, `test_lifecycle.py`, `test_auth.py`, plus orchestration cases
  in `test_app.py` (expiry closure at-most-once, within-window processing, `PollOutcome`).

## Amendment 2026-07-21 — hourly ceiling, activity measured at detection time

`poll_max_interval_s` raised **300s → 3600s**. Operational motivation: an absent employee (the
common case — the nudge lands Friday 16:00, plenty of people answer Monday) was polled every 5
minutes for the full 48h window, ~576 requests per pending for a chat nobody had touched. At the
1h ceiling that drops to ~56.

Raising the ceiling alone would have degraded the conversation. `last_activity` was the newest
`watermark`, i.e. the **message's** `createdDateTime`. A reply detected an hour after it was
written therefore reported ~1h of idle time and the *next* delay went straight back to the
ceiling — so each turn of the exchange (reply → confirmation question → "tak" → write) would cost
up to an hour, and a four-turn dialogue could eat a third of the 48h window.

`_process_pending` now returns whether it handled a new message, and `poll_replies` reports
`last_activity = now` whenever anything was handled in that tick. Silence is still measured from
the message/nudge timestamp; **activity is measured from the moment we noticed it**. Absent
employee → hourly; live conversation → back to the 10s floor immediately.

Consequence: worst-case latency for noticing the *first* reply is now 1h instead of 5 min. This is
the deliberate trade — it applies only to the first turn, and only after ~43 min of silence has
already elapsed (the geometric ramp reaches the ceiling at idle ≈ 2560s).
