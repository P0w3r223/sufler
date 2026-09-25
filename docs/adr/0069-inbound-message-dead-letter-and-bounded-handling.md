# 0069 — Inbound dead-letter: at most two handling attempts, then quarantine

Date: 2026-08-17
Status: accepted (implemented — attempt accounting corrected, inbound quarantine wired on the Teams
  door, read surface delivered 2026-08-17)
Author: P0w3r223
Related to: [ADR 0067](0067-observability-audit-journal-and-notifier-dead-letter.md) (§2 — the
  dead-letter this ADR mirrors on the *inbound* side, and the "record before the cursor moves"
  ordering it copies),
  [ADR 0045](0045-state-durability-and-graceful-shutdown.md) (atomic state write, graceful
  shutdown, and the "beat only after a good round" rule this ADR restates for the Teams poller),
  [ADR 0022](0022-proactive-dual-target-teams-push.md) (the at-least-once stance that a bounded
  attempt count deliberately departs from, on the inbound side only),
  [ADR 0015](0015-teams-delegated-graph-polling.md) (the delegated Teams door whose loop this is)

---

## Context

The Teams door polls channels, hands each new message to the agent runtime, and posts the reply.
Until recently the loop had exactly one promise: **a message stays in the stream until it is
handled**. `replied` (the dedup list) was appended *after* a successful turn, so a failure — a
transient 503 on `hostedContents`, an LLM timeout, a 429 on send — meant the next round tried
again. Nothing was ever dropped.

That promise had a failure mode the code could not survive. Handling a *single* message can kill
the process: a decompression bomb in an attachment exhausts memory, the supervisor restarts the
door, the restarted process reads the same state, selects the same message, and dies again. One
poisoned message became a **permanent restart loop** in which nothing else got through either.

The fix (shipped before this ADR, in review round 2) was a **persisted attempt counter**:
`state["attempts"][message_id]`, incremented and written to disk *before* handling, with
`_MAX_ATTEMPTS = 2`. Two is the smallest number with both properties wanted — one retry rescues a
transient error, a finite bound closes the restart loop.

That counter silently changed the door's guarantee from *"every message will eventually be
handled"* to *"at most two attempts, then the message is dropped"*, and it did so with no decision
record and **no trace of what was dropped** beyond a `logger.error` line. Meanwhile the *outbound*
path already had the opposite treatment: ADR 0067 §2 gave the notifier a `dead_letters` table
precisely so an undeliverable event is "quarantined with its reason, not skipped". The asymmetry is
backwards — an inbound message is a human being's text, an outbound event is machine-generated and
reconstructible from the source system.

Review round 3 also found the counter itself unsound. The state write sat **outside** the `try`
that guards handling:

```python
attempts[msg.id] = taken + 1
self._persist(self._state)          # ← outside the try below
try:
    ...handle...
```

`require_writable` probes the state volume **at startup only**. When the volume stops accepting
writes *during* operation (full disk, a remount to `ro`), the counter rises **in memory**, the
exception is caught by the per-channel `except` in `run()`, and after two rounds the message is
marked replied and leaves the stream forever — while the log claims "after 2 failed handling
attempts", and handling never ran once. An infrastructure failure was being charged to the message.

## Decision

### 1. The guarantee, stated

The Teams door promises: **at most `_MAX_ATTEMPTS` (2) confirmed attempts per message; after that
the message is abandoned and quarantined.** This is a deliberate, bounded departure from "nothing
is ever dropped", scoped to the inbound door. ADR 0022's at-least-once cursor on the *outbound*
side is untouched.

### 2. Only a durably recorded attempt counts as an attempt

The increment and its state write are one unit (`ChannelPoller._record_attempt`). If the write
fails, the in-memory increment is **rolled back** and the exception propagates as any other channel
error — the round ends without moving the watermark, and the message is retried next round with its
counter untouched. "The state volume cannot be written" is an infrastructure fault, not evidence
that a message is poisonous, and the two must not share a counter. A failure that outlives the
disk problem still burns attempts once the disk recovers — as it should.

### 3. An abandoned message goes to quarantine before it leaves the stream — but a refusing store never blocks the channel

Ordering is copied verbatim from ADR 0067 §2 ("record, *then* move the cursor"): write the
dead-letter entry **first**, then `_mark_replied`, then persist. A crash between the two
re-quarantines (the entry is idempotent) rather than loses.

What is **not** copied from the notifier is the reaction to a store that *refuses* the entry.
There, the cursor stays and the event is retried; here the store error is caught, logged at ERROR
with the whole entry field by field, and **the round continues** — the message is abandoned with
the log as its record. *(Revised in place after review round 4 — see "Review correction".)*

### 4. A separate store, not the ADR 0067 one

`SqliteDeadLetterStore` cannot be used as-is. Its table is keyed `(source, event_id)` with
`event_id INTEGER NOT NULL` — an `events.db` row id, from which the channel, thread and author are
recoverable by joining `events`. A Teams message has none of that: its id is an **opaque string**
from Graph (numeric-looking today, not contractually so), and it is meaningless without the channel
and thread that locate it. Reusing the table would mean `int(msg.id)` on a value that only looks
like a number, and would leave the entry with nothing to join against.

So: a sibling table `inbound_dead_letters` in the **same** `events.db` on the same `state` volume,
with the same connection discipline (`WAL`, `busy_timeout`, `check_same_thread=False` + a `Lock`),
idempotent on `(door, message_id)`, implemented in the same adapter file
(`adapters/outbound/sqlite_dead_letters.py`) as `SqliteInboundDeadLetterStore`. Two tables, one
file, one volume, one backup — the split is in the key, not in the storage.

### 5. Identifiers and a reason; never content

The row carries `door`, `message_id`, `channel`, `thread_root_id`, `sender` (AAD id), `reason`,
`attempts`, `failed_at`. It does **not** carry the message text. This is the ADR 0067 §1.3 stance
("actions and paths, never content") applied to the same class of store: the operator needs to
*find* the message, and Teams is its durable home. Copying human text into an ops database would
give it a second lifetime under a different retention rule for no operational gain.

### 6. The reason is best-effort, and says so when it does not know

The attempt counter is durable because it decides whether to abandon; the failure reason lives in
process memory (`_last_error`) because it only describes. In the common case — two transient
failures in one process run — the entry carries the real exception. In the case the counter was
built for — handling killed the process, so no `except` ever ran — there is nothing to carry, and
the entry says so verbatim (`_NO_REASON`) instead of inventing a cause. Persisting the reason in
the state file was rejected (see Alternatives).

### 7. The port is declared at the door, not in the core

`MessageDeadLetterStore` is a `Protocol` in `teams_graph/poller.py`, beside the existing
`GraphChannelClient` — the same pattern, for the same reason: abandoning an inbound message is a
property of the door, and the core takes no part in it. `app.py` injects
`SqliteInboundDeadLetterStore` over the events DB; `None` (tests, dev) keeps the old behavior where
the only trace is the log line, and the log says which of the two it is.

### 8. Two accounting defects fixed in the same pass

- **`_prune_attempts` trimmed the wrong end.** The dict is trimmed in insertion order, but
  `attempts[msg.id] = taken + 1` on an existing key does *not* move it to the end — so under cap
  pressure the entry evicted would have been the message **currently in flight**, not the
  long-abandoned one the backstop exists for. `_record_attempt` now deletes before inserting.
- **`_seed` handled a missing branch but not a null one.** `setdefault` returns the stored `None`
  for `{"attempts": null}` — a syntactically valid state file — and every round of every channel
  then died on `AttributeError`, permanently, with deleting the state file as the only exit. Seeding
  now checks the **type** of each branch and replaces a malformed one with an empty one.

## Risk register

| # | Risk | Mitigation |
|---|------|------------|
| R1 | "Dead-letter" reads as permission to drop messages more freely. | The bound is 2 and lives in one constant with its rationale; quarantine is what makes the existing bound honest, not a licence to widen it. Nothing else about the loop's retry behavior changed. |
| R2 | Quarantine is write-only — nobody opens the table. | **Closed 2026-08-17** by `sufler-diagnostics`, one command over all three stores — see "Follow-up delivered" below. *(Originally accepted as deliberate: `recent()` had no caller in `src/`, exactly like `DeadLetterStore.recent` since ADR 0067, and the ERROR log was the practical fallback.)* |
| R3 | The reason is missing exactly in the worst case (process killed). | Accepted and made explicit in the row (`_NO_REASON` text). The identifiers are complete regardless, so the message is still findable; the log holds whatever the dying process managed to emit. |
| R4 | Quarantine writes fail on the same volume trouble that caused the abandonment. | The store error is caught: the entry degrades to an ERROR log carrying every field, and the channel keeps serving everyone else. Blocking the channel to protect the *trace* was measured to cost more than the trace is worth — see "Review correction". |
| R5 | `inbound_dead_letters` grows unbounded. | Small by construction (a row only on abandonment); retention deferred to the same Faza 7 pass as `audit.db` and `dead_letters`. |
| R6 | A restarted door re-quarantines the same message with a poorer reason and a later time. | `UNIQUE(door, message_id)` + `INSERT OR IGNORE`: the first reason and the first timestamp win, exactly as on the notifier side. |

## Alternatives considered

- **Keep retrying forever (the pre-counter behavior).** Rejected — this is the crash loop the
  counter exists to close; one poisoned message stopped every channel.
- **Reuse `SqliteDeadLetterStore` with `event_id=int(msg.id)`.** Rejected: a Graph message id is an
  opaque string, the column means "row id in `events`", and the entry would carry no channel or
  thread to locate the message by.
- **Widen `dead_letters` to a TEXT key plus nullable context columns.** Rejected: it changes a table
  the notifier owns (ADR 0067) and makes every row's meaning depend on which producer wrote it, to
  save one small table in a file both already share.
- **Persist the failure reason in the state file next to the counter.** Rejected: it turns
  `attempts` from `{id: int}` into a nested shape (with a back-compat branch for existing files),
  and needs its own pruning — a durable *description* bought at the price of the shape that decides
  *behavior*. The in-memory reason plus an honest fallback costs one dict.
- **Dead-letter at the moment of the second failure, where the exception is in hand.** Rejected as
  the *primary* seam: the case the counter was built for never raises (the process dies), so the
  abandonment branch has to exist anyway; two write sites for one decision is more surface than the
  better reason is worth.
- **Automatic replay of quarantined messages.** Deferred: replay re-enters the same handler that
  already failed twice, so it needs an operator's judgement (and possibly an edited message) to be
  anything but a slower loop. The row carries everything a manual replay needs.

## Consequences

- **The guarantee change is now written down** where the next reader will look, instead of living
  in a constant's comment. Two attempts, then quarantine — for the Teams door only.
- **An infrastructure failure no longer costs a message.** A read-only or full state volume stops
  progress loudly (channel errors, stale heartbeat via a dying process) instead of quietly eating
  the stream, and the log distinguishes "cannot persist the attempt" from "handling failed".
- **The operator gets a list of what was dropped** (`sufler-diagnostics inbound`), with enough to
  open the thread in Teams and answer by hand. Nothing in that list is message content.
- **Symmetry with ADR 0067 §2**: both sides of the bridge now quarantine what they cannot process,
  with the same ordering rule, the same idempotency stance, and the same file.
- **The GitHub door is out of scope.** Its poller ingests events into `events.db` rather than
  running turns, so it has no equivalent "handling attempt" to bound; if that changes, this ADR is
  the precedent.

## Review correction (2026-08-17) — review round 4, three premises adjusted against the code

1. **(HIGH) A refusing quarantine store no longer blocks the channel (Decision §3 revised).** The
   `record` call sat before `_mark_replied` and outside the per-message `try`, so its exception left
   `for msg in messages` and `_poll_channel` entirely. Reproduced on fakes: with a permanently
   failing store, a healthy message queued *behind* a poisoned one never reached the handler —
   the channel stopped answering **anyone**. The trigger needs no `ro` remount: `SQLITE_CORRUPT`, a
   `disk I/O error`, i-node exhaustion while JSON writes still succeed, or `SQLITE_BUSY` persisting
   past `busy_timeout` all suffice. The original rationale ("silent loss of a human's text is what
   this path must not do") mis-weighed what was at stake: the message *text* lives in Teams and is
   never lost by this code; the quarantine row is a **pointer**. Blocking traded every reply on the
   channel for one pointer. Retrying bought nothing either — the per-thread watermark advances at the
   end of `_poll_channel`, so the next round would not re-select the message anyway. Now: catch, log
   at ERROR with the complete entry (the log *is* the fallback register), abandon, continue.
   Regression test `test_quarantine_refusal_does_not_stop_the_channel`.
2. **(MED) `run()` now survives a state volume that stops accepting writes.** The end-of-round
   `persist` and both `_beat()` calls sat outside any `try`, so the rollback in `_record_attempt`
   promised a survivable round that `run()` then aborted anyway (and the supervisor restarted the
   process straight into `require_writable`). The round-closing write is wrapped; a failure logs and
   the loop continues. Nothing is lost either way — the attempt counter is rolled back and the
   watermark does not move — but the process now degrades loudly instead of dying.
3. **(MED) The heartbeat goes stale when state cannot be persisted.** `_beat()` is gated on the last
   write having succeeded, mirroring the notifier's "beat only on a productive round" (ADR 0067
   §2.2). Without it, a volume remounted `ro` mid-run left the container **healthy** while no
   message could get through at all — the attempt counter cannot be persisted, so handling never
   starts. A liveness signal that survives total inability to work is not a signal.
4. **(MED) The stored reason is the exception type plus the first line of its message, capped** —
   not `repr(exc)`. The responder pipeline can raise anything, and a validation error typically
   prints the offending input value; the store's own docstring promises it holds no content, and a
   character cap bounds size, not kind. This is a reduction of exposure, not a boundary (hard rule
   4) — an exception message can always be concatenated with data.
5. **(LOW) `_seed` also repairs a non-integer attempt counter.** Hardening against `{"attempts":
   null}` left `{"attempts": {"root-1": "abc"}}` raising `ValueError` on every round of every
   channel, permanently — the same failure one level deeper. Unreadable entries are dropped
   (counting from zero means "try again", the safe side of the mistake).
6. **(LOW) `is_fresh` treats any `OSError` as unhealthy**, not just `FileNotFoundError`. A
   `PermissionError` on the heartbeat file produced a traceback where the healthcheck wanted a
   verdict; the exit code was already 1, so this buys legibility, not behavior.

## Follow-up delivered (2026-08-17) — one read surface for three stores (R2)

`sufler-diagnostics {audit|dead-letters|inbound}` (`adapters/inbound/cli/diagnostics.py`) is the
reader R2 deferred. One command, three surfaces, because the operator reaches for them in one pass
and for one reason: somebody went unanswered. Each entry prints identifiers, the reason and the
time; `--source` narrows by door (or by event source on the notifier table), `--since`/`--until`
take either a relative span (`24h`, `7d`, `30m`) or an ISO timestamp, and `--json` hands the same
fields to a script — the neighbouring `sufler-search` sets that convention.

Four properties are worth recording, because each is a decision and not an implementation detail:

- **Read-only connection.** The doors are writing this file while the operator reads it, so the
  reader keeps their concurrency discipline (`busy_timeout`, `check_same_thread=False` + a `Lock`,
  WAL left as the writers set it) and adds `mode=ro` (`adapters/outbound/sqlite_readonly.py`). The
  practical value is not protection from a hostile operator — they own the volume — but that a
  diagnostic tool cannot touch production data and, more importantly, **cannot create** the
  database or its tables. A mistyped path must come back as the operator's mistake, not as "no
  entries" over a freshly materialised empty file. A missing table is its own named error
  (`MissingTableError`): "this store has never written anything" is a different answer from "wrong
  path". The one fallback to a read-write connection covers WAL's `-shm` requirement when no writer
  is running; failing to open would be the worse answer.
- **No default path, ever.** `events.db` lives outside the repo (`~/.sufler/`), and the default
  belongs to configuration, not to a diagnostic tool. The path comes from `--db` or from the
  variable that *enables the writing side* (`SUFLER_EVENTS_DB` for both quarantines,
  `SUFLER_AUDIT_DB` for the journal); with neither, the command errors instead of guessing.
- **Filters run in SQL, before `LIMIT`.** A limit must trim what the operator asked for, not cut
  rows a filter would have dropped anyway. Time columns are TEXT, and the comparison is
  lexicographic — sound precisely because every writer stores `datetime.now(tz=UTC).isoformat()`;
  a naive boundary from the command line is read as UTC rather than as the operator's local zone.
- **Still no content.** The reader cannot show message text because the row does not hold it
  (§5), and this ADR does not add it. The output exists to *find* the message in Teams by its
  identifiers, not to reconstruct it.

The audit journal (ADR 0067 §1) had no reader either — `AuditReadService.recent` was called only
by tests — so it is covered by the same command rather than by a second one.

## Follow-ups

- Retention for the quarantine tables, with the Faza 7 `audit.db` retention pass.
- Consider surfacing quarantine *volume* as a health signal (an entry means a human went
  unanswered), rather than leaving it to `logger.error` — same open item ADR 0067 raises for the
  notifier's dead-letter.
