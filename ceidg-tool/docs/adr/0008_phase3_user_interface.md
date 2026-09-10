# ADR-0008: Phase 3 user interface — a `ui/` layer with pure texts, an injectable prompter and shared flow steps

Date: 2026-09-05
Status: accepted 2026-09-06 by the project owner
Author: P0w3r223
Related to: UZUPELNIENIE_01.md §A/§C/§D/§E, INSTRUKCJA_CLAUDE_CODE.md "Faza 3", docs/design/phase2_core.md, ADR-0004, ADR-0005, ADR-0007, docs/status.md

---

## Context

Phases 0-2 delivered a complete core: `Criteria` as the only input contract, `pipeline.py` as the only
module that knows both network and database, and a flag-driven typer CLI. Phase 3 must deliver the
layer a non-technical operator actually uses: a first screen, a five-item menu, one `count` request
followed by a deterministic cost table with four choices, a split proposal above 50 000 hits, progress
with ETA, and a closing summary — with the same logic and the same messages serving flags, the YAML
query file and the non-interactive `--tak` mode.

Four constraints shape every option below:

- `Criteria` stays the only contract between input and fetching (phase 4 plugs in there).
- Cost tables and summaries are produced by code, never by prose scattered across commands.
- Production stays behind explicit consent, resolved before any client exists.
- Everything is testable offline in pytest with no TTY.

Before this ADR the decision sequence lived inline in `cli.pobierz` and the presentation lived in two
private helpers (`_banner`, `_print_summary`). A wizard added next to them would produce a second copy
of both, which is precisely what the requirement forbids.

Gaps found against UZUPELNIENIE_01 §A, grounded in the code as it stood:

1. No entry point without flags — the callback printed help.
2. The post-count choice offered `lista / szczegoly / wyjdz`; "popraw kryteria" was missing and there
   was no cost *table*, only prose from `pipeline.estimate_text`.
3. Above 50 000 hits there was only a text hint; §C and resilience scenario 9 require a split proposal
   with a per-batch time estimate.
4. No single-NIP check anywhere.
5. `cli._print_summary` reached into the store to pick a sentence — a layering leak that would multiply
   once a wizard needed the same summary.
6. `find_resumable(criteria, deps)` needs criteria, so "resume first when a job exists" could not use
   it; `cli.wznow` open-coded a store scan.
7. Latent bug for any multi-action session: `ConsoleEvents.close()` dropped `_progress` but kept
   `_task_pages`/`_task_details`, so a second fetch in one process would update a stale `TaskID`.

Cost model (production profile, spacing 3.75 s): 50 000 hits = 2 000 list requests ≈ 2.1 h, plus
10 000 detail requests ≈ 10.4 h. 400 000 hits ≈ 16.7 h list-only. One voivodeship via the daily report
= 2 requests. Those numbers are what make the threshold and the report-first ordering real.

## Decision 1 — where the interactive layer lives

### Option A: extend `cli.py` in place

- **Description**: add prompts to the existing commands and a wizard branch in the `main` callback.
- **Pros**: smallest diff; no new import edges.
- **Cons**: `cli.py` grows from 635 to roughly 1 100 lines mixing typer declarations, prompts, rich
  rendering and flow; the flow is only reachable through `CliRunner` with scripted stdin, so the menu
  and the split proposal become hard to test; message duplication is prevented by discipline only.
- **Effort**: S · **Risk**: Med (regression surface in the one file every command shares).

### Option B: one `wizard.py` module beside `cli.py`

- **Description**: the wizard gets its own module; flags keep their own sequence; only text helpers are
  shared.
- **Pros**: `cli.py` stays roughly as is; conceptually simple.
- **Cons**: the *decisions* (resume offer, report offer, count, threshold handling) still exist twice,
  and `--tak` becomes a third variant. Silent divergence between entry points is invisible to tests
  unless every assertion is duplicated.
- **Effort**: M · **Risk**: Med.

### Option C (chosen): a `ceidg_tool/ui/` package — pure texts + `Prompter` protocol + shared flow steps

- **Description**: `ui/texts.py` is pure and produces view models (no rich, no typer, no questionary).
  `ui/prompts.py` defines a `Prompter` protocol with implementations for questionary, `--tak` defaults,
  a typer fallback and a scripted one for tests. `ui/flow.py` holds the decision steps every entry point
  shares. `ui/wizard.py` holds only the first screen, the menu and the handlers. `cli.py` becomes a thin
  adapter that builds `Criteria` and picks a prompter.
- **Pros**: "same logic, same messages" becomes structural, not aspirational — exactly one implementation
  of the count/cost/choice step and one of every message. The flow is testable without a TTY by swapping
  the prompter. The phase-4 assistant can reuse `ui/texts.py` for its confirmation.
- **Cons**: one more package; `estimate_text` and `format_duration` move out of `pipeline.py`; a short
  refactor of `cli.pobierz` that existing CLI tests must still pass unchanged.
- **Effort**: M · **Risk**: Low (additive; pipeline contracts untouched apart from documented extensions).

**Decision**: Option C. Option B is the tempting middle, but the requirement that flags, YAML and `--tak`
show *the same messages and take the same decisions* is a statement about code identity; only C makes
divergence impossible rather than merely discouraged.

## Decision 2 — how the three entry points share decisions

**Chosen: one set of steps driven by an injectable `Prompter`.** Every question is a `Question` value
with a stable ASCII id (`co_dalej`, `uzyc_raportu`, `wznowic`, `nip`, `podzial`), Polish prose and a
declared default. Entry points differ only in which prompter is installed:

| Entry point | Prompter | Behaviour |
|---|---|---|
| no arguments, TTY | `ConsolePrompter` | interactive menu and prompts |
| flags / YAML, TTY | `ConsolePrompter` (plain-`input` fallback) | asks only what the flags left open |
| `--tak` | `DefaultsPrompter` | returns the declared default; a question with no safe default raises `ConfigError` |
| pytest | `ScriptedPrompter` | answers keyed by question id, records the sequence, fails on an unexpected question |

`DefaultsPrompter` reproduces the previous `--tak` semantics exactly: production consent and
"count above the threshold without `--maks`" are questions without safe defaults, so they still fail
with exit code 3 and `tests/test_cli.py` keeps passing verbatim.

Rejected alternative: an explicit session state machine with a serialised state object. Nothing in
phases 3-4 needs it — the phase-4 assistant plugs into `Criteria`, not into the session. Revisit only
if a GUI/TUI or a remote driver appears.

## Decision 3 — the split proposal above 50 000

At 3.75 s per request, 50 000 hits cost about 2.1 h for the list alone and 12.5 h with details;
scenario 9's 400 000 hits cost about 16.7 h. A split has to produce *real jobs*, not a warning.

- **Option A — only propose narrowing the criteria.** Rejected: §C explicitly requires "podział na partie
  z szacunkiem czasu dla każdej", and an operator with a legitimately large query is left without a path.
- **Option B (chosen) — a pure date-range planner producing real `Criteria` batches.**
  `batching.plan_batches(criteria, count, ...)` partitions `data_od..data_do` into decades, years,
  quarters or months (coarse-to-fine, chosen from the range length) and returns `Batch` values whose
  `criteria` is an ordinary `Criteria`, so each batch runs through the existing `run_fetch` as its own
  run with its own checkpoint. During execution one `count_hits` per batch decides whether that batch is
  fetched or subdivided one level further; depth is bounded at month granularity, below which the tool
  refuses and asks for narrower criteria.
- **Option C — pre-measure every candidate batch before showing the table.** Kept as an opt-in
  refinement, never the default, refused under `--tak`: it would spend 20-40 requests before the operator
  has agreed to anything, breaking the "one count request after criteria" promise.

Why the date axis: it is the only orderable criterion the API filters on, batches are disjoint and
exhaustive over the chosen range, and the existing hint already points there. Splitting by
`wojewodztwo` is *not* exhaustive (entries with a missing or foreign address would silently vanish);
splitting by `status` is exhaustive but wildly unbalanced.

Resumability comes for free: the planner is pure and deterministic, so re-planning after an interruption
yields the same batches; each batch is then looked up by `criteria.fingerprint()` — completed runs are
skipped, an unfinished one is resumed. No schema change, no job table.

Verifiability: the sum of the per-batch counts is compared with the original `count`; any difference
(records with an empty start date) is reported in the summary rather than hidden.

Ordering of advice: when `reports.report_covers(criteria)` is true, the report path (2 requests) is
offered *before* the split — for a whole voivodeship the split would still be 5 500 requests. The split
is the answer for queries the report cannot serve (multi-region, `WYKRESLONY` needed, no region at all).

For a query with no date bounds the planner needs a floor: a named constant `DATE_FLOOR = 1990-01-01`
(empty periods cost one 204 response each), overridable per call through the `floor` argument. The
alternative — asking the operator "od którego roku?" — is friendlier but makes the plan
non-deterministic across sessions and therefore harder to resume, which is the property the whole
batch design rests on.

## Decision 4 — "check a single company by NIP"

**Chosen: `Criteria(nip=…)` through the existing `run_fetch`, then normalise for display.**

- `Criteria` already validates the NIP checksum, so a typo is rejected **before any request**.
- One list request returns the record and its GUID; one detail request fills PKD, contacts and the rest.
  Two requests, about 7.5 s.
- The result is a normal run, so `eksportuj` produces a one-firm workbook with no new code, the detail
  cache is shared with larger fetches, and `link_ceidg` is populated from the GUID like any API record.

Rejected: a dedicated client call (`/firma?ids=` needs the GUID, which is what we are looking up; there
is no NIP-keyed detail endpoint), and display-only without persistence (contradicts "export only from
the store", ADR-0004, and throws away a cache entry the operator just paid a request for).

## Decision 5 — `link_ceidg` for records coming from the report path

The report CSV has no record GUID, so `normalizer._public_link` returns `None` for those rows.

- **Option A (chosen)**: keep the column empty and state why in the deterministic summary. The sentence
  moves out of `cli._print_summary` into `ui/texts.summary_notes()`, driven by `ExportSummary.kind`, so
  the CLI no longer queries the store to decide which sentence to print. Cost: zero requests.
- **Option B**: opt-in link enrichment via batched NIP lookups. `/firmy` accepts repeated parameters, so
  up to 25 NIPs per request would return their GUIDs — `ceil(n/25)` requests, about 2.4 min per 1 000
  records. **Blocked on one unverified fact**: whether repeated `nip=` values are OR-ed. That is a single
  production request to settle, alongside the `ids` batch-size question already open in `docs/status.md`.
  Specified here, not implemented; if implemented it must appear as its own row in the cost table.
- **Option C**: guess a public search URL carrying the NIP. Rejected outright — an unverified deep link
  that 404s is worse for the operator than an empty cell, and it ships a permanent maintenance liability
  into every workbook.

## Decision 6 — mypy over `tests/`

The baseline errors all come from ergonomic shortcuts like `Criteria(wojewodztwo="podlaskie")`: the field
is `tuple[str, ...]` and a before-validator coerces the string at runtime, which mypy cannot see.

| Option | Verdict |
|---|---|
| A. Add a `criteria(**kw)` helper to `tests/support.py`, migrate the call sites, run `mypy ceidg_tool tests` in CI | **Chosen.** Production types stay strict, test ergonomics stay, the fix is mechanical. |
| B. Widen the production field types to `tuple[str, ...] \| str` | Rejected: every consumer would have to narrow a union that never occurs after validation. |
| C. Blanket `# type: ignore[arg-type]` | Rejected: ignores in exactly the files that will hold the new flow tests, where prompt returns are `str` and view models are nested tuples. |
| D. Leave `tests/` unchecked | Rejected: phase 3 adds the largest test surface in the project. |

## Consequences

### New and changed modules

| File | Status | Responsibility |
|---|---|---|
| `ceidg_tool/ui/texts.py` | new | **Pure.** Every user-facing block as a view model. No rich/typer/questionary/httpx/sqlite3. |
| `ceidg_tool/ui/prompts.py` | new | `Question`, `Option`, `Prompter` protocol, four implementations. Only module importing `questionary`. |
| `ceidg_tool/ui/render.py` | new | Maps view models onto rich. |
| `ceidg_tool/ui/flow.py` | new | Decision steps shared by wizard, flags, YAML and `--tak`. |
| `ceidg_tool/ui/wizard.py` | new | First screen, menu, handlers, criteria collection loop. |
| `ceidg_tool/batching.py` | new | **Pure.** Date-range split planner. |
| `ceidg_tool/safetext.py` | new | **Pure.** Control-character stripping and formula neutralisation, shared by the exporter and the screen. |
| `ceidg_tool/pipeline.py` | extended | `list_resumable`, `lookup_nip`, `run_batched_fetch`, `run_export(run_ids=…)`; `estimate_text`/`format_duration` move out. |
| `ceidg_tool/store.py` | extended | records for several runs; `find_run` by criteria hash and status set. |
| `ceidg_tool/console.py` | fixed | `close()` clears the task ids; a bar is scoped per action. |
| `ceidg_tool/cli.py` | slimmed | `_banner`/`_print_summary`/the `pobierz` decision block delegate to `ui`; new `kreator` and `sprawdz-nip` commands, `--partie` flag. |

### Menu-to-pipeline mapping

| Menu item (§A) | Flag equivalent | Pipeline calls |
|---|---|---|
| 4. Wznowić przerwane (shown first when present) | `wznow` | `list_resumable` → `run_fetch(resume_run_id=…)` → `run_export` |
| 1. Pobrać firmy według kryteriów | `pobierz` | `find_resumable` → `report_covers`/`choose_report` → `count_hits` → `estimate` → `run_fetch` \| `run_report_fetch` \| `run_batched_fetch` → `run_export` |
| 2. Zaktualizować bazę o zmiany | `aktualizuj` | `run_update` → optional `run_export` |
| 3. Pobrać gotowy raport (region + okres) | `pobierz --zrodlo raport` | `choose_report` → `run_report_fetch` → `run_export` |
| 5. Sprawdzić firmę po NIP | `sprawdz-nip <NIP>` | `lookup_nip` → `texts.firm_card` → optional `run_export` |

### Flow order (one implementation, all entry points)

`Criteria` collected → unfinished run for this fingerprint? offer resume → `report_covers` and a report
exists? offer the report path (2 requests, with its generation date and its known gaps) → **exactly one
`count` request** → cost table → `lista / szczegoly / popraw kryteria / wyjdź` → above the threshold: the
report path if available, otherwise narrowing or the split proposal, **never an automatic start** → fetch
with progress and ETA → export → summary block plus the `link_ceidg` note.

The report offer precedes `count` deliberately: the report path needs no count, and INSTRUKCJA "Faza 3"
requires it to be offered first for region+period queries. Declining it falls through to the count.

### New boundary rules (extending phase2_core rules 1-5)

6. `ui/texts.py` and `batching.py` import none of `rich`, `questionary`, `typer`, `httpx`, `sqlite3`,
   `openpyxl`.
7. Only `ui/prompts.py` imports `questionary`; only `ui/render.py` and `console.py` import `rich`.
8. `ui/*` never imports `client` or `store` — it goes through `pipeline`.
9. `cli.py` prints no user-facing sentence of its own; every block comes from `ui/texts.py`.

### What we explicitly give up

- The wizard does **not** offer switching to production. Environment resolution stays in `cli._settings`
  before anything else, and the first screen tells the operator the exact command for production. Adding
  a "switch to prod" answer would put personal-data consent behind a menu default.
- Report-sourced rows keep an empty `link_ceidg` until the multi-`nip` probe is done.
- The up-front split table shows equal-share estimates, not measured ones, unless the operator asks for
  exact counting.

### Error and exit-code policy

Non-interactive paths keep the existing exit codes (1 irrecoverable, 2 resumable, 3 configuration or
authorisation). In an interactive session a `CeidgError` raised by one action is rendered with the same
masked message and control returns to the menu; the session exits 0 when the operator chooses "wyjdź".
Schedulers never use the wizard, so exit-code fidelity is preserved where it matters.

## Test strategy

All offline, no TTY, no network — a scripted prompter plus the existing `FakeApi` and `FakeClock`.

| File | What it pins |
|---|---|
| `tests/test_ui_texts.py` | First screen contains purpose, destination, environment, token validity and the data directory; the cost table has the §A columns and derives its numbers from `Estimate`; the summary contains path and size, per-status counts, phone/e-mail percentages, sheet list and log path; the `link_ceidg` note differs by `ExportSummary.kind`. |
| `tests/test_ui_prompts.py` | `DefaultsPrompter` returns declared defaults and raises `ConfigError` for questions with no safe default; the interactive-availability truth table; the scripted prompter fails loudly on an unexpected question id. |
| `tests/test_ui_flow.py` | The question-id sequence for each branch; **exactly one count request per criteria version**; "popraw kryteria" loops back and re-counts once; "wyjdź" issues zero fetch requests; declining the report falls through to the count. |
| `tests/test_wizard_menu.py` | Resume is first when there is an unfinished run and absent otherwise; each item routes to the expected pipeline function; empty criteria are refused at collection, before any request. |
| `tests/test_batching.py` | Batches are disjoint and cover the input range exactly; granularity escalation decade → year → quarter → month; refusal below month; labels stable; replanning the same input yields identical fingerprints. |
| `tests/test_pipeline_nip.py` | Bad checksum → zero requests; found → 2 requests and a populated `link_ceidg`; not found → clear message. |
| `tests/test_pipeline_batched.py` | A completed batch is skipped on replan; an over-threshold batch is subdivided before fetching; a combined export produces one workbook with de-duplicated rows. |
| `tests/resilience/test_s9_large_count.py` | Scenario 9 promoted from manual to CI: `count = 400 000` yields a narrowing or split proposal and **no fetch request**; `--tak` without `--maks` or `--partie` exits 3. |
| `tests/test_cli.py` (extended) | Existing prod-consent and empty-criteria tests unchanged; new: the no-args dispatch predicate; flags and YAML producing the same `Criteria` render identical cost tables. |
| `tests/test_console.py` | A second fetch in one process starts a fresh progress task. |
| CI | `mypy ceidg_tool tests`; boundary rules 6-8 as a small import-scanning test. |

## Risks

| Risk | Response |
|---|---|
| `questionary` misbehaving on Windows consoles with a non-UTF-8 code page | `make_prompter` catches construction failure and falls back to a `typer.prompt`-based prompter implementing the same protocol; documented in the README. |
| Date-range batching drops records with an empty start date | The sum of batch counts is compared with the original `count`; any gap is reported in the summary instead of being silent. |
| Repeated `nip=` OR-ing unverified | Only Decision 5 option B depends on it; not shipped until one probe request confirms it. |
| Refactoring `cli.pobierz` regresses consent handling | Settings and consent resolution stay in `cli._settings`, untouched and above the UI layer; `tests/test_cli.py` runs unmodified as the regression gate. |
| A long wizard session holding the database lock | Unchanged: the lock is acquired inside `run_fetch`/`run_report_fetch`, not for the session; idling at a prompt holds nothing. |

## Decision 7 — the screen is an untrusted output path too

Found during the code review of this work: `rich` parses square brackets in every string it prints as
markup, and registry values reach the screen unchanged. A company name containing `[/b]` raises
`MarkupError` — which is not a `CeidgError`, so neither the CLI error handler nor the wizard's menu loop
catches it — `[link=…]` renders as a real clickable hyperlink to an attacker-chosen address with the tag
itself invisible, and a raw escape sequence passes through to the terminal. §B already required control
characters to be stripped, but that rule lived only in the exporter.

**Chosen**: neutralise once, at the boundary. `safetext.py` (pure) holds `strip_control` and
`sanitize_text`; the exporter keeps using the latter, and `ui/render.py` wraps every data-bearing string
in `rich.text.Text` after stripping control characters, so markup is never parsed. The tool's own
decorations stay as separate markup arguments. `ConsoleEvents.on_message` takes the same path, because it
prints report names coming from the API. Rejected: escaping markup at each call site (one missed site is
a crash) and trusting the registry (contradicts §B).

## Decisions taken while implementing

1. **Date floor**: fixed `1990-01-01`, not asked each session — determinism is what makes a replanned
   batch set recognisable by fingerprint after an interruption.
2. **Batched jobs**: one combined workbook. `run_export` accepts several run ids, de-duplicates by record
   id, and the `Metadane` sheet lists every batch run and its date range.
3. **The wizard saves criteria as a YAML query file** on request, so the interactive session can hand its
   work to the scheduler without retyping it as flags.
4. **The wizard asks before writing a workbook** for the NIP check and the update action, matching the
   flag path where `sprawdz-nip` writes only with `--out`. A single-company check is a preview; persisting
   personal data is a separate decision.
5. **The wizard asks for "cel pobrania"** before exporting, because §B requires that field in `Metadane`
   and the interactive operator is the one most likely to need the audit trail.
6. **The report path gets its own block** (`texts.report_offer`) rather than a row in the cost table: it is
   offered before the `count` request, so at that moment there is no count to tabulate against.

## Open question for the owner

Spend one production request to verify whether repeated `nip=` parameters are OR-ed? It unblocks
report-row link enrichment (Decision 5, option B) and cheaper multi-NIP checks. It needs consent because
the test environment is unreachable from this network.

**Answered 2026-09-06** (targeted probe, 3 requests on production with the owner's consent,
`docs/decisions.md`): repeated `nip=` **is** OR-ed, so option B costs `ceil(n/25)` requests, about
2.5 min per 1 000 records. Decision 5 stands as written — option A ships, option B is unblocked but
deliberately not implemented; whether the requests are worth it is a product call, not a leftover.

## Revisit when

The menu grows past seven items, a second output channel appears (TUI, GUI, web), or the phase-4
assistant needs to drive a whole session rather than only produce a `Criteria` — at that point the
explicit session state rejected in Decision 2 becomes worth its cost.

## Implementation order

The order that keeps the suite green at every step:

1. `console.py` task-id fix and `ExportSummary.kind`.
2. `ui/texts.py` with `_banner`, `_print_summary` and `estimate_text` moved in; `cli.py` delegating.
3. `ui/prompts.py` and `ui/flow.prepare_fetch`; `cli.pobierz` delegating.
4. `batching.py` and `run_batched_fetch`.
5. `lookup_nip` and `sprawdz-nip`.
6. `ui/wizard.py`, the `kreator` command and no-args dispatch.
7. mypy on `tests/`.

Steps 1-3 are pure refactoring with no behaviour change and should be reviewed against
`tests/test_cli.py` and `tests/test_pipeline_e2e.py` before anything new is added.

---

**Note 2026-09-10:** decision 3 (the wizard writes a YAML query file) is **withdrawn by
ADR-0022**. The file's reason for existing — fields reachable no other way — ended when every
filtering field got a CLI flag the same day. Decision 2 (one sentence, several entries) stands and
is now checked as a loop: the command the wizard prints is fed back through the CLI in
`tests/test_cli_phase3.py`.
