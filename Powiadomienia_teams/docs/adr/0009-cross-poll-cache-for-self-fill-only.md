# 0009 — A cross-poll schedule cache, for self-fill detection only

Date: 2026-09-07
Status: accepted
Author: P0w3r223
Related to: ADR 0003 (expiry requires evidence), ADR 0004 (self-fill detection), `runtime/snapshot.py`

---

## Context

`runtime/snapshot.py` states its own scope in the first paragraph: *"the lifetime is ONE
`poll_replies` run and not a moment longer. A cross-run cache would be something qualitatively
different."* This ADR introduces exactly that cross-run cache, so the rule has to be rewritten
rather than quietly bent.

The measurement that forced the question, taken from the client's container log:

- every listener cycle that has any open conversation fetches the **entire** team schedule —
  three pages, roughly 2000 shifts, plus `timesOff`, about four seconds;
- the cycle runs hourly, around the clock, from Friday's run until Monday's deadline;
- that is roughly **61 full fetches** per weekly cycle, of which the write path needs at most
  a handful.

The fetch cannot be narrowed: `$filter` on this endpoint cannot express a date range without
risking silently dropped entries (documented in `GraphClient.read_shifts`), so the client fetches
everything and filters locally. The collection grows by roughly 2000 shifts a year and never
shrinks, so the cost grows with the installation's age.

Two paths consume that data, and they are **not** equally sensitive:

| path | what it decides | cost of stale data |
|---|---|---|
| step 1 — `_odsiej_juz_zapisane` | which days to write to Shifts after an explicit "yes" | a **second set of entries** in the client's schedule; `create_shift` does not deduplicate and there is no undo |
| step 1.5 — self-fill detection | whether to thank a silent employee instead of nagging them | a thank-you one cycle later |

Treating both with one policy means paying the write path's price for the whole week to protect
a courtesy message.

## Decision

`runtime/pamiec_grafiku.PamiecSamouzupelnien` caches successful schedule reads across runs, with
a six-hour TTL, and **step 1.5 is its only consumer**. The write path keeps its per-run snapshot,
unchanged.

Three properties carry the decision.

### 1. Safety does not depend on the TTL — but it takes two rules, not one

Step 1.5 can be wrong in **two** directions, and the first draft of this ADR only accounted for
one of them. Both are irreversible, so both are handled.

**A stale NEGATIVE** — the cache says "not filled", the schedule says otherwise. Harmless on its
own, except for an entry whose reply deadline falls in the current cycle: step 2 would immediately
declare expiry and send *"I didn't get a reply"* to someone who filled the schedule in themselves,
closing the topic terminally. Those entries therefore bypass the cache (`odswiez`), using exactly
the predicate step 2 will use a moment later.

**A stale POSITIVE** — the cache says "filled", and the shift is no longer there (a manager deleted
a mistaken entry, a shift moved to another week). `zamknij_samodzielnie_uzupelnione` is terminal
and unconditionally tells the employee *"I can see your schedule is already filled ✅"*, so a stale
positive closes the topic **on a false statement** and leaves an empty week behind. Before this
change that mistake had a window one run long; a cache would have stretched it to the TTL.

So a cached result may be used to **not act**, never to act: whenever step 1.5 is about to close
anything, the closure is confirmed against a fresh read of those weeks. That costs one fetch **per
closure**, not per cycle — through most cycles nobody self-fills and nothing is fetched, so the
saving stands.

With both rules in place the TTL is a cost knob, not a safety knob: six hours could be six minutes
or twelve hours without changing which mistakes are possible.

**Honest note on the default configuration.** For `AWAITING_REPLY` entries the `odswiez` rule
rarely gets to fire: the calendar deadline falls at 05:00 local, inside quiet hours, and
`poll_replies` returns immediately during those — so the last cycle before the deadline and the
first one after it are more than eleven hours apart and the cache has expired anyway. The rule is
live for `AWAITING_CONFIRM` entries, where the courtesy floor moves the deadline to an arbitrary
hour, and for non-default `REPLY_DEADLINE_OFFSET_H`. It is kept because correctness should not
rest on an accident of the quiet-hours window.

### 2. Data flows one way: snapshot → memory

`SnapshotGrafiku.znane()` exposes the reads the write path already paid for, and the memory
accepts them (`przyjmij`). There is no setter in the other direction, and `PamiecSamouzupelnien`
deliberately has **no** `dla_tygodnia` / `dla_tygodni` methods — so substituting it on the write
path does not type-check. `tests/test_zrodlo_swiezosci.py` enforces the same from the syntax side:
it iterates over schedule *reads* and requires each to be taken from a receiver named `snapshot`.

Two independent signals for one mistake is intentional. The substitution is a one-line change and
no behavioural test would catch it until someone confirms "yes" on a week the cache had not
refreshed — which is to say, at the client, irreversibly.

### 3. Failures are not remembered

A failed read is remembered for one run (that is `SnapshotGrafiku`'s existing behaviour and stays).
Remembering it for six hours would disable step 1.5 for six hours after a single network blink.

## Consequences

**What this costs.** A thank-you for self-filling may arrive up to six hours later than before.
In the client's installation the practical difference is one cycle versus a few.

**What it saves.** Roughly 61 full schedule fetches per weekly cycle become roughly 10 — and the
saving grows with the size of the collection, which is the number that keeps rising.

**Diagnostic commands do not use it.** `--poll-once` and `--proba-nasluchu` pass no memory
(`pamiec=None` is the default and means exactly today's behaviour). The operator runs them to see
the state, not to save a request.

**Where this can break next.** If a future change makes step 1.5 write anything to Shifts, this
ADR's premise disappears and the memory has to go with it. The premise is *"the worst outcome is
a message, never a schedule entry"* — and that sentence is now a gate, not a hope:
`tests/test_zrodlo_swiezosci.py::test_krok_1_5_nie_pisze_do_shifts` fails if any function of
step 1.5 calls `create_shift`, `create_time_off` or `_apply_schedule`. It was added after a review
managed to insert exactly such a loop with the other three rules staying green.
