# Phase-1 decisions: what the API probe established

Date: 2026-09-05
Status: accepted (gate 1) 2026-09-05 by the project owner; the header still said "proposed" until
        2026-09-06, when the owner confirmed the acceptance had been given
Author: P0w3r223
Related to: INSTRUKCJA_CLAUDE_CODE.md (phase 1), docs/api_notes.md, docs/adr/0001, probe_out/findings.md (git-ignored)

---

All production requests on 2026-09-05 used the project owner's own token, confirmed by the
owner the same day. Rotation is revisited from 2026-09-30, see `docs/status.md`.

Probe: `scripts/ceidg_probe.py --env prod --skip-raport`, 26 requests in 152 s,
run on **production** with the owner's explicit consent because the test host
`test-dane.biznes.gov.pl` (158.66.4.154:443) times out at TCP level from this network
while `dane.biznes.gov.pl` answers instantly. Raw samples stay in `probe_out/`
(git-ignored, personal data); `tests/fixtures/` holds anonymised copies produced by
`scripts/anonymize_samples.py`.

## Answers to the phase-1 questions

| Question | Answer | Evidence | Profile field |
|---|---|---|---|
| Where does `page` start? | **0**. `page=0`, `1`, `2` return different records; `links.next` of page 0 points to `page=1`; `links.last` for `count=6 316 121`, `limit=5` is `page=1263224` = `ceil(count/limit) - 1`. | samples 02-04 | `page_start: 0`, `paging_mode: links` |
| Max effective `limit` for `/firmy`? | **25**. `limit=50`, `100`, `500` return 400 `{"code": "NIEPOPRAWNY_ROZMIAR_STRONY", "message": "Rozmiar strony powinien być z zakresu 1-25"}`. Rejected requests still consume quota. | samples 05-08 | `max_limit_firmy: 25`, `default_limit: 25` |
| No results: 204 or empty list? | **204 with an empty body** (no `Content-Length`, no JSON). Never observed `200` with `firmy: []`; the parser accepts both anyway. | samples 11, 15 | `empty_result_statuses: [204]` |
| Is `count` total hits or page size? | **Total hits**: `6 316 121` at `limit=1`. | sample 01 | `count_semantics: total` |
| Does `/firma?ids=…` batch? | **Yes**: 5 ids in one request return 5 firms under `firma[]`. Upper bound not tested (see open points). Path form `/firma/{id}` returns the same list shape. | samples 23, 24 | `ids_batch_size: 5` (verified), `detail_mode: query` |
| `nazwa` semantics | **Substring, case-insensitive**: `adam` and `ADAM` both give `count=82 954`. Polish diacritics not tested. | samples 09, 10 | none (semantic, not dialect) |
| `pkd` format | **Compact only, case-insensitive**: `6201Z` and `6201z` give `234 784`; `62.01.Z` gives 204 (treated as no match, not 400). | samples 14-16 | `pkd_format: compact` |
| `wojewodztwo` / `status` casing | `wojewodztwo` is **case-insensitive** (`podlaskie` = `PODLASKIE` = `137 693`). `status` accepted in upper case as documented; a list via repeated `status=` works; omitting `status` makes the server append all five statuses to `links`. | samples 12, 13, 17, 18 | `wojewodztwo_case: upper` (either works), `list_param_suffix: ""` |
| **Casing of *returned* identifiers** | **Two spellings, one entry.** `/firmy` and `/firma` return the record `id` in **UPPER** case, `/zmiana` returns the same identifiers in **lower** case, and `ids=` matches **case-insensitively** — a query written in the `/zmiana` spelling comes back with uppercase-id records. `/raporty` ids share the 8-4-4-4-12 shape but are **not hex** and **are** case-significant (they go into the download URL). | samples 02, 24, 25, 26; confirmed 2026-09-08 against the operator's production store (orphan rows' `detail_utc` falls inside the request window of a run that asked in lower case) | `recordid.kanoniczny_id` (ADR-0013), boundary rule 14 |
| `/raporty` | 816 reports in one unpaginated response. Keys: `id`, `nazwa`, `format`, `raport` (download URL), **`data-utworzenia`** (hyphenated). Two kinds, "Zarejestrowane działalności" and "Złożone wnioski", per voivodeship (16 + "brak województwa"), in `.csv` and `.xml`, generated daily around 06:00-06:45, retained for about 6 days (2026-08-30 to 2026-09-04). Content **not downloaded** (`--skip-raport`), so columns, separator and encoding are unknown. | sample 25 | `reports_root_key: raporty` |
| `/firma` fields and fill rates | Present in the 5-record production sample: `adresDzialalnosci`, `adresKorespondencyjny`, `dataRozpoczecia`, `email`, `id`, `link`, `nazwa`, `numerStatusu`, `obywatelstwa`, `pkd`, `pkdGlowny`, `rokPkd`, `status`, `telefon`, `wlasciciel`, `wspolnoscMajatkowa`. Absent fields are **omitted, not null** (`www`, `spolki`, `dataZawieszenia`, `zakazy`, … did not appear). PKD items use `kod`/`nazwa` (not `symbol`). Fill: `email` 1/5, `telefon` 1/5, `www` 0/5. Some list records (old entries) have `adresDzialalnosci: {}`. | samples 23, 24 | `detail_root_key: firma` |

## PKD vintage — measured 2026-09-07, and it overturned an assumption

**Every `rokPkd` the register returned is `"2025"`.** Eleven occurrences across three files —
`tests/fixtures/firma_by_ids.json` (5, anonymised from the probe) and two raw production samples,
`probe_out/samples/23_…` (1) and `24_200_firma.json` (5). **Zero** occurrences of `2007`.

This was found while sourcing the assistant's PKD dictionary (ADR-0011). The dictionary had been
specified as **PKD 2007**, and the only evidence for that was `tests/conftest.py`, a hand-written
test double carrying `"rokPkd": "2007"`. ADR-0011's own finding F7 cited that line as though it
were a measurement of the API. It was a fixture we wrote ourselves — the same shape as the
2026-09-06 finding that a test "passed for the wrong reason", except here the fabricated value
drove a design decision rather than an assertion.

The vintage matters because the two classifications differ in content, not just in numbering.
`4933Z` — "Transport pasażerski na żądanie pojazdem z kierowcą", present in the production sample —
does not exist in PKD 2007. A 2007 dictionary would therefore have rejected codes the register
actually uses, and the operator would have read that rejection as a model error.

The synthetic fixture now says `2025`, so the test double stops contradicting the measured API.

## Which vintage does the `pkd` parameter index? — measured 2026-09-07 (group C, 3 requests)

The section above measured the vintage the register *returns* in records. It left the other half
open, and said so: the phase-1 probe had measured only the **format** of the `pkd` parameter
(compact, `62.01.Z` gives 204 rather than 400), never its **vintage**. That gap was the reason
group C had to precede gate 3 — a dictionary built from the wrong classification would have made a
PKD-filtered walk meaningless.

`scripts/ceidg_probe_pkd_vintage.py --env prod`, three `limit=1` requests (counts only; no personal
data fetched or written), run with the owner's consent given in session.

| `pkd=` | Exists in | HTTP | `count` |
|---|---|---|---|
| `6201Z` | PKD 2007 only — software, before the change | 200 | **234 605** |
| `6210B` | PKD 2025 only — other software activity | 200 | **142 294** |
| `4933Z` | PKD 2025 only — positive control, seen in real records | 200 | **31 402** |

The positive control returned 31 402, so the other two numbers may be read.

**The result landed in the runbook's third branch, not its stated pass.** The pass criterion was
`6210B` non-zero *and* `6201Z` zero. Instead both vintages return hits. The criterion had been
written on an unstated either/or assumption — that the parameter indexes one vintage — and the
register does not work that way.

**What the three counts settle:** codes from the shipped PKD 2025 dictionary do reach records.
`6210B` and `4933Z` exist only in PKD 2025 and both return large sets, so the filter is not
indexing PKD 2007 exclusively. That was the failure group C was ordered before gate 3 to catch, and
it did not happen.

**What they do not settle was settled the same day, offline, and it reversed the conclusion** — see
the next section. The reading first written here, that the API "carries a translation layer", was an
inference stated with more confidence than three counts can carry, and it was wrong.

## The register is mid-transition: a 2025-only dictionary misses 8.6 % of the records

Measured 2026-09-07 on `probe_out/raport_sample.zip`, the 21 MB production report already on disk —
**285 026 real records, zero requests.** The report CSV carries `GlownyKodPkd`, `PozostaleKodyPkd`
and, decisively, **`RokPKD` per record**. That column is what separated the two explanations the
counts above could not.

| Measurement | Value |
|---|---|
| Records with a main PKD code | 285 026 |
| `RokPKD` = **2007** | 167 139 (**58.6 %**) |
| `RokPKD` = 2025 | 117 887 (41.4 %) |
| 2007-coded records whose main code *also* exists in PKD 2025 (unchanged code strings) | 95 322 (57.0 %) |
| Records whose **main** code is not in `pkd2025.yaml` | 71 817 = 25.2 % |
| **Records unreachable by *any* code in `pkd2025.yaml`** | **24 494 = 8.6 %** |

> **Corrected 2026-09-08.** This table previously carried only the first row and labelled it
> "unreachable by any code", which is the second row's question. `pkd=` matches any of a record's
> codes — measured at zero requests from the operator's store (`pkd=6201Z`: 13 records, 9 with the
> code only in the secondary list, deepest at position 30; `9621Z`+`9602Z`: 357 records, 62 such,
> deepest at position 50). The operative gap is **8.6 %**. Note also that 285 026 counts rows with a
> non-empty `GlownyKodPkd`; the archive has 287 256 rows, the remaining 2 230 having no main code at
> all and all carrying `RokPKD=2007`. The archive covers **one voivodeship (wielkopolskie)**, so
> "of the register" overstates its scope.

**Each record carries exactly one vintage, and the filter matches the stored string.** The two
result sets are therefore disjoint, not overlapping: `pkd=6201Z` selects records that have not
migrated, `pkd=6210B` selects records that have. The legal basis says as much — the transition from
PKD 2007 runs to **31.12.2026**, still four months away — and `normalizer.py` has described `rokPkd`
as "2007 lub 2025" all along.

The arithmetic proposed in the section above is what falsified the translation reading. Under
translation, `6210A + 6210B` should equal `6201Z` = 234 605, so `6210A` should be near 92 000. In the
report `6210A` appears 1 141 times against `6210B`'s 7 185 — a ratio near 1:6.3, which scales the
API's 142 294 to roughly 22 600, nowhere near 92 000. Independently, the report's `6201Z`:`6210B`
ratio (10 669:7 185 = 1.48) tracks the API's (234 605:142 294 = 1.65), which is what you expect if
the API simply matches the code as stored. **The one production request is not needed** — the
answer was already on disk.

**The consequence is a coverage gap, and it is not the assistant's alone.** It applies to every
PKD-filtered query — the wizard's eight questions and the `--pkd` flag as much as the assistant,
because it is a property of the register, not of any input path. The most frequent unreachable
codes are ordinary trades, not exotica:

| PKD 2007 code, absent from PKD 2025 | What it is | Records |
|---|---|---|
| `9602Z` | hairdressing and beauty treatment (2025 splits it into `9621Z`/`9622Z`) | 6 811 |
| `4520Z` | maintenance and repair of motor vehicles | 5 912 |
| `4120Z` | construction of residential and non-residential buildings | 4 684 |
| `4339Z` | other building completion and finishing | 4 248 |
| `6201Z` | computer programming | 3 865 |

**This blocks gate 3 as written.** The runbook's walk sentence is *"salony fryzjerskie w Łomży"*, and
`9602Z` is the single most frequent unreachable code in the register. The assistant would answer with
`9621Z`, the register would return only the migrated salons, and the operator would read a confident,
incomplete result with no error anywhere in the path — the same failure shape as the 2007 dictionary
that was avoided in phase 4a, arriving from the opposite direction.

**The remedy is already downloaded and was closed on a false premise.**
`PKD/KluczePKD_2007_2025.xls` is the official GUS transition key. The open item about building a
mapping table from it was closed on 2026-09-07 with the reasoning "the model already translates
2007 → 2025, so the capability exists without it". That reasoning addressed the wrong direction: the
gap is that a **2025** code fails to reach **2007**-coded records, so what is needed is the reverse
map — expanding each chosen 2025 code with its 2007 equivalents until the transition ends. The item
is reopened. Note the cost mechanism, because it is easy to read backwards: every code goes into
the **same** URL as another `pkd=` parameter, so an added code costs no request of its own — it
widens `count`, hence pages, which `estimating.estimate` already prices. Expansion is therefore
cheaper than "one more filter, one more request" suggests, and needs no new cost machinery. What
makes it a decision rather than a chore is the ambiguity: 209 of the 264 expansions pull in sibling
industries. Designed in **ADR-0012** and built the same day (accepted 2026-09-07).

### The GUS transition key, measured (2026-09-07, zero requests)

`PKD/KluczePKD_2007_2025.xls` converted with LibreOffice (BIFF8, same as the structure file), sheet
`2007-2025`, rows at `Poziom` = 5 (subclass to subclass): **1 084 mappings covering 727 of the 728
codes in `pkd2025.yaml`.**

| Measurement | Value |
|---|---|
| PKD 2025 codes needing at least one extra PKD 2007 filter | **357 of 728 (49 %)** |
| Extra filters per such code | 1.71 on average; worst case 37 |
| Expansions that are **clean** (the predecessor brings nothing else) | **51** |
| Expansions that are **ambiguous** | **306** |
| Distinct PKD 2007 predecessors | 283, of which **124 still exist as PKD 2025 codes with a different meaning** |

The first four numbers read 264 / 1.45 / 55 / 209 when first measured, because the generator was
dropping every predecessor that is itself a live PKD 2025 code. The stated reason — "the 2025 filter
already covers it" — was false: the filter covers the **record**, not the **industry the operator
asked for**. `8551Z` is "Pozaszkolne formy edukacji sportowej" in PKD 2007 and "Pozostałe formy
edukacji sportowej" in PKD 2025, two different trades sharing a string. Dropping those mappings left
**93 PKD 2025 codes with no predecessor at all** — fitness clubs (`9313Z`), bakeries (`1071Z`),
vegetable growing (`0113Z`) among them, i.e. exactly the ordinary trades this work exists for.
Caught by the code review of the implementation, 2026-09-07.

**The ambiguity is the design problem, and it has no free answer.** It comes in two shapes, and
both are visible on the confirmation screen with their own sentence:

1. **A 2007 subclass that PKD 2025 split** cannot be un-split by a query. `9602Z` "Fryzjerstwo
   i pozostałe zabiegi kosmetyczne" leads to both `9621Z` (hairdressing) and `9622Z` (beauty), so
   adding it to a hairdresser query reaches every un-migrated salon **and** every un-migrated
   beautician. The worst cases are far wider: `4791Z` leads to 36 different 2025 codes, `4799Z` to
   35, `8299Z` to 26.
2. **A 2007 code that still exists in PKD 2025 meaning something else.** `8551Z` was "Pozaszkolne
   formy edukacji sportowej"; today it is "Pozostałe formy edukacji sportowej", while fitness clubs
   moved to `9313Z`. Adding `8551Z` to a fitness query therefore also collects businesses that carry
   it as a *current* code. 124 predecessors are in this position.

Weighted by real records from the report (main code only), for the industries the gate-3 walk would
touch:

| PKD 2025 code | Reachable today | Added by its 2007 predecessor | Ratio |
|---|---|---|---|
| `9621Z` hairdressing | 2 266 | 6 811 (`9602Z`) | **3.0x** |
| `9622Z` beauty | 4 016 | 6 811 (`9602Z`) | 1.7x |
| `9531A` vehicle repair | 2 330 | 5 912 (`4520Z`) | 2.5x |
| `4399Z` other specialised construction | 3 397 | 4 684 (`4120Z`) | 1.4x |
| `9130Z` conservation and restoration | 34 | 4 951 (`4120Z`, `9003Z`) | 145x |

So a gate-3 walk asking for hairdressers in Łomża would find roughly **one in four**. Note these
per-code figures must not be summed into a register-wide total — `9602Z` appears under two 2025
codes and `4520Z` under three, so a naive sum double-counts. The de-duplicated register-wide number
is the one in the table above: **8.6 % unreachable by any code the record carries**. The 25.2 %
in the row above it answers a different question — records whose *main* code is absent — and it
is the figure this file, `CLAUDE.md` and two ADRs quoted for the wrong sentence until 2026-09-09.

Both directions are therefore wrong by default: not expanding loses most of an industry, expanding
blindly merges industries the operator asked to distinguish — for 209 of the 264 codes.

**Consequence for `KOD_PKD_Z_INNEGO_ROCZNIKA`.** The limitation was added because the model silently
translates a 2007 code the operator typed into a 2025 one. The assumed harm was that the operator's
code would have matched nothing; it would in fact have matched 234 605 records, and the substitute
matched a **disjoint** set of 142 294. So the substitution does not narrow the query — it *replaces*
the population. The operator-facing sentence is true about the classification but incomplete about
the consequence, and the cost table cannot close the gap: it prices what will be fetched, with
nothing to compare against, and the interpretation is confirmed before the `count` request runs
(`ui/flow.py`). Disclosure, not control.

**Asymmetry worth writing down:** `--pkd 6201Z` on the flag path goes through `criteria.normalize_pkd`,
which checks shape only, so the flag reaches those 234 605 records today. The assistant path refuses
the same code, because `assistant/pkd.py` validates against the 2025 dictionary. Two entry points,
the same input, different outcomes.

## Repeated `pkd=` is OR — measured 2026-09-07 (ADR-0012 gate item 1, 3 requests)

Nobody had ever measured this. Repeated `nip=` was measured OR on 2026-09-06 and repeated `status=`
works, and `api_notes.md` lists `pkd[]` as a list parameter — but `pkd` itself was never tested,
while the tool has been sending multi-code PKD queries all along (the assistant returned four codes
in run A1 and fifteen in A7). No group-A run reached a fetch, so the behaviour had never touched the
API. Under AND those queries would have returned nothing and the operator would have read
*"brak firm"*.

`scripts/ceidg_probe_pkd_or.py --env prod`, three `limit=1` requests, counts only, with the owner's
consent given in session. The two codes are deliberately from disjoint vintages, which makes the
prediction exact rather than approximate.

| Request | `count` |
|---|---|
| `pkd=9621Z` (PKD 2025, hairdressing) | 38 201 |
| `pkd=9602Z` (PKD 2007, hairdressing and beauty) | 187 149 |
| `pkd=9621Z&pkd=9602Z` | **225 350** |

**38 201 + 187 149 = 225 350, to the unit.** Two conclusions, and the second was free:

1. **Repeated `pkd=` is OR-ed.** ADR-0012's options C/D/E rest on this and it holds. So do today's
   multi-code assistant queries, which had been running on an untested assumption.
2. **The exact sum proves the vintages are disjoint from the API side.** Not one record matched
   both codes; had any record carried both a 2007 and a 2025 code, the union would have been smaller
   than the sum. This corroborates the per-record `RokPKD` finding independently of the report.

**And it puts a national number on the gap.** Hairdressing under PKD 2025 reaches 38 201 of 225 350
records — **17 %**. A regional snapshot had suggested 25 %; nationally it is worse. An operator
asking the assistant for hairdressers today gets roughly one in six, with no error anywhere in the
path. That is the strongest single argument in the ADR-0012 file.

## The PKD 2025 dictionary as built (2026-09-07)

Built by `scripts/build_pkd.py` from `StrukturaPKD2025.xls`, downloaded from GUS by the owner on
2026-09-07 (sheet "PKD 2025", column "Podklasa"). The file is genuine BIFF8, which `openpyxl` cannot
read; LibreOffice converted it to XLSX losslessly — deliberately not Excel, whose import heuristics
turn `01.11.Z` into a date. The source SHA-256 is in the generated header, so the build is
reproducible without keeping the spreadsheet in the repository.

| Fact | Value |
|---|---|
| Subclasses | **728** |
| Divisions | 87 |
| Range | `0111Z` … `9900Z` |
| Truncated names, duplicate names | none |
| Longest name | 198 characters (full wording preserved) |
| Legal basis | Council of Ministers regulation of 18.12.2024, **Dz.U. 2024 poz. 1936**, in force 1.01.2025; transition from PKD 2007 runs to 31.12.2026 (ISAP) |
| Rendered prompt block | 46 886 characters ≈ 13 400 tokens — under the estimate in ADR-0011 |

**Cross-check against the register**: all nine code+name pairs the API actually returned resolve, and
their names match the dictionary **character for character** — including `4933Z` ("Transport
pasażerski na żądanie pojazdem z kierowcą"), which exists only in the 2025 vintage.

**How different the vintages are, concretely.** `6201Z` — "Działalność związana z oprogramowaniem",
one of the most common codes for a software business under PKD 2007 — **does not exist in PKD 2025**:
programming moved from 62.01 to 62.10 (`6210A` games, `6210B` other). `3030Z` split into `3031Z`
(civil) and `3032Z` (military). Had the assistant shipped with a 2007 dictionary, "firmy
programistyczne" would have produced `6201Z`, the API would have answered 204, and the operator would
have read "brak firm" — a confidently wrong answer with no error anywhere.

~~**Still open, and cheap to settle**: what the `pkd` **query parameter** indexes.~~ — **answered
2026-09-07**, and not the way this paragraph expected. The filter matches the code **as stored on the
record**, and records carry one vintage each while the transition to PKD 2025 runs (to 31.12.2026).
So both vintages return hits and their result sets are disjoint. See "Which vintage does the `pkd`
parameter index?" and the section after it, which measures the coverage gap this opens: 8.6 % of the
sample carries no code at all from the shipped 2025 dictionary.

## Facts beyond the question list

- **Rate-limit headers exist**: `X-Rate-Limit-Limit: 1000`, `X-Rate-Limit-Remaining`,
  `X-Rate-Limit-Reset` (epoch milliseconds) on every response, 400 included. They
  describe only the 1000/60 min window; the 50/3 min window is not exposed. The client
  keeps its own two-window limiter (ADR-0003) and uses the headers as a cross-check and
  for the "remaining budget" message.
- **Diagnostic headers**: `X-Gravitee-Transaction-Id` and `X-Correlation-ID`. Log both;
  they are what the operator will ask for.
- **Error body shape**: `{"code": "<UPPER_SNAKE>", "message": "<Polish text>"}` on 400.
- **Dates are not validated by the server**: `dataod=01.01.2014` returned 200 with
  `count=3 615 975` (silently reinterpreted) instead of 400. `Criteria` must keep
  sending strict `YYYY-MM-DD`; a wrong format would produce a wrong dataset, not an error.
- `/zmiana` works on production: 3 days = `count=38 744` (about 13 k changes per day),
  `limit=100` accepted, pages from 0, `links.last` = `page=387`. `limit=500` from the
  documentation was not tested.
- Akamai in front of production sets an `ak_bmsc` cookie; the client ignores cookies.

## Consequences for the code and the estimates

- Page numbering is settled, but `links.next` stays the primary cursor (ADR-0002);
  `page_start: 0` is the fallback.
- Cost model at **3.75 s** per request with `limit=25`. That is the spacing the shipped limiter
  actually applies: `min_spacing_s` **is 3.75 s** in both shipped profiles, and both rate
  windows (48/180 s and 960/3600 s) work out to the same 3.75 s per request, so
  `estimating.effective_spacing` — which takes the largest of the three — agrees with all of them.
  (Corrected 2026-09-08: this paragraph previously said `min_spacing_s` was 3.6 s. It was raised to
  3.75 s on 2026-09-06, because 3.6 s let the busiest 180 s window hold 49 requests.)

| Scenario | Requests | Time |
|---|---|---|
| List of 1 000 firms | 40 | about 2.5 min |
| Details for 1 000 firms, batch of 5 (verified; 25 rejected) | 200 | about 12.5 min |
| Whole voivodeship list (137 693 firms) through `/firmy` | 5 508 | about 5.7 h, over the hourly budget: needs resume across sessions |
| Whole voivodeship through the daily report | 2 | about 10 s plus download (21 MB ZIP) |

- Reports **do** replace the API for regional queries (see the mini-probe section
  below): one daily snapshot per voivodeship with contacts and PKD codes.
- Data minimisation: raw production JSON in SQLite is a conscious trade-off (ADR-0004);
  default cache TTL 7 days, cleanup after 30 days, one database file per environment.

## Mini-probe results (7 requests on production, `scripts/ceidg_miniprobe.py`)

| Question | Answer | Profile field |
|---|---|---|
| Max `ids` per `/firma` | **Between 5 and 24.** 25 and 50 ids return 400 `NIEPOPRAWNA_ILOSC_IDENTYFIKATOROW` with a truncated message ("Maksymalna ilość identyfikatorów wpisów to "), so the exact cap is not stated. 5 stays as the verified value; 10 and 20 can be tried in a later run. | `ids_batch_size: 5` |
| `/zmiana?limit=500` | **Works**: 500 ids on one page (`count=12 197` for one day). | `max_limit_zmiana: 500` |
| `links.next` on the last page | **Present and equal to `self`** (`count=1`: `next == self == last`). Guard 3 of ADR-0002 (`next` equals the current URL) is the one that terminates paging; guard 2 (missing `next`) never fires on this API. | `paging_mode: links` |
| Report content | **A full daily snapshot of the voivodeship, not a delta.** "Zarejestrowane działalności - województwo wielkopolskie" (.csv, 2026-09-04): ZIP 21 MB, one file `Zarejestrowane działalności.csv`, 68 MB, UTF-8 with BOM, separator `;`, **287 256 rows, 24 columns**: `Lp.`, `Nip`, `Regon`, `NazwaPodmiotu`, `Nazwisko`, `Imie`, `Telefon`, `Email`, `AdresWWW`, `KodPocztowy`, `Powiat`, `Gmina`, `Miejscowosc`, `Ulica`, `NrBudynku`, `NrLokalu`, `GlownyKodPkd`, `PozostaleKodyPkd`, `RokPKD`, `StatusDzialalnosci`, `DataRozpoczeciaDzialalnosci`, `DataZakonczeniaDzialalnosci`, `DataZawieszeniaDzialalnosci`, `DataWznowieniaDzialalnosci`. | `reports_root_key` |

### Consequence: reports replace the API for regional queries

This overturns the earlier assumption. For any query of the shape "voivodeship +
start-date range (+ status, PKD, town)" the report path costs **one request** (plus one
for the report list) and yields all firms of the region with contacts and PKD codes,
versus about 5 500 list requests (5.7 h) for the same region through `/firmy`, and it
even includes phone/e-mail/www that the list endpoint lacks. Filtering by date, status,
PKD or town happens locally on the CSV. Limits of the report path:

- reports exist per voivodeship (16 + "brak województwa"); a query without a region,
  or by NIP/name only, still goes through the API;
- **the snapshot contains only existing entries**: a gate-2 run on 2026-09-05
  (podlaskie, start dates in 2014, 300 rows) returned `AKTYWNY`, `ZAWIESZONY` and
  `WYLACZNIE_W_FORMIE_SPOLKI` but no `WYKRESLONY`, while the same period through
  `/firmy` (Łomża, Q1 2014) had 10 of 40 records `WYKRESLONY`. Queries that need
  deregistered firms must use the API; `report_covers()` refuses the report path when
  `status` includes `WYKRESLONY`;
- the CSV lacks `adresKorespondencyjny`, `obywatelstwa`, `spolki`, PKD names and the
  record `id`; the `link_ceidg` column has to be built from NIP;
- the snapshot is one day old (generated around 06:00-06:45) and retained about 6 days;
- the ZIP must be streamed to disk and the CSV parsed row by row (68 MB per region).

Phase 3 therefore offers the report path first for regional queries, exactly as the
instruction anticipated. "Złożone wnioski" reports were not inspected (likely daily
application deltas).

## Targeted probe results (3 requests on production, 2026-09-06, with the owner's consent)

`scripts/ceidg_probe_nip_ids.py`. Both inputs (record ids and NIPs) came from the samples the
earlier probes had already saved, so no request was spent getting them.

| Question | Answer | Consequence |
|---|---|---|
| Are repeated `nip=` parameters OR-ed? | **Yes.** Two different NIPs in one request returned `count=2` and both records. | Up to 25 NIPs per request. This unblocks ADR-0008 decision 5 option B: `link_ceidg` for report-sourced rows can be filled by batched NIP lookups at `ceil(n/25)` requests, about 2.5 min per 1 000 records. Also makes multi-NIP checks cheap. **Specified, not yet implemented.** |
| Does `/firma` accept 10 or 20 `ids`? | **No.** Both return 400 `NIEPOPRAWNA_ILOSC_IDENTYFIKATOROW`, the same code as 25 and 50. | The cap is between 5 and 9, so `ids_batch_size: 5` stays. The hoped-for 4x saving on detail fetches does not exist. Testing 6-9 would cost four more requests for a saving of at most 1.8x — not worth it unless detail fetching becomes the bottleneck. |

## Still open

- Exact `ids` cap (now narrowed to 6-9, was 6-24); `nazwa` with Polish diacritics; contact fill
  rates on a larger sample (the report CSV can answer this offline for a whole region).
- Layout of the `.xml` variant and of "Złożone wnioski" reports.

## What statuses the daily report actually contains

**Measured 2026-09-09 on `probe_out/raport_sample.zip`, zero requests** — 287 256 rows, one
voivodeship (wielkopolskie), one daily snapshot.

| `StatusDzialalnosci` | Rows | Share |
|---|---|---|
| `Aktywny` | 219 798 | 76.52 % |
| `Zawieszony` | 58 370 | 20.32 % |
| `Działalność prowadzona wyłącznie w formie spółki cywilnej` | 9 088 | 3.16 % |

**Three values, and that is the whole list.** Neither `WYKRESLONY` nor any spelling of "oczekuje na
rozpoczęcie działalności" appears. `report_covers` refused the report path only for the first of
those, so a query for entries awaiting their start date took the report path and came back **empty,
with no error** — and since the report path costs four orders of magnitude less, `--zrodlo auto`
chose it. Fixed as audit item A10.

Two things worth keeping separate. The measurement covers one voivodeship on one day, so "absent
from this snapshot" is not "impossible" — it is, however, exactly the question `report_covers` asks.
And `STATUS_TEXT_TO_API` maps seven texts of which this archive emits **one**; the extra entries
cost nothing and may be right, but they are not evidence that the report contains those statuses.
This is the distinction the project got wrong once already, when a hand-written fixture was cited
as a measurement of the API.

Rows without an identifier, measured in the same pass: **315** carry neither `Nip` nor `Regon`
(0.11 %). Their name, surname, given name and start date are filled in **100 %** of those rows and
distinguish all 315 with **zero collisions**; adding the full address changes nothing, while its own
fields are filled 23-69 %. That is the evidence behind ADR-0016.

## Which filters match a fragment, and which match exactly

The report path reproduces `/firmy`'s filters locally (`reports.matches_criteria`), so a
difference in matching semantics makes the two paths return different sets while a comment
promises they return the same one. Audit item F12. Measured 2026-09-09.

| Field | Semantics | How it was settled |
|---|---|---|
| `nazwa` | **fragment**, case-insensitive | phase 1 probe (`adam` = `ADAM` = 82 954) |
| `miasto` | **fragment**, case-insensitive | **zero requests**, from the operator's store |
| `kod` | question does not arise | `Criteria` validates it to `15-333`, so a fragment can never be sent, and at a fixed length "contains" and "equals" coincide |
| `powiat`, `gmina`, `ulica` | **unmeasured** | one production request, inconclusive (below) |
| `imie`, `nazwisko` | **unmeasured** | one production request, inconclusive (below) |

**`miasto`, settled at zero cost.** Run `eb1df3a8` in the operator's own store was fetched
with `miasto=['Łomża']` and came back with four records whose city is `Stara Łomża przy
Szosie` or `Stara Łomża nad Rzeką` — names that *contain* "Łomża" and do not equal it, with
the match in the middle rather than at the start. Checked further: none of those four carries
"Łomża" in its correspondence address or in any other address on the record, so the match
cannot have travelled another route. The same run returned `ŁOMŻA` in capitals, confirming
case-insensitivity. `matches_criteria` compared the city exactly, so the report path was
**dropping** those records.

**The other five, two production requests, inconclusive — and instructive anyway.**
`scripts/ceidg_probe_match_semantics.py` sent middle-slice fragments of one real record's
values, in two groups (`powiat`+`gmina`+`ulica`, then `imie`+`nazwisko`), each ANDed with
`wojewodztwo`. **Both returned HTTP 204**, the measured signal for an empty result. Since
parameters are ANDed, that means at least one field in each group does *not* match by
fragment — but not which one, and two requests cannot say more.

Two things not to get wrong about that result. The `wojewodztwo` value was not a confound:
`to_params` upper-cases it regardless of input, so the probe sent exactly what the successful
runs sent. And the `ulica` fragment carries a caveat worth naming — the stored value includes
the `ul.` prefix, so a server indexing the bare street name would reject the fragment for a
reason unrelated to exact matching.

**The conclusion that survives is the one that goes against intuition: the text-field family
is not uniform.** `nazwa` and `miasto` match by fragment; at least two of the other five do
not. So the semantics of one field say nothing about its neighbours — which is precisely the
assumption that kept `miasto` on exact comparison while the register was matching fragments.

Settling the remaining five costs **five requests**, one per field: a middle-slice fragment of
a value known to exist, ANDed with `miasto` (now known to be a fragment filter) to bound the
population. Until then those five stay on exact comparison, and `matches_criteria`'s docstring
says which of its choices are measurements and which are defaults.

## Repeated `miasto=` is OR — measured 2026-09-09 (1 request)

The assistant answers *"…we Wrocławiu oraz Gdańsku"* with two cities, and `Criteria.to_params`
ships them as two `miasto=` parameters in one request. Repeated `pkd=`, `nip=` and `status=`
had each been measured OR-ed; `miasto=` never had. Under AND the query would return **empty
with no error**, because no entry has two business addresses in two cities — the silent-subset
failure, on the path the tool exists to make easy.

    GET /firmy?miasto=Gdańsk&miasto=Wrocław&limit=1  →  HTTP 200, count = 242 415

**One request settles it, and only because the answer is asymmetric.** Under AND the set is
empty by construction, so any `count > 0` rules AND out. A zero would have ruled out only OR
and needed a second measurement to say whether the cause was AND or a rejected duplicate
parameter.

The voivodeship was deliberately left out of the query: added, it would AND against both
cities (Wrocław is dolnośląskie, Gdańsk pomorskie) and return nothing regardless of the
semantics — the probe would have measured its own mistake.

Note what this measurement is **not** evidence for. It says nothing about the other repeated
parameters, and nothing about matching semantics: 2026-09-09 also established that the
text-field family is not uniform (F12 above), so `pkd` being OR-ed never implied `miasto`
would be. It had to be measured, and now it is.

## What columns the daily report actually has — measured 2026-09-10 (0 requests)

Read from the header of `probe_out/raport_sample.zip` (wielkopolskie, one daily snapshot), so it
cost nothing. Twenty-four columns, in this order:

    Lp., Nip, Regon, NazwaPodmiotu, Nazwisko, Imie, Telefon, Email, AdresWWW, KodPocztowy,
    Powiat, Gmina, Miejscowosc, Ulica, NrBudynku, NrLokalu, GlownyKodPkd, PozostaleKodyPkd,
    RokPKD, StatusDzialalnosci, DataRozpoczeciaDzialalnosci, DataZakonczeniaDzialalnosci,
    DataZawieszeniaDzialalnosci, DataWznowieniaDzialalnosci

Two consequences, both acted on the same day when `Criteria` gained the four parameters that
brought it level with the public search form (`docs/research/public-search-parity.md`).

**`NrBudynku` and `NrLokalu` are there**, so the report path can filter on the new `budynek` and
`lokal` criteria locally, at no extra request. `row_to_record` had been mapping both columns since
phase 3 — only the filter was missing.

**Nothing about a civil partnership is there.** No `NipSC`, no `RegonSC`, no column from which one
could be derived. A filter whose column does not exist does not narrow a result set, it empties it:
every comparison against a missing value is false. Since the report path is four orders of magnitude
cheaper, `--zrodlo auto` would have picked exactly that silent emptiness — the A10 shape, entered
through a filter instead of through a status. `report_covers` therefore declines when criteria carry
`nip_sc` or `regon_sc`, and `pipeline.run_report_fetch` refuses independently, because `--zrodlo
raport` and resume reach it without passing the wizard's gate.

Also worth noting for anyone comparing the two sources: the archive carries **no voivodeship
column** — it is taken from the report's name — and the four dates it does carry
(`DataZakonczenia`, `DataZawieszenia`, `DataWznowienia` beside the start date) are *content*, not
filters. `/firmy` has no parameter for any of them, so the register cannot be asked "who suspended
in 2025"; the report can answer it only after the whole archive is downloaded and filtered locally.

## `imie` and `nazwisko` match exactly — measured 2026-09-10 (4 requests, production)

Two of the five fields ADR-0018 left `unmeasured` are now settled, and they came for free: the
queries were being chosen for a demo on the owner's own industry, so the pair test cost two extra
requests on top of a selection that was happening anyway. Consent for the production calls was
given in session.

The shape is a pair differing **only** in truncation, with everything else identical:

| Criteria | `count` |
|---|---|
| `nazwisko=Nowak` + `pkd=6210B` + `miasto=Poznań` | **23** |
| `nazwisko=Nowa` + `pkd=6210B` + `miasto=Poznań` | **0** |
| `imie=Marek` + `nazwisko=Nowak` + `miasto=Poznań` | **27** |
| `imie=Mare` + `nazwisko=Nowak` + `miasto=Poznań` | **0** |

**Both fields match exactly.** The truncated value is a *prefix*, so zero rules out "contains" and
"starts with" in one measurement; the full value returning 23 and 27 under the same criteria is the
positive control that keeps the zero from being an artefact of an empty population.

Two consequences. `reports.matches_criteria` was already comparing both with `_equals_ci` — so the
report path was **right about these two by luck, and is now right on evidence**; the docstring says
so. And three fields remain unmeasured (`powiat`, `gmina`, `ulica`), which is the remainder of
ADR-0018's option B3 — three requests, not five.

Worth keeping straight: this says nothing about `ulica`, which carries the `ul.` prefix in the
stored value and is the field where a fragment would be most useful. The text-field family is not
uniform (2026-09-09), and that lesson survives this measurement intact.
