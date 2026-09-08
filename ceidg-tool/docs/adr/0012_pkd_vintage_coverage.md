# ADR-0012: The PKD 2007→2025 coverage gap — expand where it is clean, ask where it is not

Date: 2026-09-07
Status: **accepted 2026-09-07 by the owner**
Author: P0w3r223
Related to: docs/decisions.md (PKD vintage sections), docs/status.md (open item "PKD vintage
            coverage"), docs/test-runs-phase4.md (groups C and D), ADR-0008, ADR-0011,
            docs/design/phase2_core.md, PKD/KluczePKD_2007_2025.xls

---

## Context

The register is mid-transition. `pkd=` matches the code **as stored on the record**, each record
carries one vintage (`RokPKD`), and the transition from PKD 2007 runs to **31.12.2026**. The tool
knows only PKD 2025 (`ceidg_tool/data/pkd2025.yaml`, 728 subclasses), so every PKD-filtered query
silently returns a subset. Measured 2026-09-07 (see `docs/decisions.md` for provenance):

| Measurement | Value |
|---|---|
| Records in the sample (one voivodeship, daily snapshot) | 285 026 |
| `RokPKD` = 2007 | 167 139 (58.6 %) |
| Unreachable by any code in `pkd2025.yaml` | 71 817 (**25.2 %**) |
| PKD 2025 codes needing at least one PKD 2007 filter | **357 of 728** |
| …of which expand **cleanly** (the predecessor brings nothing else) | **51** |
| …of which expand **ambiguously** | **306** |
| Distinct PKD 2007 predecessors | 283, of which **124 are still live 2025 codes with a different meaning** |
| Worst fan-out | `4791Z` → 36 codes, `4799Z` → 35, `8299Z` → 26 |

*(The first four figures were 264 / 55 / 209 / 159 when this ADR was written. The code review of
the implementation found that the generator was dropping 230 mappings — every predecessor that is
itself a live PKD 2025 code — on the stated grounds that "the 2025 filter already covers it". It
covers the **record**, not the **industry the operator asked for**: `8551Z` means "Pozostałe formy
edukacji sportowej" today, so adding it to a fitness-club query brings a different trade along.
That rule left **93 PKD 2025 codes with no predecessor at all**, fitness clubs and bakeries among
them. Those mappings are now in, as a second kind of ambiguity with its own sentence on screen.)*

Per industry, weighted by real records (main code only):

| PKD 2025 code | Reachable today | Added by its 2007 predecessor | Ratio |
|---|---|---|---|
| `9621Z` hairdressing | 2 266 | 6 811 (`9602Z`) | **3.0x** |
| `9531A` vehicle repair | 2 330 | 5 912 (`4520Z`) | 2.5x |
| `4399Z` other specialised construction | 3 397 | 4 684 (`4120Z`) | 1.4x |
| `9130Z` conservation | 34 | 4 951 (`4120Z`, `9003Z`) | 145x |

Both defaults are wrong. Not expanding loses most of an industry with no error anywhere in the
path; expanding blindly merges industries the operator asked to distinguish, for 209 of the 264
codes. `9602Z` cannot be un-split by a query: a record coded `9602Z` carries no evidence of whether
it is a hairdresser or a beautician. **No mechanism removes that; the only honest choice is which
error to make, made visibly, per query.**

This blocks gate 3: its walk sentence is *"salony fryzjerskie w Łomży"*, and hairdressing is the
worst-affected trade in the register.

**Provenance caveat that shapes the design.** The percentages and per-code ratios above come from
**one voivodeship's** snapshot on one day. They are sound as evidence that the gap is large and
which trades it hits; they are **not** national constants, and shipping them as per-code estimates
would be exactly the fabricated-number failure `scripts/build_pkd.py` exists to prevent. Runtime
numbers must come from `count`, never from a table in the source.

### What the code says today (checked, not assumed)

- `Criteria` (`ceidg_tool/criteria.py`) is the only contract; `to_params` renders every `pkd` value
  as a repeated key into **one** URL. `client.count` and `client.iter_pages` share `_list_params`.
  **Consequence: an added code costs no extra request by itself — it raises `count`, hence pages,
  which `estimating.estimate` already prices.**
- `ui/flow.prepare_fetch` holds the sequence resume → report → one `count` → cost table → choice.
  `tests/test_ui_flow.py::test_exactly_one_count_request_precedes_the_cost_table` pins it by
  counting requests, not text.
- `reports.matches_criteria` filters the daily CSV locally on `criteria.pkd`. On the report path
  the vintage costs **zero** requests, and `report_covers` (one voivodeship, no `WYKRESLONY`)
  covers gate 3's own walk.
- `normalizer.py` already exports `rok_pkd` ("Rok klasyfikacji PKD (2007 lub 2025)") into `Firmy`.
- `assistant/pkd.validate_codes` rejects any code outside the 2025 dictionary;
  `criteria.normalize_pkd` on the `--pkd` flag checks shape only. Same input, two outcomes — the
  asymmetry `docs/decisions.md` records.
- List URLs are **not** length-checked (`max_url_length` is applied only in `client._chunk_ids`).
  Each `pkd=XXXXX` is ~10 characters against a 4 000 limit, so even a 37-code query is safe; worth
  knowing, not worth a mechanism.

### The one thing nothing rested on — now measured (2026-09-07, same day)

**Whether repeated `pkd=` is OR-ed** had never been checked, although the tool has been sending
multi-code PKD queries all along. Repeated `nip=` was measured OR (2026-09-06) and `status=` works;
`api_notes.md` lists `pkd[]` as a list parameter; `pkd` itself, never. It was written up here as a
gate item, and three `limit=1` requests settled it the same day:

| Request | `count` |
|---|---|
| `pkd=9621Z` (PKD 2025) | 38 201 |
| `pkd=9602Z` (PKD 2007) | 187 149 |
| both together | **225 350** = the exact sum |

**Repeated `pkd=` is OR-ed**, so every option below stands. The exactness carries a second result
for free: no record matched both codes, so the vintages are disjoint from the API side too — the
`RokPKD` finding, confirmed by a different instrument. And it supplies the national figure the
regional sample could only approximate: a PKD 2025 hairdressing query reaches 38 201 of 225 350
records, **17 %**. The voivodeship snapshot suggested 25 %; nationally the gap is wider.

## Constraints any option must respect

1. `Criteria` is the only contract between input and fetch.
2. The cost table precedes consent, and the request budget shown must be the one spent.
3. Boundary rules (`docs/design/phase2_core.md`), especially 13: `assistant/*` imports no
   `client`/`store`/`pipeline`.
4. The PKD data is **generated from the official source**, never hand-written, never model-written.
5. The confirmation screen shows the PKD **name** — the operator's only control over whether they
   got the industry they asked for.
6. `ui/texts.py` authors every operator-facing sentence; `cli.py` authors none.

## Options considered

### Option A — Do nothing; narrow the gate-3 walk

Change nothing in code. Pick an unaffected industry for the gate-3 sentence and let the transition
expire on 31.12.2026.

- **Pros**: zero effort, zero new data, zero new surface, nothing to delete later. The problem
  genuinely does end by itself in under four months.
- **Cons**: every PKD query keeps returning a silent subset (25.2 % of the register) for those four
  months. Worse, it changes what gate 3 *measures*: the acceptance criterion is "a person without
  API knowledge reaches a finished file", and steering the walk away from the affected trades makes
  the gate pass on the easy case while the failure mode it was meant to expose stays live. The
  register may also lag the legal deadline — 58.6 % were still 2007-coded four months before it.
- **Effort**: S · **Risk**: Med — the risk is that "it expires by itself" is a fact about the
  regulation, not about the data.

### Option B — Unconditional disclosure, no transition key

One sentence from `ui/texts.py` whenever `criteria.pkd` is non-empty: the register is mid-transition
and results may be incomplete until 31.12.2026.

- **Pros**: S effort, no new data file, no new module, honest.
- **Cons**: fires on all 728 codes, including the 464 where nothing is lost — a warning that is
  usually false trains the operator to ignore it, which costs more than it buys. It offers no
  remedy: through the assistant the operator cannot even type the 2007 code. And a *conditional*
  sentence (fire only for the 264) needs the transition key anyway, so the "no data file" saving is
  smaller than it looks.
- **Effort**: S · **Risk**: Med — a warning nobody can act on is disclosure theatre.

### Option C — Expand only where the expansion is clean (55 codes); disclose the other 209

Build the reverse map from the GUS key. When a chosen 2025 code has 2007 predecessors that lead
**only** to it, add them to the query automatically and show them on the confirmation screen with
their names. When the predecessor is ambiguous, add nothing and say what is out of reach.

- **Pros**: no extra requests; no question added to the flow; over-inclusion is impossible by
  construction (a clean predecessor contains nothing else); the screen still shows names, so the
  existing control keeps working.
- **Cons**: leaves 209 codes — including hairdressing, vehicle repair and construction, i.e. the
  most frequent unreachable codes — as disclosure without control, which is precisely the defect
  `docs/decisions.md` already names about `KOD_PKD_Z_INNEGO_ROCZNIKA`. Gate 3 still walks the
  incomplete path.
- **Effort**: M · **Risk**: Low.

### Option D — Option C plus an explicit operator choice for the ambiguous 209

Same as C; additionally, when an ambiguous predecessor exists, `flow` asks one question before the
count: narrow (2025 only) or wide (add the 2007 predecessor, which also covers *these* industries,
named from the key). The answer is recorded **in `Criteria`**, so it reaches the query file, the
fingerprint, the resume identity and `Metadane`.

- **Pros**: the operator decides which error to make, on the one screen they act on, with the
  sibling industries named rather than described. Non-interactive runs keep today's behaviour
  unless the query file says otherwise.
- **Cons**: a question asked without numbers ("wider or narrower?") is hard to answer well.
- **Effort**: M–L · **Risk**: Low–Med.

### Option E (chosen) — Option D, with both populations priced before the question

As D, but when an ambiguous expansion exists the flow spends **two** `count` requests — narrow and
wide — and the question carries the two numbers ("2 266 firm w PKD 2025; 9 077 po dołączeniu
`9602Z`, które obejmuje też kosmetykę"). The chosen population's count feeds the existing cost
table; no further count is spent.

- **Pros**: turns disclosure into control — the operator sees the size of what they are about to
  miss or about to over-collect, per query, measured rather than estimated. It also removes the
  temptation to ship regional statistics as constants. The invariant stays boundable and testable.
- **Cons**: +1 request (3.75 s) on queries with an ambiguous expansion; the "exactly one count"
  invariant must be restated and its test rewritten.
- **Effort**: L · **Risk**: Med — the risk is invariant creep, addressed by the restatement below.

### Rejected outright

| Option | Why |
|---|---|
| Expand all 264 automatically | Merges industries the operator asked to distinguish, for 209 codes, with no screen able to say so. |
| Expand inside `to_params` behind a `Criteria` flag | `describe()` and the fingerprint would stop describing the query actually sent, and `reports.matches_criteria` would need a second copy of the rule. |
| Ship per-code record shares as static data to avoid the second count | Those numbers are one voivodeship on one day. A national-looking constant derived from them is the fabricated-number failure `build_pkd.py` exists to prevent. |
| Post-filter a widened result back to the narrow industry | A `9602Z` record carries no evidence of which side of the split it belongs to. Name-substring heuristics over registry text are not an answer. |
| Put the vintage in the model's schema | The model would be choosing the population. Expansion is deterministic; `AssistantAnswer` stays closed. |

## Decision

**Option E.** The gap is 25.2 % of the register and lands hardest on ordinary trades; the argument
for doing nothing is a fact about a regulation rather than about the data, and gate 3's own walk is
the case the tool handles worst. Between the sub-options, the deciding argument is that this project
has twice held that a screen the operator acts on must carry the *consequence*, not just the fact:
the second count is what makes "you will miss two thirds of the salons" a number instead of a
disclaimer, and it costs one request out of an hourly budget of a thousand.

### Sub-decision 1 — the expansion happens in the input layer and is materialised into `Criteria`

The reverse map is applied **once**, producing a plain `Criteria` that already carries every code
that will be sent. Everything downstream — `to_params`, `fingerprint`, `describe`, resume,
`reports.matches_criteria`, `Metadane` — keeps working unchanged, and the API path and the report
path stay consistent for free. This mirrors ADR-0011's shape: the assistant is a producer of
`Criteria`, not a new pipeline; the transition map is a *transformer* of `Criteria`, not a new
dialect.

### Sub-decision 2 — `Criteria` gains one field: `pkd_2007: tuple[str, ...]`

Rendered as additional `pkd=` parameters, validated by `normalize_pkd`, deduplicated like the rest.
The alternative — merging everything into `pkd` — loses the distinction between what the operator
asked for and what we added, so `describe()` would present `9602Z` as the operator's own choice.
Keeping them apart lets one sentence say "asked: `9621Z`; also sending: `9602Z` (PKD 2007)" on the
screen, in the query file and in `Metadane`. Cost: one field in the contract, one line in
`reports.matches_criteria` (union both tuples), one `add(...)` in `to_params`.
`AssistantAnswer` does **not** gain the field.

### Sub-decision 3 — the decision point is `flow.prepare_fetch`, before resume and before count

One site serves all four inputs. Order becomes:

```
is_empty  ->  build narrow/wide candidates (pure, 0 requests)
          ->  resume lookup over BOTH fingerprints (store only, 0 requests)
          ->  report offer (unchanged)
          ->  [if an ambiguous expansion exists and no report chosen]
                 count(narrow), count(wide)  -> vintage block -> question
          ->  cost table for the chosen population (reuses its count)
          ->  unchanged from here
```

Resume must be looked up for both candidates because the choice changes the fingerprint; otherwise
an interrupted wide run would be invisible to a narrow re-entry. This is a store query, so it costs
nothing. Clean expansions are applied without a question but **are shown**; ambiguous ones are the
only thing that asks.

### Sub-decision 4 — the invariant is restated, not weakened

Old: *exactly one `count` per criteria version*. New: **at most two `count` requests before
consent — one per candidate population — and none after the choice.** The fetch reuses the count
already taken. `tests/test_ui_flow.py` keeps counting requests: one for every query without an
ambiguous expansion, two with, never three, and zero between the choice and the fetch.

### Sub-decision 5 — non-interactive behaviour is data, never a default that changes silently

`pkd_2007` is a `Criteria` field, so a query file written by `offer_yaml` records the operator's
actual choice and reruns it identically. `--tak` with the field unset keeps **today's** behaviour
(narrow) — an existing scheduled job must not change population because a new version shipped — and
emits one warning line naming the flag and the codes that were not covered. A new CLI flag
(`--pkd-2007 / --bez-pkd-2007`) sets it explicitly; `cli.py` authors no sentence, as always.

Two limits of this, found while implementing and left as they are. First, a **narrow** choice is
recorded by the field's absence (`offer_yaml` uses `exclude_defaults=True`), so an interactive
rerun from that file asks again and spends two counts; the scheduled path is unaffected, because
`--tak` narrows anyway, which is where "reruns it identically" has to hold. Second, a query file
that already carries `pkd_2007` **plus** an explicit `--bez-pkd-2007` is a contradiction, and the
tool refuses rather than silently letting one win — but `--tak` alone never conflicts, because a
saved choice is the operator's decision and beats the non-interactive default.

### Sub-decision 6 — the map is generated, dated and expiring

New `scripts/build_pkd_transition.py`, in the shape of `scripts/build_pkd.py`: reads the converted
`PKD/KluczePKD_2007_2025.xlsx` (sheet `2007-2025`, rows at `Poziom` = 5), writes
`ceidg_tool/data/pkd2007_2025.yaml` — carrying, per 2025 code, its PKD 2007 predecessors **with
their names** (column `Nazwa grupowania PKD 2007`, verified complete for all 159 needed codes) —
with the source filename, its SHA-256, the build date, the legal basis and `wygasa: 2026-12-31` in
the header. **Ambiguity is computed from the key**, never asserted by hand — so
the clean/ambiguous split is a property of the data, verifiable by rebuilding, not a number in a
docstring. It has two sources, both derived: the fan-out of a 2007 code across several 2025 codes,
and the `zywe_2025` list — predecessors that are themselves live 2025 codes, so adding one drags in
its present-day meaning. A new pure module `ceidg_tool/pkdmap.py` loads it
(mirroring `assistant/pkd.load_pkd`) and answers two questions: which 2007 codes a 2025 code needs,
and which other 2025 codes each of them also reaches.

**Precondition, checked 2026-09-07 and satisfied**: the confirmation screen's control is the
*name*, so every PKD 2007 code shown must carry one from the official source. The key does — column
`Nazwa grupowania PKD 2007`, 656 subclasses, none empty or truncated, covering all 159 codes the
expansion needs. So the generator reads names and codes from the same file in one pass, and no
second GUS download is required. The rule behind the check stands regardless: a code shown without
a name, or with a name from anywhere else, breaks the one control the operator has. **Never let a
model supply those names.**

## Consequences

### Files

| File | Change |
|---|---|
| `scripts/build_pkd_transition.py` | new — generator, provenance header, ambiguity computed |
| `ceidg_tool/data/pkd2007_2025.yaml` | new — generated, expiring |
| `ceidg_tool/pkdmap.py` | new — pure; joins boundary rule 6's pure-module list |
| `ceidg_tool/criteria.py` | `pkd_2007` field, validator, `to_params`, `describe()` |
| `ceidg_tool/reports.py` | `matches_criteria` unions both tuples (one line) |
| `ceidg_tool/ui/flow.py` | candidate build, dual-fingerprint resume, vintage step |
| `ceidg_tool/ui/prompts.py` | one question, `safe_default=True` (narrow), id `rocznik_pkd` |
| `ceidg_tool/ui/texts.py` | vintage block with both counts and the sibling industries. **Two items listed here were not built**: the cost-table note is redundant, since the vintage block already precedes the cost table with both numbers; the `--pkd 6201Z` hint needs a public accessor on `TablicaPkd` that nothing else wants yet, and the flag path already reaches those records. What *was* fixed instead: `hints_block` had been teaching `62.01.Z` as **the** PKD example — a code absent from PKD 2025 that the assistant itself refuses |
| `ceidg_tool/cli.py` | `--pkd-2007 / --bez-pkd-2007` |
| `docs/design/phase2_core.md` | rule 6 gains `pkdmap.py` |
| `docs/status.md`, `docs/decisions.md`, `docs/test-runs-phase4.md` | gate-3 walk restored to hairdressing; open item closed with this ADR |

The `assistant/` package is **untouched**: no prompt change, no dictionary change, no schema change,
rule 13 unaffected, prompt caching unaffected. The model keeps choosing from PKD 2025 only.

### What this also fixes, for free

`--pkd 6201Z` reaches 234 605 records while the assistant refuses the same code. With the map
loaded, the flag path can say what it is looking at ("`6201Z` is PKD 2007; its PKD 2025 counterparts
are `6210A`/`6210B`") — the same mechanism in the other direction, closing the asymmetry
`docs/decisions.md` records.

On the report path the widened filter costs nothing and the split can be reported **exactly** from
the CSV, since every row carries `RokPKD`. That is the path gate 3's walk will most likely take.

### What we explicitly give up

- **The 209 ambiguous codes stay ambiguous.** A widened hairdresser query returns beauticians. The
  screen says so, by name; nothing removes it.
- **One more question in an interactive flow**, and it only appears where it must.
- **A second `count`**, bounded and only for ambiguous expansions.

### Tests

Boundary rule 6 over `pkdmap.py`; a data test in the shape of `tests/test_assistant_pkd_data.py`
(every key canonical, every 2025 key present in `pkd2025.yaml`, mapping count pinned with its
source, clean/ambiguous split derived from the file); `Criteria` round-trip with `pkd_2007` through
YAML and fingerprint; `reports.matches_criteria` matching a 2007-coded record; `flow` request counts
(1 / 2 / never 3, and none after the choice); resume found from either candidate; `--tak` with the
field unset keeps today's parameters and warns.

## Gate items — before any code

1. ~~**Measure repeated `pkd=`.**~~ — **done 2026-09-07, and it passed.** `9621Z` = 38 201,
   `9602Z` = 187 149, both together = **225 350** — the exact sum, so repeated `pkd=` is **OR-ed**
   and the two vintages are disjoint from the API side as well. Options C/D/E stand, and so does
   today's multi-code assistant query, which had been running on this assumption untested. The
   measurement also puts a national figure on the gap: a PKD 2025 hairdressing query reaches
   **17 %** of hairdressers. Recorded in `docs/decisions.md`; probe in
   `scripts/ceidg_probe_pkd_or.py`.
2. ~~**Establish where PKD 2007 names come from.**~~ — **closed 2026-09-07: the key already has
   them.** `KluczePKD_2007_2025.xlsx` carries a `Nazwa grupowania PKD 2007` column; at subclass level
   it yields **656 codes with names, none empty, none truncated, no code carrying two different
   names**, and all **159** codes actually needed for expansion are among them. No second GUS
   download, and the names on the confirmation screen come from the same official source as
   everything else.
3. ~~Owner approval of this ADR.~~ — **given 2026-09-07.** All three gate items are closed;
   implementation may start.

### ~~If the OR assumption fails~~ — measured, it does not

Kept for the record, because the branch was real when the ADR was written: had `pkd=` been AND-ed,
options C/D/E would have collapsed onto one query per code (`batching.py` gaining a second
granularity beside dates — n times the requests and n times the pages) or the report path alone.
The 2026-09-07 probe closed it: repeated `pkd=` is OR-ed, exactly.

## Risks

| Risk | Response |
|---|---|
| ~~Repeated `pkd=` is not OR~~ | **Closed 2026-09-07** — measured OR, exact sum (38 201 + 187 149 = 225 350) |
| ~~The key lacks PKD 2007 names~~ | **Closed 2026-09-07** — it has them: 656 subclasses, none empty or truncated, all 159 needed codes covered |
| The model returns 15 codes (run A7), widening to ~37 | URL stays ~400 chars against 4 000; a larger `count` meets the existing 50 000 threshold and the split path, which is the designed behaviour |
| A widened run and a narrow run are confused on resume | Different fingerprints by construction; resume is looked up for both candidates |
| The map outlives its usefulness | Header carries `wygasa: 2026-12-31`; removal is one data file, one module, one flow step, one field |
| Someone regenerates the map from memory or a web summary | Same rule and same reason as `pkd2025.yaml`; the generator prints the counts and the SHA-256 lands in the header |

## Revisit when

The three-request probe answers gate item 1 (immediately — the design branches on it); after
31.12.2026, re-measure the `RokPKD` shares from a fresh daily report (offline, zero requests) and
delete `pkdmap.py`, its data file and the flow step if the 2007 share is negligible; or earlier, if
the register's share of 2007 codes drops far enough that the widened and narrow counts stop
differing materially — at which point the question stops earning its screen.
