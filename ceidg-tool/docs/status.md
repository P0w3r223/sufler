# Project status and plan

Date: 2026-09-07 (phases 0-2 built 2026-09-05; gates 1 and 2 accepted, gate 3 open)
Status: living document (update at every gate)
Author: P0w3r223
Related to: INSTRUKCJA_CLAUDE_CODE.md, UZUPELNIENIE_01.md, docs/decisions.md, docs/adr/, CLAUDE.md

---

## Phases

| Phase | Gate | State | Evidence |
|---|---|---|---|
| 0 Environment | – | done | `pyproject.toml`, `.venv`, `requirements.lock`, `scripts/ceidg_probe.py`, `docs/api_notes.md` |
| 1 API probe | gate 1 accepted 2026-09-05 | done | `docs/decisions.md`; probe + mini-probe run on **production** with the owner's consent (test host unreachable); anonymised fixtures in `tests/fixtures/` |
| 2 Core | **gate 2 accepted 2026-09-06** | done | At the gate (2026-09-05): `ceidg_tool/` (19 modules), 188 offline tests, ruff + mypy strict clean, CI on Linux + Windows; two production runs (API path 11 requests, report path 2 requests); three code reviews and one test review applied. Before acceptance (2026-09-06): the two workbooks were verified against the phase-2 specification point by point — tables, autofilter, frozen header, text-typed identifiers, real dates, provenance with no gaps, `Slownik` covering 100 % of columns, zero formula cells — then the ergonomics pass below, a code review of it, and a rebuild of both files from the database. |
| 3 User interface | **gate 3 accepted 2026-09-09** | done | walked on production the same day (25 requests, two workbooks), and the walk's blind spot produced one code fix — see "Gate 3 — the walk". | `ceidg_tool/ui/` (texts, prompts, render, flow, wizard), `batching.py`, `estimating.py`; wizard on `ceidg-tool` with no arguments, plus `kreator`, `sprawdz-nip` and `--partie`; design in ADR-0008; `mypy ceidg_tool tests` clean, resilience scenario 9 automated |
| 3b Invariants | – | done 2026-09-06 | ADR-0009: boundary rules 9 and 10 closed and enforced by scan, `richtext.py` as the single `rich` seam, `--force` on `pobierz`/`wznow` (the flag the lock message already promised), resilience scenarios 1, 2 and 8 automated; 565 offline tests |
| 3c Workbook ergonomics | – | done 2026-09-06 | Assessment of the gate-2 artifacts turned up usability defects the specification never named; fixed, reviewed, and pinned by tests. See "Gate review 2026-09-06" below. |
| 3d Rate limits | – | done 2026-09-06 | Measured, not assumed: the limiter's busiest 180 s window held 49 requests against an API limit of 50. Spacing corrected to 3.75 s, the server's own budget header turned into a brake, the probes moved onto the shared request log, `profile_hash` narrowed to the dialect. |
| 3e Long operations | – | done 2026-09-06 | Found by the owner walking gate 3: a progress bar that stopped for six minutes at a time. Thirteen defects behind it, most sharing one shape — progress and lock heartbeat counted per page instead of per request. See "Long operations" below. 637 offline tests. |
| 3f Egress policy | – | done 2026-09-07 | ADR-0010 decision 8(a): `httpclient.py` as the only construction site for `httpx.Client` (boundary rule 11), `AllowedHostsTransport` narrowed to the selected environment, and §E's DNS-stub criterion finally tested. Found by reading the documents against the code, not by a failure. |
| 3g Report download | – | done 2026-09-07 | ADR-0010 finding F3: the 21 MB archive downloaded in silence and left the database lock without a heartbeat for the whole transfer — and, after the second review, neither did any long wait anywhere in the program. 672 offline tests. |
| 3h Secret masking + rule 11 | – | done 2026-09-07 | ADR-0011 findings F1 and F2, both live in the tree and independent of phase 4: masking was JWT-shaped, and rule 11's scan was `httpx`-shaped. 679 offline tests. |
| 4 Language assistant | **ADR-0011 accepted 2026-09-07** | built end to end; exercised against the real API; awaiting gate 3 | `ceidg_tool/assistant/` — schema, PKD 2025 dictionary (728 subclasses), prompt builder, translation to `Criteria`, and `caller.py` over the SDK; boundary rules 6, 12 and 13 enforced; the assistant key resolves keyring → env → `.env` and is masked everywhere the token is; the wizard asks for a description first and `pobierz --opis` shares that implementation. 768 offline tests, none skipped. |
| 4e Test runs A and B | – | done 2026-09-07 | `docs/test-runs-phase4.md`: two passes of group A plus group B, **zero CEIDG requests**, a few grosze. Group A settled the one thing no offline test can see — whether a real answer fits under adaptive thinking — and closed an open item; group B found two defects, both in *messages*, both fixed and pinned. B4 and groups C-E stay with the owner. |
| 4f Test run C | – | run 2026-09-07; **it blocks gate 3** | The PKD vintage probe, 3 production requests with the owner's consent, plus a zero-cost measurement over the 21 MB production report already on disk. The `pkd` filter matches the code **as stored**, and the register is mid-transition (to 31.12.2026): of 285 026 real records, 58.6 % still carry PKD 2007 codes and **8.6 % carry no code the shipped `pkd2025.yaml` knows** (corrected 2026-09-09 from 25.2 %, which counts main codes only). `9602Z` (hairdressing) is the most frequent unreachable code, and the gate-3 walk sentence is about hairdressers. No code changed — the fix is a design decision with a cost. See `docs/decisions.md`. |
| 4g ADR-0012 | **accepted 2026-09-07** | designed; built in 4h | Option E of five: apply the GUS transition key in the input layer, materialise into `Criteria.pkd_2007`, expand silently where clean (55 codes), price both populations and ask where ambiguous (209). Its gate item — never measured — came back good: repeated `pkd=` is **OR-ed** (38 201 + 187 149 = 225 350 exactly), which also proves the vintages disjoint from the API side and puts the national gap at **17 %** for hairdressing. |
| 4h Vintage coverage | – | built 2026-09-07 | ADR-0012 implemented: `scripts/build_pkd_transition.py`, `ceidg_tool/data/pkd2007_2025.yaml` (264 codes, 159 predecessors, all named), pure `pkdmap.py` (boundary rule 6), `Criteria.pkd_2007`, the vintage step in `ui/flow.py`, `--pkd-2007/--bez-pkd-2007`. Invariant restated and measured: two `count` when the question is asked, one otherwise, none after the choice. Old fingerprints preserved, so interrupted fetches stay resumable. |
| 4i Verification alone | – | done 2026-09-07 | Everything not needing the owner, at zero CEIDG requests: every vintage screen rendered through real `rich` (found a duplicated sentence no view-model assertion could see), four real model calls proving the step fires on ordinary Polish queries, `Metadane` and the query file checked on real artefacts, three stale `62.01.Z` hints removed. Groups D, E and B4 remain the owner's — the test host is dead and production needs consent. |
| **5 Record identifier identity** | **ADR-0013 accepted 2026-09-08** | built, reviewed, migration verified on a copy of the production store | The overnight `aktualizuj` of 2026-09-07/08 finished `zakonczony` and delivered **nothing**: all 13 401 of its records sat at `detail_state='brak'` while their details lay in the same database under a different spelling of the same identifier. `/zmiana` returns identifiers in lower case, `/firmy` and `/firma` in upper, and `firma.id` is a case-sensitive primary key — so every changed entry was written twice and the detail cache could never hit. `ceidg_tool/recordid.py`, canonicalisation in `client._records_of`, `KanonicznyId` through `client` and `store` (boundary rule 14, carried by mypy rather than the AST scan), schema v3 merge migration, and the `run_firma(firma_id)` index the cascade always needed. 952 offline tests. |
| 5b Observability | – | built, reviewed twice (the second round blocked and was right) | The other two findings from the same overnight log, plus one from the tester. `touch_lock()` returns `bool` and `pipeline.LockHeartbeat` reacts to a lost lease and names a machine suspend; `_LogEvents` puts every wait into the log file with its predicted resume time; `update_summary` gained the reference point that would have turned a three-hour silent loss into a sentence. 999 offline tests. |

## Phase 3 — what was built

- **One decision sequence for every entry point.** `ui/flow.py` holds the order (resume →
  report → one `count` request → cost table → choice → optional split → fetch → export →
  summary). Flags, the YAML query file, `--tak` and the wizard differ only in which
  `Prompter` is installed, so they cannot drift apart.
- **First screen and summary** come from `ui/texts.py` as view models with no output
  library, so both are asserted in tests without a terminal.
- **Cost table** with the four columns from §A, and the post-count choice now offers
  "popraw kryteria", which was missing.
- **Split above 50 000** (`batching.py`): the coarsest date partition whose average batch
  fits the threshold, refined per batch during execution. Batches are ordinary `Criteria`,
  so each is a normal run with its own checkpoint; the planner is deterministic, so a
  replan after an interruption skips finished batches and resumes the unfinished one.
  In `--tak` the split needs the explicit `--partie` flag.
- **Single company by NIP** (`pipeline.lookup_nip`): the checksum is validated locally, so
  a typo costs no request; a hit costs two and stays in the store for `eksportuj`.
- **Defects found and fixed during this phase** (each pinned by a test):
  - The screen was an unguarded output path. `rich` parses square brackets as markup, so a
    company name containing `[/b]` ended the program with a traceback the error handler
    could not catch, `[link=…]` produced a clickable link to an attacker-chosen address,
    and an escape sequence reached the terminal. Registry text now goes through
    `safetext.strip_control` and `rich.text.Text` (ADR-0008, decision 7).
  - The wizard's criteria loop had no exit: `questionary` turns Ctrl+C into an empty
    answer, so an operator could only leave by killing the process.
  - `lookup_nip("")` validated to criteria with no filter and would have paged the whole
    registry before crashing.
  - `store.find_run` matched any run kind, so a report run could be mistaken for a
    completed batch and its rows merged into a batched workbook.
  - `ConsoleEvents.close()` kept stale progress task ids, breaking the second action
    inside one wizard session.
- **Review rounds**: one architecture pass before the work, one test pass, and three code
  reviews (the initial one blocked on the two screen and prompt defects above, the second
  verified the fixes empirically, the third covered the follow-up changes). Every finding
  was applied; the fixes that were not observable from the suite now have regression tests,
  including a mutation check on the batch threshold.

## Token

The JWT in `.env` is the owner's own token (issued 2026-09-05, no `exp` claim); it was used
for the probe and the gate-2 runs. The owner confirmed this on 2026-09-05 and asked to stop
treating it as an open item. **Revisit from 2026-09-30**: refresh or rotate it through
https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00 and store it with `ceidg-tool token zapisz`
rather than in `.env`. A changed token starts a fresh limiter history (different fingerprint
in `request_log`), which is harmless.

## What the owner has to do next

With gate 2 accepted on 2026-09-06, **gate 3 is the only one left**, and it is the one that cannot
be delegated: the requirement is that a person without API knowledge reaches a finished file.

0. ~~**Run the PKD vintage probe first.**~~ — **done 2026-09-07** (group C, 3 production requests,
   with your consent given in session), **and it did its job: it blocks the walk below.** The `pkd`
   filter matches the code as stored, and the register is mid-transition to PKD 2025 (to
   31.12.2026), so a 2025-only dictionary reaches roughly three quarters of it — measured over
   285 026 real records at zero further cost. `9602Z`, hairdressing under PKD 2007, is the most
   frequent unreachable code, and the walk below is written around hairdressers. **Decide the fix
   first** (see phase 4f and `docs/decisions.md`); the walk measures nothing useful until then.
1. **Walk the wizard on a real terminal** — that is gate 3 (group D). Run `ceidg-tool` with no
   arguments (with `PYTHONUTF8=1`) and go through one fetch end to end. Everything below the
   terminal is covered by offline tests; how it reads and feels is the part only the owner can judge.
   Note the practical obstacle: the test host does not answer from this network, so a walk that
   reaches a file needs `--srodowisko prod --produkcja` and fresh consent. — **Walked on
   2026-09-09 with a scripted operator; see "Gate 3 — the walk" below.** Two passes reached two
   real workbooks for 25 requests. What that walk cannot supply is the half the gate is actually
   about: whether the screens *read* well to somebody who does not know the API. That judgement
   is still yours, and the transcripts are there to be read rather than re-run.
2. **Run resilience scenarios 1, 2 and 8 by hand** (`docs/resilience-report.md`, group E). Each has
   an automated equivalent that passes; §E asks for a real killed process, a real network cut and a
   real full disk, dated in the report. **Group B4 belongs with them**: the assistant under a real
   network cut, which is the one case of group B that a sandbox cannot induce honestly.
3. **Decide whether to implement report-row `link_ceidg` enrichment.** The probe that blocked it
   is done (2026-09-06, 3 requests on production with your consent): repeated `nip=` **is** OR-ed,
   so ADR-0008 decision 5 option B is unblocked — `ceil(n/25)` requests, about 2.5 min per 1 000
   records, and it would appear as its own row in the cost table. Not implemented; it is a
   feature decision, not a leftover.
4. ~~**Decide whether phase 4 starts.**~~ — **started and built, 2026-09-07.** The seam never
   moved: `Criteria` is still the only contract between any input and any fetch, and the assistant
   ends by producing one. See phases 4a-4e.

## Phase 3b — invariants closed (2026-09-06)

Three items that were blocked on nobody, designed in ADR-0009 and reviewed on completion.

- **Boundary rule 9 finished.** `aktualizuj`, `eksportuj`, `wyczysc`, `runy`, both `token`
  subcommands and the interrupt handler no longer build their own sentences; `cli.py` imports
  no `rich` and makes no print call. The sentences moved verbatim into a message catalogue in
  `ui/texts.py`. Two of them existed in two copies — one markup-parsed, one not — and are now
  single-sourced with the wizard.
- **Boundary rule 10 enforced.** `tests/test_boundaries.py` now scans the three `rich`-importing
  modules and rejects any printed argument that is neither the program's own literal, nor a
  locally built renderable, nor a call to `safe`. The scan is itself tested against ten
  snippets, four of them the shapes that actually crashed the program in phase 3 — a scan that
  always passes is indistinguishable from one that works.
- **A defect found by designing scenario 1, not by running it.** The lock error told the
  operator to use `--force`, and no command had that flag; after a killed process the
  documented `ceidg-tool wznow` was therefore blocked for up to ten minutes. `--force` now
  exists on every command that takes the lock, it is spent once rather than at every batch, the
  message says when the lock expires on its own, and forcing a lock that was still **live**
  warns the operator — the flag looks the same whether it inherits a corpse or elbows a
  colleague, and only that warning tells them which happened.
- **Two review rounds.** The first blocked on two high-severity findings (the `--force` flag
  never reached a fresh batch, so `pobierz --partie --force` failed exactly as before; and the
  CLI scan saw only `rich`, so `typer.echo` would have walked straight through a rule the test
  reported as closed) plus four scan holes demonstrated empirically. The second verified the
  fixes against the original reproductions, lifted the block, and found three more gaps of the
  same shape. All applied. The repeated shape — a `rich` object trusted to carry text whose own
  arguments nobody scanned — is now two subset assertions rather than a list to remember, so a
  fourth round of the same finding fails as a test instead of arriving as a review comment.
- **Scenarios 1, 2 and 8 automated** (`tests/resilience/test_s1_…`, `test_s2_…`, `test_s8_…`).
  §D allows that; §E still wants the manual runs, so the report keeps the two claims apart.
  Scenario 2 produced a number worth knowing: a two-minute outage costs the whole retry ladder
  and about seven minutes of waiting, because the 10 → 30 → 60 s rungs end while the network is
  still down.

## Decisions taken by the owner (2026-09-06)

- **`wyczysc --wszystko --tak` no longer deletes unattended.** Irreversible destruction now needs
  a second, explicit flag (`--potwierdzam-usuniecie`), exactly like production consent; without it
  the non-interactive mode refuses with exit code 3. This changes an existing CLI contract: a
  scheduled job that relied on `--tak` alone will now fail loudly instead of emptying the database.
- **Targeted production probe run** (3 requests, consent given in session). Results in
  `docs/decisions.md`: repeated `nip=` **is** OR-ed; `ids` batches of 10 and 20 are both rejected,
  so the cap sits between 5 and 9 and `ids_batch_size: 5` stays.
- **Boundary rules 1-5 closed.** All ten rules **as they stood on 2026-09-06** are enforced by
  `tests/test_boundaries.py`; none rests on review any more. (There are **fourteen** today: 11-13
  arrived with ADR-0010 and ADR-0011, and rule 14 is carried by mypy strict rather than the scan.) The scan reads both `from ..store import` and
  `from ceidg_tool.store import` — the review found it saw only the first, which would have made
  rules 2, 5 and 8 stop applying the day an editor auto-imported the absolute form. Both the
  mutation check and the two-import-forms check are now tests rather than notes.

### Argued against rather than applied

- **`wyczysc --wszystko` still does not take the database lock.** The review is right that on
  Linux it will unlink a file another process is writing. Taking the lock means opening the
  store first — and this command is partly the remedy for a store that cannot be opened, so
  the guard would fail exactly when it is needed most. The deletion loop now collects failures
  and continues instead of stopping at the first busy file, which covers the Windows case (the
  file is locked, so it is not deleted and is named in the message). Revisit if the tool ever
  runs where two people share a data directory.
- ~~`typer.confirm` accepts only `y`/`n`~~ — **fixed on the owner's instruction (2026-09-06).**
  All three entry points now read a confirmation the same way, through `prompts.is_yes` and
  `prompts.confirm_line`. The mechanism stayed where ADR-0008 put it: consent still resolves in
  `cli._settings` above the `ui` layer, and the `Prompter` protocol is unchanged — only the
  reading of the answer is shared.

  Two things the review caught in the first attempt, both worth remembering:

  - **Prefix matching on `t`/`y` was a destructive bug, not a shortcut.** "teraz nie" and
    "to nie" — ordinary Polish refusals — begin with `t`, so they read as *yes* and deleted the
    database. Matching is now exact (`t`, `tak`, `y`, `yes`), and those three phrases are test
    cases. A convenience that widens consent is worse than the inconvenience it removed.
  - **The wizard's main path never reached the shared code.** `questionary.confirm` binds `y`
    and `n` as keys and silently discards everything else, so an operator typing "tak" at
    "Zapisać wynik do skoroszytu?" (default: no) got *no* and no explanation — the workbook the
    tool exists to produce was not written. The wizard now asks as text and parses with the same
    function, at the cost of one Enter.

  The two prompts differ in one deliberate way. In the wizard an answer that is neither yes nor
  no ("tak.", "jasne") is **asked once more**, because there the same silent loss of intent costs
  a workbook and the operator is sitting there anyway. In `cli._confirm` it means no immediately:
  those two prompts guard irreversible actions, so ambiguity has to fall the safe way without a
  second chance to fat-finger it.
- **`probe_out/` has no retention story.** It accumulates raw production records (names, phone
  numbers, e-mail addresses) from every probe run, is git-ignored, and `wyczysc` does not touch
  it because it lives in the working directory rather than the user data directory. Deleting it
  by hand is safe: only `scripts/anonymize_samples.py` and the probes read it.

## Gate review 2026-09-06 — what the owner accepted, and what the review found

The owner reviewed gates 1-3 in one session. Gate 1 was confirmed as accepted on 2026-09-05 (the
header of `docs/decisions.md` had gone stale and said "proposed"); ADR-0008 and ADR-0009 were
accepted; **gate 2 was accepted** after the workbook work below. Gate 3 stays open, because its
requirement — a person without API knowledge reaching a finished file — is the one thing no test
can stand in for.

The findings below came out of checking documents against code rather than reading them alone.
That distinction earned its keep three times: the accepted cost model quoted a constant the
program does not use, the workbook met its specification while being awkward to work in, and the
limiter sat one request from the API ceiling while its configuration claimed a 4 % margin.

**The accepted cost model used the wrong constant.** `docs/decisions.md` quoted 3.6 s per request —
`min_spacing_s` — while the shipped limiter applies 3.75 s, because both rate windows work out to
that and `estimating.effective_spacing` takes the largest of the three. Every number in the table was
therefore lower than what the tool shows the operator. Corrected in place, with the derivation spelled
out so the next reader does not "simplify" it back.

**The workbook met its specification and was still awkward to use.** Every structural claim held on
both gate-2 artifacts — tables, autofilter, frozen header, text-typed identifiers, real dates,
provenance columns with no gaps, `Slownik` covering 100 % of columns, zero formula cells. What the
specification never asked about was what happens when a human scrolls:

- `freeze_panes` froze the header only. `Firmy` has 43 columns, so reaching `telefon` or
  `pkd_glowny_nazwa` left values with no visible row identity. Freezing is now declared per sheet
  (`normalizer.FREEZE_AFTER`) and covers the identity columns.
- Column A was `id`, a 36-character GUID nobody reads, 38 characters wide and — after the change
  above — inside the frozen area. `Firmy` now leads with `nip` and `nazwa`; `id` sits in the
  technical block next to `link`, where it is still the join key for the child sheets.
- `FieldSpec.max_width` caps the width of columns whose content is never read in the cell (GUIDs,
  URLs). The cap is display-only; the value stays whole, which is what the test asserts.
- The report path left 11 of the 43 `Firmy` columns structurally empty (plus `pkd_nazwa` in the
  `PKD` sheet) — the daily CSV has no `terc`, no
  `adresKorespondencyjny`, no PKD names, no record id. Those columns are now **hidden rather than
  removed**: the schema stays identical for both sources, so an automat reading by header name loses
  nothing, and `Metadane` gains a `kolumny_ukryte` row so a hidden column never looks like a missing
  one. The set lives in `reports.UNFILLED_COLUMNS`, derived from `row_to_record`, and a test refuses
  a name that no sheet actually has — a typo there would hide nothing and report nothing.

**The code review caught the same defect one layer down.** The ergonomics pass gave `max_width` to
`Firmy.id` but not to the `id` in `_CHILD_KEY_FIELDS`, where it sits in column A of `PKD`, `Spolki`
and `Adresy` — so freezing through column B pinned a 38-character GUID to the screen permanently in
three sheets. Before the change it could at least be scrolled away. Fixed, and the frozen area of
every sheet is now capped by a test rather than by attention. The review also replaced the
hand-written half of the `UNFILLED_COLUMNS` test with one derived from `row_to_record`: a list
written from memory only catches the drift someone remembered, while the dangerous direction is the
other one — add a mapping, leave the name in the set, and a filled column keeps being hidden with no
error, no gap in the sheet and no red test. Two review findings were left open on purpose and are in
"Open items": `liczba_spolek` and the two predicates for "is this a report export".

**The limiter ran one request closer to the API ceiling than the configuration claimed.**
`min_spacing_s` was 3.6 s — the figure the API documentation recommends — while both rate windows
independently imply 180/48 = 3600/960 = **3.75 s**. A minimum below the window-implied pace does not
make the tool faster; it lets requests go out densely and then repays the debt with a longer stall.
Measured on the real limiter with a fake clock (1 500 requests): the busiest 180 s window held **49**
requests, one above our own declared 48 and one below the API's 50. At 3.75 s the peak is exactly 48
and average throughput falls by 0.16 %. Both profiles now carry 3.75 s, and three tests pin the
property: no shipped profile may burst past its own window, none may reach a documented API limit,
and `rate.min_spacing_s` must equal `estimating.effective_spacing` — because the cost table shown to
the operator was already computing 3.75 s while the limiter paced at 3.6 s.

Consequence worth knowing: `profile_hash` covered the whole profile, `rate` included, so this change
invalidated resume for any run started before it. Nothing was stranded (both runs in the production
store are `zakonczony`), and the coupling itself is now gone — the hash was narrowed to the dialect
the same day, see the section above.

**A run in the production store carries the wrong `kind`.** The gate-2 report run
(`0a06df59-…`) is labelled `kind='firmy'` although all 300 of its records carry
`zrodlo='CEIDG_RAPORT'`. Current code sets `kind="raport"` correctly; the row was written by the
older code, before the phase-3 fix. **Export no longer cares** — it reads `zrodlo` from the records
(see the resolved findings below), so the workbook is correct without touching the database. What
still reads the label: `runy` shows it, and resume/batch logic could treat a report run as a `firmy`
run — the confusion phase 3 closed on the query side. The row was left as it is on purpose; the
owner chose the data-derived predicate over editing production data by hand.

## Long operations: silence is a defect (2026-09-06, gate-3 walk)

The owner walked the wizard, chose "update the database", and reported a progress bar that jumped
to 500/2891 and went quiet. The program was working correctly — and that is exactly the problem:
a process that looks hung gets killed. Nine defects came out of this, most of them the same shape.

**One root cause behind most of them.** The update loop did per *page* what the normal fetch path
does per *batch*, and a `/zmiana` page is 500 identifiers — up to a hundred detail requests, over
six minutes. The same off-by-a-layer error appeared three more times: the report loop counted
*matched* records instead of *scanned* rows (narrow criteria: one event per 287 000 rows), the
export path had no event channel at all (measured 453 records/s, so ~10 min of silence for a
voivodeship report), and the progress display was only ever stopped in `cli.py`'s `finally` — so in
the wizard a live `rich` bar survived the whole session and overwrote the closing summary, the
export question and the menu. That last one is what the owner actually saw: the program was not
silent, it was waiting for an answer nobody could see.

Fixed: progress and lock heartbeat per detail batch; a running counter instead of
`page_index * len(ids)` (which walked **backwards** on a short final page — 2891 changes would show
… 2500, then 2346); progress and heartbeat in the report loop driven by scanned rows; an event
channel in `exporter.py` with its own bar; `Events.close()` promoted into the protocol so every
operation ends its own display; `KeyboardInterrupt` handled in the report path; abandoned `w_toku`
runs no longer immortal in retention (report records with personal data used to stay forever); the
bar no longer prints the literal `None` as a total; a single source stream across export parts
instead of re-reading and re-normalising every earlier part; free-space check for CSV and JSONL, not
only xlsx; a corrupt cached report ZIP replaced instead of failing identically forever.

**`aktualizuj` now asks before starting.** The earlier objection — that the change count is only
known after the first page — was wrong: `/zmiana` carries `count` for the whole range, so one
`limit=1` request prices the work exactly as `count` does for `/firmy`. It follows the same sequence
as a fetch now: count → cost table → question → work. `--tak` takes the default and still runs
unattended. Plan and execution share `update_range`/`update_windows`, so the table cannot describe
different work than the run.

`write_csv` was the last of them: it called `source()` once per sheet, so `--formaty csv` read the
database and re-normalised every record four times, and with `xlsx` and `jsonl` a single export made
about seven full passes over SQLite. It now writes all sheets in one pass into temporary files
alongside the targets and renames them only after the whole write succeeded — each file appears
atomically, and while the *set* is not a transaction, no file is ever left cut in half.

A parallel test review found six of these independently and, more usefully, caught two fixes that
had **not** actually landed: a `touch_lock()` line lost to a crashed helper script, and
`events=deps.events` that `ruff format` had reflowed out of a patch. It also found tests passing for
the wrong reason — a report-row helper whose typo would silently add a 25th column, and
`ScriptedPrompter.confirm` doing `bool(answer)`, so `"nie"` read as **yes** and a test asserting
refusal was asserting acceptance. 635 offline tests.

## Three rate-limit items closed (2026-09-06)

All three were on the open list after the limiter measurement; the owner asked for all three.

- **The budget the server reports is now a brake, not a log line.** `X-Rate-Limit-Remaining`
  was read into a field nobody consulted. The limiter's own windows only count what passed
  through `request_log`, but the quota is charged to the **token** — a probe, a second machine
  or Postman spends it invisibly, and that header is the only evidence. When the server says
  fewer than `BUDGET_RESERVE_DEFAULT` (10) requests remain, the limiter holds until the
  server's own reset moment, under its own wait reason so the operator is told *why* the pause
  is long. It brakes only when **both** numbers are usable: without a reset time, guessing an
  hour of stall would be worse than taking one 429. The hold is clamped to the longest window,
  so a bogus or misread `X-Rate-Limit-Reset` (seconds vs milliseconds) cannot park the tool for
  a day. A client-level test proves the header reaches the limiter, not just the log.
- **The probes share the tool's request history.** `scripts/probe_support.py` gives all three
  probes the real `RateLimiter` over the real `request_log`, replacing their private counters
  and `time.sleep`. The mutual blindness is gone in both directions: the probe now paces around
  a running fetch, and the fetch sees the probe's requests. The warning that used to live in a
  docstring ("do not run this during a download") is a mechanism now. It reads `CEIDG_DATA_DIR`
  the way the tool does — a probe writing to the default directory while the tool works
  elsewhere would share nothing while claiming otherwise, which is worse than not sharing.
  Tests assert the behaviour (the tool actually waits because of what the probe spent), not
  just that a row was written.
- **`profile_hash` covers the dialect, not the pacing.** `rate` is excluded, so tuning the
  limiter no longer strands runs in progress — and pacing is what one tunes after a 429, which
  is exactly when there is something to resume. Two tests hold the line: a `rate` change must
  not move the hash, and six dialect fields must each still move it. `max_pages` and
  `max_url_length` are also safety knobs rather than dialect, but they stay in: every name
  removed from the hash permits resuming on a slightly different profile than the one that
  fetched, so the list grows only when there is a reason.

## Review findings resolved by the owner's decisions (2026-09-06)

- **"Is this a report export" is now one predicate, and it reads the data.**
  `store.record_sources(run_ids)` returns the `zrodlo` values actually stored with the records —
  a column with a `CHECK` constraint — and both the column hiding and `ExportSummary.kind` derive
  from it. Two consequences beyond tidiness: the two predicates can no longer disagree, and the
  mislabelled production run **needs no database edit** — its 300 records say `CEIDG_RAPORT`, so the
  export recognises it, hides the right columns and prints the right sentence about `link_ceidg`.
  A test reproduces exactly that shape (`kind='firmy'` on the run, `CEIDG_RAPORT` on every record);
  another pins that a mixed export hides nothing, because there the same columns may be filled by
  the API path. The old test passed for the wrong reason — its helper labelled the run `raport`
  while saving records as `CEIDG_API`, which is the same confusion the production row has.
- **`liczba_spolek` is empty rather than `0` for report rows.** The daily CSV knows nothing about
  civil partnerships, so counting an absent list produced "this firm has no civil partnership" —
  a sentence the source cannot say, and one that made `liczba_spolek = 0` match everything. On the
  API path an absent `spolki` key really does mean zero, because the API omits empty fields instead
  of sending `null`, so only the report source returns `None`. The column joins the hidden set.

## Phase 3f — the egress policy, and the acceptance criterion nobody had tested (2026-09-07)

§E lists five acceptance criteria. Four had evidence; the fifth — "brak połączeń do hostów spoza
listy dozwolonych (test z zaślepką DNS)" — had none, and the gap was not cosmetic.

**The hole.** `pipeline.build_deps` built `httpx.Client(verify=True, follow_redirects=False)`,
leaving `trust_env` at its httpx default. With no transport injected, that makes httpx read
`HTTPS_PROXY`/`ALL_PROXY` from the environment, so every request — `Authorization` header included,
and the token's payload carries a PESEL — would have gone to a host nobody compared against
`ALLOWED_HOSTS`. Both existing host checks (`apiprofile`, `client._checked_host`) read the **URL**,
so both passed while the socket went elsewhere. `SSL_CERT_FILE` could likewise replace the CA
bundle, which makes "TLS with certificate verification" mean "verified against whatever the
environment names".

**Why it survived.** The test seam was hiding it. `tests/support.py` built its own client with a
`MockTransport`, and httpx skips environment proxies whenever a transport is injected — so the one
production line that constructed a client had no coverage at all, and the thing that would have
exposed it was exactly the thing that suppressed it.

**What was built.** `ceidg_tool/httpclient.py` owns the single `build_http_client()`, and every
transport — test doubles included — is wrapped in `AllowedHostsTransport`, which refuses at the
layer where the socket opens. `build_deps` narrows the gate to the host of the *selected*
environment, so a `links.next` naming production cannot leave a run started against test. Boundary
rule 11 (only `httpclient.py` builds an `httpx.Client`) joined the AST scan with its own ten-case
self-test, and `tests/resilience/test_egress_allowlist.py` is the §E test: `socket.getaddrinfo`
stubbed to record and refuse, with a positive control so it cannot pass on an empty list.

**Three mechanisms, not one — the review's main finding.** The first version described the whole
change as `trust_env=False` and had one test for the wrong half. Environment proxies stop applying
because a transport is *always* injected; `trust_env=False` on the client is a second lock.
`SSL_CERT_FILE` is neutralised by `trust_env=False` passed to `httpx.HTTPTransport` — that single
value is the entire mechanism, and deleting it left all 654 tests green. It now has a test that
discriminates (with `trust_env=True` httpx raises `FileNotFoundError` at construction).

**The probes carried the same hole** through `urllib.request.urlopen`, which builds a `ProxyHandler`
from the environment by default. All three now share `probe_support.no_proxy_opener()`, with a test
— its correctness is subtle (`ProxyHandler({})` registers no handler and works only because
`build_opener` then skips the default one), which is the kind that a refactor breaks in silence.

**Consequence the owner should know:** on a network with a proxy or TLS interception the tool now
refuses to connect instead of routing the token through a middleman. That is what §B asks for. If it
ever has to change, it changes in `ceidg_tool/httpclient.py` and nowhere else.

Two smaller findings from the same reading, both fixed here:

- **`RateProfile.min_spacing_s` still defaulted to 3.6 s** while both shipped profiles carry 3.75 s.
  The tests pinned the shipped profiles, so a hand-written profile passed through `CEIDG_PROFILE`
  without a `rate:` block silently re-opened the burst the 2026-09-06 measurement closed (49 requests
  in the busiest 180 s window against an API limit of 50) — and `estimating.effective_spacing` would
  have gone back to disagreeing with the limiter, which is the exact drift the gate review found.
- **A test asserting the foreign-`links.next` refusal gained a second reason to pass.** Now that the
  fake API routes through `AllowedHostsTransport`, the same exception arrives whether the check runs
  before the limiter or in the transport. `assert clock.sleeps == []` restores the distinction;
  verified by mutation (with both `_checked_host` calls removed the run waits 3.75 s and the test
  goes red).

## Phase 3g — the last silent stretch: the report archive (2026-09-07)

`run_report_fetch` printed "Pobieram raport … kilkadziesiąt MB" and then said nothing until the whole
transfer finished. The archive is 21 MB (68 MB of CSV), and between `_acquire_lock` and the first
`store.touch_lock()` — a thousand CSV rows later — there was no heartbeat at all, while
`DEFAULT_LOCK_STALE_S` is 600 s and the client's retry ladder alone can burn 400 s restarting the
transfer from zero. So the database lock could expire under a process that was working: the same
defect phase 3e closed for `/zmiana` pages, one layer down, measured in bytes rather than requests.

`Events` gained `on_download`; `client.download_report` takes a progress callback invoked every
512 KiB (≈42 events for 21 MB, and at least one per 10 s even on a 50 kB/s link, with the first one
fired right after the headers). `pipeline` passes a callback that does **both** `store.touch_lock()`
and `events.on_download(...)` — the heartbeat rides the callback rather than the `Events` protocol
because touching the database belongs to `pipeline` alone under boundary rule 5. `ConsoleEvents`
renders it as a fourth bar in whole MiB, and a response without `Content-Length` keeps `?` as its
total rather than inventing one.

The tests pin the cadence from both sides (too few events is silence, one per 64 KiB chunk would be
336 events on a real report, each touching the database), that the first event is `(0, total)`, that
a cached archive reports **no** download at all, and that at least one heartbeat lands before the
first CSV row. `Recorder.progress_signals()` gained the new channel, and the two report-scan tests
moved to a narrower `scan_signals()` so download events cannot give them a second reason to be green.

## Decisions taken by the owner (2026-09-07)

- **The egress policy is accepted.** The owner approved it in session on 2026-09-07, knowing the
  consequence: the tool no longer honours `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` or
  `SSL_CERT_FILE`/`SSL_CERT_DIR`, so on a network with a proxy or TLS interception it fails to
  connect instead of routing a PESEL-bearing token through a middleman. This changes an existing
  behaviour: a machine that previously reached the API through a corporate proxy will now stop with
  a transport error, and that is the intended answer, not a regression to investigate. If it ever
  has to change it changes in `ceidg_tool/httpclient.py` and nowhere else — boundary rule 11 is what
  keeps that true. ADR-0010 decision 8(a) is therefore **accepted and shipped**; the rest of
  ADR-0010 (the `link_ceidg` enrichment itself, decisions 1-7) stays `proposed` and undecided.

## Two review rounds, and what the second one caught (2026-09-07)

Both rounds are worth recording, because the second one found that the first round's headline fix
was half a fix — the shape this project keeps meeting.

**Round 1 (egress).** No critical or high findings. The important one was MEDIUM: the change
described three different mechanisms as one `trust_env=False`, and the only one that actually does
work in production — `trust_env=False` passed to `httpx.HTTPTransport`, which is what stops
`SSL_CERT_FILE` replacing the CA bundle — had **no test**; deleting it left all 654 tests green. The
reviewer demonstrated that by running it. Environment proxies, it turns out, are neutralised by
always injecting a transport, not by `trust_env` on the client. Fixed with a test that discriminates
(with `trust_env=True` httpx raises `FileNotFoundError` at construction), and the attribution
corrected in four documents. Four low findings applied: the rule-11 scan did not see
`import httpx as hx`; `no_proxy_opener()` had no test; `test_next_link_to_foreign_host_is_refused`
had gained a second reason to pass; and three documents carried stale counts. The reviewer's open
question — should the gate be bound to the *selected* environment rather than to both hosts — was
answered by implementing it.

**Round 2 (download progress, and verification of round 1).** All five round-1 findings verified
closed against the code rather than against the summary. Three new MEDIUM findings, all applied:

- **The heartbeat rode on bytes arriving, so the retry ladder the fix named was still uncovered.**
  `download_progress` fires only after a 200 and only while bytes flow. A failure before the
  response headers skips it entirely, and the delay is spent inside `limiter.acquire`, which touches
  nothing: four failed attempts are up to 640 s against a 600 s lock. The fix is a decorator,
  `_LockHeartbeatEvents`, whose `on_wait` touches the lock before delegating — `on_wait` is
  announced *before* every sleep, so the beat precedes the pause, and `touch_lock` is conditioned on
  PID, so it is a no-op when this process holds no lock. That also closes the same gap in the list
  and detail loops, where it had been open since phase 2. Pinned by a test with a flaky transport
  and verified by mutation.
- **`raporty --pobierz` downloads the same 21 MB archive and was still silent.** It now passes the
  progress channel and stops the bar before the closing sentence.
- **`ConsoleEvents.on_download` was the only progress channel with no test of its own**, so
  `close()` forgetting the fourth task id was unpinned — exactly the defect that file exists for.
  The `close` test now covers all four ids and is named for it.

Two low findings from the same round: a sub-MiB archive rendered as `0/?`, turning a *known*
Content-Length into the question mark reserved for unknown ones (`math.ceil` now), and two documents
miscounted the rule-11 self-test in two different directions. The reviewer also asked whether
`Content-Length` could disagree with decoded bytes; it can, if the gateway ever enables
`Content-Encoding`, so the size is now reported as unknown in that case rather than letting the bar
pass 100 %.

## Phase 3h — masking was shape-specific, and a rule was library-specific (2026-09-07)

Both came out of designing phase 4 and neither waited for it: they were true of the shipped tree,
and both stop being free the day a second secret and a second HTTP library arrive.

**Masking knew one shape.** `config.mask_tokens` matched a JWT and nothing else, so the guarantee
CLAUDE.md and `docs/design/phase2_core.md` both state — the token stays out of logs, messages, the
database and output files — held for exactly one secret. `richtext.safe` and
`logsetup.MaskingFormatter` both call it, so both inherited the limit, and scenario 7 could not
notice because it planted only the CEIDG token.

Two mechanisms now, in order: **registered values**, then **known shapes**. The value registry is
what the review turned up as the sharper half — `inspect_token` explicitly supports an *opaque,
non-JWT* CEIDG token, and `tests/test_config.py` pins that path, so a pattern list alone would have
left "we mask every secret" meaning "we mask every secret whose shape we guessed". `load_settings`
registers the token it resolved; a value shorter than 12 characters is refused, because masking that
eats ordinary text is worse than none. Scenario 7 now plants every sample, greps the whole tree for
every sample, and asserts masking in all three channels the documents name — with a real
`LogRecord` through `MaskingFormatter`, because the first version called `mask_tokens` three times
under three names and would have stayed green if the formatter had been gutted.

**Rule 11 knew one library.** The scan matched the literal module name `httpx`, and `anthropic` 1.x
is built on `httpx2`. A second HTTP stack would have walked through a rule reporting itself closed —
the shape ADR-0009 found when the `cli.py` scan saw only `rich` while `typer.echo` was open. Modules
and factories are data now, the scan covers `scripts/` too (the probes carry the same production
token), and its self-test gained the second stack's three shapes.

One shape is deliberately **not** covered: `Anthropic(...)`, which builds a client internally.
It belongs to rule 12 in ADR-0011, which asks for something rule 11 cannot express — one owning
module plus explicit `api_key=` and `http_client=` — and which cannot be checked before that module
exists. Recorded in the scan and in the design document rather than left as a gap someone rediscovers.

**Review**: no critical or high findings; three medium, all applied. The most useful was that a
mutation only proves as much as the assertion it reddens: reverting the masking widening reddened a
test that called `mask_tokens` directly, which said nothing about the two other channels the claim
covered.

## Phase 4a — the pure layer of the assistant (2026-09-07)

ADR-0011 accepted; the half that needs neither a key, nor the network, nor the SDK is built and
fully tested. Nothing here talks to a model yet.

`ceidg_tool/assistant/` holds five modules, four of them under boundary rule 6 (no `rich`, no
`typer`, no `httpx`, and now no `httpx2` and no `anthropic` either — a pure module that imports the
SDK stops being runnable on an install without the optional extra, and "the wizard and the CLI work
without the assistant" has to be true at install time as well):

- `schema.py` — `AssistantAnswer` with `extra="forbid"`, the closed `OgraniczenieKod` enum, and a
  JSON schema post-processed to `additionalProperties: false` with every property required
  (pydantic produces neither). **`max_rekordow` and every transport-shaped field are absent**, which
  is what makes consent and the 50 000 split guard non-widenable — an absence, not a guard.
- `pkd.py` — loads the dictionary, refuses a key that is not already canonical, and rejects a code
  outside the classification *by name and vintage*. That message is the whole point: the probe
  measured that the API answers an unknown `pkd` with **204, not 400**, so without the dictionary a
  hallucinated code reads to the operator as an empty register.
- `prompt.py` — a deterministic, sorted system block (an unstable prefix is a cache that never
  reads) with today's date deliberately in the *user* turn, since a date in the prefix would sit in
  front of the whole dictionary and invalidate every request.
- `translate.py` — `AssistantAnswer` → `Criteria`, dictionary check first, then `criteria.py`. This
  is where "every PKD code and every parameter passes the validator" stops being a promise and
  becomes a call graph.
- `__init__.py` — the `Assistant` protocol and `AssistantResult`, which carries **(code, name from
  the local dictionary)** pairs. A wrong code is invisible to an operator; a wrong industry name is
  not.

**Boundary rule 13** joins the scan: `assistant/*` imports none of `client`, `store`, `pipeline`, so
§B's "fetched records never" is a missing edge in the import graph rather than care taken while
building a prompt. Rule 12 (one owner for the SDK client, always with explicit `api_key=` and
`http_client=`) is written down but cannot be enforced until `caller.py` exists — recorded rather
than left as a gap.

**The PKD dictionary is not in the tree, on purpose — and its vintage is 2025, not 2007.** The
owner asked why we were taking the older classification, and the measurement answered it: every
`rokPkd` the register returned is `2025` (11 occurrences, three files), while the entire case for
2007 was one hand-written line in `tests/conftest.py` that ADR-0011 had cited as if it were a
measurement of the API. `4933Z`, present in the production sample, does not exist in PKD 2007 at
all. The fixture is corrected, `docs/decisions.md` carries the measurement, and ADR-0011's finding
F7 is marked confirmed with its conclusion inverted. A list written from memory or lifted from a
summarised web page would pass all four
of the ADR's automated checks — canonical keys, entry count, agreement with codes the register
actually returned — while being quietly wrong in names nobody cross-checks. The GUS search is a JS
application, so there is no file to fetch programmatically. Instead `scripts/build_pkd.py` converts
an official export mechanically (no model in the loop), keeps only subclasses, refuses two different
names for one code, and writes the legal source, the build date and the source file's SHA-256 into
the generated header. Until it is built, `load_pkd` refuses with the exact command to run and
`tests/test_assistant_pkd_data.py` skips with the same sentence — a skip is more honest than green
when nothing was checked.

**Delivered 2026-09-07.** The owner downloaded the official GUS files; `StrukturaPKD2025.xls` is
genuine BIFF8, which `openpyxl` cannot read, so LibreOffice converted it — deliberately not Excel,
whose import heuristics turn `01.11.Z` into a date. **728 subclasses**, 87 divisions, `0111Z` …
`9900Z`, no truncated or duplicate names, legal basis Dz.U. 2024 poz. 1936 recorded in the generated
header alongside the source's SHA-256. All four content tests are now active and green (724 tests,
none skipped).

The sourcing-error risk the Gate was written against is **gone at the source**: the file came from
GUS, and all nine code+name pairs the register actually returned match it character for character.
The manual spot-check is therefore no longer owed — it existed to catch a list from the wrong place.

~~**A follow-up worth its own decision**: the 2007→2025 mapping file~~ — **answered by test run A5
(2026-09-07) and closed.** The model already translates `62.01.Z` into its PKD 2025 counterpart on
its own, so the dictionary's refusal path is never reached and no mapping table is needed. What
*was* missing is that it did so **silently**: the operator saw different codes than they typed and
was not told why. The new `KOD_PKD_Z_INNEGO_ROCZNIKA` limitation code says it out loud.
`PKD/KluczePKD_2007_2025.xls` is therefore no longer needed and can be deleted — an open item closed
by a run that cost a fraction of a grosz and no register request at all.

## Phase 4b — the caller, the second egress, and rule 12 (2026-09-07)

`assistant/caller.py` exists and is the only module that imports `anthropic`. Everything below was
**measured on the installed packages**, not carried over from `httpx` 0.28 — ADR-0011 named that
assumption as risk number one:

- `anthropic` 1.4.0 pulls in **`httpx2` 2.12.0**, exactly the second HTTP stack finding F9 predicted.
- `httpx2.Client` and `httpx2.HTTPTransport` take the same `transport` / `trust_env` /
  `follow_redirects` arguments, and `trust_env=False` on the transport blocks `SSL_CERT_FILE` there
  too — checked by constructing it both ways against a non-existent CA file.
- **`httpx2` resolves names through `socket.getaddrinfo`**, so the suite-wide DNS guard in
  `tests/conftest.py` covers model traffic. That was finding F3, open on an assumption; it is now a
  measurement.

`httpclient.py` grew `build_model_http_client` and the host rule moved into one pure
`refuse_foreign_host` shared by both stacks — this project has twice refused to keep two copies of a
security-critical rule. The two allowlists are never unioned, and a test proves the model client
refuses `dane.biznes.gov.pl`.

**Rule 12 is enforced, and its second half is the one that matters.** "Who builds the SDK client" is
easy; "with what" is where the risk lives. Without an explicit `api_key=` the SDK walks its own
credential chain — env vars, then an `ant auth login` profile on disk — so the tool would spend a
credential nobody gave it and `sprawdz-token` could not describe it. Without `http_client=` it builds
its own transport with `trust_env` at the default, i.e. outside the gate, which is precisely what
`pipeline` did for CEIDG until phase 3f. The scan asserts both keywords are present, rejects
`Anthropic(**kwargs)` deliberately, and ships with a six-shape self-test. Verified by mutation:
deleting `http_client=` from `caller.py` turns it red.

**The caller's shape.** One request per attempt, at most two per interpretation (one repair round
trip over the identical cached prefix). Streaming, because arriving tokens *are* the liveness signal
over a 13 400-token prompt — `Events` gained `on_model` as its sixth channel, fired once before the
first token. `max_retries=0` on the SDK plus our own single retry, so a 429 is **announced** through
`on_wait(REASON_MODEL_RETRY)` rather than spent silently inside the SDK. Timeout is 90 s, not the
SDK's ten-minute default, which is not an interactive number. Every failure degrades to "questions
one by one" rather than blocking the operator.

Twelve tests run the real SDK code over an `httpx2.MockTransport`, so the seam does not travel a
different path than production — the lesson phase 3f paid for. One of them asserts §B **on the
bytes**: the request body consists only of parts we know, the user turn matches the two-substitution
template exactly, and a planted key, `Bearer`, a NIP, a database path and a data directory are all
absent. That test caught a false positive on itself — `nip` and `regon` are *schema field names* and
must be there — which is why the assertion is structural rather than keyword-fishing.

## Phase 4c — the second credential (2026-09-07)

The owner's key now has somewhere to live, and it lives there the way the CEIDG token does.

`Settings.anthropic_key` (`repr=False`) resolves **keyring → `ANTHROPIC_API_KEY` → `.env`** and
`load_settings` registers its value with `mask_tokens`, so it is masked by value as well as by
shape — the opaque-token lesson from phase 3h applies to any secret, not just to one that failed to
match a regex. `token zapisz --asystent` and `token usun --asystent` share the keyring helpers with
the CEIDG token (one `username` parameter, not a second copy of the code), and `sprawdz-token` gains
one line: source and fingerprint, **never the value**.

**One difference from the CEIDG token, and it is the point.** A missing token stops the program
before the first request; a missing key merely turns the assistant off. The instruction requires the
wizard and the CLI to work without the assistant, so absence is a normal state with a sentence
attached ("brak klucza — asystent wyłączony"), not an error.

**The tool never consults the SDK's own credential chain** — not `ANTHROPIC_AUTH_TOKEN`, not an
`ant auth login` profile on disk, not the federation variables. Boundary rule 12 forces an explicit
`api_key=` on the SDK side; a test pins that our own resolution ignores that chain too. Otherwise the
tool would spend a credential nobody gave it, and `sprawdz-token` could not describe it.

**A signature change with a wide blast radius, caught by the suite.** Generalising
`read_token_from_keyring` to take the keyring entry name broke every CLI test at once — four test
fixtures stubbed it with a zero-argument lambda. That is the suite doing its job: 28 red tests named
the seam immediately, and the fix is one character (`lambda *_: None`) plus a comment saying why.

## Phase 4d — the wizard wiring, and a review that blocked (2026-09-07)

The review of slice 2 **blocked** on four high findings, three of them ADR-0011 Gate items the ADR
itself named as merge preconditions. All are closed:

- **CI never installed the `asystent` extra**, so `tests/test_assistant_caller.py` could not even
  collect on any matrix leg — the "748 passing" figure was a local fact, not a CI one, and boundary
  rules 11 and 12 were scanning an import graph without the second stack in it.
- **`requirements.lock` was never regenerated.** §B requires pinned dependencies and the unpinned
  half was the one that opens a socket with a spendable credential on it. It now carries both HTTP
  stacks, which is also the evidence the ADR wanted a reader to see.
- **The model client's egress guarantees were held by a docstring.** Every test injected a mock
  transport, so `httpx2.HTTPTransport(verify=True, trust_env=False)` — the single mechanism behind
  §B's TLS half on the second stack — was never constructed. That is the *exact* shape phase 3f
  closed for CEIDG. Six new tests build the real transport, including the discriminating
  `SSL_CERT_FILE` one (verified by mutation) and the missing half of the cross-gate pair: the CEIDG
  client refusing `api.anthropic.com`, i.e. the direction fetched records would take.
- **`max_tokens=2048` would have truncated every real call.** On Opus 5 thinking is on by default
  and effort defaults to `high`, and thinking tokens count against `max_tokens`. The ceiling would
  have been consumed before the JSON was emitted, surfacing as "that is not valid JSON" — a message
  naming the wrong cause — then burning the repair attempt on an identical ceiling. No offline test
  could see it, because a mock hands back a complete object. Now `effort: "low"` (which ADR-0011's
  cost table had already assumed), a ceiling with room for thinking, and `stop_reason` inspected so
  truncation has its own sentence.

Three of the mediums were the same lesson at different scales: **the promised bound of two requests
per interpretation was four** (retry × repair, each proved in isolation, their product untested);
**`on_model` fired only on text deltas**, so the thinking phase — most of the latency — was silent
in the one place CLAUDE.md calls a defect; and **the SDK wraps our own gate refusal in
`APIConnectionError`**, so a deliberate refusal was reported as "no connection" and pointlessly
retried. Also fixed: the SDK's loggers now bypass neither `MaskingFormatter` nor a live progress
bar, three SDK exception classes no longer escape as tracebacks, and `retry-after` is honoured.

**The wizard wiring lands with it**, which is what makes two shipped sentences true — the review
caught that `assistant_key_saved()` promised a wizard behaviour that did not exist yet. Now:
`collect_criteria` asks for a description first and an empty answer falls through to today's eight
questions unchanged; `pobierz --opis` shares that implementation through
`flow.collect_from_description`; the confirmation question has `safe_default=False`, so `--tak`
refuses rather than acting on an interpretation nobody read; and **the first screen's destination row
split in two** — that row is a §A acceptance criterion and would have become false the moment the
assistant was used.

The step sits **upstream** of `prepare_fetch`, and a test pins the consequence: a confirmed
interpretation spends **zero** CEIDG requests, so the "exactly one `count`" invariant, the cost
table and the consent path are untouched by construction.

## Phase 4e — test runs A and B: what the real model showed (2026-09-07)

`docs/test-runs-phase4.md` is the runbook: five groups, ordered cheapest-and-most-invalidating
first. The ordering carries one real insight — the PKD vintage probe (group C) must precede gate 3
(group D), because if the `pkd` parameter turns out to index PKD 2007 the shipped dictionary is
wrong and a PKD-filtered walk would return something meaningless.

**Groups A and B are run.** Both cost **zero CEIDG requests**, so neither needed consent for
personal data. What makes that possible is structural: the assistant sits upstream of `count`, so
answering "wróć do menu" at the interpretation screen ends the walk before a single register
request. Groups C, D and E remain, and so does B4.

**Group A — eight sentences, and the review's prediction confirmed.** Every response was complete
JSON, none truncated: the `effort: "low"` + `MAX_TOKENS` fix works against the real API, where
`max_tokens=2048` would have spent the ceiling on thinking and surfaced as "that is not valid JSON".
No offline test could have seen that — a mock hands back a finished object. Prompt caching also
works: **24 854 tokens read from cache** on every call after the first, which is the difference
between roughly 9 gr and 1 gr per question. The two dangerous cases came back clean: no invented
revenue filter (`BRAK_DANYCH_FINANSOWYCH` stated instead), and no record cap, because the schema has
no such field and resilience scenario 9 depends on that absence.

Three findings, none of them a code defect and all three invisible to any offline test:

- **The model already translates PKD 2007 → 2025 by itself** — `62.01.Z` came back as `6210A`/
  `6210B` with correct names, so the dictionary's refusal path is never reached. It did so
  **silently**, which is the part that mattered: the operator saw codes they had not typed and was
  not told why. Fixed with a new limitation code, `KOD_PKD_Z_INNEGO_ROCZNIKA`, using the
  architecture's own mechanism — the model picks the code, `ui/texts.py` authors the sentence. This
  also closed the open item about building a mapping table from `KluczePKD_2007_2025.xls`: the
  capability exists without it.
- **The same sentence produced materially different queries on different runs** — four construction
  codes in A1, fifteen (a whole division) in A7. Repeated `pkd=` means those are different queries
  with different counts and costs. One prompt instruction ("the narrowest set, never all subclasses
  of a division") with the *reason* attached brought the spread from 4-15 down to 2-4. Run-to-run
  variation remains and cannot be closed by a prompt; what makes it acceptable is that the cost
  table prices the query before anything is fetched.
- **The model added a filter nobody asked for** (`status: AKTYWNY` for hairdressers). Visible on the
  confirmation screen — the control working as designed — and it stopped happening after the
  narrowness instruction. One observation, not a proof.

**A trap closed on the way.** Nothing pinned the limitation codes to their texts, so the new code
could have failed three silent ways: no description for the model (it never learns the code exists),
no sentence for the operator (`interpretation` skips unknown codes, so a limitation the model *did*
notice is swallowed), or a sentence with no code. Three key sets are now pinned equal by test and
the prompt's limitation list is **generated from the enum** rather than transcribed.

**Group B — five of six, and two defects, both in messages.** Run in a sandbox directory with its
own `.env`, because `DEFAULT_ENV_FILE` is relative to the working directory and the real file holds
two secrets. B1 (no key), B2 (rejected key — exit 3, and the key value appears nowhere under the
data directory, log included) and B3 (`ANTHROPIC_BASE_URL` pointed at a foreign host — refused in
2.2 s, i.e. before any network, naming the allowed host rather than reporting "no connection")
passed as written. B4 needs the network adapter actually disabled and stays with the owner.

- **B5 — a model call spent to learn something already known.** `--opis` with `--tak` cannot be
  confirmed: the confirmation question carries `safe_default=False`, so the refusal is certain from
  the first line of the command. The program nevertheless asked the model and rendered an
  interpretation first — 7.4 s and a paid call before an inevitable refusal, the same mistake as
  sending a request for a NIP whose checksum fails locally. The guard moved up into `cli.pobierz`,
  where consent-shaped decisions already live; now 2.2 s and no call, and the message says what to
  do instead.
- **B6 — a message that named the wrong cause.** With the dictionary moved aside the tool said
  "brak klucza API albo pakietu `anthropic`", pointing at two things that were both present:
  `_build_assistant` caught `CeidgError` and returned a bare `None`, so the specific reason died
  there. `Deps` now carries `assistant_reason`, and `load_pkd`'s own sentence — file, rebuild
  command, GUS source — reaches the screen. The generic sentence stays, but only where the reason
  really is unknown.

Both are pinned by tests: one asserts the model is never called when `--tak` is present, the other
that a known reason reaches the operator instead of the default.

## Phase 4f — group C, and the coverage gap it uncovered (2026-09-07)

Three production requests, counts only, with the owner's consent given in session. This was the item
the runbook insisted had to precede gate 3.

| `pkd=` | Exists in | `count` |
|---|---|---|
| `6201Z` | PKD 2007 only | 234 605 |
| `6210B` | PKD 2025 only | 142 294 |
| `4933Z` | PKD 2025 only (positive control) | 31 402 |

**The run missed the pass criterion the runbook wrote for it, and following that miss to the end is
what made group C worth its three requests.** The criterion was `6201Z` = 0; it returned 234 605. It
had been written on an unstated either/or assumption — that the parameter indexes one vintage. The
filter in fact matches the code **as stored on the record**, and the transition from PKD 2007 runs
to 31.12.2026, so each record carries one vintage and the two result sets are disjoint.

**The decisive measurement cost nothing.** `probe_out/raport_sample.zip`, the 21 MB production report
already on disk, carries `RokPKD` per record. Across **285 026 real records**: 58.6 % still carry PKD
2007 codes; 57 % of those happen to be reachable anyway because their code string is unchanged
between vintages; and **24 494 records — 8.6 % of the sample — cannot be reached by any code in
`pkd2025.yaml`** (corrected 2026-09-08; the figure previously given here, 71 817 = 25.2 %, counts
records whose *main* code is absent, which is a different question — `pkd=` matches any of a
record's codes, measured at zero requests from the operator's own store. The sample is one
voivodeship, not "the register"). The most frequent unreachable codes are ordinary trades: `9602Z` hairdressing
(6 811), `4520Z` vehicle repair (5 912), `4120Z` building construction (4 684), `4339Z` finishing
work (4 248), `6201Z` programming (3 865).

**This blocks gate 3, and it would have blocked it invisibly.** The runbook's walk sentence is
*"salony fryzjerskie w Łomży"* and `9602Z` is the most frequent unreachable code there is. The
assistant answers `9621Z`, the register returns only migrated salons, and nothing anywhere in the
path reports an error. That is the same failure shape phase 4a avoided when it refused to write a
PKD dictionary from memory — a confident, complete-looking answer that is quietly wrong — arriving
from the opposite direction.

**What I got wrong earlier the same day, recorded because the correction is the point.** I wrote
that the dictionary was "confirmed right" and that no code needed to change, and I offered two
explanations for the both-vintages result as though they were exhaustive. A code review named a
third — per-record vintages — and pointed at `normalizer.py`, which has described `rokPkd` as "2007
lub 2025" all along, and at the transition period already recorded in `docs/decisions.md`. It was
right. My proposed discriminating request (`6210A` ≈ 92 000 under translation) was not needed: the
report gives `6210A`:`6210B` ≈ 1:6.3, scaling to ≈ 22 600, which falsifies translation outright.

**The remedy exists and was closed on a false premise.** `PKD/KluczePKD_2007_2025.xls`, the official
GUS transition key, is downloaded and unused. The item about building a mapping table was closed on
2026-09-07 because "the model already translates 2007 → 2025". That is the wrong direction: the gap
is a **2025** code failing to reach **2007**-coded records, so what is needed is the reverse
expansion. Reopened, and it is a design decision rather than a chore, because each added code is
another `pkd=` parameter in the **same** URL — so it costs no request of its own, only a wider
`count` that the existing cost table already prices. The decision is about ambiguity: 209 of the 264
expansions merge industries the operator asked to distinguish. Designed in **ADR-0012** and built the same day (accepted 2026-09-07).

No code was changed at this point — the shape of the fix was the owner's call, taken the same day. Built in phase 4h below.

## Phase 4g — ADR-0012, and an assumption nobody had tested (2026-09-07)

The coverage gap from phase 4f went to the architect. **ADR-0012** (`docs/adr/`, accepted by the owner the same day)
picks option E of five: apply the GUS transition key **once, in the input layer**, materialise the
result into `Criteria` as a separate `pkd_2007` field, expand silently where the expansion is clean
(55 codes), and where it is ambiguous (209) spend a second `count` and let the operator choose with
both populations priced. `assistant/` is untouched — the model keeps choosing from PKD 2025 only.

**Two corrections to what phase 4f recorded**, both from reading the code rather than reasoning
about it:

- **Expansion does not multiply requests.** Every `pkd` value goes into the **same** URL as a
  repeated parameter (`criteria.py`), so an added code costs no request of its own — it widens
  `count`, hence pages, which `estimating.estimate` already prices. The decision is about ambiguity,
  not about the request budget. The earlier wording invited the opposite reading.
- **The invariant is restated, not weakened**: at most two `count` requests before consent, one per
  candidate population, and none after the choice. The test keeps counting requests.

**The ADR's own gate item was the find of the phase.** Nothing had ever measured whether repeated
`pkd=` is OR-ed — yet the tool has been sending multi-code PKD queries since phase 4d, and the
assistant returned four codes in run A1 and fifteen in A7. No group-A run reached a fetch, so the
assumption had never touched the API. Under AND every such query would have returned nothing and the
operator would have read *"brak firm"*.

Three more requests settled it: `9621Z` = 38 201, `9602Z` = 187 149, both together = **225 350** —
the exact sum. Repeated `pkd=` is OR-ed, so options C/D/E stand and today's behaviour is sound. The
exactness also proves the vintages disjoint from the API side, confirming the `RokPKD` finding with
a second instrument, and it replaces the regional estimate with a national one: **a PKD 2025
hairdressing query reaches 17 % of hairdressers**, against the 25 % the voivodeship snapshot
suggested.

Gate item 2 is closed too: the transition key already carries PKD 2007 names (column
`Nazwa grupowania PKD 2007`, 656 subclasses, none empty or truncated, all 159 codes the
expansion needs among them), so no second GUS download is required and the confirmation
screen's names come from the same official source as everything else. **Only owner approval
remains** — and it was given the same day, so the ADR is accepted and phase 4h below
is its implementation.

## Phase 4h — ADR-0012 built (2026-09-07)

Accepted by the owner and implemented the same day. What it does, from the operator's side: a
PKD-filtered query that would silently miss un-migrated records now either covers them or says it
does not, and where covering them would drag in a neighbouring trade, the operator chooses with
both populations counted.

| Piece | What it is |
|---|---|
| `scripts/build_pkd_transition.py` | Generator over the official GUS key. Provenance header: source name, SHA-256, legal basis, `Wygasa: 2026-12-31`. Refuses to build if any PKD 2007 code lacks a name — the confirmation screen shows names, and a nameless code is not shippable |
| `ceidg_tool/data/pkd2007_2025.yaml` | Generated: 357 PKD 2025 codes with predecessors, 283 PKD 2007 codes, all with names |
| `ceidg_tool/pkdmap.py` | Pure (boundary rule 6). Computes the clean/ambiguous split **from the data** at load time (51 / 306), so it is a property of the file rather than a number in a comment |
| `Criteria.pkd_2007` | Separate from `pkd` so `describe()` can say "you asked for `9621Z`; I am also sending `9602Z`" instead of presenting our addition as the operator's choice |
| `ui/flow.py` | Candidate pair, dual-fingerprint resume, the vintage step |
| `--pkd-2007 / --bez-pkd-2007` | Explicit choice for scripted runs |

**Three things worth recording, because each was a decision rather than a transcription:**

- **The ambiguity is context-sensitive, and the implementation uses that.** `9602Z` is ambiguous
  for a hairdressing query because it also reaches beauty — but an operator who asked for *both*
  `9621Z` and `9622Z` loses nothing by adding it, so no question is asked. `rozszerz()` computes
  "also reaches" against the **selected set**, not against the whole table. The same is true of
  `6210A`+`6210B`, which is what a software query looks like.
- **Old fingerprints had to survive.** `model_dump` includes every field, so adding `pkd_2007`
  would have changed the fingerprint of every query ever run and made interrupted fetches
  unresumable. `canonical_json` drops the field when empty; verified by fingerprinting a criteria
  before and after the change (`aafa94e6b1da6a80`, unchanged). Wide still differs from narrow,
  which is what resume needs.
- **`--tak` costs one request, not two.** With the question predetermined by the default, spending
  a second `count` would buy knowledge we already have — the phase-4e B5 defect exactly. The guard
  sits in `cli.pobierz`, where consent-shaped decisions live, and a single sentence from
  `ui/texts.py` names the codes that were skipped. It fires only when an expansion actually
  existed, so it is not a warning that is always true.

Measured against the ADR's invariant: two `count` requests when the question is asked, one in every
other branch including both flags, and none after the choice.

**The code review blocked this and was right to.** Five findings applied, the first two of them
real defects in behaviour rather than in wording:

- **A clean expansion orphaned the operator's own fingerprint.** Resume was looked up for the two
  candidates but never for the pre-expansion criteria — which is exactly what `--tak` and
  `--bez-pkd-2007` store. A scheduled fetch killed mid-page was invisible to an interactive
  re-entry with the same `--pkd`, so the operator restarted from zero with the work in the
  database. No version skew needed; both existing resume tests used `9621Z`, whose expansion is
  purely ambiguous, so the gap never showed. Three candidates are now looked up.
- **The generator was dropping 230 mappings for a reason that was false**, leaving 93 PKD 2025
  codes with no predecessor at all — fitness clubs, bakeries, vegetable growing. See
  `docs/decisions.md`; the table is rebuilt and those codes now expand as a second kind of
  ambiguity, with their own sentence on the confirmation screen.
- **`--bez-pkd-2007` was silently ignored** when the query file already carried `pkd_2007`: the
  tool fetched the wide population while the operator asked for the narrow one. It now refuses the
  contradiction by name. `--tak` alone does *not* conflict — a saved choice is the operator's
  decision and beats the non-interactive default.
- **The name guard was one-sided.** A missing PKD 2025 name would have reached the screen as a bare
  code, the failure the 2007 guard exists to prevent. Both sides now raise.
- **`hints_block` taught `62.01.Z` as the PKD example** — a code absent from PKD 2025 that the
  assistant refuses. The wizard was teaching the operator a code its own tool rejects.

## Phase 4i — verification without the owner (2026-09-07)

The owner asked for everything that does not need them to be done alone. The test API host settles
the boundary: `test-dane.biznes.gov.pl` resolves but **times out at TCP level after 8 s**, while
production connects in 0.03 s — re-measured today. So groups D and E cannot run against the test
environment, and production needs consent for personal data. Everything below cost **zero CEIDG
requests**. Full write-up in `docs/test-runs-phase4.md` (group F).

**Rendering found a defect the view models could not.** Every vintage screen was rendered through
the real `rich` path for the first time. For codes whose PKD 2007 and 2025 names are identical
(`1086Z`), the "what it means today" line repeated the column beside it word for word and read as a
bug in the program. The `Block` was correct, which is exactly why no assertion caught it. Fixed with
a second phrasing and pinned by a test that reads rendered text rather than fields.

**Four real model calls confirmed the feature is not theoretical.** *Salony fryzjerskie*, *kluby
fitness*, *piekarnie* and *warsztaty samochodowe* all produce codes with PKD 2007 predecessors, so
all four reach the new screen. The last one exercises the context-sensitivity against real model
output: the model chose two of the three repair subclasses, so only the third is named as coming
along. Three of these four trades are the ones the code review found orphaned by the generator's
dropped mappings — before that fix they would have got no expansion and no screen at all.

**Three more stale hints, all teaching a code the tool refuses.** `hints_block`, the wizard's PKD
question and `--pkd`'s typer help all offered `62.01.Z` as *the* example — absent from PKD 2025 and
rejected by `assistant/pkd.validate_codes`. Found by reading the rendered help, not the source.

Also verified end to end on real artefacts: `Metadane` records which population was fetched, in both
the human sentence and `kryteria_json` (read back with `openpyxl`); and `pobierz -z file
--bez-pkd-2007` refuses the contradiction with exit 3 and a Polish sentence before any request.

854 offline tests, 66 of them resilience. All four gates green.

## Phase 4j — the assistant looked hung, and two channels were at fault (2026-09-07)

Found by the owner on the first real gate-3 walk: after typing the sentence, the screen showed
`Asystent (tokeny) 72/?` and nothing moved. Three defects, all in the same family the project calls
a defect by name.

- **The token counter stood still through the thinking phase.** `caller._stream` counted only
  `text_delta` characters, and Opus 5's thinking emits `thinking_delta` — which is most of the
  latency. Phase 4d had fixed `on_model` to *fire* during thinking, but with an unchanging value,
  which on screen is indistinguishable from a hang. Thinking characters now count too.
- **Nothing on the bar moved by itself.** With `total=None`, `TimeRemainingColumn` renders nothing,
  so the only moving part was the frozen counter. A `TimeElapsedColumn` now sits beside it —
  seconds always advance, whatever the model is doing. It benefits the report download too, whose
  total is unknown under `Content-Encoding`.
- **The bar was never closed before the interpretation was printed**, so a live `rich` display
  would have overwritten the interpretation screen and the confirmation question. This is exactly
  the defect gate 3 caught on 2026-09-06 on the fetch path, which taught `pipeline` to close the
  bar with the operation. The assistant is a sixth progress channel and arrived after that fix, so
  it never inherited the lesson. `collect_from_description` now closes in a `finally`, and a test
  pins the **ordering**, not merely the call.

Also added: one sentence before the call setting the expectation ("zwykle trwa to kilkanaście
sekund… do rejestru nie idzie jeszcze żadne zapytanie"), authored in `ui/texts.py` as always.

Two tests had to change and both changed for the right reason. The phase-4d test asserted "progress
fires during thinking" via the proxy `tokens == 0`, which my fix invalidates — it now asserts the
count actually **rises** and never goes backwards. And `flow_deps`, a deliberately minimal double
whose docstring says it exists to fail when the flow starts reading something new, failed exactly
as designed when the flow began closing the bar.

856 offline tests, all four gates green.

## Gate 3, first walk with the assistant — a finished file, on the path that tests least (2026-09-07)

The owner walked the wizard on production after the phase-4j progress fixes. **A finished workbook
was produced**, which is the headline criterion. Verified from the artefacts rather than from the
report: 56 rows over 43 columns, four sheets, `Firmy` leading with `nip`/`nazwa`, tables with
autofilters on all three data sheets, freeze at `C2`, `Slownik` covering every column, and
`Metadane` carrying purpose, run id, counts, source and the hidden-column list (`pkd_nazwa` among
them, correctly, and correctly hidden).

**Total cost: 3 CEIDG requests** — two `raporty`, one `raport`. And that is the finding: the
assistant supplied `wojewodztwo: podlaskie`, so `report_covers` matched and the walk took the
**report path**. Consequences, all by design and all skipping what gate 3 exists to measure:

- **No `count` request was made at all** (the log has no `firmy` call for this run), so the **cost
  table never appeared**. That table is the §A acceptance criterion and the centre of the consent
  model.
- The API list/details path, batching and the rate limiter under load were not exercised.
- `link_ceidg` is empty, with twelve other columns, because the report has no record id.
- **The vintage question was asked without numbers**, which is the half of ADR-0012 that turns
  disclosure into control. Narrow was chosen (no `pkd_2007` in the stored criteria).

**What narrow cost, measured locally at zero requests** from the very archive the run downloaded:
Łomża has **214** hairdressers in that report — 56 coded `9621Z` (PKD 2025) and **158 coded
`9602Z`** (PKD 2007). The workbook holds **26 %** of them. The 56 in the workbook match the 56 in
the archive exactly, so the local filter is right; the gap is entirely the vintage choice.

`aktualizuj` and `eksportuj` were not run (no `zmiana` run today, one workbook).

### Finding: on the report path the wide choice is free, and the tool does not say so with numbers

The regional CSV is downloaded and filtered locally either way, so choosing *szerzej* costs **zero**
extra requests — the question even says so. But the default is narrow and the screen carries no
counts, because they are unknown *before* the download. After it they are exact and free: the tool
could report both populations from the archive it already holds. That would turn the report path's
weakest disclosure into its strongest, and it is the one place where "measured, not estimated" can
be had for nothing. Not built — it is a design decision, and it belongs to the owner.

## Phase 5 — the identifier had two identities, and nothing could see it (2026-09-08)

Found by reading the log of the owner's overnight run rather than by a failure. `aktualizuj`
started 2026-09-07 19:09 and ended 2026-09-08 09:01 — 13 h 52 min of wall clock for what the cost
table had priced at 2 h 49 min. **The estimate was right**: `UpdatePlan.requests` predicted 2 708
requests, 2 681 went out, and the work took 3 h 02 min. The remaining ten hours were two gaps,
1 h and 9 h 50 min, each ending in a `getaddrinfo` failure — the machine sleeping, not the tool
hanging; the retry ladder picked itself up on wake and the run completed.

**And the run delivered nothing.** Every one of its 13 401 records was `detail_state='brak'`,
while the details for all 13 401 sat in the same database, complete, under the uppercase spelling
of the same identifier. Measured before the fix: 31 860 rows describing 16 310 entries, 15 550 of
them present twice, 15 550 orphan rows — which is exactly the set `purge_older_than` deletes, so
the next `ceidg-tool wyczysc` would have destroyed the night's work.

The mechanism is in ADR-0013. What belongs here is the pattern, because it is the third instance:
**the evidence had been sanitised of the property that mattered.** `scripts/anonymize_samples.py`
uppercased every identifier while building fixtures, so `tests/fixtures/zmiana.json` carried a
spelling `/zmiana` has never returned; and the `/firma` doubles echoed back the identifier they
were asked for, which is the one thing the register does not do. 768 offline tests agreed about a
world that does not exist. After both were corrected the fixture alone still did not go red —
proof that no test had ever walked `/zmiana` → `/firma` through the store.

Built: `recordid.py` (canonical form, hex GUIDs only — `/raporty` identifiers share the shape but
are not hex and are case-significant), canonicalisation in `client._records_of`, `KanonicznyId`
through `client` and `store`, boundary rule 14, schema v3 merge migration reported through
`Deps.warnings`, and `tests/fixtures/api_traits.yaml` with `tests/test_api_traits.py` — the claim
stored apart from the sample, audited against `probe_out/` when the raw samples are present.

**Two defects the tester found while covering it**, both applied:

- **`run_update` lost the `nieznaleziony` state.** `save_details` ran before `link_ids`, and on the
  `/zmiana` path the `firma` row does not exist until `link_ids` — so
  `UPDATE … SET detail_state='nieznaleziony'` matched no row and vanished. The entry stayed `brak`,
  returned to `pending_detail_ids` on every later run and was bought again. Same silent-cost family
  as the identifier defect, approached from the other side. `link_ids` now runs before the details.
- The migration ran in **64 s** on the operator's database, and the cause was not the merge:
  `run_firma.firma_id` is the child half of a foreign key while the primary key leads with
  `run_id`, so every `DELETE FROM firma` scanned the whole link table looking for cascades.
  Measured with and without the index: **66.28 s → 0.08 s**. `wyczysc` had been paying the same
  toll unmeasured. Two earlier hypotheses (blob rewriting, WAL) were tested and both were wrong —
  the phase timings named the statement, which is why the third attempt was the right one.

Verified on a copy of the production store: 31 860 → 16 310 rows, 17 058 links unchanged, 15 955
detail records unchanged, **13 401 records of the overnight run recovered**, zero orphans,
`integrity_check` ok, `foreign_key_check` clean, 1.12 s, and a second open changes nothing.

## Phase 5b — three guarantees that had no observer (2026-09-08)

ADR-0013 named the shape the identifier defect shares with two others found in the same
overnight log: **a guarantee whose violation has no observer.** All three are now closed, and
the anonymiser — the fourth instance, which erased the evidence for the first — was closed with
phase 5.

- **The lock lease.** `touch_lock()` updated `WHERE environment = ? AND pid = ?` and threw the
  `rowcount` away. The row is keyed by environment, so zero updated rows means exactly one thing:
  another process holds this lock. It now returns `bool`, and `pipeline.LockHeartbeat` reacts —
  re-acquiring when the lease merely lapsed, and raising `LockLostError` (a `ResumableError`, so
  the checkpoint stands and the operator gets `wznow`) when someone really took it. The tool has
  warned the process that *takes* a live lock with `--force` since phase 3b; the victim was never
  told. Same mechanism, other half.
- **A sleeping machine.** The same heartbeat measures the wall-clock jump between beats and names
  a gap longer than the lock's stale threshold for what it is. A frozen process cannot beat, so a
  9 h 50 min suspend expires a 600 s lease under a working program — and it explains, in one
  sentence, the wrecked finish time, the empty request history and the expired lock at once.
- **Waiting left no trace.** `on_wait` reached the screen only, and `ConsoleEvents` suppresses
  anything under 5 s, so the log of 2026-09-07/08 held two holes that could not be told apart —
  from each other, or from a limiter hold. Both turned out to be the machine sleeping (the budget
  brake fires only at `remaining <= 10`, and the last request before the gap reported 214), but
  settling that took a *different* line of the log, which is the point. `_LogEvents` sits
  at the composition seam beside `_LockHeartbeatEvents` and records every wait with its
  **predicted resume time**, which is what makes a gap computable rather than merely visible.
  `on_message` goes through `safetext.strip_control` first: the log file is a second
  terminal-bound channel, and boundary rule 10 knows only about `rich`.
- **The summary had no reference point.** `update_summary` printed *"Zmienionych wpisów: 13401,
  szczegółów: 0."* after three hours and 2 681 requests. The number was right and had nothing to
  be compared against, so "0" read as "nothing needed adding" and meant "all of it was lost".
  `RunResult.unresolved` counts the run's entries still at `brak` — neither fetched, nor missing,
  nor failed — and the sentence says so when it is not zero (on the `pobierz --szczegoly` path too,
  through `ExecuteResult.notes`; it was computed and read by nobody there, which is the same shape
  appearing inside its own fix).

**The review blocked this phase, and its sharpest finding was about the evidence, not the code.**
A single limiter wait can outlast the lock — the budget brake clamps to the longest window (3600 s)
against a 600 s lease — so `RateLimiter` now sleeps in `WAIT_SLICE_S` slices with a heartbeat before
each. But two of the new assertions could not fail: `pytest.approx` on an epoch timestamp (~1.7e9)
carries a **relative** tolerance of ±1700 s, so the test named "the beat precedes the slice" passed
with the beat moved *after* it; and `assert gap <= WAIT_SLICE_S` compared a result against the very
constant that produces it, so raising the slice to 900 s — above the lease, the exact defect being
closed — kept the suite green. Both are fixed, the epoch trap turned out to affect three
pre-existing tests as well (including one whose whole purpose is the correctness of the announced
resume time), and the real invariant `WAIT_SLICE_S < DEFAULT_LOCK_STALE_S` now has a test of its
own. A third finding was worse than a defect: two comments asserted the false machine-sleep alarm
was fixed when it was not — the slice heartbeat called `store.touch_lock` directly and never moved
the detector's marker. One `LockHeartbeat` per `Deps`, injected into the limiter, closes it;
verified both ways, a 3600 s hold raises no alarm and a 9 h 50 min wall jump still does.

## Audit and remediation (2026-09-08)

A full audit ran on 2026-09-08: six independent read-only passes, an eleven-mutation sweep, and one
synthesis. Everything is in `docs/audit-2026-09-09.md`, including the ranked remediation list and
what has been closed since. Three things belong here because they change how this file should be
read.

**The documents were the weakest artifact, not the code.** The audit's own summary: prose 7 146
lines against 10 551 of code and **17 613 of tests** — the largest artifact in this project is the
test suite, which refutes the standing suspicion of over-investment in documentation. What is true
instead is that 20 % of `docs/` is duplicated narrative, and that twelve distinct stale-document
defects were found across four days. Two of them were actively steering work: ADR-0003 still
recommended lowering `min_spacing_s`, which is the change measured dangerous on 2026-09-06, and
three documents put the PKD coverage gap at 25.2 % when the figure answering the sentence they
actually wrote is **8.6 %** (`pkd=` matches any of a record's codes, settled at zero requests from
the operator's own store). Both are corrected.

**The 1 076-line retrospective log below is the one control the audit would positively remove.**
Nothing consults it — one Python citation, not mechanised — and it is a second copy of the session
briefs in another language, the copy that is *not* auto-loaded. Its only demonstrated effects were
two errors it caused elsewhere. The recommendation is to trim it to the live sections plus "Argued
against rather than applied", which exists nowhere else. That deletion was **not** done today: a
~900-line removal is unreviewable between two code stages in the very file each stage updates, so
it is its own piece of work.

**Everything else the audit assessed as earning its keep.** All 13 ADRs have a defect behind them
(ADR-0010's feature was never built and the ADR still paid for itself, by splitting out the proxy
hole and the silent 21 MB download). Boundary rules 7 and 9-14 have recorded catches; rules 1-6 and
8 have none, and the audit explicitly recommends **keeping** them rather than removing on absence of
evidence. Every test cluster earns.

### Closed on 2026-09-08

- **A1** — `aktualizuj` skipped the entries `/zmiana` reported as changed and reported them as
  refreshed. The only defect found that was actively losing data, and it was activated by the
  ADR-0013 repair: fixing one silent loss unmasked another.
- **A2** — `wyczysc --starsze-niz 0` kept 30 days of personal data and said it had purged.
- **A5** — an exported row could contradict the newer data in the same row.
- **A6** — `--lista` could not switch off `szczegoly: true` from a query file.
- **ADR-0014** — the register-free mode (`--demo`), which also removes the audit's second
  survivability blocker.
- **A3, A4, A7, A8, A9, A10** — the whole remaining Tier A, closed 2026-09-09 with ADR-0015 and
  ADR-0016. See the "Stages 4-8" section of the audit document for what each one was and how it
  was measured.

### Tier A — closed 2026-09-09

**A3, A4, A7, A8, A9 and A10 are fixed**, each mutation-checked, in stages 4-8 of
`docs/audit-2026-09-09.md`. Two needed an ADR: **ADR-0015** (open edges in batched queries) and
**ADR-0016** (identity of a report row the register gave no number to); both **accepted
2026-09-09**. Five zero-request measurements ran first and three of them changed the plan:
A3 turned out to be 2.96 % rather than 1.21 %, A9 turned out to need **no migration** (zero `HASH:`
rows in your store), and A10 was confirmed against the production archive rather than argued from
prose.

**F12 — the defect is fixed; two of its seven fields remain unmeasured.**

The real defect was found at **zero requests**: `miasto` matches by fragment server-side (the
operator's own store holds four `Stara Łomża …` records returned by `miasto=['Łomża']`), while
`matches_criteria` compared it exactly — so the report path was dropping them. Fixed and
mutation-checked 2026-09-09. `kod` needed no measurement either: `Criteria` validates it to
`15-333`, so a fragment can never be sent.

Two production requests (consent given in session) went to the remaining five and came back
**inconclusive**: both grouped fragment queries returned empty, which proves at least one
field per group is not a fragment filter without saying which. The finding that survives is
that the field family is **not uniform**, so no field's semantics may be inferred from a
neighbour's. `powiat`, `gmina`, `ulica`, `imie` and `nazwisko` stay on exact comparison and
the docstring now says which choices are measured and which are defaults. Settling them costs
**five more requests**, one per field — your call whether it is worth it.

Tiers B, C and D, and the reasoning behind each deferral, are in the audit document.

## Phase 6 — the UX pass that found three dead ends (2026-09-09)

**How it was measured.** A harness drove the *real* wizard (`wizard.run_wizard`, `flow`,
`ConsoleView`) with a scripted operator — the only substitution was the human — against the demo
register with the **live** assistant. Eleven scenarios, seven interpretations, **zero CEIDG
requests**. The report path was exercised separately on the real 21 MB wielkopolskie archive
already in `probe_out/`, placed in the cache directory so neither network request fired: 287 256
rows filtered in 13 s, 281 records for Gniezno, the workbook produced and then deleted.

**What held.** Two words (`fryzjer poznań`) → `miasto: Poznań; PKD: 9621Z`. Two typos
(`fryzjezy w poznaiu`) → identical. An impossible filter (`duże firmy IT w Warszawie`) → four PKD
codes plus three limitation sentences, no refusal. A NIP with dashes → the firm card with the
hostile name neutralised and the public-search link.

**What did not, and it was one shape three times: a message, the lost description, the menu.**

| Path | Old behaviour | Now |
|---|---|---|
| `wszystkie firmy` | empty interpretation screen, default answer **"tak, szukaj"**, then `Błąd: Podaj przynajmniej jedno kryterium` | clarification round (ADR-0017) |
| any 0-hit query | one sentence and `wyjdz`, so *"popraw kryteria"* never appeared at a zero | menu of ranked widenings, each with its reason |
| report not covering the criteria | `ConfigError` naming `--zrodlo`, a flag the wizard does not have | the reason, then an offer of the API path |

Three smaller findings from the same runs, all fixed: a bad NIP printed a raw `ValidationError`
with a link to errors.pydantic.dev (the same leak sat in the wizard's form and had a third copy
inside `assistant/translate.py` — now one `criteria.bledy_po_polsku`); the vintage question was
asked even when both populations counted **zero**; and the demo's first screen said *"asystent
wyłączony (brak klucza…)"* while pointing at the keyring, when the true reason is that
`_settings_demo` deliberately reads neither `.env` nor the keyring.

**And one the fix uncovered.** With contacts finally on screen, the list-path workbook turned out
to report `z telefonem 0 (0%)` — a structural consequence of not fetching details, reading as a
measurement. Measured on the demo run's workbook (48 records): **22 of 43 columns empty and none
hidden** — 18 of them on the real anonymised `/firmy` fixtures, the other four being status dates
no active entry carries. Meanwhile the
report path — the rarer source — hid 12 and explained itself in two sentences. Both paths now
behave the same way: `normalizer.KOLUMNY_TYLKO_ZE_SZCZEGOLOW` (13 columns, measured against 38 real
anonymised `/firmy` records) is hidden, the screen says so, and the contact rows are replaced by a
sentence naming what is missing and how to get it.

Tests: **1247** (was 1227). New file `tests/test_pomoc_operatorowi.py`. Two older tests reversed
their assertions and say so in their docstrings — both had been pinning the defect.

**The report path closed on production the same day, with the owner's consent: exactly two
requests.** `GET raporty` (1.11 s) then `GET raport` (7.12 s), quota counter 1000 → 999 → 998,
21 MB archive of 2026-09-08, 282 records for Gniezno — one more than the 2026-09-05 archive held.
And the run earned its keep immediately: the summary claimed *"telefon i e-mail: nie pobrano"*
for an export whose rows carried 63 phones and 68 e-mails. Contacts have **two** sources —
`detail_json` on the API path and the CSV row on the report path — and the fresh predicate saw
only the first. A silent untruth fixed on the common path had become a loud one on the rare path.
Corrected, then verified by re-exporting the same run from the store at zero further requests.

**ADR-0017 accepted by the owner 2026-09-09**, after the production verification.

**Code review of the whole change, same day** — no runtime defect, five text findings and four
smaller ones, all applied. The one worth carrying forward: `prepare_fetch`'s docstring, the
`flow` module header, `assistant/__init__.py` and CLAUDE.md all still promised *"najwyzej dwa
`count`"* on a function that had just grown a loop spending one per accepted widening. That is
the second time this sentence has gone stale (it said "exactly one" until 2026-09-08), and it is
the sentence the next change reads first. Also found: the model's proposals reach the terminal
as **option labels**, which bypass `richtext.safe` and land in `input(...)` — `strip_control`
now runs in `translate._przytnij`, at the boundary; and the fresh contact predicate would have
mis-hidden contact columns for a mixed report+API export, unreachable from today's CLI but
closed anyway.

**Open after this phase:** the model's proposals are generic when the description carries
nothing to work from, which is inherent but worth re-reading after gate 3.

## Gate 3 — the walk (2026-09-09)

**How it was driven.** The same technique as the phase-6 UX pass, aimed at production instead of
the demo: `wizard.run_wizard`, `flow`, `ConsoleView`, `build_deps` and `ScriptedPrompter` are all
the production objects, and the only substitution is the human. `CEIDG_DATA_DIR` pointed at a
scratch directory, so the owner's 51 MB store was never opened. Two passes, both to a finished
workbook, both `zakonczony`.

**Cost, measured from `request_log` rather than estimated: 25 requests, every one HTTP 200**, no
retry and no 429. Seventeen `/firma`, seven `/firmy`, one `/raporty`. The pre-walk forecast said
about eleven, and the gap has a cause worth keeping: **the assistant path cannot set a record cap
— by design (ADR-0011) — so pass 1 fetched all 71 hits** instead of a capped 20. A forecast that
assumes a cap the operator has no way to give on that path is wrong on every assistant walk, not
just this one.

| Pass | Input | Result |
|---|---|---|
| 1 | *"salony fryzjerskie w Gnieźnie"* (assistant) | `miasto: Gniezno; PKD: 9621Z`, 71 firms, 6 sheets, 48 KB |
| 2 | the eight questions, `max_rekordow: 10` | 3 740 hits counted, 10 fetched, 4 sheets, 20 KB |

**What the walk confirmed on production for the first time.**

- **The ADR-0013 identity invariant holds against the real register.** The two passes returned
  71 and 10 records, and the store ended with **79 firms and zero duplicates under canonicalisation**
  — the two entries common to both queries were merged, not written twice. This is the defect that
  cost 2 681 requests and delivered nothing on 2026-09-08, and until now it had only ever been
  checked offline.
- **The vintage question states sizes, not warnings.** `9621Z` alone: **71 firms**; with the PKD
  2007 predecessor: **486**; difference **415** — both counted before the question was asked, and
  the screen named what the wider choice drags in (`9622Z`, beauty services). ADR-0012's whole
  argument, visible on real numbers.
- **The assistant's answer survived the confirmation screen it does not control.** The PKD name
  (*Działalność fryzjerska*) came from the vendored dictionary, not from the model — which is the
  one control the operator has over a wrong code.
- **The report offer fired on pass 2** and named what the archive lacks (OCZEKUJĄCE and WYKREŚLONE
  entries, correspondence address, citizenships, companies) before offering "2 requests instead of
  thousands".
- **Both workbooks are what the specification asks for**: Excel tables with autofilter, header and
  identity columns frozen at `C2`, `nip`/`regon`/`kod_pocztowy` as text, dates as real dates,
  **zero formula cells**, and `Metadane` agreeing with the screen (`liczba_trafien_count` 71 =
  `liczba_pobranych_rekordow` 71, three pages).

**One observation, not a defect.** The operator's summary carries `run_id` as a bare UUID. It is
needed — `eksportuj --run-id` consumes it — but it is the one line on that screen written in the
program's vocabulary rather than the operator's.

**And one non-finding worth recording, because it looked like a finding.** No log file appeared,
while the summary named its path. That is the harness, not the tool: `setup_logging` is called by
`cli.py`, and driving `run_wizard` directly skips it. Checked before it was written down, which is
the rule this project keeps re-learning.

### The walk's own blind spot, and the defect found by closing it

A scripted operator types what the source says is valid. That is the opposite of the gate's
subject, so the inputs were re-run as **what somebody who does not know company data would
actually type** — nine of them, at zero requests, because `Criteria` validates before any
request goes out.

**Five are absorbed, and that is the tool working**: `aktywny` → `AKTYWNY`, `Wielkopolskie` →
`wielkopolskie`, `96.21.Z` → `9621Z` (the notation GUS itself prints), `9621z` → `9621Z`, and a
NIP with dashes or spaces → ten digits. Leading and trailing spaces are trimmed and
`"Gniezno, Poznań"` splits into two values in `CriteriaAnswers.set_list`.

**Four are refused in English.** `bledy_po_polsku` passes `blad["msg"]` through, which is Polish
only when the message came from one of our own validators. Pydantic's built-in errors arrive in
English and go to the screen that way:

| What the operator types | What the operator is told |
|---|---|
| `status: czynna` (or `aktywne`) | `Input should be 'AKTYWNY', 'WYKRESLONY', …` |
| `data_od: 01.01.2020` | `Input should be a valid date or datetime, invalid character in year` |
| `max_rekordow: dziesięć` | `Input should be a valid integer, unable to parse string as an integer` |

Compare the half that works: `wojewodztwo: wielkopolska` answers *"nieznane województwo
'wielkopolska'; dozwolone: dolnośląskie, …"* — the whole list, in Polish, ready to copy from.
`pkd: fryzjer` answers *"PKD 'fryzjer' musi mieć postać 62.01.Z albo 6201Z"*.

So the phase-6 repair that replaced three `ValidationError` dumps with one `bledy_po_polsku` did
half the job and reads as if it did all of it: it strips the pydantic URL and the `Value error, `
prefix, which is what made the old dumps unreadable — but it never translated, and nothing
observes that. `status` is a free-text question with the hint `np. AKTYWNY`, so guessing a Polish
word for it is the expected mistake, not an exotic one. **This is a gate-3 finding: it lands on
the exact person the gate is about, and it is invisible from every offline test, because the
tests assert the message the code produces.**

**What is still owed.** The gate asks whether a person without API knowledge reaches a finished
file, and a scripted operator cannot answer the *reading* half of that. The transcripts of both
passes are the evidence to read; acceptance stays with the owner. The English-message defect above
is open and is the one thing this walk found that changes code rather than documents.

## Open items
- ~~Repeated `miasto=` has never been measured~~ — **measured 2026-09-09, it is OR** (one
  production request: `miasto=Gdańsk&miasto=Wrocław` → `count = 242 415`). The owner's example
  *"salony fryzjerskie, firma ubezpieczeniowa we Wrocławiu oraz Gdańsku"* therefore works end
  to end: the assistant resolves it to `miasto: Gdańsk, Wrocław`, `PKD: 6622Z, 9621Z`, and the
  register ORs both halves. Kept for the reasoning: under AND the query would have returned
  empty with no error, and the answer could not be inferred from `pkd` being OR-ed, because the
  same day established that the text-field family is not uniform (F12).

- **CI is red, and this entry said it was green.** — `2e29540` did record a green run, but the
  next two pushes (`d5cd71a`, `dfd950f`, 2026-09-08) both **failed** and nobody looked. Fixed
  2026-09-09; the diagnosis is the same shape as the first one and is worth reading twice.
  `tests/test_demo_markers.py` asserted the phrase *"zero żądań"* in `--help` output. At 80
  columns rich wraps the option description mid-phrase and puts two table borders between the
  words, so the assertion is false while the help is perfectly correct — and `pobierz` is the
  **only** command wide enough to escape it, which is why the first version of the new guard,
  written against `pobierz`, passed with the defect restored. Widths 60, 100 and 200 all pass
  too, so "check it narrow" would not have found it either. The fix compares against help with
  the box-drawing block and whitespace normalised away, guarded by a test over every command ×
  four widths. The deeper cause is a comment that was wrong: both CLI fixtures pinned
  `cli.console.width` and claimed that settled help rendering. It does not — `--help` is drawn
  by typer's own console (`typer.rich_utils`), which nothing was pinning. A false comment kept
  the real question from being asked for two red runs. Kept below, the earlier diagnosis: There had never been a commit, so
  the workflow had never executed — "CI runs the same four gates on Linux and Windows" was a
  configuration, not an observation. The first Linux run turned up ten tests that were measuring
  the **terminal width of the machine they ran on**: `rich` drops an option's name from `--help`
  when the column is narrow, so `test_pobierz_advertises_the_batch_flag` and friends failed at a
  width our Windows console never produces. Reproduced locally at `COLUMNS=40`, fixed by pinning
  the console width in both CLI test fixtures (the app's `console` is built at import, so the env
  var alone does not reach it), and the whole suite now passes at 40, 80 and 200 columns. The
  guarantee those tests carry is that a flag *exists* — `--force` was added in phase 3b precisely
  because the lock message named a flag no command had — not that it fits in N columns.
  The second CI run then failed at full width, which settled the actual cause: **colour**.
  `rich` styles an option's *name*, so with colour on, `--partie` reaches the buffer split by
  escape sequences and `"--partie" in output` is false while the flag is plainly on screen.
  GitHub Actions turns colour on by default and our Windows console does not, which is the whole
  local/CI difference. `NO_COLOR` does not override `FORCE_COLOR` in this version of `rich`,
  `TERM=dumb` does — so the fixtures use that, and a guard test asserts the runner's output
  carries no ANSI at all, so a future change in that precedence reports itself once instead of
  as ten unreadable substring failures. One console test needed a different fix for the same
  ambient cause: `FORCE_COLOR` makes `rich` treat a `StringIO` as a terminal and **animate** the
  progress bar, so intermediate frames — including the one before the total is known — land in
  the buffer; `force_terminal=False` pins it. The suite now passes with `FORCE_COLOR=1` and
  `COLUMNS=40` set, which is the condition CI actually runs under.
- ~~**The production store has not been migrated yet.**~~ — **done, verified 2026-09-08.** The
  store is schema v3: 16 310 firms, **zero** duplicates, zero lower-case identifiers, zero orphans,
  all seven runs `zakonczony`, 15 955 entries carrying fetched details. The `wyczysc` warning that
  hung off this item is void with it.
- ~~**`aktualizuj` is worth re-running once after the migration**, to see the cache hit.~~ —
  **withdrawn 2026-09-08, and the reason matters more than the item.** "Zero detail requests" stopped
  being evidence of anything: the audit found `aktualizuj` was *also* producing zero, by skipping
  the very entries `/zmiana` reported as changed (item A1). The measurement could no longer tell the
  repair from the defect. A1 is fixed, and the demo now demonstrates the property offline — 3 detail
  requests on the first run over a window, 0 on the second. What still needs the real register is a
  different half: that a lower-case identifier from `/zmiana` resolves through `/firma` to a detail
  that lands. That is a bounded run (`aktualizuj --od <T-2h> --do <T>`, order of 50-60 requests) and
  it is read off the screen — the new stale-detail counter — not off a request count.

- Manual resilience scenarios 1, 2, 8 (`docs/resilience-report.md`) — each now has an
  automated equivalent, but §E asks for a real killed process, a real network cut and a real
  full disk, dated in the report. Those runs are still open and are the owner's to make.
- `link_ceidg` for report records: the column stays empty (ADR-0008, decision 5) because the
  report CSV has no record id, and the summary says so. **Designed on 2026-09-07 in ADR-0010**
  (status: proposed) and still not implemented — it is your call, and the ADR reframes it: a NIP
  batch is a `/firmy` request, so enrichment costs `ceil(n/25)`, exactly what the same records cost
  through the API list path. It is therefore worth its requests only when you want a report-cheap
  workbook that *also* carries contacts and links; for links alone the API path is strictly better
  and gives real GUIDs. Two preconditions before any code: your approval, and **one production
  request** to settle the batch size — "25 NIPs per request" is an inference, and `ids` is the
  standing precedent for this API capping repeated identifiers far below the obvious number
  (5 works; 10, 20, 25 and 50 all return 400).
- ~~§E: no test proving connections stay inside the allowlist~~ — **closed 2026-09-07**, see
  phase 3f above and the §E section of `docs/resilience-report.md`. It was the only acceptance
  criterion in §E with no evidence behind it, and it was hiding a live defect.
- `ids` batch size: **answered** — 10 and 20 both return 400, so the cap is 5-9 and
  `ids_batch_size: 5` stays. Narrowing it to the exact value would cost four requests for at
  most a 1.8x saving on detail fetches; not worth it unless that becomes the bottleneck.
- ~~Boundary rules 1-5 still rest on review~~ — **stale entry, removed 2026-09-06.** It
  contradicted the phase-3b section two headings above and the design document: all ten rules of
  that day have been enforced by `tests/test_boundaries.py` since 2026-09-06 — there are fourteen
  now, 1-13 by the scan and 14 by mypy (`CORE_FORBIDDEN` covers
  1-4, `test_only_the_pipeline_knows_both_the_network_and_the_database` covers 5). A living
  document that keeps a closed item open is worse than one that says nothing about it.
- ~~Phase 4: the PKD dictionary and its spot-check, `caller.py`, the second credential, the UI
  wiring~~ — **all closed 2026-09-07** (phases 4a-4d above; the spot-check stopped being owed when
  the file came from GUS itself). **Group C is now run too** (2026-09-07, phase 4f) and it did not
  come back clean: the `pkd` filter matches the code as stored, the register is mid-transition, and
  a 2025-only dictionary cannot reach 8.6 % of it. So phase 4 is **not** all evidence any more —
  there is code to decide and write. Open: the vintage-coverage decision (below), then **group D**
  (gate 3, which is also the assistant's first use through the wizard) and **B4** (the assistant
  under a real network cut).
- ~~**PKD vintage coverage — open, and it blocks gate 3.**~~ — **designed, accepted and built
  2026-09-07** (ADR-0012, phase 4h above). A chosen PKD 2025 code reached only the records that had
  migrated; it now expands with PKD 2007 predecessors from the official GUS key, silently where the
  expansion is clean and by asking — with both populations counted — where it is not. Gate 3 is
  unblocked, and its hairdressing walk sentence is now the right one to use, because it exercises
  the new step instead of dodging it. The whole mechanism expires on 31.12.2026 with the transition;
  the data file's header says so and names what to delete.
- **The demo removes the "cannot run this without production data" blocker (2026-09-08, ADR-0014).**
  `--demo` answers from a synthetic register generated in-process: no socket, no token, no real
  personal data. It is the first way to run this tool that does not require a Profil Zaufany, and
  the walkthrough plus the recorded results are in `docs/demo-walkthrough.md`. Still open on the
  demo: the report path (`/raporty` answers empty, so `--zrodlo auto` falls back to the API), a
  recorded-response path for the assistant, and the schema-level `DEMO` marker that ADR-0014 defers.
- The assistant has never run through the wizard on a real terminal. Every real call so far went
  through `scripts/assistant_smoke.py` or a single CLI command in a sandbox; the wizard path is
  covered offline and by construction (`collect_from_description` is shared with `--opis`), but the
  reading of it is gate 3's business.
- ~~**Version control: deliberately none.**~~ — **changed 2026-09-08 by the owner.** The initial
  commit `d6a46e9` (157 files, 41 993 lines) is pushed to the **`ceidg-tool` branch** of
  `BIAP-Inteligentne-Technologie/PIWorkmate`. That repository's `Main` carries an unrelated
  product, and the owner chose this target after being shown the two share no history — so the
  standing constraints are: never push to `Main`, never force-push, and remember that our
  unfiltered `on: push:` CI spends the organisation's Actions minutes on every push (**filtered
  to `branches: [ceidg-tool]` on 2026-09-09**, on both triggers). Secrets and
  production data stay out by `.gitignore`, verified before the push: `.env`, `probe_out/`,
  `*.sqlite`, `wyniki/`, `PKD/`. The three JWT-shaped strings in the test suite were compared
  against the real token and are synthetic.
