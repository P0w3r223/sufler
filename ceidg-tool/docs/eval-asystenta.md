# Assistant evaluation — 110 queries, invariance under typos

Date: 2026-09-09
Status: accepted
Author: P0w3r223
Related to: ADR-0011 (the assistant), ADR-0017 (the clarification round), docs/test-runs-phase4.md (group A), scripts/eval_asystenta.py, tests/eval/zapytania.yaml

---

## What was run, and why it is shaped this way

**110 distinct queries, 140 model calls, 168 s, four workers, zero CEIDG requests.** The assistant
sits upstream of `count`, so an evaluation that stops at `Criteria` reaches no register, reads no
CEIDG token and puts nobody's personal data on screen. The whole cost is model calls.

Three design choices, each with a reason that is not aesthetic:

**15 seeds × 5 perturbations, not 75 hand-written expectations.** A typo, lower case and missing
diacritics do not change what the operator meant, so they do not need an answer of their own — the
assertion is agreement with the seed. That is a metamorphic relation rather than a value, and it
means 15 gold answers to maintain instead of 75.

**Three tolerances, not one.** Group A measured on 2026-09-07 that the same sentence produced 4 PKD
codes and then 15 (2–4 after the prompt fix), and recorded the conclusion that run-to-run variation
is a property of the model. Comparing whole `Criteria` for equality would therefore manufacture
false failures — the known weakness of Exact Match in text-to-SQL evaluation, which the field
replaced with execution-level comparison. Stable fields are compared exactly; the PKD set by
intersection and a cardinality bound.

**pass@1 beside pass^3.** The operator does not repeat a question three times to see whether the
program still agrees with itself, so the number that matters is pass^3, and the gap between the two
is the measure of how much instability the model contributes.

The PKD expectations are **name patterns resolved against the shipped dictionary**, never codes
written down by hand. `CLAUDE.md` forbids the latter for a measured reason, and the harness refuses
to start if a pattern matches nothing or more than four codes.

## Results

| Class | Queries | Calls | Result |
|---|---|---|---|
| I — seeds | 15 | 45 | **pass@1 15/15, pass^3 15/15** |
| I — perturbations | 75 | 75 | **invariance 60/75 = 80 %** |
| II — no usable filter | 10 | 10 | **10/10** enter the clarification round |
| III — filter the register lacks | 10 | 10 | **10/10** name the right limitation, none fabricated |

**PKD codes per answer: 1–2, mean 1.1** (102 answers with one code, 18 with two). Group A's post-fix
spread was 2–4 and its pre-fix blow-up was 15, so the narrowing instruction has held and then some.
The harness threshold of 6 was never approached.

**Response time: median 4.6 s, min 3.5 s, max 10.4 s.**

## The finding: one field, and it is not the one the perturbations were aimed at

Fifteen invariance breaks, and **all fifteen are `wojewodztwo` alone**. `miasto`, `pkd`, `status`,
`szczegoly` and both dates never disagreed once across 75 perturbations.

The model infers the voivodeship from the city in **48 of 120 class-I answers (40 %)** — not always,
not never, and not correlated with the kind of perturbation:

| Perturbation | Breaks |
|---|---|
| P1 lower case | 3 |
| P2 no diacritics | 3 |
| P3 typos | 2 |
| P4 politeness prefix | 3 |
| P5 no diacritics + typos | 4 |

The clearest case needs no typo at all:

    "stolarze w Białymstoku"                          -> wojewodztwo: ['podlaskie']
    "dzień dobry, poproszę stolarze w Białymstoku"    -> wojewodztwo: []

A greeting flipped it. And note what the seeds say: repeating the *identical* sentence three times
gave the identical answer every time (pass^3 = 100 %). The instability is invisible to repetition
and appears only when the surface of the sentence changes in a way that should not matter — which
is precisely the case a metamorphic test exists to reach and a golden-answer test cannot.

**Why it is not cosmetic.** `miasto` and `wojewodztwo` are different parameters, so the register
ANDs them. `miasto=Białystok` and `miasto=Białystok & wojewodztwo=podlaskie` are therefore not the
same query: any entry whose voivodeship field is empty or says something else drops out of the
second. Whether the two populations actually differ was settled the same day, at two `count`
requests — **they differ by 980 entries**; see the next section but one.

## A defect in this evaluation's own scoring, found before it was believed

The first summary reported **0 % for the perturbation class**. That was false and it was the
harness's fault, not the model's: perturbations carry no gold answer, so they were left unscored
(`ok=None`), and the counter treated "not scored" as "failed". The scoring of perturbations now
happens inside the harness, against the seed's own answer, so the JSON carries a verdict instead of
a hole for a later counter to guess at.

Two smaller corrections of record from the same run, both of the same shape — a cause asserted
without measurement. The run was reported mid-flight as taking over twenty minutes and hitting
model rate limits, and the slowness was attributed to adaptive thinking. It took **168 seconds**,
the median call was 4.6 s, and nothing was rate-limited; the output was invisible because it was
piped through `tail`, which flushes only at the end.

## The measurement, the fix, and the re-run

**Two `count` requests on production settled whether it mattered.** Without the PKD filter, to keep
the population large and therefore sensitive:

    miasto=Białystok                          ->  50 725
    miasto=Białystok & wojewodztwo=podlaskie  ->  49 745
    difference                                ->     980  (1.93 %)

So the inferred voivodeship removes 980 entries the operator asked for. Whether they are Białystok
records filed under a different voivodeship, records with the field empty, or localities matched by
`miasto`'s fragment semantics is **not** distinguished here; the effect is, and the effect is what
the operator receives.

**The cause was not a missing rule.** `assistant/prompt.py` already said *"Nie zgaduj województwa,
gdy padła sama nazwa miasta, **chyba że jest jednoznaczna**"* — and Białystok, Katowice, Toruń and
Gdańsk are unambiguous, so the model was **obeying** the instruction. The exception licensed the
behaviour; the 40/60 split was its judgement of "unambiguous" wobbling with surface noise. The fix
removes the exception and attaches the measurement, rather than adding a second rule to argue with
the first.

**Re-run on the identical set, same day, same model:**

| Metric | Before | After |
|---|---|---|
| Invariance (perturbations) | 60/75 = **80 %** | 75/75 = **100 %** |
| Answers carrying a voivodeship | 48/120 = 40 % | **0/120** |
| Breaks by field | `{wojewodztwo: 15}` | `{}` |
| Seeds pass@1 / pass^3 | 15/15 | 15/15 |
| Class II / class III | 10/10 | 10/10 |

Nothing regressed, and the run cost 162 s.

**Then the counter-cases, because the set had none and that is one-sided optimisation.** Removing an
inferred voivodeship must not remove a stated one — otherwise the repair cures one silent loss by
producing another. Three seeds were added (S16 names only a voivodeship, S17 the same, S18 names a
city **and** a voivodeship) and run with all five perturbations: **24/24**. The stated voivodeship
survives; only the inferred one is gone.

## What is open

- ~~**The `wojewodztwo` inference.**~~ — **measured, fixed and re-measured 2026-09-09**, see the
  section above. Kept here because the shape recurs: the rule existed, the exception inside it was
  the defect, and no amount of adding rules would have found that.
- **The set is not saturated but it is close** — three of four classes are at 100 %. Anthropic's
  guidance is explicit that an eval passing everything has stopped supplying signal, so the next
  version should add cases drawn from real operator failures rather than from imagination.
- **The seed sentences are the author's, not operators'.** Group A's eight came from the same place.
  The first real operator session should be mined for sentences and folded in here.
