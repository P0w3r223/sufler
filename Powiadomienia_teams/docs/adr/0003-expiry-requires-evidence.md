# Expiry is a verdict about evidence, not about the clock

Date: 2026-07-22
Status: accepted
Author: P0w3r223
Related to: ADR 0002 (adaptive listener — reply-window lifecycle), CLAUDE.md (safety invariants),
deploy/README-docker.md (known limitations)

---

## Context

`poll_replies` runs in two phases per tick: first read and process replies, then expire pendings
whose reply window (`reply_window_hours`, default 48 h) has elapsed. Phase 2 applied `is_expired`
to **every** still-open pending, using only a timestamp comparison.

That comparison measures one thing and asserts another. The anchor is a Microsoft Graph **server**
timestamp — the moment a message was sent. The verdict it produces is a claim about the
**employee**: `EXPIRED_TEXT` says *"Nie dostałem odpowiedzi"* ("I got no reply"). The two diverge
whenever the service was not listening, because the silence budget burns during downtime even
though nobody was silent.

Downtime longer than the window is not hypothetical. `_handle_auth_loss` stops the service until a
human runs `--login`; with `restart: unless-stopped` the container simply retries every ~10 minutes
until then. A session lost on Friday evening exceeds 48 h before Monday morning, and a fresh
deployment — new consent, new Conditional Access rules, a cold MSAL cache — is the likeliest moment
for exactly that.

Three defects followed, all reproduced against the real code before this change:

1. **Contradiction inside a single tick.** After downtime, phase 1 read the employee's reply and
   sent "Zapiszę grafik… Potwierdź »tak«", and phase 2 immediately sent "Nie dostałem odpowiedzi,
   więc na razie nic nie zapisuję" — to the same person, in the same run. The pending landed in
   terminal `EXPIRED`, so the employee's "tak" would never be read again and no shifts were ever
   written. Per-pending knowledge existed but was discarded: `_process_pending` returned a `bool`
   that was collapsed into a single `progressed |= …` for the whole tick.
2. **A failed read expired the pending anyway.** An exception while reading the chat was logged and
   swallowed; phase 2 then expired that pending and told the employee they had not replied. A Graph
   outage silently cost a person their schedule for the week — asserting absence of evidence as
   evidence of absence.
3. **Nothing bounded usefulness.** Even honoured correctly, a "tak" read during the target week has
   nothing left to write: part of the week is already in the past.

Because `EXPIRED` is absorbing on both sides (it drops out of the listener **and** out of
`run_once`'s idempotence check), each of these failures was silent, one-way, and reported to the
administrator in the weekly summary as "expired without reply" — a statement that was false.

## Decision

**Expiry requires three facts, not one:** the deadline passed, the chat was read successfully in
this tick, and that read returned nothing new.

`_process_pending` now returns `lifecycle.ReadOutcome` (`HANDLED` / `NOTHING_NEW`; an exception at
the call site becomes `UNKNOWN`) instead of a boolean, and `poll_replies` keeps the result **per
person**. Phase 2 calls `lifecycle.should_expire(...)`, which expires only on `NOTHING_NEW`.
A pending missing from the outcome map defaults to `UNKNOWN`: the service's own silence is never
read as the employee's.

The policy lives in `reminders/lifecycle.py` rather than in the orchestrator, so the invariant
*"we do not say »I got no reply« without a successful read"* can be read in one place. `is_expired`
survives unchanged as a pure deadline predicate; `should_expire` composes it with the evidence
precondition.

**The window is a promise to the employee, bounded by the usefulness of the write.** What counts is
when a reply was *sent*, not when we managed to read it — so a reply that arrives late, or is read
late, is honoured. The bound is enforced **per entry at the moment of writing**, not by closing the
conversation: `lifecycle.still_writable` drops entries that have already **ended**, and
`_apply_confirmed_yes` computes that set *before* committing `APPLIED`. Something left → write it
and confirm. Nothing left → `EXPIRED` with a separate message (`STALE_WEEK_TEXT`), because
`EXPIRED_TEXT` would be a lie in precisely the case where the employee has just said "tak".

The criterion is the entry's **end**, not its start: a shift running right now is still true and
worth having in the schedule; a shift that ended yesterday is a false statement of fact to whoever
reads the roster.

A coarser rule — closing any pending whose target week had begun — was implemented first and
rejected in review. It was evaluated against the time of *reading*, so a "tak" sent at 23:58 on
Sunday was discarded when the next poll fell after midnight, even though not a single day had been
lost yet. That directly contradicted this ADR's own principle. Per-entry filtering subsumes it:
it saves the part of the week still ahead instead of discarding all of it, and it needs no separate
closing rule at all.

## Consequences

**Good.** Every sentence the bot sends about a person is now true by construction: "I got no reply"
requires a successful read that found no reply. Downtime of any length no longer consumes anyone's
window, and a Graph read outage postpones expiry instead of terminating the conversation. All three
defects are fixed by one mechanism — no state-schema migration, no new configuration knob, no
change to the "at most once" commit ordering.

**Costs and limits.** A pending whose chat is permanently unreadable stays open instead of being
tidied away; it will be pruned by `prune_terminal` only after it finally expires, and until then it
keeps being polled. Expiry now depends on the client's behaviour, so the listener's tests must
supply a client that reads successfully in order to observe expiry at all. `STALE_WEEK_TEXT` adds a
third closing message to maintain and translate.

**Rejected alternatives.** *Skip expiry only for pendings handled in this tick* — the anchor stays
stale, so the closing message would arrive on the next tick roughly 10 s later; it treats the
symptom. *Grace period derived from heartbeat mtime* — introduces a configuration knob and delays
legitimate expiries, while duplicating what the read outcome already tells us. *Make `EXPIRED`
non-absorbing so late replies reopen it* — breaks the terminal-status invariant that `run_once`'s
idempotence rests on, and this change removes the situation that would call for it. *Raise
`reply_window_hours`* — moves the trigger threshold without fixing anything.

## Verification

Reproduced before the fix and asserted after it, in `tests/test_app.py`:
`test_reply_read_after_window_is_honoured_not_expired` (defect 1),
`test_failed_chat_read_does_not_expire` (defect 2),
`test_confirmation_writes_only_the_days_still_ahead` and
`test_confirmation_with_nothing_left_closes_with_truthful_message` (defect 3, both directions),
`test_no_confirm_gets_its_own_message_not_no_reply` (a reply without confirmation is not silence),
and `test_genuine_silence_still_expires_after_successful_read` as the control — genuine silence
must still expire, or the evidence precondition would have quietly turned expiry into dead code.
The regression tests for defects 1 and 2 were confirmed to fail against the pre-change source with
the new tests in place; the control passes on both. `tests/test_lifecycle.py` covers
`should_expire` across every `ReadOutcome` and `still_writable` at the boundary (finished, in
progress, future, mixed).

Accepted gaps, deferred to 0.2.2 — listed because the claim above is "true by construction", and
these are the places where construction does not yet reach:

- A permanently unreadable chat yields a pending that never expires: `_record_failure`'s circuit
  breaker guards interpretation, not the read itself.
- `catchup_grace_hours ≥ 56` could still create a reminder for a week already under way.
- An employee whose reply failed interpretation `_MAX_PENDING_FAILURES` times keeps
  `AWAITING_REPLY`, so on expiry they are told "I got no reply" — they did reply; we failed to
  understand it. Same family as the case `NO_CONFIRM_TEXT` closes, one step further back.
- The administrator's summary counts `APPLIED` as "zapisane grafiki", but `APPLIED` is committed
  *before* the write and survives a failed `create_shift` — so a person whose schedule never
  materialised is reported as done, and will not be chased. The mirror image of the mislabelled
  expiry counter this ADR fixed; correcting it needs a state-schema field, hence the deferral.
