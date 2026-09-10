# ADR-0021: What standing the report path has — alternative, or foundation

Date: 2026-09-09
Status: **proposed** (diagnosis only; no code changed — this is the one open *product* question)
Author: P0w3r223
Related to: ADR-0008 (decision sequence), ADR-0010 (report link enrichment), ADR-0016 (report row
identity), ADR-0018 (matching semantics), docs/audit-2026-09-09.md (the open product question),
docs/audit-architecture-2026-09-09.md (F-P4, F-P5, F-A5, §5.4)

---

## Context

The previous audit left exactly one open product question: two paths to the same spreadsheet whose
costs differ by four orders of magnitude. The API path spends one request per 25 records at 3.75 s
spacing; the report path delivered **287 256 records in a single request**. It recommended that the
report path deserve the polish currently going to the API path, and nothing has been decided since.

External reconnaissance changes the framing, and it is worth stating precisely what it changes.

**The canonical shape in this class of system is three parts, not two.** Companies House,
Brønnøysundregistrene and GLEIF all run snapshot → change stream → point API. Companies House ships
the snapshot **carrying a `timepoint`**, so the stream attaches exactly where the snapshot ended.
Brønnøysund publishes a nightly full file plus an updates endpoint. GLEIF publishes three golden
copies a day plus 8-hour, 7-day and 31-day deltas.

**We already have all three endpoints** — `/raporty`, `/zmiana`, `/firma` — and `store.py` is
already built for it: one `firma` table with `zrodlo IN ('CEIDG_API','CEIDG_RAPORT')` (`store.py:76`)
and `run.kind IN ('firmy','raport','zmiana')` (`store.py:49`). The persistence layer is not the
obstacle. The decision layer is: `ui/flow.py:384` treats the report as an *alternative source*,
chosen per query, rather than as the base the other two maintain.

**What is genuinely missing is not the change path but the consistency marker.** CEIDG publishes no
`timepoint` equivalent, so a snapshot↔changes seam has to be closed with an overlapping time window
rather than a server-supplied cursor.

**And bulk is also the reconciliation channel, not only the bootstrap.** Companies House users report
roughly two million lost PSC timepoints across one day boundary, and events visible in REST and on
the website but absent from the stream. The "poorer and a day older" channel is simultaneously the
higher-completeness one — which is an argument for periodic full reloads that the current design has
no place for.

### What the register's own GUI already does, and what it does not

The warehouse's report application exports to **XML, PDF, Excel, Word, CSV, JSON**. Its parameters
are *Stan na dzień, Data od, Data do, Województwo, Powiat, Gmina, Miejscowość*. Across all 41 ready
reports, **PKD appears as a criterion in none**. The ready report files are per voivodeship, CSV or
XML, published around 05:00, and removed after about seven days.

That is the first evidence-based statement of this tool's advantage: **selectivity by industry and
multi-voivodeship cross-sections**, plus merging into one workbook — not volume, where the
register's own bulk files win outright. It also means the report path in this tool is doing
something the operator cannot get from the GUI: filtering an archive by PKD locally.

### What is wrong with the report path today

- **Zero matches is a dead end.** `ui/flow.py:397-399` returns before the widening loop at `:443`, so
  ADR-0017's menu is structurally unreachable. `pipeline.py:752-771` finalises the run as
  `zakonczony` with `set_run_count(run_id, 0)` and the export writes an empty workbook.
- **It is admitted by default while matching five unmeasured fields exactly** (ADR-0018).
- **It does not resume at all** — only `pobierz` does (audit item D8).
- **Its server-side lifetime is nowhere recorded.** "Retencja" in this project means *our* retention;
  nothing notes that report files vanish after roughly seven days.
- `aktualizuj` sits outside the `Criteria` contract entirely: `pipeline.run_update:917` takes
  `(since, until)` and fetches details for **every** identifier `/zmiana` reports, with no
  intersection with the population the store holds (`pipeline.py:967-989`). The cost table prices it
  honestly and calls the number "zmienionych wpisów" — true of the register, not of the operator's
  store.

## Options

### A. Keep the report path as an alternative; fix its defects

Close the zero-hit dead end, settle the matching semantics (ADR-0018), add resume, record the
seven-day lifetime. Nothing structural changes.

- Cost: small to medium, all of it already on the ranked list.
- Effect: the cheap path stops misleading; the architecture stays as designed.
- Against it: the machinery that dominates this codebase — limiter, batching, resume, cost tables —
  is machinery the report path barely needs, and the API path stays the default mental model for a
  question ("all hairdressers in Wielkopolska") the report answers in one request.

### B. Snapshot-first: the report becomes the base, `/zmiana` maintains it, `/firma` enriches a subset

`pobierz` loads from the archive where it covers the criteria; `aktualizuj` applies `/zmiana` over an
overlapping window (because there is no `timepoint`); `/firma` is spent only on the subset the
operator's query names or the delta reports. Periodic full reloads serve as reconciliation.

- Cost: large. It is a change to the decision layer and to `aktualizuj`'s scope, not to storage.
- Effect: matches the shape four registries independently converged on, and matches the economics
  measured here.
- Against it, honestly: the report carries no per-record details — the 355 `CEIDG_RAPORT` rows in the
  operator's store all sat at `detail_state='brak'` **as measured by the 2026-09-08 audit; this audit
  did not re-measure it, because that database is outside the working tree** — has a fixed column
  set, and covers only entries that are active or suspended. Details are exactly what makes the
  workbook worth having.
- **Unmeasured precondition:** whether `aktualizuj` should intersect `/zmiana` identifiers with the
  store's population. Under B it must, or every update buys the whole country. That is a scope
  decision with no ADR today.

### C. Decide nothing structural; record the economics on screen

Make the cost table state, when a report covers the criteria, what each path costs — one request
versus N hours — so the operator chooses with the numbers in front of them rather than by a default.

- Cost: small.
- Effect: moves the decision to the operator per query, which is where `--zrodlo auto` already
  half-puts it, but with the numbers the audit measured.
- Against it: it does not answer whether the codebase's centre of gravity is in the right place.

## Decision

**Proposed: A now, C alongside it, and B recorded as an explicit product question for the owner —
not taken.**

A and C are repairs and disclosures, and every element of A is already ranked in
docs/audit-architecture-2026-09-09.md. B is a different product: it changes what `aktualizuj` means,
it needs the `/zmiana` intersection decided, and it trades per-record details for volume. The
external evidence says B is the shape this class of system converges on; the measured facts about
*this* register say the details are the reason the workbook is worth having. That tension is not
resolvable by an auditor.

**What would settle it cheaply:** the register's own GUI now covers "a whole voivodeship, no filter,
into Excel" without this tool at all. So the question worth answering first is not "bulk or API" but
**which queries this tool exists for that the GUI cannot serve** — and the measured answer so far is
"anything selecting by industry, and anything crossing voivodeships". If that is the product, the
report path is a filtering engine over an archive, `/zmiana` is its freshness signal, and B stops
being speculative. If the product is per-record detail, A is the right answer and B never applies.

## Consequences

- If A: four items from the ranked list close and nothing structural moves.
- If B is ever taken: `store.py` needs no schema change; `ui/flow.py:384` and `pipeline.run_update`
  do; and `aktualizuj`'s scope becomes an ADR of its own.
- Either way, the seven-day server-side lifetime and the ~05:00 publication cycle belong in
  `docs/decisions.md`, which is where measured facts about this API live and which currently records
  neither.
