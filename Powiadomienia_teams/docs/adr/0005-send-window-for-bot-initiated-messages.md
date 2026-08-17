# Send window for bot-initiated messages, and a start that refuses a silent schedule

Date: 2026-08-17
Status: accepted
Author: P0w3r223
Related to: ADR 0002 (adaptive listener — reply-window lifecycle), ADR 0003 (expiry requires
evidence), ADR 0004 (self-fill detection and reply memory), `scheduler/send_window.py`,
`config.py`, `app.py`

---

## Context

The bot has, until now, had exactly one notion of *when* to speak: `RUN_WEEKDAY`/`RUN_HOUR` for the
weekly run, and "immediately" for everything else. Everything else is not a small set. Three
message kinds are **initiated by the bot** rather than answering a person:

1. the weekly request to fill in the schedule,
2. the closing message after the reply window expires (ADR 0002/0003),
3. the thank-you sent when someone filled Shifts on their own (ADR 0004).

All three are driven by pollers that run continuously. A reply window that opens on Friday at 16:00
and lasts `reply_window_hours` expires *whenever it expires* — including at 22:00, at 03:00, and on
Sunday. The system had no way to express "the bot may not open a conversation now": the only lever
was to stop the service, which also stops it from answering people.

This is not a cosmetic concern. A message from a work bot at 22:00 is read as an expectation to
respond at 22:00, and the closing message in particular ("I didn't get an answer") is the one whose
tone ADR 0003 already treated as a correctness question rather than a politeness question.

**The second half of the problem is that silence looks exactly like health.** Once a window exists,
a schedule outside it produces a service that starts cleanly, logs cheerfully and never sends
anything: every run bounces off the quiet hours and is pushed to the next open slot. The only trace
is an INFO line. The same shape of defect already existed one level down, in `DRY_RUN`: anything
outside the recognised truth values was read as `False`, so `fasle`, a shell-quoted `"true"`, or a
truncated `tru` silently meant **live mode** — sending to the whole team and writing to the
customer's schedule. Both are fail-open defaults on the two switches that decide whether the bot
talks to humans at all.

## Decision

**A send window applies to bot-initiated messages only, and never to replies.** Pure calendar
logic lives in `scheduler/send_window.py` (injected `moment`, no I/O), computed in the team's
timezone: `in_send_window` and `next_send_window`. Configuration is three variables —
`SEND_WINDOW_START_HOUR` (8), `SEND_WINDOW_END_HOUR` (18), `SEND_WINDOW_WEEKDAYS` (`0,1,2,3,4`).
The interval is closed on the left and open on the right (`start <= hour < end`), so `8..18` means
"08:00 through 17:59".

**A reply to a person's message is never gated.** They just wrote and are waiting; silence would be
worse than a message at 22:00. The gate covers only the three kinds listed above.

**Dry-run is always inside the window.** Nothing leaves the process in that mode, and blocking a
trial run after hours would take away the operator's ability to verify a deployment at any time.

**Outside the window a message waits — it is not retried into a burst.** Terminal state is
persisted immediately, so the decision that a topic is closed survives a restart even when the
*courtesy* message announcing it has not gone out yet. A deferred closing message whose send later
fails is **not** retried: it is a courtesy, not a record, and retrying risks a series.

**Waiting has its own ceiling, and past it the message IS dropped.** The queue reuses
`past_hard_ceiling` — `HARD_CEILING_MULTIPLIER × reply_window_hours` from the entry's last activity
— because a closing message that arrives several days late is no longer a courtesy but a riddle,
and an entry holding an undelivered message stays outside `prune_terminal` indefinitely. The drop is
logged as a warning; the terminal status is already persisted, so nothing about the *decision* is
lost, only its announcement. **This interacts with the window, and the interaction is
configuration-dependent.** At the default `REPLY_WINDOW_HOURS=48` the ceiling is 144 h and no
weekend can reach it. At `REPLY_WINDOW_HOURS=8` the ceiling is 24 h, while a deferral from Friday
19:00 to Monday 08:00 is ~62 h — so with a short reply window the weekend silently eats the closing
message. We accept this rather than special-casing the ceiling for queued messages: losing one
courtesy is cheaper than either repeating it or letting an entry live forever, and the two knobs are
independent for good reasons. An operator shortening `REPLY_WINDOW_HOURS` below roughly a third of
the longest quiet stretch should expect it.

**A deferred run is a third outcome, not a failure.** `WynikPrzebiegu` gains `ODLOZONY` next to
`UDANY`/`NIEUDANY`, because the two repairs are opposite: a failed run is retried quickly inside the
grace window (`CATCHUP_GRACE_HOURS`), while a deferred run **waits for the window to open**, which
can be the whole weekend. Consequently a deferred run **suspends** the grace countdown (`wygasa =
None`) rather than consuming it; when a real attempt then fails, the grace budget starts from that
attempt. `ZalegloscPrzebiegu` carries three separate fields — `termin` (the target *week*, never
"now"), `nastepna_proba`, `wygasa` — because with the window open `next_send_window` returns "now",
so without a separate next-attempt field a failed run would loop without breathing.

**Start refuses a schedule that could never fire, and an unrecognised boolean.** `Settings.validate`
now cross-checks the run schedule against the window: `run_weekday` must be one of
`send_window_weekdays`, and `run_hour` must lie in `[start, end)`. An empty `send_window_weekdays`
is rejected outright — "never" is not a way to disable the window; the full week `0,1,2,3,4,5,6` is.
`_bool` stops treating unknown text as `False` and raises `ConfigError` instead, the same way `_int`
already did.

## Consequences

- The bot no longer opens conversations outside working hours; people who write to it after hours
  still get answered in the same turn.
- A Friday-evening expiry no longer loses the week: the run is deferred with its *target week*
  intact and its grace budget suspended, and fires when Monday's window opens.
- Two classes of silent misconfiguration became loud: a schedule outside the window, and a typo in
  `DRY_RUN`. Both previously produced a service that looked healthy.
- One new cost: a deployment that wants the bot to speak at unusual hours must widen the window
  explicitly. That is the intended trade — the previous behaviour expressed no preference at all.
- `send_window.py` is pure and unit-tested in isolation (`tests/test_send_window.py`); the
  orchestration decisions (defer vs fail, suspend vs count grace) are tested in `tests/test_app.py`.

## Alternatives considered

- **Gate every outgoing message, including replies.** Rejected: it converts the bot's one
  responsive behaviour into an unpredictable one, and the person on the other side has no way to
  know why nothing came back.
- **Treat a deferred run as a failure.** This is what the code did before the third state existed,
  and it is precisely the bug: a 6 h grace window from Friday 16:00 expired at 22:00 while the send
  window opened on Monday, so the week vanished with no error anywhere.
- **Drop out-of-window courtesy messages instead of queuing them.** Simpler, but it makes the
  closing message conditional on the hour at which a timer happened to expire — the same
  arbitrariness ADR 0003 removed from the *content* of that message.
- **Warn instead of failing on a schedule outside the window.** Rejected for the reason the whole
  second half of this ADR exists: a warning in a log is indistinguishable from a healthy run in
  every way that matters to an operator who is not reading the log.
