# ADR-0010: Filling `link_ceidg` for report rows by batched NIP identity lookup

Date: 2026-09-07
Status: proposed, except decision 8(a) — the egress policy — which the project owner
accepted on 2026-09-07 and which is shipped (see docs/status.md, phase 3f)
Author: P0w3r223
Related to: ADR-0008 (decision 5), ADR-0009, docs/design/phase2_core.md, docs/decisions.md, docs/status.md, UZUPELNIENIE_01.md §A/§B/§C/§E

---

## Context

The report path is the cheap path the tool offers first: `flow.prepare_fetch` offers it *before* the
single `count` request, and for a region+period query it costs 2 requests instead of thousands
(`docs/decisions.md`, mini-probe). Every workbook it produces has `link_ceidg` structurally empty,
because the daily CSV carries no record GUID and `normalizer._public_link` refuses anything that is
not one (`normalizer.py:155`). So the recommended path yields the least verifiable workbook, and
`texts.summary_notes` has to say so.

ADR-0008 decision 5 recorded option B (batched NIP lookups) and blocked it on one unverified fact.
The 2026-09-06 targeted probe removed the block: repeated `nip=` **is** OR-ed. `docs/status.md`
lists the feature under open items as "your call whether it is worth the requests". Nothing has been
designed.

Four facts from the code shape every option below:

- `Criteria` is the only contract between input and fetch. A NIP batch is `Criteria(nip=(…))`, which
  `to_params` already renders as repeated `nip=` pairs — no new input contract is needed.
- `firma.id` is the primary key and the target of `run_firma.firma_id` (`store.py:60-86`). For report
  rows it is `reports.record_id_for(row)`, deterministic from the CSV.
- Export is offline by contract (`run_export`: "Eksport wyłącznie z bazy — zero żądań"; ADR-0004).
  Anything that needs a request must happen at fetch time and persist, or re-export would not
  reproduce the workbook.
- The rhythm of `Events` and `store.touch_lock()` is counted in requests or rows read (CLAUDE.md).
  Anything added here inherits that rule.

## The arithmetic that reframes the decision

`/firmy` returns at most 25 records per request (`max_limit_firmy`), and a NIP batch is a `/firmy`
request. So enrichment costs `ceil(n/25)` requests — **exactly what the same n records cost through
the API list path.** That single identity settles what this feature is and is not worth:

| What the operator wants | Report path | Report + enrichment | API path |
|---|---|---|---|
| basic list, no links | 2 | – | `n/25` |
| basic list **with** `link_ceidg` | impossible | `2 + n/25` | `n/25` |
| list + phone/e-mail/www | 2 | `2 + n/25` | `n/25 + n/5` (details) |
| list + contacts + `link_ceidg` | impossible | `2 + n/25` | `n/25 + n/5` |
| + PKD names, correspondence address, spółki, obywatelstwa | impossible | impossible | `n/25 + n/5` |

Enrichment is therefore **not** a cheaper way to get links than the API — it is the same price. It is
worth its requests only against the fourth row: report + enrichment costs about one sixth of API list
+ details for the same contact-bearing, link-bearing workbook (n = 1 000: 42 requests / 2.6 min
versus 240 requests / 15 min). Against the second row it is a wash and the API path is strictly
better, because the API gives real GUIDs, `WYKRESLONY` entries and today's data rather than
yesterday's snapshot.

Wall clock at the shipped 3.75 s spacing (`estimating.effective_spacing`, both profiles):

| Report rows enriched | Requests | Time |
|---|---|---|
| 300 (the gate-2 report run) | 12 | 45 s |
| 1 000 | 40 | 2.5 min |
| 5 000 | 200 | 12.5 min |
| 20 000 | 800 | 50 min |
| 50 000 (`LARGE_COUNT_THRESHOLD`) | 2 000 | 2.1 h |
| 287 256 (whole wielkopolskie) | 11 491 | **12.0 h**, across sessions |

The last row is the one the interface has to defend against: enriching a whole voivodeship costs more
than fetching the whole voivodeship list through `/firmy` (5 508 requests, 5.7 h) and yields less.

## Decision 1 — what enrichment writes

### Option A (chosen): the record id only; `link_ceidg` derives from it

One `/firmy` request per 25 NIPs; from each returned record we keep exactly two fields, `id` and
`wlasciciel.nip`, and store the GUID against the matching row. `link_ceidg` becomes
`_public_link(id_ceidg)`. Nothing else the response carries is written.

- **Pros**: no field in the workbook ever mixes two moments in time; `zrodlo='CEIDG_RAPORT'` stays
  true for every value in the row; the store writes two scalars per row instead of rewriting a JSON
  blob; exactly one column changes state, so the hidden-column rule stays a one-line diff.
- **Cons**: the response also carries `link`, `terc`, `simc` and `ulic` (verified in
  `tests/fixtures/firmy_page0_limit5.json`), and we discard them.
- **Effort**: S · **Risk**: Low.

### Option B: merge everything the response carries into the row

Additively fill `link`, `terc`, `simc`, `ulic` from the API record, keeping report values where they
exist.

- **Pros**: unhides five columns instead of one for the same requests; nothing is wasted.
- **Cons**: `terc`/`simc`/`ulic` are territorial codes for the address the API knows *today*, next to
  a street the report knew *yesterday*. For an entry that moved between the snapshot and the lookup,
  the row is internally inconsistent, and the `zrodlo` column says the whole row came from the
  report. The tool would be manufacturing a row that no single source ever asserted — the same
  objection ADR-0008 raised against a guessed deep link and `liczba_spolek = 0`.
- **Effort**: M · **Risk**: Med.

### Option C: re-key the rows to the recovered GUID

Rewrite `firma.id` and `run_firma.firma_id`, making report rows first-class API rows.

- **Pros**: `link_ceidg` works with zero normalizer changes; details become fetchable afterwards.
- **Cons**: breaks the report path's own idempotency. `record_id_for` is derived from the CSV, so a
  second scan of the same report re-creates a `NIP:…` row for a firm already re-keyed, and the
  workbook silently doubles it. Plus a primary-key rewrite across a foreign key declared
  `ON DELETE CASCADE` with no `ON UPDATE`, and a merge whenever an API run already holds the same
  GUID.
- **Effort**: L · **Risk**: High.

**Decision: option A.** The cost of B is a row that lies about its own provenance, and this codebase
has twice chosen an empty cell over a fabricated one (`link_ceidg` itself, `liczba_spolek`). C's
failure mode is silent duplication, which is the worst kind. Storing the GUID beside the row rather
than as the row keeps a later, separately-priced detail fetch possible without promising it.

## Decision 2 — where the recovered id lives

Schema v3, two additive columns on `firma`:

```sql
ALTER TABLE firma ADD COLUMN id_ceidg TEXT;
ALTER TABLE firma ADD COLUMN id_state TEXT NOT NULL DEFAULT 'brak'
      CHECK (id_state IN ('brak','znaleziony','nieznaleziony','niejednoznaczny'));
```

`id_state` mirrors `detail_state`, which is the shape the schema already uses for "we asked and this
is what came back". It is what stops a resumed enrichment from re-asking about a NIP the register
does not know: without it, every resume would re-spend requests on the same misses forever.

`RawRecord` gains `id_ceidg: str | None = None`; `normalize` sets `merged["id_ceidg"] = raw.id_ceidg`
next to `merged["id"] = raw.id`, and the `link_ceidg` `FieldSpec` becomes
`lambda r: _public_link(_first(r, "id_ceidg", "id"))`. API rows have `id_ceidg = None` and fall
through to `id` unchanged, so the existing behaviour is untouched by construction.

Rejected: writing the GUID into `list_json` under a private key. It avoids the migration but puts a
tool-invented key inside a blob ADR-0004 describes as the raw API response, and it rewrites a large
blob per row instead of two scalars.

Consequence to accept knowingly: bumping `SCHEMA_VERSION` to 3 means an older build refuses the
database (`store._migrate` raises when `user_version > SCHEMA_VERSION`). Single-machine tool,
acceptable; worth one line in the release note.

The GUID is stored on `firma`, which is shared across runs, so a second report run over the same
region inherits the enrichment for overlapping rows and re-asks nothing. Coverage reported to the
operator is always computed per export, over the exported run ids.

## Decision 3 — batch size, and one probe that has to happen first

The batch is **`profile.max_limit_firmy` (25)** — derived, not configured. A `/firmy` response cannot
carry more than a page, so asking about 26 NIPs either costs a second request or loses one; the page
limit *is* the batch limit. No new profile field, therefore no change to `dialect_json` and no
`profile_hash` churn stranding in-flight runs (the consequence `docs/status.md` records from the
`min_spacing_s` change).

**Precondition, not an assumption.** `docs/decisions.md` measured OR-ing with **two** NIPs. "Up to 25
NIPs per request" and every `ceil(n/25)` downstream of it are inferences. The `ids` parameter is the
standing precedent for this API capping repeated identifiers well below the obvious number: 5 works,
10/20/25/50 all return `400 NIEPOPRAWNA_ILOSC_IDENTYFIKATOROW`. **One production request with 25
NIPs settles it** and must run, with the owner's consent, before this is implemented. Both inputs are
already in `probe_out/`, so the probe spends one request and no lookups.

Rejected: a self-halving batch size on 400. It would make the cost table shown to the operator wrong
mid-run, which is the one property `flow` exists to protect. If the cap turns out to be lower, it is
a measured constant, and if it is not 25 it belongs in `ApiProfile` as a dialect fact with the resume
consequence spelled out.

Two response cases the loop must handle without guessing:

- a NIP matching **no** record → `id_state='nieznaleziony'`;
- a NIP matching **more than one**, or a page that came back full with `count > limit` (so some
  requested NIPs cannot have fitted) → `id_state='niejednoznaczny'` for every NIP not uniquely
  resolved. Never a link chosen by tie-break.

NIPs are checksum-filtered with `criteria.nip_checksum_ok` before batching — the CSV is registry text
and therefore hostile input, and `Criteria(nip=…)` raises on a bad checksum, which would otherwise
kill a whole batch of 25. A rejected NIP costs zero requests, exactly as in `lookup_nip`.

## Decision 4 — when the operator is asked, and what they see

The count of matched rows is not knowable before the CSV scan, so enrichment cannot be priced in the
pre-work cost table the way a fetch is. It is priced in two places instead:

1. **Before anything starts**, `texts.report_offer` gains one row — a rate, not a total:
   *"link_ceidg: pusty; uzupełnienie kosztuje 1 zapytanie na 25 firm (ok. 2,5 min na 1 000)"*. That
   block is already the report path's cost disclosure ("2 zapytania zamiast tysięcy"), and a rate is
   the honest thing to state when the multiplicand is still unknown.
2. **After the scan, before any enrichment request**, its own cost table with the exact number — the
   same shape `aktualizuj` uses (`plan_update` → `texts.update_cost_table` → question). Here the
   count costs **zero** requests: it is a SQL pass over the run's rows. The table carries requests,
   time, what gets filled (`link_ceidg`, nothing else) and one comparison note computed from the same
   pure `estimating.estimate` — what the identical set would cost through `/firmy` with details — so
   the operator can see when the API path is the better buy. Above `LARGE_COUNT_THRESHOLD` records
   the note says so outright.

Question `uzupelnic_linki`, `default="nie"`, `safe_default=True`: `--tak` declines and an unattended
job never silently spends 11 000 requests. `pobierz --linki` installs
`OverridePrompter({"uzupelnic_linki": "tak"})`, exactly the `--partie` pattern.

## Decision 5 — where the step lives in the one sequence

The step must run after the fetch and before the export, and both fetch entry points (`cli.pobierz`,
`wizard.handle_fetch`) must take it identically.

| Option | Verdict |
|---|---|
| A. A new `flow.offer_links(...)` that each entry point calls between `execute` and `export_and_report` | Rejected for the reason ADR-0008 rejected its own option B: a step two call sites must *remember* is divergence discouraged, not divergence impossible. |
| **B. Inside `flow.execute`, which gains the `prompter` both callers already hold** | **Chosen.** One call site, so the wizard and the flags cannot drift. `execute` already prints (`_run_batches` renders the batches table), so its docstring's "bez pytań i bez wypisywania tabel" is corrected rather than newly violated — see finding F6. |
| C. Inside `export_and_report` | Rejected: it would put network requests inside the export step and break "export is repeatable without network" (ADR-0004, `run_export` docstring). |
| D. Only as a separate command | Rejected as the *only* route — it makes the operator run two commands and export twice to get one complete file, against the requirement that a person without API knowledge reaches a finished file. Kept as an *additional* route, see Decision 6. |

The trigger is data-derived, never decision-derived: `pipeline.plan_links(run_ids, deps)` counts rows
with `zrodlo='CEIDG_RAPORT' AND id_ceidg IS NULL AND nip IS NOT NULL`, so an API run or a batched
fetch yields zero and the step silently does nothing. That is the same principle as
`store.record_sources` replacing the two "is this a report export" predicates: ask the data, not the
label. Resume paths (`cli.wznow`, `wizard.handle_resume`) bypass `execute` and need no change — a
report run cannot be resumed (`run_fetch` refuses `kind != 'firmy'`), so nothing they touch is
enrichable.

## Decision 6 — interruption and resume

Enrichment is **additive, idempotent and interruptible at any request**, and every partial state is a
valid state:

- the worklist is a **query**, not a materialised queue — the phase-2 pattern `pending_detail_ids`
  already uses. Resume is "run it again";
- it takes the database lock through `_acquire_lock`, calls `store.touch_lock()` and
  `events.on_links(done, total)` **after every request** (per request, never per page — the phase-3e
  off-by-a-layer error, in a loop whose natural page would be 500 rows);
- `KeyboardInterrupt` and `ResumableError` are caught, the run's status is **not** touched (it is
  already `zakonczony`; the records are complete and only the links are partial), and the flow
  **still exports**. An interrupted enrichment produces a workbook with `link_ceidg` filled for the
  rows that got there;
- the closing summary then names the command that finishes the job:
  `ceidg-tool uzupelnij-linki --run-id …`, which runs the same `pipeline.enrich_links` behind the
  same cost table and can re-export. That command is also the "decide later" route for an operator
  who declined.

No `run` row is created: enrichment fetches no records, it annotates existing ones. `request_log`
carries the audit trail the limiter needs; `runy` stays a list of fetches.

## Decision 7 — hidden columns and the closing summary

`reports.UNFILLED_COLUMNS` keeps its meaning (what the daily CSV *cannot* fill) and gains a sibling:

```python
FILLED_BY_LINK_LOOKUP: frozenset[str] = frozenset({"link_ceidg"})
```

with two subset-style assertions in the spirit of ADR-0009 —
`FILLED_BY_LINK_LOOKUP <= UNFILLED_COLUMNS`, and every name in it is a real column of a real sheet.
`run_export` computes `hidden = UNFILLED_COLUMNS - FILLED_BY_LINK_LOOKUP` when any exported row is
enriched, and `UNFILLED_COLUMNS` otherwise. A partially enriched export therefore **shows** a
partially filled `link_ceidg` rather than hiding a column that has values in it — the correct
direction, and the reason the rule is "hide only what nothing here could fill".

Rejected: deriving the hidden set empirically from the values actually written. It would hide a
column that is merely empty for a narrow query, which is exactly the distinction the
`UNFILLED_COLUMNS` docstring was written to preserve.

`ExportSummary` and `texts.SummaryInput` gain `links_filled` and `links_possible` (report rows
carrying a NIP), and `texts.summary_notes` grows a third branch — all counts, all pure, all
assertable without a terminal:

- none filled → today's sentence, plus the command that would fill it and its price;
- some filled → "link_ceidg wypełniony dla X z Y wierszy", the reason for the rest
  (`nieznaleziony` / `niejednoznaczny` / no NIP in the report), and the command to finish;
- all filled → "link_ceidg wypełniony dla X z N wierszy; N−X wierszy nie ma NIP-u w raporcie".

`Metadane` gains a `uzupelnienie_linkow` row next to `kolumny_ukryte`, for the same reason that row
exists: a column that changed state without explanation looks like a defect. No per-row enrichment
timestamp is stored — `pobrano_utc` keeps describing the report scan, which is where every *value* in
the row still comes from. Revisit that the day enrichment writes anything but identity.

## Decision 8 — what is **not** in this work

Two items surfaced next to this design. Both belong in **separate, smaller pieces of work that ship
first**, in this order.

### (a) Egress is not constrained where the socket opens — ship first, alone

§E requires "brak połączeń do hostów spoza listy dozwolonych (test z zaślepką DNS)". There is no such
test, and `pipeline.build_deps` builds `httpx.Client(verify=True, follow_redirects=False)` leaving
`trust_env` at its default, so `HTTPS_PROXY` routes every token-bearing request through a host nobody
checks (findings F1, F2).

It is separate from enrichment because it is a **security boundary and an unmet acceptance
criterion**, live today regardless of whether enrichment is ever built; because its risk is the
opposite kind (turning `trust_env` off can break a machine that needs `SSL_CERT_FILE`, so it needs
its own acceptance); and because bundling it would put a security fix behind a product decision the
owner may decline. It ships *first* because every new outbound call site — including enrichment's —
should inherit the policy rather than be audited afterwards.

Shape, in one paragraph so it can be approved: a new `ceidg_tool/httpclient.py` owning the single
`build_http_client(*, transport=None)`, which sets `trust_env=False` (killing env proxies and
`SSL_CERT_FILE`/`SSL_CERT_DIR`), keeps `verify=True, follow_redirects=False`, and always wraps the
transport in an `AllowedHostsTransport` that refuses any `request.url.host` outside
`config.ALLOWED_HOSTS` with `UntrustedLinkError` — a check at the layer where the connection happens,
independent of `client._checked_host`, which checks strings. `tests/support.py:139` stops building
its own client and goes through the same factory with `MockTransport` inside, so the production
construction is finally on a tested path. Boundary rule 11 — *only `httpclient.py` names
`httpx.Client`* — joins the AST scan, and the DNS stub becomes a real test: patch
`socket.getaddrinfo` to record and refuse, then assert (i) a real transport resolves exactly
`dane.biznes.gov.pl` (positive control, so the harness cannot pass vacuously), (ii) with
`HTTPS_PROXY` set to a foreign host it *still* resolves only that name — the case that fails today,
and (iii) session-wide, an autouse fixture fails any test that resolves anything else.

**Shipped 2026-09-07, and not exactly as proposed above.** The code review of this step found the
paragraph conflated three mechanisms into one: environment proxies are neutralised because a
transport is *always* injected, not by `trust_env` on the client; `trust_env=False` matters on
`httpx.HTTPTransport`, where it stops `SSL_CERT_FILE` replacing the CA bundle, and that half had no
test. The shipped signature is `build_http_client(*, transport=None, allowed=ALLOWED_HOSTS)`, with
`build_deps` narrowing the gate to the host of the selected environment. Read
`docs/design/phase2_core.md` (rule 11) and `docs/resilience-report.md` (§E) for what is actually in
the tree; the paragraph above is kept as the proposal it was.

### (b) The 21 MB ZIP download is silent and lets the lock die — ship second, alone

`run_report_fetch` emits one `on_message` and then nothing until the whole transfer finishes, and
between `_acquire_lock` and the first `store.touch_lock()` (1 000 CSV rows later) there is no
heartbeat at all, while `DEFAULT_LOCK_STALE_S` is 600 s and the client's retry ladder alone can burn
400 s restarting the transfer from zero (finding F3).

It is separate from enrichment because it is a **defect in the phase-3e family that is live on the
shipped report path**, and because it must not wait on a decision the owner may answer with "not
worth the requests". It ships before enrichment because enrichment makes the report path longer, not
because it depends on it.

Shape: `Events` gains `on_download(done_bytes, total_bytes | None)`; `client.download_report` gains a
`progress: Callable[[int, int | None], None] | None` parameter invoked from the streaming loop at
most every 512 KiB (≈42 events for 21 MB, ≥1 event per 10 s even on a 50 kB/s link);
`pipeline.run_report_fetch` passes a callback that does **both** `store.touch_lock()` and
`deps.events.on_download(...)`. The heartbeat rides the callback rather than the `Events` protocol
because touching the database is pipeline's alone under boundary rule 5, and the client may not know
the store. `ConsoleEvents` renders it as a fourth task in whole MiB with a literal description
("Raport ZIP (MB)"), because one `Progress` cannot give one task its own `DownloadColumn`, and
`total=None` is already handled by `_CountColumn` for a response without `Content-Length`.

**The one real cost of splitting**: the `Events` protocol changes twice — `on_download` in (b),
`on_links` here — so `NullEvents`, `ConsoleEvents` and the test doubles get swept twice, about
fifteen lines. That is cheap against coupling a security fix and a live defect to a feature decision.
With no version control in this repository (the owner's decision, `docs/status.md`), there is no
branch that can isolate a half-finished feature, and a review that must judge a security boundary and
a new feature in one pass is the shape that produced phase 3b's blocked first round.

At five progress channels (`on_page`, `on_details`, `on_export`, `on_download`, `on_links`) the
protocol wants generalising into one stage-keyed event with literal labels in `console.py`. That is a
refactor with no behaviour to gain today; it is the revisit trigger, not this work.

## Consequences

| File | Change |
|---|---|
| `store.py` | schema v3 (`id_ceidg`, `id_state`), `iter_run_records` selects them, `pending_link_rows(run_ids)`, `save_link_ids(...)`, `link_coverage(run_ids)` |
| `records.py` | `RawRecord.id_ceidg: str \| None = None` |
| `normalizer.py` | `link_ceidg` extractor prefers `id_ceidg`; `normalize` passes it through |
| `reports.py` | `FILLED_BY_LINK_LOOKUP` + its two subset assertions |
| `client.py` | `one_page(criteria)` — exactly one `/firmy` request at `max_limit_firmy`, no paging, so the cost table cannot be undercounted by a stray `links.next` |
| `pipeline.py` | `LinkPlan`, `plan_links` (0 requests), `enrich_links` (lock, per-request heartbeat and events, interruption-safe), `run_export` hidden-set and `ExportSummary` coverage fields |
| `estimating.py` | `estimate_links(records, profile)` — pure |
| `ui/texts.py` | `link_cost_table`, `report_offer` row, `summary_notes` third branch, `Metadane` sentence |
| `ui/prompts.py` | `UZUPELNIC_LINKI` |
| `ui/flow.py` | `execute` takes the prompter and runs the offer; docstring corrected |
| `cli.py` | `--linki` on `pobierz`; new `uzupelnij-linki` command (authoring no sentence of its own) |

Explicitly given up: `terc`, `simc`, `ulic` and `link` stay hidden for report rows even though the
enrichment response carries them; report rows still cannot be upgraded with `/firma` details (the
stored GUID makes that possible later, it does not deliver it); no per-row enrichment timestamp.

## Test strategy

All offline, no TTY, no network, on `FakeApi`/`FakeClock`.

| What | Pinned |
|---|---|
| `plan_links` | zero requests; counts only report rows without a GUID and with a checksum-valid NIP; an API run plans nothing |
| `enrich_links` | `ceil(n/25)` requests and no more; one `touch_lock` and one `on_links` per request (not per page); a NIP with no match is never re-asked after resume; two matches → `niejednoznaczny` and no link; a full page with `count > limit` resolves nobody it did not see |
| interruption | `KeyboardInterrupt` mid-batch leaves the run `zakonczony`, the enriched prefix persisted, and the export still runs |
| export | partial enrichment shows `link_ceidg` and hides the other twelve columns; full enrichment likewise; no enrichment reproduces today's workbook byte-for-byte |
| flow | both entry points ask `uzupelnic_linki` in the same position; `--tak` never enriches; `--linki` does; declining spends zero requests |
| texts | the three summary branches and the cost table, asserted as view models |
| migration | a v2 database opens, migrates and keeps every existing row and run |

## Revisit when

The `nip` batch probe returns anything other than 25; enrichment starts writing a field that is not
identity (then per-field provenance is owed); report rows need `/firma` details (then Decision 2's
option C returns, with the idempotency problem to solve first); or the progress protocol reaches five
channels.

## Appendix — findings from reading the code against the documents

| # | Severity | Finding |
|---|---|---|
| F1 | **High** | `pipeline.py:122` builds `httpx.Client(verify=True, follow_redirects=False)` with `trust_env` at its default. In httpx 0.28.1 that means environment proxies apply whenever no explicit transport is passed (`_client.py:685`: `allow_env_proxies = trust_env and transport is None`) and `SSL_CERT_FILE`/`SSL_CERT_DIR` can replace the CA bundle (`_config.py:34-36`). `HTTPS_PROXY` therefore sends the token — a JWT carrying a PESEL — to a host never checked against `config.ALLOWED_HOSTS`, and `client._checked_host` cannot see it because it inspects the URL, not the connection. Violates §B and §E. |
| F2 | **Medium-High** | §E's "brak połączeń do hostów spoza listy dozwolonych (test z zaślepką DNS)" has no test: no file in the repository mentions `socket`, `getaddrinfo`, `trust_env` or a proxy, and `docs/resilience-report.md` carries all ten numbered scenarios but not this bullet. Compounding: `tests/support.py:139` builds its own `httpx.Client(transport=…)`, so the production construction line has zero coverage — and because httpx skips env proxies when a transport is injected, the test seam is precisely what hides F1. |
| F3 | **Medium** | The database lock gets no heartbeat during the report download. `run_report_fetch` acquires it (`pipeline.py:355`) and the first `store.touch_lock()` is 1 000 CSV rows later (`pipeline.py:408`); between them sit the 21 MB transfer and, on a flaky link, the retry ladder (10+30+60+300 s, `client.py:49`), each attempt restarting from zero. `DEFAULT_LOCK_STALE_S` is 600 s, so a slow or interrupted transfer expires the lock under a working process — the defect phase 3e closed for `/zmiana` pages, still open one layer down. |
| F4 | **Medium** | "Up to 25 NIPs per request" is an inference presented as a measurement. `docs/decisions.md` measured OR-ing with **two** NIPs; `docs/status.md` and ADR-0008 both quote `ceil(n/25)` as settled. The `ids` parameter is the standing precedent for this API capping repeated identifiers below the obvious number (5 works; 10, 20, 25, 50 all 400). One production request settles it and must precede implementation. |
| F5 | **Low** | `ExportSummary.kind` and the hidden-column set can disagree, which the comment at `pipeline.py:941` says they cannot. `hidden` comes from `from_report`, `kind` from `"raport" if from_report else run.kind`. A report run with zero matched records gives `record_sources() == set()` → `from_report` false → `hidden` empty → `kind` falls back to `run.kind == "raport"`, so the summary prints both report notes, one of which points at a `kolumny_ukryte` row that `build_metadata` did not write (`if hidden_columns:`). Reachable today via `pobierz --zrodlo raport` with criteria that match nothing; latent for any future multi-run export. |
| F6 | **Low** | `flow.execute`'s docstring says "Bez pytań i bez wypisywania tabel" while `_run_batches` prints a per-batch message and `texts.batches_table` (`flow.py:216` vs `261, 266`). It matters because that docstring is what the next author reads when deciding where a post-fetch step belongs — this ADR's Decision 5. |
| F7 | **Low** | `RateProfile.min_spacing_s` still defaults to `3.6` (`apiprofile.py:35`) although both shipped profiles carry 3.75 and the measurement behind the change (49 requests in the busiest 180 s window against a self-declared 48) is recorded in `prod.yaml` and `docs/status.md`. The tests pin the *shipped* profiles (`test_apiprofile.py:22`, `test_ratelimit.py:264`), so a hand-written profile supplied through `CEIDG_PROFILE` without a `rate:` block silently re-opens the burst. `estimating.effective_spacing` still returns 3.75, so the cost table and the limiter would disagree again — the exact drift the gate review found. |
