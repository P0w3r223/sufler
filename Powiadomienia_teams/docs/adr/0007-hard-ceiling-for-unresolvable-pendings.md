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
is called in `_read_new`, which runs **before** `_record_failure`. An exception therefore reaches
the per-person `except Exception` in `poll_replies` and becomes `ReadOutcome.UNKNOWN`: `fail_count`
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

**This weakens ADR 0003, and the weakening is the point.** After the ceiling, an entry can reach a
terminal status without the successful read that ADR 0003 requires.

**What makes that acceptable is the silence, and nothing else.** ADR 0003 does not forbid closing;
it forbids *asserting something about the employee's behaviour without evidence*. Its whole argument
is about the truth of a sentence — `EXPIRED_TEXT` claims "I got no reply". A closure that sends no
sentence makes no claim, so no claim is false. This is why the ceiling must not go through
`domkniecia.zamknij_bez_zapisu`: that function sends `EXPIRED_TEXT`, and routing the ceiling
through it would turn a bookkeeping decision into a lie to a person.

**Scope: only `UNKNOWN` from a failed read.** The listener has a second source of "we established
nothing" — a thread that stopped being 1:1, where a stranger's message must not be taken for the
employee's reply. Both collapsed into `UNKNOWN`, so this change splits them: the stranger case
becomes `ReadOutcome.BLOCKED`, and neither the counter nor the ceiling touches it.

The reason is this ADR's own premise, not test convenience. The motivation above is *"silent,
one-way, and looks like a quiet week"*. In the stranger case none of that holds: `_zglos_obcych_raz`
already calls the operator and tells them what to do. An alert that is already ringing does not need
a second bell, and an entry whose owner has been told is not abandoned. Applying the ceiling there
would also mean closing someone's week because a colleague wrote in their thread — a cause that has
nothing to do with the person being closed.

`BLOCKED` is a fourth enum value, which the proposed compatibility contract **N34**
(`plan-rozwoju.md` §11, quoted in `service.py`) would otherwise speak against. N34 exists so that an
image rollback is a plain version swap, which means it protects values **written to disk**: an older
image must be able to read the state file it finds. `ReadOutcome` lives only in memory for the
duration of one tick and is never serialised, so the reason behind N34 does not reach it — and the
on-disk status enum, which N34 does cover, is left untouched by this ADR. `should_expire` still
refuses to expire on `BLOCKED`, so the safety property of ADR 0003 is unchanged for that path.

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
`_commit` therefore zeroes `unknown_count` alongside `fail_count`, and that must stay when C3
lands. Without it, three deterministic interpretation failures already raise "chat unreadable" for
a chat that reads perfectly well.

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
against the recovered 0.2.19 source and are unmarked by the change that fixes them.

The control in the other direction must keep passing:
`test_trwale_nieodczytywalny_czat_nie_wygasa_i_nie_gubi_odpowiedzi` holds an entry 71 h old through
eight cycles and requires it to stay open — the ceiling must not turn into "expire on any failed
read". The three stranger-thread tests (`test_obcy_nadawca_NIE_pozwala_wygasic_wpisu`,
`test_obcy_nadawca_alertuje_RAZ_a_nie_przy_kazdym_odpytaniu`,
`test_dlawienie_alertu_o_obcych_nie_wycisza_INNEGO_czatu`) must pass unchanged; they are the
executable form of the `BLOCKED` split, since each holds an entry 142 h old and the middle one runs
exactly three cycles.

`tests/test_lifecycle.py` covers `should_expire` for `BLOCKED` and the ceiling predicate at its
boundary and with no anchor. `tests/test_config.py` covers refusal to start when the ceiling is
`<= 0`, at or below the courtesy floor, or above one week.
