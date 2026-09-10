# ceidg-tool

Python CLI that pulls sole-trader records from the CEIDG API v3 data warehouse into an Excel
workbook for an operator with no API knowledge. Work proceeds in phases; each one ends when the
owner accepts it, not when the tests go green.

## Facts that change how you work here

**The test API host is unreachable from this network.** `test-dane.biznes.gov.pl` times out at TCP
level while `dane.biznes.gov.pl` answers instantly. So `--srodowisko test` reaches nothing: every
live check has to run against production, and production carries real personal data. Ask for the
owner's consent in the current session before any request goes out, and pass
`--srodowisko prod --produkcja` explicitly. Everything else is covered by the offline suite.

**Polish output needs `PYTHONUTF8=1`.** Without it the CLI, the probe scripts and pytest mangle
diacritics on this Windows console. Prefix every Python invocation with it.

**The project is under version control since 2026-09-08, and its remote is somebody else's repo.**
The initial commit (`d6a46e9`, 157 files) went to the **`ceidg-tool` branch** of
`BIAP-Inteligentne-Technologie/PIWorkmate` — a private org repository whose `Main` holds an
unrelated product (WorkMate: `src/workmate`, Teams notifications, its own ADRs 0064+). The owner
chose this after being shown that the two share no history. Two consequences worth holding on to:
never push to `Main` and never force-push anything here, because that branch is another team's
active work. CI in `.github/workflows/ci.yml` is filtered to `branches: [ceidg-tool]` on both
`push` and `pull_request` since 2026-09-09 — until then it was unfiltered and every push, a typo
fix included, spent the organisation's Actions minutes on a four-way matrix. Keep the filter:
its second half guards a case that has not happened yet, because a `pull_request` trigger takes
its workflow file from the PR's **base** branch, so an unfiltered copy landing in `Main` would
run our matrix on another team's every pull request.

**The token in `.env` belongs to the owner.** It is not a borrowed credential and needs no action
before 2026-09-30; from that date, remind them to refresh it. Its payload carries a PESEL, so it
stays out of logs, messages, the database and output files — `config.mask_tokens` and
`richtext.safe` are what keep it there.

**Silence is a defect, and it is measured in requests.** An operation here can run for half an hour
against a 3.75 s request spacing, so a stretch without output does not read as "working" — it reads
as hung, and a hung-looking program gets killed. The rhythm of `Events` and of `store.touch_lock()`
is therefore counted in **requests sent or rows read**, never in pages or matched records: a
`/zmiana` page is 500 identifiers (up to a hundred requests), and a report page is however many rows
happen to match. That off-by-a-layer error produced four separate defects on 2026-09-06, one of
which let the database lock expire under a working process. `Events.close()` belongs to the
operation that opened the bar — a live `rich` display overwrites everything printed after it.

The same doctrine applies to **waiting**, and it took until 2026-09-08 to finish. A single wait can
outlast the lock: the budget brake clamps to the longest window (3600 s) while the lease expires
after 600 s, so `RateLimiter` sleeps in `WAIT_SLICE_S` slices with a heartbeat before each one, and
`WAIT_SLICE_S < DEFAULT_LOCK_STALE_S` is an invariant a test guards (the two constants cannot see
each other — `ratelimit` may not import the database). Waiting also has to reach the **log file**,
not only the screen: `_LogEvents` records every wait with its *predicted resume time*, because a
duration alone cannot tell a limiter hold from a suspended laptop after the fact.

**The tool refuses to go through a proxy, on purpose.** `httpclient.build_http_client` is the only
place an `httpx.Client` is made (boundary rule 11). It always injects a transport, which is what
actually stops httpx reading `HTTPS_PROXY` from the environment, and it passes `trust_env=False` to
`httpx.HTTPTransport`, which is what stops `SSL_CERT_FILE` replacing the CA bundle — two different
mechanisms for two halves of §B, easy to confuse and worth keeping straight. `AllowedHostsTransport`
then refuses any host outside the *selected environment*, at the layer where the socket opens.
Consequence on a corporate network: the tool fails to connect rather than handing a PESEL-bearing
token to an interceptor. `docs/resilience-report.md` carries the §E evidence.

**The PKD dictionary is generated, never written by hand or by a model.**
`ceidg_tool/data/pkd2025.yaml` (728 subclasses) comes from `scripts/build_pkd.py` over an official
GUS export; the header carries the legal basis and the source's SHA-256. **The vintage is 2025, not
2007** — every `rokPkd` the register returns says so, and `6201Z`, the classic software code, does
not exist in 2025 at all. Two rules follow. First, never regenerate it from memory or from a
summarised web page: such a list passes every automated check here — canonical keys, entry count,
agreement with the codes the register returned — while being quietly wrong in names nobody
cross-reads, and that inverts the one control the operator has (the confirmation screen shows the
PKD *name*, so a wrong code should read as a wrong industry; a fabricated name makes the screen
agree with the model). Second, never trust a fixture about the API: the whole 2007 detour rested on
one hand-written line in `tests/conftest.py` that an ADR cited as a measurement.

**The dictionary is right and it is not enough — the register is mid-transition.** Measured
2026-09-07: the `pkd` filter matches the code **as stored on the record**, and PKD 2007 stays legal
until 31.12.2026, so each record carries one vintage. Over 285 026 real records in
`probe_out/raport_sample.zip` (which has `RokPKD` per row, and is why this cost no requests): 58.6 %
still carry 2007 codes, and **8.6 % of the sample is unreachable by any code in `pkd2025.yaml`** —
`9602Z` hairdressing, `4520Z` vehicle repair, `4120Z` building, `6201Z` programming. So a PKD-filtered
fetch silently returns a subset, on every input path, the `--pkd` flag included; this is a property
of the register, not of the assistant. Do not treat "the code is valid" as "the query is complete".

**Corrected 2026-09-08 — this paragraph said 25.2 %, which answers a different question.** `pkd=`
matches **any** of a record's codes, not just `pkdGlowny`, settled at zero requests from the
operator's own store: `pkd=6201Z` returned 13 records of which **9 carried the code only in the
secondary list** (as deep as position 30), and `9621Z`+`9602Z` returned 357 of which **62** did
(position 50). So the operative figure is "no code the record carries is in the dictionary" =
**24 494 = 8.6 %**; the old 25.2 % (71 817) counts records whose *main* code is absent, which is not
what the sentence claimed. Two smaller corrections in the same breath: the archive is
**wielkopolskie**, not "the register", and its 285 026 counts rows *with a main code* — the file
holds 287 256, the other 2 230 having an empty `GlownyKodPkd` and all carrying `RokPKD=2007`. The
trap is real and roughly three times smaller than this file used to claim.

Two traps follow. The classification and the filter are different things: "6201Z does not exist in
PKD 2025" is true, while "the API would reject it" never was — it returns 234 605 records. And the
cost table does not cover this: it prices what will be fetched, with nothing to compare against, and
the interpretation is confirmed before `count` runs, so it is a spend control, never a scope one.

**One entry, two spellings — `id` is a value, not a string.** `/firmy` and `/firma` return the
record identifier in UPPER case, `/zmiana` returns the same identifiers in lower, and `ids=`
matches either way. `firma.id` was a case-sensitive primary key, so `aktualizuj` wrote every
changed entry twice — a husk linked to the run and a full record linked to nothing — and the
detail cache could never hit. The night of 2026-09-08 that cost 2 681 requests and delivered zero
usable records. Canonicalisation lives in `recordid.py` (boundary rule 14, carried by mypy through
`KanonicznyId`, not by the AST scan) and applies **only to hex GUIDs**: `/raporty` identifiers
share the 8-4-4-4-12 shape, are not hex and are case-significant because they go into the download
URL, and report rows are keyed `NIP:`/`REGON:`/`HASH:`. Do not relax that pattern to `[0-9A-Za-z]`.

**The evidence gets sanitised of exactly what matters — check the generator, not just the fixture.**
`scripts/anonymize_samples.py` uppercased every identifier while building fixtures, so the offline
suite asserted the absence of the property that broke production; the `/firma` doubles echoed back
the identifier they were asked for, which is the one thing the register does not do. This is the
third instance of the shape, after the hand-written `rokPkd` line and `tests/support.py` building
its own `httpx.Client`. `tests/fixtures/api_traits.yaml` now states the measured per-endpoint
properties in words, `tests/test_api_traits.py` holds the fixtures to them and audits against
`probe_out/` when it is present, and `tests/support.registry_id` is the one line that makes a
double behave like the register.

**A guarantee whose violation has no observer is not a guarantee.** Three closed on 2026-09-08 and
they rhyme: the identifier invariant broke behind a lenient `.upper()`; the database lock lease was
lost behind a discarded `rowcount` (a 9 h 50 min machine suspend expires a 600 s lease under a
working process, and only the process *taking* a lock was ever warned); ten hours of waiting left
no trace because `on_wait` reached the screen only. When adding a guard, ask what would print if it
were violated — and if the honest answer is "nothing", that is the defect, not the guard.

**Registry values are hostile input.** Names come from a public register that anyone can write into.
They reach both a spreadsheet, where a leading `=` is a formula, and a terminal, where `rich` reads
square brackets as markup and escape sequences steer the screen. `safetext.py` neutralises the spreadsheet half and `richtext.safe` the terminal half;
anything new that prints or exports registry text goes through one of them. `richtext.py` is
the only module allowed to hand `rich` a string from outside — that is boundary rule 10, and
`tests/test_boundaries.py` enforces it by scanning the syntax of every print call.

## Commands

```
PYTHONUTF8=1 .venv/Scripts/python -m pytest         # no network; no `-q` — see below
PYTHONUTF8=1 .venv/Scripts/python -m mypy ceidg_tool tests
.venv/Scripts/ruff check ceidg_tool tests scripts
.venv/Scripts/ruff format --check ceidg_tool tests scripts   # --check, because bare `format` rewrites and cannot fail
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool     # the wizard
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool pobierz --demo -w wielkopolskie --szczegoly
```

**Do not add `-q` to that command.** `addopts = "-q"` is already in `pyproject.toml:51`, so an
explicit one makes it `-qq` — and at that verbosity pytest prints no summary line at all, only the
progress dots. Three of this file's revisions quoted a pass count that the documented gate is
incapable of printing; that is the mechanical reason the number kept drifting. `ci.yml` had the
same duplicate and lost it on 2026-09-10.

The skip that matters is `tests/test_api_traits.py::test_atrapa_demo_zgadza_sie_ze_zmierzona_pisownia[raporty]`:
the demo double serves no `/raporty`, which is the open demo edge `docs/status.md` names, and it is
the one place that gap is visible from a gate. **The sentinel is that test's name, not a total** —
when it stops being skipped, the report path got a double. Do not restate it as a count: the count
is not stable enough to carry it. Locally, with `probe_out/` present, the suite is **1302 passed,
1 skipped**; in CI, where `probe_out/` is git-ignored and therefore absent, five further tests skip
honestly and the same tree reports **1297 passed, 6 skipped** — both measured on 2026-09-10, the
second by moving `probe_out/` aside for one run rather than by subtracting five. A total quoted without naming its
environment has been wrong three times here.

The last one needs no token and reaches no register — use it to see the tool work before
touching anything real.

CI (`.github/workflows/ci.yml`) runs the same four on Linux and Windows, Python 3.11 and 3.12.

## Where the design lives

- `docs/status.md` — the living plan: phases, gates, open items. Update it at every gate.
- `INSTRUKCJA_CLAUDE_CODE.md` and `UZUPELNIENIE_01.md` — the requirements. The supplement wins
  wherever the two disagree.
- `docs/decisions.md` — what the API probe measured (page numbering, page limit, batch size, how
  empty results are signalled, report contents). These are observations, not guesses; check here
  before assuming how the API behaves.
- `docs/adr/` — architecture decisions: 0008 the phase-3 user layer, 0011 the assistant, 0012 the
  PKD 2007→2025 transition, 0013 the identity of a record identifier (and the schema v3 migration
  that follows from it), 0014 the register-free mode that `--demo` runs on, 0015 open edges in
  batched queries, 0016 the identity of a report row the register gave no number to, 0017 the
  clarification round (which reverses ADR-0011's "no clarification round trip in v1").
- `docs/design/phase2_core.md` — module map and the numbered boundary rules.
- `docs/resilience-report.md` — the ten resilience scenarios and how each is covered.
- `docs/test-runs-phase4.md` — the five groups of runs that need a real model or a real register,
  with the results of A and B. Anything about how the model *actually* behaves is measured there,
  not argued: what a mock returns is what we wrote into it.
- `docs/audit-2026-09-09.md` — the 2026-09-08 audit: six read-only passes, an eleven-mutation
  sweep, the synthesis, and the ranked remediation list with what has been fixed since. Read the
  Tier A table before assuming a defect is still open, and the "controls" pass before deciding a
  rule is ceremony.
- `docs/demo-walkthrough.md` — how to walk the demo, and what the recorded run actually proved
  (and did not).

**A batched query must send the same filter as the un-batched one (ADR-0015).** `plan_batches`
fills a missing date edge with `DATE_FLOOR = 1990-01-01` or `today` so the plan is reproducible and
an interrupted run stays resumable. Those two substitutes are **planning** values and must not reach
`Criteria`: until 2026-09-09 they did, so splitting a query silently changed its result set —
measured at **482 of 16 310 records (2.96 %)** on the operator's own store, 76 registered before
1990 and 406 with a start date in the future, which CEIDG accepts. The first batch therefore sends
no `data_od` and the last no `data_do` when the operator gave none, `refine()` carries the open edge
down, and the split table says `1990-1999 i wcześniej`. Do **not** add a legacy-fingerprint fallback
for old batches: an old closed `[1990-01-01, …]` batch is a different population, so recognising it
as fetched would preserve the exact defect. The shortfall sentence used to blame "entries without a
start date" — there are **zero** such entries in that store; it now names both candidate causes and
claims neither.

**A report row's identity is a declared subset, never "everything" (ADR-0016).** Rows with neither
NIP nor REGON are keyed by a hash that included `Lp.`, the ordinal within a download — so the same
sole trader got a new identity in every archive, which is ADR-0013's defect on the other source.
The key is now *name + surname + given name + start date*, built in `recordid.py`, taking **values**
rather than a CSV row so that a future migration reading `firma.list_json` cannot compute a third
digest. "All columns except `Lp.`" is the tempting wrong answer: a status change or a new phone
number would mint a new identity. Measured on 287 256 archive rows: 315 such rows, zero collisions
under that key, and the address adds no discrimination while being 23-69 % filled. The operator's
store holds **zero** `HASH:` rows, which is why no migration ships with it — re-check that before
applying this to another store.

## Structural facts

`Criteria` is the only contract between any input and any fetch. Flags, the YAML query file, the
wizard and the phase-4 assistant all produce one; nothing downstream accepts anything else.

`pipeline.py` is the only module that knows both the network and the database. `ui/` reaches the
world through it and never imports `client` or `store`. Two consequences of that rule are worth
knowing before touching either side: the report download takes its lock heartbeat through a
callback (`DownloadProgress`), and so does the limiter (`RateLimiter(heartbeat=…)`), because
neither `client` nor `ratelimit` may see the database.

`recordid.py` owns the canonical form of an entry identifier, and `pipeline.LockHeartbeat` is the
single object that touches the database lock — one per `Deps`, injected into the limiter. Both are
"exactly one place" rules with a measured reason behind them, not tidiness: the first because the
register spells one identifier two ways, the second because a detector that subtracts consecutive
beats is wrong the moment some beats bypass it.

`ui/texts.py` produces view models with no output library, so every screen is asserted in tests
without a terminal. `cli.py` authors no user-facing sentence of its own; `ui/render.py` turns
blocks into `rich`.

One decision sequence lives in `ui/flow.py`: resume → report → `count` → cost table → choice →
optional split → fetch → export → summary. The `count` step is **one request, two when the PKD
vintage question is asked, and one more per widening the operator accepts at a zero result** —
all of them before consent, none after it. `flow.py` counts both vintage populations deliberately
and before the operator answers, so the question can state the size of what will be missed or
gained instead of "the result may be incomplete"; when both come back **zero** the question is
not asked at all, because a choice between nothing and nothing settles nothing. The widening
requests are bounded by the number of filters (each turn drops one), priced on screen before the
operator picks, and never spent under `--tak`. **This sentence has now been wrong twice** — it
said "exactly one" until 2026-09-08 and "at most two" until 2026-09-09 — and it is the sentence
the next change will trust, so correct it in the same commit that moves the code.

**No operator input ends in a dead end (ADR-0017).** Three paths used to hand the operator a
message, discard what they had written and drop them back in the menu, and a UX pass on
2026-09-09 measured all three on the demo register with the live assistant. A description with no
extractable filter now opens a **clarification round** — entered on `kryteria.is_empty()`, never
on whether the model happened to ask, because a guarantee that depends on the model is not a
guarantee. Zero hits open a menu of **widenings computed by `Criteria.poszerzenia()`**, each
dropping one filter, ranked by how often that filter is the culprit and each carrying the reason.
A report that does not cover the criteria states which of four reasons applies and offers the API
path. Every one of the three keeps its non-interactive behaviour unchanged, and the `safe_default`
of each new question is where that is written down: widening must never happen for a schedule,
because it changes the population somebody asked for. `aktualizuj` follows the same shape through `prepare_update` — one cheap
`count_changes` request, a cost table, a question — because `/zmiana` returns the count for the
whole range, not just the page. Entry points differ only in which `Prompter` is installed, which
is what keeps their messages identical.

**`/zmiana` is the staleness signal, and it beats the cache.** `aktualizuj` takes identifiers from
`/zmiana` — the register saying "these changed" — so the freshness threshold for their details is
**the end of the change window**, not a cache TTL. `store.stale_detail_ids` takes that threshold
from its caller for exactly this reason. Until 2026-09-08 it computed a seven-day TTL itself, so an
entry changed yesterday but fetched three days ago was skipped, kept its pre-change `detail_json`,
and was counted as refreshed; on the operator's own database 742 of 2 891 identifiers (25.7 %)
recurred between two runs 23 hours apart. `count_run_unresolved` cannot see this and never could —
such a record is `pobrany`, i.e. resolved and untrue — so `store.outdated_details` exists as its
observer and reaches the log, not only the screen. The defect was invisible until the ADR-0013
repair restored the cache: **fixing one silent loss activated another.** A change range ending in
the future is refused rather than trimmed, because a watermark in the future makes the *next* run
skip everything in between.

**The tool runs without the register, and that is a product feature (ADR-0014).** `--demo` answers
from a synthetic register generated in-process: no socket opens, no CEIDG token is read, and the
corpus is generated rather than recorded, because the anonymiser leaves NIPs in query strings and
has already erased the one property a test was meant to check. This exists less for the demo than
for survival: the `test` environment is dead (2 802 production requests ever, **zero** test ones)
and a token needs a Profil Zaufany, so before this there was no way to run the tool at all without
real personal data. Two rules follow. The substitution belongs in `cli`, never inside `build_deps`
— a standing test asserts production builds its client with `transport=None`, and rule 11 stays
intact because the demo's `MockTransport` goes through the same `build_http_client`. And the five
markers in ADR-0014 are mandatory **jointly**: first screen, `Metadane` row, `DEMO_` filename
prefix (including under `--out`), separate data directory, refusal to combine with production. Drop
one and a demo workbook becomes indistinguishable from a production one.

**The demo corpus writes no reference data from memory, and that rule was learned twice in one
day.** `ceidg_tool/demo/korpus.py` declares PKD *code pairs* and reads every name from the
generated dictionaries, refusing to build if a code is missing or if the pair is not a real
2007→2025 succession — the first draft had invented names and two codes (`5610A`, `8690E`) that do
not exist in PKD 2025 at all. The same draft gave four of five cities the wrong `powiat`: a city
with county rights files under its own name (`Kalisz` → `Kalisz`), and `kaliski` belongs to a
village. Both passed every automated check, because nothing checked them.

The boundary rules in `docs/design/phase2_core.md` say which module may import what. Rules 1-13
are enforced by `tests/test_boundaries.py` as an AST scan rather than by discipline; rule 14 (one
producer of `KanonicznyId`) is carried by mypy strict instead, and the design document says so
rather than counting it into the scan. Rule 12 has two
halves and the second matters more: one owner for the model SDK client, **and** an explicit
`api_key=` and `http_client=` in every `Anthropic(...)` call — without them the SDK reaches for an
ambient credential chain and builds its own transport outside the egress gate. Rule 9 (`cli.py`
authors no sentence) is what makes rule 10 checkable at all — see ADR-0009 before loosening
either, and note the two subset assertions there: a `rich` object trusted to carry text must
itself be scanned, and every channel rule 10 knows about is forbidden in `cli.py`. Rule 11 stands
in the same relation to the egress policy: only `httpclient.py` builds an `httpx.Client`, so "no
connection leaves for a host outside `ALLOWED_HOSTS`" is answerable by reading one module.

## Conventions

Code comments, docstrings and user-facing text are Polish; documents under `docs/` are English.
Line length 100, mypy strict over both `ceidg_tool` and `tests`. Comments carry the *why* — most of
the ones here record a defect that was found and closed, so a comment that explains a guard is
usually load-bearing history rather than noise.

Fetched production data stays out of the repository. `tests/fixtures/` holds anonymised copies made
by `scripts/anonymize_samples.py`; raw samples live in the git-ignored `probe_out/`.

A phase that touches behaviour ends with a code review, and a review finding is applied or argued
against explicitly, not silently dropped.

And the one rule from the top of this file that a long session must still have in view: **no
request reaches production without the owner's consent, given in the current session**, and
`--srodowisko prod --produkcja` is passed explicitly. The token's payload carries a PESEL and the
register holds real people, so this is the only action here that cannot be taken back. It is
restated at the end deliberately: opening context loses weight as a session fills, and this is
the sentence that must not be the one that fades.
