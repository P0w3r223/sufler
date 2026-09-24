# ceidg-tool

Python CLI that pulls sole-trader records from the CEIDG API v3 data warehouse into an Excel
workbook — for an operator with no API knowledge, and since 2026-09-23 for an agent driving it with
flags. Work proceeds in phases; each ends when the owner accepts it, not when the tests go green.

## The three rules that cannot be worked around

**No request reaches production without the owner's consent, given in the current session**, and
`--srodowisko prod --produkcja` is passed explicitly. The token's payload carries a PESEL and the
register holds real people; this is the only action here that cannot be taken back.

**The test API host is dead.** `test-dane.biznes.gov.pl` times out at TCP level, so
`--srodowisko test` reaches nothing and every live check needs production. Everything else is
covered by the offline suite, and `--demo` runs the whole tool against a synthetic register with no
socket and no token.

**`PYTHONUTF8=1` on every Python invocation.** Without it the CLI, the probes and pytest mangle
Polish diacritics on this Windows console. (The JSON envelope is pure ASCII and survives either
way — that is deliberate, see below.)

## The repository carries no credential, and that was measured

Neither the CEIDG token nor the assistant key is in this repository — not in the working tree, not
in any commit, not on any branch. Measured 2026-09-24 over all 4681 blobs of a clone fetched that
day (so: its branches and tags, not `refs/pull/*` and not branches created later),
searching both for the **values** this machine actually holds and for the two **shapes**
`SECRET_PATTERNS` knows. Zero hits by value. Twenty-three by shape, every one of them a labelled
fixture (a key whose own text says it is invalid and for testing, the jwt.io sample) — the check is worth
repeating exactly this way, because a shape match is also what a rotated *real* token looks like.
`.env` is git-ignored, and that it was never tracked is a separate measurement:
`git log --all -- .env` is empty.

`tests/test_brak_poswiadczen.py` keeps it that way, and **it scans history, not the working tree**
— blobs reachable from `HEAD`, blobs in the index, tracked files on disk, and untracked-unignored
ones. The first version read the tree only, so a token committed and deleted in the next commit
left the suite green; deleting the file is exactly the reflex a red test produces, and for the CEIDG
token rotation does not undo publication because the payload carries a PESEL. Two details that look
like fussiness and are not: NUL bytes are stripped before matching (PowerShell 5.1 writes UTF-16LE,
so a pasted token would be invisible), and the fixture allowlist is by **value hash**, not by path,
so a real key pasted into a file that already holds a fixture still fails.

**The observer for the history half sits in the test, not in a workflow file**, and that is not
belt-and-braces. `actions/checkout` defaults to `fetch-depth: 1`, under which the history scan sees
one commit and says nothing — green, which is the worst failure mode a guard has. This repository's
own `ci.yml` triggers on branch `ceidg-tool`, which no longer exists, so the `fetch-depth: 0` in it
runs nowhere; after the move, *their* workflows run the suite. A guarantee resting on someone
else's configuration is not a guarantee, so the fixture fails on a shallow clone itself. For the
same reason the history walk is scoped by `git rev-parse --show-prefix`: empty here, `ceidg-tool/`
after the move. Without it the move would widen the scan to the whole monorepo — `rev-list` ignores
the working directory — and redden their merge gate on `claude_summary/`'s fixtures.

Consequences for anyone who clones this: the README's opening block and `.env.example` say in so
many words that a credential has to be supplied, and a token is a human errand behind Profil
Zaufany. The rule the code applies is narrower than "commands that reach the register" — **a token
is required everywhere except `szukaj-pkd`, `token zapisz|usun` and `--demo`**, including
`eksportuj`, `runy`, `wyczysc`, `kreator` and `sprawdz-token`, which touch no network but resolve
settings first. Four documents said otherwise until a parametrized test over
`zarejestrowane_polecenia()` started measuring it. State that as a limitation
of the package, never as a defect of the checkout.

## Where this code lives now

This project is the **fourth sub-project of `PIWorkmate`** and lives in `ceidg-tool/` on `Main`,
since 2026-09-10 (`../docs/adr/0074-where-ceidg-tool-should-live.md`, at the repository root). It arrived with all 36
commits of its former standalone branch, whose authorship was unified in the same operation — so
every commit number changed. The standalone `ceidg-tool` branch was deleted on 2026-09-11.

The root of the repository holds an unrelated product. Changes here go the repository's ordinary
way — a topic branch and a PR to `Main` — and **force-pushing stays forbidden**, because the
history this project now shares is another team's active work.

**The quality gate is the root's, and it runs on ubuntu with `uv`** — the `ceidg-tool` entry in the
root `.github/workflows/ci.yml`. What stayed in the root's `.github/workflows/ceidg-tool.yml` is the
axis the matrix does not have: Windows and a second Python version, installed with `pip` from
`requirements.lock`. Both installs are pinned and both describe **one** resolution:
`requirements.lock` is generated from `uv.lock` (`uv export --frozen --no-hashes --all-extras
--no-emit-project`) and never written by hand, which `tests/test_locki_zgodne.py` enforces in both
directions. Before 2026-09-11 that axis delivered no pinning at all: the lock was read by nothing
and had drifted from `uv.lock` by two packages. The axis is narrowed by `paths`, not by branch,
because the matrix is four-way and a typo fix anywhere would otherwise spend the organisation's
Actions minutes on four full runs of this suite.

Both checkouts take **`fetch-depth: 0`**. `tests/test_brak_poswiadczen.py` scans blobs reachable
from `HEAD`, and at the default depth of one it would see a single commit and say nothing — so the
fixture fails on a shallow clone rather than passing in silence.

**The root `.gitignore` reaches into this directory.** Its rules are unanchored, so `*.xlsx`,
`*.db`, `*_state.json`, `RAPORT-*.md` and `*.tar` apply here too, on top of this project's own
`.gitignore`, whose deeper rules win where they negate. Nothing tracked today is affected (checked
file by file at the import), but a new file named like a generated report or workbook will vanish
from `git add` without a word. Negate it here if you ever need one tracked.

**ADR numbering is shared with the root and with `krs-tool`.** This project's ADRs were shifted to
**0024-0026** on 2026-09-24 because `0023` is taken (`0023_krs_company_risk_assessment.md`) and
`tests/test_adr_numbering.py` enforces uniqueness; each carries a line naming its previous number.

## Measured facts that change how you work

**The PKD dictionary is generated, never written by hand or by a model.**
`ceidg_tool/data/pkd2025.yaml` (728 subclasses) comes from `scripts/build_pkd.py` over an official
GUS export; the header carries the legal basis and the source's SHA-256. Regenerating it from
memory or from a summarised web page passes every automated check here — canonical keys, entry
count, agreement with codes the register returned — while being quietly wrong in names nobody
cross-reads. That inverts the operator's one control: the confirmation screen shows the PKD *name*,
so a wrong code should read as a wrong industry; a fabricated name makes the screen agree with the
model.

**The dictionary is right and it is not enough.** The `pkd` filter matches the code **as stored on
the record**, PKD 2007 stays legal until 31.12.2026, and each record carries one vintage. Measured
2026-09-07 over the 285 026 rows of `probe_out/raport_sample.zip` (a **wielkopolskie** archive, not
"the register"): 58.6 % still carry 2007 codes and **8.6 % (24 494) carry no code present in
`pkd2025.yaml`**. So a PKD-filtered fetch silently returns a subset on every input path, `--pkd`
included. `pkd=` matches **any** of a record's codes, not just `pkdGlowny` — the older 25.2 % figure
counted records whose *main* code was absent, which answers a different question. Two traps follow:
the classification and the filter are different things (`6201Z` does not exist in PKD 2025 and the
API returns 234 605 records for it), and the cost table prices what *will* be fetched with nothing
to compare against, so it is a spend control and never a scope one.

**One entry, two spellings — `id` is a value, not a string.** `/firmy` and `/firma` return the
identifier UPPER, `/zmiana` returns it lower, and `ids=` matches either. Because `firma.id` is a
case-sensitive primary key, `aktualizuj` wrote every changed entry twice and the detail cache could
never hit: 2 681 requests and zero usable records on the night of 2026-09-08. Canonicalisation lives
in `recordid.py` (boundary rule 14, carried by mypy through `KanonicznyId`) and applies **only to
hex GUIDs** — `/raporty` identifiers share the 8-4-4-4-12 shape, are not hex, and are
case-significant because they go into a URL. Do not relax that pattern to `[0-9A-Za-z]`.

**The evidence gets sanitised of exactly what matters — check the generator, not just the fixture.**
Four instances so far: a hand-written `rokPkd` line an ADR cited as a measurement;
`scripts/anonymize_samples.py` uppercasing every identifier, so the suite asserted the absence of
the property that broke production; `tests/support.py` building its own `httpx.Client`; and on
2026-09-24 the `--wynik json` gate, which ran `sprawdz-nip` with a NIP outside the demo corpus, so
`"firma": null` and no record was ever serialised — while the command crashed on every real hit.
`tests/fixtures/api_traits.yaml` states the measured per-endpoint properties in words and
`tests/test_api_traits.py` holds the fixtures to them.

**A guarantee whose violation has no observer is not a guarantee.** When adding a guard, ask what
would print if it were violated; if the honest answer is "nothing", that is the defect, not the
guard. This is the review standard here, and the method is mutation: change the claim, run the
gates, and see whether anything goes red.

**Silence is a defect, and it is measured in requests.** An operation can run half an hour at 3.75 s
spacing, and a hung-looking program gets killed. The rhythm of `Events` and `store.touch_lock()` is
counted in **requests sent or rows read**, never in pages or matched records — a `/zmiana` page is
500 identifiers, up to a hundred requests, and that off-by-a-layer error produced four defects on
2026-09-06, one of which let the lock expire under a working process. `Events.close()` belongs to
the operation that opened the bar; a live `rich` display overwrites everything printed after it.

The same applies to **waiting**: a single wait can outlast the lock (the budget brake clamps to
3600 s while the lease expires after 600 s), so `RateLimiter` sleeps in `WAIT_SLICE_S` slices with a
heartbeat before each, and `WAIT_SLICE_S < DEFAULT_LOCK_STALE_S` is a tested invariant the two
constants cannot check themselves (`ratelimit` may not import the database). Waits reach the **log
file** with their *predicted resume time*, because a duration alone cannot tell a limiter hold from
a suspended laptop.

**The tool refuses to go through a proxy, on purpose.** `httpclient.build_http_client` is the only
place an `httpx.Client` is made (rule 11). It always injects a transport — that is what stops httpx
reading `HTTPS_PROXY` — and passes `trust_env=False` to `httpx.HTTPTransport`, which is what stops
`SSL_CERT_FILE` replacing the CA bundle. Two mechanisms for two halves of §B, easy to confuse.
`AllowedHostsTransport` then refuses any host outside the selected environment. On a corporate
network the tool fails to connect rather than hand a PESEL-bearing token to an interceptor.

**Registry values are hostile input.** Names come from a register anyone can write into, and they
reach a spreadsheet (where a leading `=` is a formula) and a terminal (where `rich` reads brackets
as markup and ESC steers the screen). `safetext.py` neutralises the spreadsheet half, `richtext.safe`
the terminal half, and `richtext.py` is the only module allowed to hand `rich` an outside string —
boundary rule 10, enforced by an AST scan over every print call.

**A batched query must send the same filter as the un-batched one (ADR-0015).** `plan_batches` fills
a missing date edge with `DATE_FLOOR = 1990-01-01` or `today` so the plan is reproducible, but those
are **planning** values and must not reach `Criteria`: until 2026-09-09 they did, and splitting a
query silently changed its result set — 482 of 16 310 records (2.96 %), 76 registered before 1990
and 406 starting in the future, which CEIDG accepts. Do **not** add a legacy-fingerprint fallback
for old batches: an old closed `[1990-01-01, …]` batch is a different population.

**A report row's identity is a declared subset, never "everything" (ADR-0016).** Rows with neither
NIP nor REGON are keyed by *name + surname + given name + start date*, built in `recordid.py` from
**values** rather than a CSV row, so a future migration cannot compute a third digest. The tempting
wrong answer is "all columns except `Lp.`" — a status change would then mint a new identity.
Measured on 287 256 archive rows: 315 such rows, zero collisions, and the address adds no
discrimination.

**`/zmiana` is the staleness signal, and it beats the cache.** The freshness threshold for details
of changed entries is **the end of the change window**, not a cache TTL, which is why
`store.stale_detail_ids` takes it from its caller. Until 2026-09-08 it computed a seven-day TTL
itself, so an entry changed yesterday but fetched three days ago kept its pre-change `detail_json`
and counted as refreshed — 742 of 2 891 identifiers (25.7 %) on the operator's own database.
`count_run_unresolved` cannot see this (such a record is `pobrany`, i.e. resolved and untrue), so
`store.outdated_details` exists as its observer and reaches the log. A change range ending in the
future is refused rather than trimmed.

**The tool runs without the register, and that is a product feature (ADR-0014).** `--demo` answers
from a synthetic register generated in-process — generated, not recorded, because the anonymiser
has already erased a property a test was meant to check. The substitution belongs in `cli`, never
inside `build_deps` (a standing test asserts production builds its client with `transport=None`).
**Six markers, mandatory jointly**: first screen, `Metadane` row, `DEMO_` filename prefix (including
under `--out`), separate data directory, refusal to combine with production, and `"demo": true` in
the result envelope — the last because nothing behind an agent re-reads the first screen.

**The demo corpus writes no reference data from memory**, and that rule was learned twice in one
day. `demo/korpus.py` declares PKD *code pairs* and reads every name from the generated
dictionaries, refusing to build if a code is missing or if the pair is not a real 2007→2025
succession — the first draft invented names and two codes that do not exist. It also gave four of
five cities the wrong `powiat`. Both passed every automated check, because nothing checked them.

## Two output channels, one owning module each (ADR-0024)

Everything a person reads — screens, warnings, errors, progress — goes to **stderr**, through the
one console `richtext.make_console(stderr=True)` builds. **stdout carries the result envelope and
nothing else**, written only by `jsonout.py`, and only under `--wynik json`. State the change as
"stdout is now empty unless you ask for JSON", not "errors moved": `eksportuj`, `sprawdz-token`,
`wyczysc` and `token *` moved too with no envelope in exchange, so `sprawdz-token > out.txt` writes
an empty file. `--help` is the exception and stays on stdout, because click writes it.

That is **boundary rule 15**, stated on `json.dump` rather than `json.dumps` on purpose: `dumps`
returns a string, writes nothing, and already lives in six modules, so a rule naming it would be red
on its first run. It counts a bare `print` package-wide — measured 2026-09-24, one `print` in
`pipeline.run_update` made `json.loads(stdout)` fail with all 122 boundary tests green.
`console.print` is deliberately **not** counted: it writes wherever its console points, which is
rule 10's territory.

The envelope is a pure value in `ui/wynik.py`, built from what `flow` already returns, so the screen
and the envelope cannot disagree. `ui/wynik.py` may not import `pipeline` — a trap rule 6's scan
cannot see, because it reads imported *roots*; a separate test pins its relative imports to
`{criteria}`. Two consequences: **exit code 4 exists only under the flag** (`brak_trafien` and
`nic_do_zrobienia` are 4 with it and 0 without, so no existing schedule starts alerting on a
legitimately empty day), and `LineEvents` is chosen on `console.is_terminal` — not on the flag,
because `rich` renders intermediate `Live` frames only on a terminal, and tying the fix to the flag
would leave that silence open for everyone who is not an agent.

## Commands

```
PYTHONUTF8=1 .venv/Scripts/python -m pytest         # no network; no `-q` — see below
PYTHONUTF8=1 .venv/Scripts/python -m mypy ceidg_tool tests
.venv/Scripts/ruff check ceidg_tool tests scripts
.venv/Scripts/ruff format --check ceidg_tool tests scripts   # --check: bare `format` rewrites and cannot fail
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool     # the wizard
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool pobierz --demo -w wielkopolskie --szczegoly
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool szukaj-pkd fryzjer   # zero requests, no token
```

**Do not add `-q`.** `addopts = "-q"` is already in `pyproject.toml`, so an explicit one makes it
`-qq` — and at that verbosity pytest prints no summary line at all. Three revisions of this file
quoted a pass count the documented gate cannot print; that is the mechanical reason the number kept
drifting.

Counts, both measured 2026-09-24: **1510 passed, 1 skipped** locally with `probe_out/` present;
**1505 passed, 6 skipped** without it (measured by moving the directory aside, not by subtracting
five). A total quoted without naming its environment has been wrong three times here.

The skip that matters is
`tests/test_api_traits.py::test_atrapa_demo_zgadza_sie_ze_zmierzona_pisownia[raporty]`: the demo
double serves no `/raporty`, the open demo edge `docs/status.md` names. **The sentinel is that
test's name, not a total** — when it stops being skipped, the report path got a double.

The last two commands need no token and reach no register. `szukaj-pkd` needs no database either:
two packaged YAML files and a pure function, which is why it is also the first command anyone runs
while setting the tool up.

## Structural facts

`Criteria` is the only contract between any input and any fetch. Flags, the wizard and the assistant
all produce one; nothing downstream accepts anything else. The **YAML query file was withdrawn**
(ADR-0022) once every field had a flag: it was a second input format that could do nothing the first
could not, while being one more place to be wrong about what would be fetched. Its replacement is
`texts.polecenie_powtarzajace` — the wizard prints a ready-to-paste command, and a test feeds that
command back through `CliRunner`. Note it carries the PKD vintage as `--pkd-2007` rather than as
codes, the one place it is not equivalent to what the file stored.

`pipeline.py` is the only module that knows both the network and the database; `ui/` reaches the
world through it and never imports `client` or `store`. Two consequences: the report download takes
its lock heartbeat through a callback (`DownloadProgress`), and so does the limiter
(`RateLimiter(heartbeat=…)`), because neither `client` nor `ratelimit` may see the database.
`pipeline.LockHeartbeat` is the single object touching the lock — one per `Deps` — because a
detector that subtracts consecutive beats is wrong the moment some beats bypass it.

`ui/texts.py` produces view models with no output library, so every screen is asserted without a
terminal. `cli.py` authors no user-facing sentence of its own (rule 9), which is what makes rule 10
checkable by a syntactic scan at all.

One decision sequence lives in `ui/flow.py`: resume → report → `count` → cost table → choice →
optional split → fetch → export → summary. The `count` step is **one request, two when the PKD
vintage question is asked, and one more per widening the operator accepts at a zero result** — all
before consent, none after. Both vintage populations are counted before the operator answers, so the
question can state what will be missed or gained; when both come back zero the question is not asked
at all. **This sentence has been wrong twice**, and it is the sentence the next change will trust —
correct it in the same commit that moves the code.

**No operator input ends in a dead end (ADR-0017).** A description with no extractable filter opens
a clarification round — entered on `kryteria.is_empty()`, never on whether the model happened to
ask, because a guarantee that depends on the model is not a guarantee. Zero hits open a menu of
widenings from `Criteria.poszerzenia()`. A report that does not cover the criteria states which of
four reasons applies. All three keep their non-interactive behaviour, and the `safe_default` of each
question is where that is written down: widening must never happen for a schedule.

Boundary rules 1-15 live in `docs/design/phase2_core.md`. Rules 1-13 and 15 are an AST scan in
`tests/test_boundaries.py`; rule 14 is carried by mypy strict instead, and the design document says
so rather than counting it into the scan. Rule 12 has two halves and the second matters more: one
owner for the model SDK client **and** an explicit `api_key=` and `http_client=` in every
`Anthropic(...)` call, without which the SDK reaches for an ambient credential chain and builds its
own transport outside the egress gate.

## Where the design lives

- `docs/status.md` — the living plan: phases, gates, open items. Update it at every gate.
- `docs/design/phase2_core.md` — module map and the numbered boundary rules.
- `docs/adr/` — 0008 the user layer, 0011 the assistant, 0012 the PKD transition, 0013 record
  identity, 0014 the demo mode, 0015 batched-query edges, 0016 report-row identity, 0017 the
  clarification round, 0022 the query file withdrawn, **0024 the machine output channel, 0025 the
  assistant switch and its measured cost, 0026 `szukaj-pkd`**. 0018-0021 came out of the 2026-09-09
  audit and are **proposed** — read their status line before treating any as settled.
- `.claude/skills/ceidg-tool/SKILL.md` — how to **drive** the tool from flags rather than build it.
  In the repository because every fact in it is a fact about this code.
- `docs/decisions.md` — what the API probe measured. Observations, not guesses; check here before
  assuming how the API behaves.
- `INSTRUKCJA_CLAUDE_CODE.md` and `docs/reference/uzupelnienie-01.md` — the requirements; the
  supplement wins where they disagree. It is cited in two shapes on purpose: prose gives the
  path, code cites the bare name (`uzupelnienie-01.md §B`), because the full path pushes
  docstrings past the 100-character limit ruff enforces.
- `docs/audit-2026-09-09.md` — the audit, its mutation sweep and the ranked remediation list. Read
  the Tier A table before assuming a defect is still open.
- `docs/resilience-report.md`, `docs/test-runs-phase4.md`, `docs/demo-walkthrough.md`,
  `docs/research/public-search-parity.md`.

## Conventions

Code comments, docstrings and user-facing text are Polish; documents under `docs/` are English. Line
length 100, mypy strict over both `ceidg_tool` and `tests`. Comments carry the *why* — most of the
ones here record a defect that was found and closed, so a comment explaining a guard is usually
load-bearing history rather than noise.

Fetched production data stays out of the repository: `tests/fixtures/` holds anonymised copies, raw
samples live in the git-ignored `probe_out/`.

A phase that touches behaviour ends with a code review, and a review finding is applied or argued
against explicitly, never silently dropped.
