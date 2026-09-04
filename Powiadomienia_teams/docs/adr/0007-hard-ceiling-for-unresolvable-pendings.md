# A pending nobody can resolve gets a hard ceiling, and closing it says nothing

Date: 2026-09-04
Status: accepted
Author: P0w3r223
Related to: ADR 0003 (expiry requires evidence — **this ADR weakens it deliberately**),
ADR 0002 (adaptive listener — poll cadence), `reminders/lifecycle.py`, `runtime/listener.py`,
`runtime/domkniecia.py`, `state.py`

---

## Context

ADR 0003 made expiry a verdict about evidence: the bot may say *"Nie dostałem odpowiedzi"* only
after a successful read that found nothing. That was right, and this ADR does not take it back.

It also named the price, in its own "Accepted gaps" section:

> A permanently unreadable chat yields a pending that never expires: `_record_failure`'s circuit
> breaker guards interpretation, not the read itself.

That gap is not theoretical, and it is worse than "not tidied away". `client.list_chat_messages`
is called at the top of `_process_pending`, **before** the `try` that leads to `_record_failure`.
An exception therefore reached the per-person `except Exception` in `poll_replies` and became
`ReadOutcome.UNKNOWN`: `fail_count`
does not grow, the watermark does not move, and `should_expire` correctly refuses to expire. The
pending stays open forever, and because the state key is `member_id`, it **blocks that person's
reminder every following week**. The only trace is a `logger.exception` inside the container.

The failure is therefore silent, one-way and permanent, while the process stays alive, the
heartbeat keeps beating and the healthcheck stays green. From the outside it is indistinguishable
from a quiet week — which is exactly the property ADR 0003 set out to destroy elsewhere.

Nothing in the running system closes this loop. There is no counter of unresolved cycles and no
alert, so the only path back is a human noticing, months later, that one person out of eight has
stopped being asked.

## Decision

**Two mechanisms, one for the operator and one for the entry.**

1. **A counter with a threshold alert.** `PendingReminder.unknown_count` counts *consecutive* cycles
   whose chat read raised. At equality with `_PROG_CYKLI_BEZ_ODCZYTU` the operator is alerted
   exactly once, with a separate `INFO` alert when reads recover — the pattern already used by
   `_PULS_PROG_ALERTU` and `_PROG_ALERTU_PRZEKROCZEN`. The counter is **separate from `fail_count`**
   on purpose: crossing `fail_count`'s threshold sends *"Nie do końca zrozumiałem"* into the chat,
   and here the chat is the thing that cannot be reached.

2. **A hard ceiling on the entry's age** (`sufit_wpisu_bez_odczytu_h`, default 144 h). Past it, an
   entry that is still unresolved is closed **silently**: status `EXPIRED`, no message to the
   employee, one alert to the operator.

   **The ceiling requires both conditions — the age *and* the run of failed reads.** Age alone is
   not enough, and the difference is not academic: after any downtime longer than the ceiling
   *every* open entry is older than it, so a single 429 on the first tick back would close them
   all, silently, together with the replies waiting in their chats. That is strictly worse than the
   defect this ADR fixes, because 0.2.19 at least kept the reply readable. The conjunction costs
   nothing — below the ceiling the counter grows every tick anyway, and three ticks at the backoff
   ceiling are about three hours against 144 — and it makes the operator's warning arrive before
   anything disappears, which is what the alert text already promises.

**This weakens ADR 0003, and the weakening is the point.** After the ceiling, an entry can reach a
terminal status without the successful read that ADR 0003 requires.

**What makes that acceptable is the silence, and nothing else.** ADR 0003 does not forbid closing;
it forbids *asserting something about the employee's behaviour without evidence*. Its whole argument
is about the truth of a sentence — `EXPIRED_TEXT` claims "I got no reply". A closure that sends no
sentence makes no claim, so no claim is false. This is why the ceiling must not go through
`domkniecia.zamknij_bez_zapisu`: that function sends `EXPIRED_TEXT`, and routing the ceiling
through it would turn a bookkeeping decision into a lie to a person.

**Scope: only a failed read of the chat itself.** `UNKNOWN` meant three different things at once,
and a counter built on it could not be honest. This change splits them by *what actually failed*:

- `READ_FAILED` — `list_chat_messages` raised. Returned from **one** place, inside a `try` that
  wraps nothing but that call. The only value the counter counts and the only one that moves an
  entry towards the ceiling, because it is the only one that says the channel is unreachable.
- `BLOCKED` — the read succeeded but the thread stopped being 1:1. Excluded deliberately:
  `_zglos_obcych_raz` has already called the operator, and closing here would end someone's week
  because a colleague wrote in their thread.
- `UNKNOWN` — the read succeeded and handling blew up afterwards. Excluded because the chat
  answered. This is the case a single collapsed value got wrong: a truncated read of the
  **schedule** (`GraphTruncatedReadError`, which bypasses `_record_failure` by design) raised the
  "chat unreadable" counter for a chat that read perfectly, and pointed the operator's alert at the
  wrong subsystem.

Every exclusion follows this ADR's own premise, not test convenience. The motivation is *"silent,
one-way, and looks like a quiet week"*. Where the operator has already been called, or where the
chat demonstrably answers, none of that holds — and a counter that fires anyway teaches the operator
to distrust the one channel they have.

The two new enum values are what the proposed compatibility contract **N34**
(`plan-rozwoju.md` §11, quoted in `service.py`) would otherwise speak against. N34 exists so that an
image rollback is a plain version swap, which means it protects values **written to disk**: an older
image must be able to read the state file it finds. `ReadOutcome` lives only in memory for the
duration of one tick and is never serialised, so the reason behind N34 does not reach it — and the
on-disk status enum, which N34 does cover, is left untouched by this ADR. `should_expire` passes
only `NOTHING_NEW`, so both new values keep blocking expiry and the safety property of ADR 0003 is
unchanged on every path.

**The ceiling is derived from the weekly cycle, not from the reply window.** 144 h = 6 days, one day
short of the 168 h cycle. The anchor is `lifecycle._anchor` (`max(watermark, bot_last_message_at,
nudged_at)`) — how long ago anything happened here — not `week_start`, because the ceiling measures
a stalled conversation, not distance from a target week.

The one-day margin is not decoration. A stalled entry's anchor is in the worst case `nudged_at`,
set during the weekly run, so a 168 h ceiling would fall **exactly when the next run starts** — a
race whose outcome depends on which code touches the state file first. Losing that race is not
harmless: `run_once` would find last week's entry still open and take the overwrite branch, which
alerts the operator that *an agreement never reached the schedule* — a sentence that is false here,
because no agreement was ever made. The margin has to cover catch-up inside the grace window
(`CATCHUP_GRACE_HOURS = 6`), a cycle deferred by quiet hours (up to 11 h at `20–7`) and DST drift
(1 h): 18 h, inside 24 h.

The setting is **its own explicit key**, deliberately outside the `REPLY_*` family.
`REPLY_WINDOW_HOURS` is on `config.USUNIETE_NAZWY` precisely because it rolled two different roles
into one number; naming the ceiling into that family would invite an operator to recreate the
divergence that 0.2.13 closed. It cannot be switched off — `<= 0` is a `ConfigError`, because a
disabled ceiling restores this exact defect without a sound.

## Consequences

**Good.** A chat that cannot be read stops blocking a person indefinitely: the operator hears about
it after three cycles and the entry leaves circulation after six days. No employee is ever told
something untrue as a result, because no employee is told anything at all.

**Costs and limits.**

- An entry can now close without evidence. If the read outage ends on day seven, the entry is
  already gone; that week is lost for that person and the operator's alert is the only handle.
- The weekly summary counts the silent closure under `wygasle` → "zamknięte bez zapisu"
  (`STATUS_DO_POZYCJI`). That is formally true but loses the cause. A dedicated status is **not**
  the way out — that is exactly the on-disk enum growth N34 speaks against, and `service.py` already
  carries a safety net for statuses it does not recognise — so the cause lives in the alert and in
  the `--stan` column instead.
- One new state field and one new configuration key. Both are backward *and* forward compatible:
  `state.py` filters by `_FIELDS`, so an older image discards the unknown key rather than failing,
  and rollback by image tag stays safe.
- `domkniecia.py`'s module docstring said a topic ends in **four** ways and each must tell the
  truth. There is now a fifth that says nothing — which is also the truth, and is written down
  there rather than left implicit.

**A condition this ADR imposes on the watermark fix (C3), which is not part of it.** C3 will wrap
two sends that currently stand outside any `try`. Their exceptions will then reach the same
per-person handler and arrive as `UNKNOWN` — inflating the read counter for a **send** failure.
After the split above this is exactly right: the chat was read, a send failed, and the read counter
must not move. No coupling between the two changes is needed. An earlier draft of this ADR required
`_commit` to zero `unknown_count` for that reason; the split makes it redundant, and it was removed
rather than left as defensive decoration.

**Rejected alternatives.** *Ceiling on `fail_count` instead of a new field* — the failed read never
reaches `_record_failure`, so the counter it would rely on does not move; this is the defect, not a
fix for it. *Merge the two counters* — crossing `fail_count`'s threshold writes to the chat, which
is unreachable by construction here. *Route the closure through `zamknij_bez_zapisu`* — sends
`EXPIRED_TEXT`, i.e. tells the employee they did not answer, in the one case where we have no idea
whether they did. *A parallel "who could not be read" set beside `outcomes`* — two sources of truth
about one verdict; this code has already failed on that shape once. *Make the ceiling switchable
off* — restores the defect silently, which is worse than not having the option.

## What this ADR does not decide

Whether `_ZGLOSZONE_OBCE` (in-process alert throttling for stranger threads) should move into the
state file so it survives a restart. Its comment promises that the `UNKNOWN` counter will "take
over" that role; with `BLOCKED` split off, that promise is now simply wrong and is corrected in the
same change. Making the throttle durable is a separate decision with its own risk.

## Verification

`tests/test_app.py`: `test_nierozstrzygniete_cykle_powinny_alarmowac_po_progu` (alert exactly once
at the threshold, across `threshold + 5` cycles) and `test_twardy_sufit_zamyka_wpis_CICHO_i_z_alertem`
(`EXPIRED`, `client.sent == []`, one operator alert) — both were `xfail(strict=True)` guards written
against the recovered 0.2.19 source and are unmarked by the change that fixes them. The ceiling
guard's body now loops `_PROG_CYKLI_BEZ_ODCZYTU` times instead of running one tick: the property it
protects (silent closure with an alert) is untouched, but a single tick would freeze in place the
age-only behaviour this ADR rejects above.

The control in the other direction must keep passing:
`test_trwale_nieodczytywalny_czat_nie_wygasa_i_nie_gubi_odpowiedzi` holds an entry 71 h old through
eight cycles and requires it to stay open — the ceiling must not turn into "expire on any failed
read". The three stranger-thread tests (`test_obcy_nadawca_NIE_pozwala_wygasic_wpisu`,
`test_obcy_nadawca_alertuje_RAZ_a_nie_przy_kazdym_odpytaniu`,
`test_dlawienie_alertu_o_obcych_nie_wycisza_INNEGO_czatu`) must pass unchanged; they are the
executable form of the `BLOCKED` split, since each holds an entry 142 h old and the middle one runs
exactly three cycles.

`tests/test_lifecycle.py` covers `should_expire` across every `ReadOutcome` — as a set comparison,
so a value added later cannot slip through unexamined — and the ceiling predicate at its boundary,
with a moving anchor and with no anchor at all. `tests/test_config.py` covers refusal to start when the ceiling is
`<= 0`, at or below the courtesy floor, or above one week.

---

## Amendment, 2026-09-04 — C3 landed as anticipated

The watermark fix (wave 4) shipped, and the expectation recorded above held: the two sends it
wrapped now raise into the per-person handler as `UNKNOWN`, not `READ_FAILED`. The read counter is
therefore **reset, never incremented** — the chat was read, so a run of failed reads has demonstrably
ended — and no entry is pushed towards this ceiling by a failed **send**. (An earlier wording here
said the counter "does not move"; measured, it goes to zero. The conclusion is unchanged, the
sentence was not.) No coupling between
the two changes was needed, and none was added.

One correction to the note above: it says the rollback would go through `_record_failure`. It does
not. That function writes state and sends a message, and calling it from a `finally` block — during
unwinding, often after `AuthExpiredError` — would mean writing to a person with a dead token, the
failure wave 1 closed. Wave 4 uses a snapshot plus a restore function whose signature carries no
`settings`, `state` or `client`, so it has nothing to do I/O with. The loop ceiling still comes for
free, from `_record_failure` called one level up in `_process_pending`.

This closes the nine defects found when the 0.2.19 sources were recovered. No `xfail` remains.
