# ADR-0015: A batched query must send the same date filter as the un-batched one

Date: 2026-09-09
Status: **accepted 2026-09-09 by the owner** (implemented and mutation-checked the same day)
Author: P0w3r223
Related to: ADR-0008 (decision 3, split by start date), docs/audit-2026-09-09.md (item A3)

---

## Context

`plan_batches` tiles a query by start date. When the operator gave no date range, it fills the
missing edges with `DATE_FLOOR = 1990-01-01` and `today` so that the plan is reproducible between
sessions — asking the operator for a starting year would break resume.

Those two substitutes do not stay inside the planner. `_split` writes them into every batch's
`Criteria`, and `Criteria.to_params` ships them to the API. So the same question asked two ways
sends two different filters:

| | `dataod` | `datado` |
|---|---|---|
| un-batched | *(not sent)* | *(not sent)* |
| batched | `1990-01-01` | *(today)* |

Every record whose `dataRozpoczecia` falls outside that window is silently absent from the batched
result — and only from the batched one. Splitting is offered exactly when the result is large, so
the loss lands on the runs that matter most.

**Measured 2026-09-09** on the operator's own store (16 310 records, zero requests):

| | |
|---|---|
| records before 1990-01-01 | **76** (earliest 1968-10-13) |
| records after today | **406** (latest 2027-05-10) |
| total unreachable by a batched query | **482 = 2.96 %** |
| records with no start date at all | **0** |

Two things follow. The loss is roughly 2.5× what the audit estimated from a different sample
(1.21 %). And the operator-facing sentence that explains the shortfall — *"różnica to wpisy bez daty
rozpoczęcia działalności"* (`ui/flow.py`) — is refuted outright: there are no such entries in the
store, while there are 482 entries the injected bounds exclude. The warning names a cause that does
not exist and hides one that does.

A future upper bound is not an anomaly: CEIDG accepts a registration with a start date ahead of
today, so `today` as a ceiling is wrong by construction, not by accident.

## Decision

**Open the outer edges.** Tiling keeps using `DATE_FLOOR` and `today` as *planning* bounds — the
plan stays deterministic and resume keeps working — but the criteria handed to the API drop the
bound the operator never gave:

- the **first** batch sends no `data_od` when the input had none;
- the **last** batch sends no `data_do` when the input had none;
- every interior batch is unchanged, and a query that *did* carry both dates is unchanged entirely.

The union of the batches is then exactly the un-batched query, which is the property that was
missing.

### Why the tile bounds stay dates

`refine()` splits one batch further when it turns out too large, and labels come from the period.
Both need a concrete `[od, do]`. Making them `date | None` would push the openness into every
consumer; carrying it as two booleans on the `Batch` keeps the tiling arithmetic total and lets
`refine()` pass the flags down to the sub-batch that inherits each edge.

### The fingerprint changes, and that is the mechanism working

`Batch.fingerprint()` is `criteria.fingerprint()`, so the first and last batch of an undated plan
get new fingerprints. An interrupted run planned before this change will not recognise them.

**This must not be papered over with a legacy-fingerprint fallback.** An old batch 1 covering
`[1990-01-01, 1999-12-31]` is a *different population* from the new one covering `(…, 1999-12-31]`.
Recognising it as "already fetched" would preserve exactly the defect being fixed — the entries
before 1990 would stay missing, now with a resume marker asserting they were collected.

Exposure was measured before the change: **zero** runs in the operator's store are `w_toku` or
`przerwany` (all seven are `zakonczony`). Interior batches keep their fingerprints either way.

### The shortfall note stops naming a cause it cannot know

`BatchResult.missing` is `expected - counted`: the pre-split `count` minus what the batches saw.
After this change the injected-bounds cause is gone, and what remains is genuinely ambiguous —
registry drift between the `count` request and the batch runs, and (in principle) entries carrying
no start date at all, which no date-partitioned batch can reach. The sentence names both and claims
neither.

`missing` also discarded its own opposite: `max(0, expected - counted)` silently swallows the case
where the batches saw **more** than the count predicted, which is the same registry drift pointing
the other way and had no observer at all. It gets one.

### An open-ended batch is never "already fetched"

Opening the last edge had a consequence the first draft of this ADR missed, and a code review
measured it: the last batch used to carry `data_do = today`, so its fingerprint changed daily and
`find_run(..., statuses=("zakonczony",))` never matched it. A stable fingerprint turned that
accident into a trap — planning the same undated query on two consecutive days now yields **37
identical fingerprints**, so a repeat `pobierz --partie` reports `pominięta (już pobrana)` for every
batch and returns no new records, while the un-batched path always starts a fresh run.

The fix is not to reintroduce the fabricated bound. It is to notice that the two kinds of batch are
not the same kind of thing: a **closed** tile `[2020-01-01, 2020-12-31]` is a population that cannot
grow, so "already fetched" is true of it; an **open** tile `(…, ∞)` grows with every new
registration, so "already fetched" is false of it by construction. `run_batched_fetch` therefore
skips a completed batch only when its upper edge is closed.

An *interrupted* open batch still resumes from its checkpoint — that path is separate and unchanged.
The cost is that a same-day resume refetches a completed trailing batch; the alternative is a tool
that answers a repeated question with yesterday's data and calls it a fetch.

## Alternatives rejected

| | Why not |
|---|---|
| **Warning only** — rewrite the sentence, keep the loss | The audit classes A3 as *loss of data*, not as a wording defect. A correct sentence about an incorrect result is still an incorrect result. |
| **Wider fabricated bounds** (1957, 2099) | The same defect with a smaller constant. It would also re-litigate the reasoning that produced `DATE_FLOOR = 1990` and leave the tool wrong at the new edges. |
| **Ask the operator for the range** | Breaks reproducibility of the plan, which is what makes an interrupted batched run resumable — the reason `DATE_FLOOR` exists. |
| **Post-filter after fetching everything** | There is nothing to post-filter: the records were never fetched. |

## Consequences

- A batched fetch of an undated query now returns the same population as the un-batched one. On the
  operator's store that is 482 records (2.96 %) that used to be unreachable.
- The first and last batch of an **undated** plan change fingerprint; interrupted batched runs
  planned before this change replan from scratch. Measured exposure at the time of the change: zero.
- The split table shows the open edges as `1990-1999 i wcześniej` / `2020-2029 i później`, so the
  screen says what the batch actually fetches rather than what its tile is called.
- One assumption is recorded rather than measured: that omitting `dataod` means "no lower bound"
  server-side. It is safe to lean on — `--od` and `--do` are already independent flags and
  `Criteria.to_params` already ships one-sided queries on every un-batched path — and the change
  makes batches send *less* than before, converging on the un-batched shape rather than inventing
  a new one. Settling it costs one production request and is not worth blocking on.
