# ADR-0018: One matching semantics for `Criteria`, and what to do with fields nobody measured

Date: 2026-09-09
Status: **proposed** (diagnosis only; no code changed)
Author: P0w3r223
Related to: ADR-0008 (`Criteria` as the only contract), ADR-0010 (report path), ADR-0014 (demo
register), docs/audit-architecture-2026-09-09.md (findings F-P5, F-E5, F-A1)

---

## Context

`Criteria` is meant to be the only contract between any input and any fetch. It is now interpreted
in **three** places, and two of them disagree:

| Site | `miasto` | `ulica` | `gmina` | Basis |
|---|---|---|---|---|
| `criteria.to_params:388` | sent to the server | sent to the server | sent to the server | the register decides |
| `reports.matches_criteria:270` | fragment (`:319`) | **exact** (`:321-329`) | exact, against `adresDzialalnosci.gmina` | `miasto` measured 2026-09-09; the rest unmeasured, and the docstring at `:293` says so |
| `demo/rejestr._pasuje:69` | **exact** (`:77-87` + loop `:89-96`) | **fragment** (`:100-102`) | **compared against the entry's `miasto`** (`:81`) | none measured; written a day apart from `reports.py` |

The `gmina` row is not a difference of semantics but of *field*: `demo/rejestr.py:81` reads
`("gmina", wpis.miasto)`, so on the demo register a `gmina` filter matches the **city** name.
Nothing measured says the two are interchangeable, and `reports.py` treats `gmina` as its own
address key.

Measured, run on the demo: `pobierz --demo -m "Łomża"` returns 48 firms, `pobierz --demo -m "Łomż"`
returns 0 and a "nothing found" screen. The register answers the second one non-empty — run
`eb1df3a8` with `miasto=['Łomża']` returned entries from `Stara Łomża przy Szosie`
(`docs/decisions.md:429-443`).

Two structural facts make this worse than a pair of bugs.

**First, the report path is the default.** `--zrodlo auto` plus `UZYC_RAPORTU(default="tak")` sends
an ordinary operator down `matches_criteria`. `report_covers` (`reports.py:357-367`) admits that
path on voivodeship count and statuses alone, so criteria carrying `ulica`, `powiat`, `gmina`,
`imie` or `nazwisko` pass through and are compared **exactly** against server semantics nobody has
measured. If any of them is a substring match server-side, the report path silently returns a
subset while `texts.report_offer:765-783` lists only what the report lacks in *content*.

**Second, nothing binds the three sites together.** `reports.py` is one of the modules the design
document never placed (audit finding F-A1), and no boundary rule names it or `demo/`. The demo
double is faithful to every property somebody wrote into `tests/fixtures/api_traits.yaml` and
unfaithful to every property that lives only in `docs/decisions.md` — which is the same shape as
every double defect this project has recorded.

The 2026-09-09 probe (`scripts/ceidg_probe_match_semantics.py`, two production requests) sent
mid-value fragments in two groups; both returned 204. That proves **at least one field per group**
is not a fragment match, and does not say which. The practical lesson recorded then still holds and
is the reason this ADR exists: **the text-field family is not uniform**, so one field's semantics
may not be carried to its neighbour.

## Options

### A. One predicate, one table, `unmeasured` as a value the code can refuse to guess

Extract a `matching.py` (pure, boundary rule 6) holding one predicate parameterised per field by
`ApiProfile`: `exact` / `fragment` / `unmeasured`. `reports.matches_criteria` and
`demo/rejestr._pasuje` both call it. The profile gains a per-field semantics table whose values are
sourced from `docs/decisions.md`, and `unmeasured` is not a synonym for `exact` — it is a state the
caller must handle.

- Cost: small; one new pure module, two call sites, one profile section.
- Effect: the divergence stops being possible; adding a field forces a decision about its semantics.
- Risk: the profile grows a section that participates in `profile_hash`, so old runs' fingerprints
  must be checked before it does — the same trap `Criteria.canonical_json` already carries.

### B. Leave both implementations, add a rule and a test asserting they agree field by field

- Cost: smaller; one test, one boundary-rule entry.
- Effect: the disagreement becomes a failure instead of a silent divergence.
- Risk: preserves two copies of a security- and correctness-relevant predicate, which is the exact
  argument ADR-0009 used when it collapsed two copies of the terminal neutraliser into
  `richtext.py`. They will drift again; the test only says when.

### The second, separable question: what `report_covers` does with an unmeasured field

Both options above leave this open, and the two wave-1 agents that found it prescribed **opposite**
remedies. This is the part that changes what the operator gets.

**B1 — refuse.** `report_covers` returns false when criteria carry any field whose semantics are
`unmeasured`, so the tool takes the API path. The operator never receives a silent subset.
Cost: a query the report might have answered correctly now costs up to three hours instead of one
request, and `_powod_braku_raportu` gains a fifth reason so the refusal is explained.

**B2 — disclose.** `report_covers` still admits the path, and `report_offer` gains one sentence
naming the fields whose matching may differ, or `_powod_braku_raportu` gains the fifth reason as a
warning rather than a refusal.
Cost: keeps the cheap path, and moves a judgement to the person least able to verify it — the
operator cannot know whether `ulica` is a fragment match server-side, because nobody does.

**B3 — measure, then neither.** Five production requests, one per field
(`powiat`, `gmina`, `ulica`, `imie`, `nazwisko`), each sending a mid-value fragment of a known
record. Each answer converts one `unmeasured` into `exact` or `fragment` and the question dissolves.

## Decision

**Proposed: A + B3, with B1 as the interim behaviour until the five measurements exist.**

Option A because the project has twice paid for a security- or correctness-relevant predicate
existing in two copies, and has twice concluded the same thing. B3 because five requests is a small
price for removing a guess from the default path, and because this project's standing rule is that a
fixture may not be trusted about the API. B1 in the meantime because a silent subset is the defect
shape this codebase has spent the most effort eliminating, and because the cost of refusing is
visible to the operator while the cost of disclosing is not.

**This is the owner's choice and the reason it is written as three parts:** B1 and B2 are a genuine
trade-off between a slow correct answer and a fast possibly-incomplete one, and reasonable people
pick differently. B3 removes the trade-off for 5 requests.

## Consequences

- `matching.py` joins the pure layer and must be named in boundary rule 6.
- `reports.py` and `demo/` acquire a place in the module map, which finding F-A1 says they lack.
- Under B1, an operator with an `ulica` filter loses the report path until it is measured. That is
  visible, explained, and reversible by the five requests.
- The demo double stops being able to disagree with the report path, which removes one instance of
  the project's most frequently recorded defect shape.
- Untouched: `criteria.to_params`, which sends the filter and lets the register decide. Nothing in
  this ADR changes what a request looks like.

---

## Addendum 2026-09-10 — two more fields entered the table before the decision was taken

`Criteria` gained `budynek` and `lokal` (parity with the public search form,
`docs/research/public-search-parity.md`). They are compared **exactly** in both places this ADR
names — `reports.matches_criteria` and `demo/rejestr._pasuje` — and the two sites were written to
agree field by field, which is option B's discipline applied by hand rather than option A's shared
predicate.

Three things this does not change, and one it sharpens:

- The divergence this ADR documents (`miasto`, `ulica`, `gmina`) is untouched.
- `nip_sc` and `regon_sc` never reach `matches_criteria`: the daily archive has no such column
  (measured), so `report_covers` and `pipeline.run_report_fetch` both refuse the report path.
- The server's semantics for `budynek` and `lokal` are **unmeasured**, like the five in the table.
  They are documented as a choice with a stated reason (the manual says "full number"; a fragment
  would match "12" against "112"), not as a measurement.

What it sharpens: the hand-kept agreement now spans seven fields across two copies of the predicate,
which is exactly the drift argument option A makes. The addendum is deliberately not a decision —
the owner's choice between A+B3 and B still stands open.
