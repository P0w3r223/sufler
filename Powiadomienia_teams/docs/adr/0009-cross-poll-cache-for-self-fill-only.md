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

### 1. Safety does not depend on the TTL

An entry whose reply deadline falls in the current cycle bypasses the cache (`odswiez`). That is
the one case where stale data has an irreversible consequence: step 2 would immediately declare
expiry and send *"I didn't get a reply"* to someone who filled the schedule in themselves, closing
the topic terminally. For every other entry the worst outcome is a later thank-you — a direction
step 1.5 already accepts, since an employee who fills the schedule *during* a run is only noticed
in the next one.

So the TTL is a cost knob, not a safety knob. Six hours could be six minutes or twelve hours
without changing which mistakes are possible.

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
a later thank-you"*, and it is worth checking that sentence before extending that step.
