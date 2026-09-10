# ADR-0020: The PKD 2007 layer expires on a measured condition, not on a date in a comment

Date: 2026-09-09
Status: **proposed** (diagnosis only; no code changed)
Author: P0w3r223
Related to: ADR-0012 (PKD vintage coverage — this ADR corrects its expiry date and its stated
removal cost), docs/audit-architecture-2026-09-09.md (F-A6, F-O4, §5.1), CLAUDE.md

---

## Context

ADR-0012 shipped a transition layer with an end date. `ceidg_tool/data/pkd2007_2025.yaml:15` says
`Wygasa: 2026-12-31 — koniec okresu przejściowego`, and ADR-0012:374 records the removal cost as
"one data file, one module, one flow step, one field". Three of those four statements have since
become false, and the fourth was never accurate.

### The date is wrong

Two research passes, neither seeing the other, read the primary acts and converged.
**Ustawa z 21.11.2025 o zmianie ustawy o statystyce publicznej** (Dz.U. 2025 poz. 1792, promulgated
16.12.2025; art. 12 in force from 31.12.2025) added the mechanism that rozporządzenie 1936 never
had:

- **art. 12 ust. 1** — entities that have not changed their entry by 31.12.2026 have the activity
  subject replaced automatically **"w terminie do dnia 31 stycznia 2027 r., jeżeli jest to
  możliwe"**. A window, not a moment.
- **art. 12 ust. 2** — where automatic replacement is not possible, in CEIDG **"po dniu 31 stycznia
  2027 r. wykreśla się wpis z urzędu"**.
- **art. 11** — this already happened once: entries carrying `93.29.Z` unchanged by 31.12.2025 had
  it replaced on **1.01.2026**, and ust. 2-3 changed **the entry's other PKD 2007 codes at the same
  time**. A selected sub-population was reclassified eight months ago and nothing here noticed.

So `wygasa: 2026-12-31` points at a date after which the old vintage is still in the data for at
least a month, and possibly much longer.

### The date is now movable

Art. 40 ust. 1 pkt 2 and ust. 7 delegate the *okres równoczesnego stosowania* to a Council of
Ministers regulation; art. 18 keeps rozporządzenie 1936 in force until a new one is issued. Neither
pass found such a regulation or a draft as of 2026-09-09. A hard constant in a file header is
therefore an assumption carrying risk, not a durable fact — and the operator's own pages disagree
with the statute already (biznes.gov.pl and the GUS FAQ, last updated 26.02.2025, both say
1.01.2027; the act says *by* 31.01.2027, and the act has precedence).

### The removal cost is understated, and one part of it is irreversible

Measured two ways, because they answer different questions. The **field** `pkd_2007` appears in
**4 production modules and 9 test files**: `criteria.py` (`:82`, `:256`, the validator at `:283`,
`poszerzenia` at `:329` with its reset at `:376`, `wszystkie_pkd:420-422`, `canonical_json:424-431`,
`:460-463`), `cli.py:448,524-531`, `ui/flow.py` in **two** functions (`_z_rocznikiem:222-231`,
`_kandydaci:234-273`), and `ui/texts.py:876`. The **layer as a whole** — what a removal would
actually touch — is **7 production modules and 13 test files** plus the data file, adding
`pkdmap.py`, `demo/korpus.py` and `pipeline.py`, which owns the map's seam (`:53` import, `:103` the
`Deps` field, `:318` the load) although it never names `pkd_2007` itself.

The sharp part is `canonical_json`: **deleting the carve-out after the transition invalidates every
stored run fingerprint**, which is resumability. Removing this layer is a migration, not a deletion.

### Nobody has said what happens to the data afterwards

Pass 2 searched the integrator pages, the Akademia documentation index and the changelogs: there is
**no** release note, changelog or communication tying the API to the PKD transition, and none about
the fate of `rokPkd` or the behaviour of the `pkd=` filter. That is an established absence.

### A comment nothing reads is a known-ineffective pattern

Expiring-TODO linters break the build on a date, and their maintainers treat that as a liability —
eslint-plugin-unicorn ships `checkDatesOnPullRequests: false` by default so the failure does not land
on whoever happens to open a PR that day. Maipradit et al. (EMSE 2020) found 58 % of commits removing
an "on-hold" debt comment did not fix the problem, motivating the work with the observation that
developers do not track the external event. Ericsson/O2 on 6.12.2018 — roughly 32 million subscribers
offline from a certificate whose expiry was known to the second — is the strongest evidence that
"the deadline is recorded" and "somebody will act" are different propositions.

And this project already has the doctrine: *a guarantee whose violation has no observer is not a
guarantee*. `wygasa: 2026-12-31` has no observer. Nothing would print if it passed.

## Options

### A. Correct the date and leave the mechanism as it is

Change `2026-12-31` to `2027-01-31` in the header, ADR-0012, `pkdmap.KONIEC_PRZEJSCIA`, `cli.py:452`
and `ui/texts.py:916`.

- Cost: trivial.
- Effect: the documents stop being wrong.
- Against it: it is the same unobserved pattern with a better number, and the number is delegated to
  a regulation that can move again. This is the option that guarantees a third correction.

### B. An observer on the measured condition, with the date as a secondary trigger

A test — or a `raporty`-path check — that reads the vintage share from a report the operator already
has on disk and **fails, or prints loudly, when the share of unreachable entries falls below a
stated threshold**, and separately when the calendar passes the recorded legal date. ADR-0012's
"Revisit when" section already half-states this ("or earlier, if the register's share of 2007 codes
drops far enough"); what is missing is the thing that checks and shouts.

- Cost: small. The measurement is already written and costs zero requests over an archive on disk:
  today it reads **24 494 = 8.59 %** of 285 026 rows carrying no code the dictionary knows,
  reproducing `CLAUDE.md` exactly.
- Effect: the layer announces its own obsolescence from data instead of from a calendar, which is
  the variant the external evidence supports and which survives the regulation moving.
- Risk: the threshold is a judgement. It should be recorded with its reason, like every other
  constant here.

### C. Plan the removal as a migration now, execute later

Write down what deleting the layer costs — including that `canonical_json`'s carve-out cannot simply
go, because old fingerprints stop matching and interrupted runs stop resuming — and keep it with the
data file.

- Cost: small, and it is documentation.
- Effect: the removal stops being "delete one file" in somebody's memory. It is complementary to B,
  not an alternative.

## Decision

**Proposed: B + C, and A as their first step.**

Correct the date to 2027-01-31 everywhere it appears, and record *why* it changed and that it is now
set by regulation rather than statute. Add the observer over the vintage share, with the threshold
and its reason written down. Replace ADR-0012:374's removal sentence with the actual surface —
six modules, ten test files — and name the `canonical_json` carve-out as the part that needs a
migration decision rather than a deletion.

Two things are **not** proposed here, deliberately. Removing the layer: the transition has not ended
and the measured gap is still 8.6 %. And guessing what art. 12 ust. 2's "wykreśla się wpis" covers —
the whole entrepreneur entry, or only the activity subject. The statute says "wpis" while the
parallel KRS provision deliberately says "przedmiot działalności", which argues for the broader
reading; the consequence is severe enough that it should be asked of the operator, not inferred.

## Consequences

- One measurement in the tree becomes load-bearing, so it needs the treatment every other
  load-bearing measurement here gets: a stated source, a date, and a reason for its threshold.
- `docs/decisions.md` gains the legal timeline, which it currently does not carry — it records what
  the probe measured about the API, and the PKD sunset is the one deadline this architecture has.
- Re-measuring the vintage share on a **fresh** report costs 2 production requests and would also
  re-test finding F-O4 (the 141 rows labelled `RokPKD=2025` that carry a provably-2007 code) on
  post-reclassification data. Worth doing once, with the owner's consent, before the threshold in
  option B is chosen.
