# Status: the living plan

Date: 2026-09-10
Status: living — update at every step gate
Author: P0w3r223
Related to: `adr/0001_zakres_etapu_1_i_granica_offline.md`, `design/etap1_core.md`, `niezmierzone.md`

---

A step ends when the owner accepts it, not when the tests go green. Each step below states what is
produced, how to check it without a network, and which invariant gains an observer.

## Step 0 — decisions and document skeleton — **done 2026-09-10**

Produced: `adr/0001`, `design/etap1_core.md`, `niezmierzone.md`, this file, `../CLAUDE.md`. No code.

How to check: read against the "Gate" table and the open-questions table of
`ceidg-tool/docs/adr/0023`, row by row, answering "where does this row have an observer in stage 1,
or why does it have none".

Invariant with an observer: **none, and that is worth saying out loud.** This step produces the
*statement* of the invariants; steps 1-6 supply the observers.

## Step 1 — empty sub-project, closed offline boundary, four green gates — **done 2026-09-10**

Produced: `pyproject.toml`, `.gitignore`, the copied helper modules (`errors`, `clock`, `secrets`,
`logsetup`, `safetext`, `richtext`, `progress`, `console`), `texts.py` and `render.py`, a `cli.py`
printing the first screen, a fifth entry in the root CI matrix, `tests/test_granice.py` covering
rules 1, 2, 3, 6 and 7 with their self-tests, `tests/test_bramka_ci.py` reading the root workflow,
`tests/test_pierwszy_ekran.py`.

Gates, measured 2026-09-10: **51 passed**, `ruff check` clean, `ruff format --check` clean, `mypy`
strict clean over 19 files.

**Acceptance was a demonstration of red, and the plan's phrasing needed correcting.** "Adding
`import httpx` lights three tests" is not true and cannot be — each observer catches a *different
vector*, which is the point of having three:

| Seeded violation | What fired |
|---|---|
| `import httpx` in `krs_tool/texts.py` | rule 1, the import-graph scan |
| `"httpx>=0.27"` added to `[project].dependencies` | rule 2, the manifest check |
| `socket.create_connection(...)` inside the suite | rule 3, the socket ban (permanent test) |

Each was seeded, observed red, and reverted; the suite returned to 51 passed.

Two findings from step 1 worth carrying forward. The scan copied from `ceidg-tool` matched import
**roots**, so `import urllib.request` and a bare `import_module("httpx")` both slipped through the
first version — both are covered now, and both are in the self-test table. And two files legitimately
know `socket`, because their job is to forbid it; the exemption list is closed at exactly those two
and has its own test, since an exemption without an observer is the shape a violation hides in.

`scripts/` is not yet in the mypy or lint scope: the directory does not exist until step 2 brings the
anonymiser, and a path declared before it exists breaks the gate.

Invariant with an observer: absence of network, in the import graph, in the manifest and at the
socket, each with a self-test.

## Step 2 — the extract becomes a read model, the operator file becomes evidence

Produces: `identity.py`, `odpis/`, `scripts/anonimizuj_odpisy.py`, `probki/` (ignored), anonymised
`tests/fixtures/`, `tests/fixtures/odpis_traits.yaml`, `test_odpis_traits.py`,
`test_anonimizator.py`, `docs/pomiary.md`.

**Blocked on the owner:** manually saved extracts — at minimum a healthy company, one in
liquidation, one **suspended**, a **partnership of natural persons**, and one with a non-calendar
financial year. Without the middle two, rows 4 and 5 of `niezmierzone.md` stay open and stage 1 ends
with them open, deliberately.

How to check: `krs-tool pokaz --plik odpis.json` prints a card, reporting unreadable entries as
unreadable rather than guessing.

Invariant with an observer: a claim about the register cites a file with a hash; the anonymiser
cannot delete a property the traits file names.

## Step 3 — the signal catalogue as data, before any rule runs

Produces: `signals/reguly/*.yaml`, `signals/katalog.py` (loader-guard), `signals/reguly/zablokowane.yaml`,
`scripts/pokaz_katalog.py`.

How to check: a one-page printout of the catalogue — code, level, statutory basis, lifetime,
excluding premises. This is the artefact a lawyer reads, and it is the object of acceptance.

Invariant with an observer: six premises cannot quietly become five; `zywotnosc` has no default;
a rule on the blocked list refuses to load.

## Step 4 — rules over the read model

Produces: `signals/model.py`, the rule implementations, `signals/terminy.py`, boundary rules 4, 5, 8,
9, 10 and 11 with self-tests.

How to check: `krs-tool ocen --plik odpis.json` over every sample. On the missing-statement path the
result is `Nieustalony` with the unresolved premises listed — not an accusation.

Invariant with an observer: no rule reads the clock; six months and fifteen days live only in the
catalogue; the accusatory lexicon does not occur in the tree.

## Step 5 — the report the owner reads

Produces: `raport/texts.py`, `raport/render.py`, `raport/markdown.py`, `marktext.py`, golden reports.

Sections that are the object of acceptance: signals with level, statutory basis, observation date
from `stanZDnia` and a quote from the extract; **"unresolved"**; and **"what this tool does not
claim"** — including, explicitly, that it does not see the debtors register, the strongest single
signal, rather than omitting that source in silence.

How to check: reports for every sample side by side, plus a fixture with a hostile name rendered to
both channels.

Invariant with an observer: every printed signal carries four things, and the golden test fails when
any is lost; two output channels have two neutralisers and one scan.

## Step 6 — journal and replay

Produces: `dziennik/zapis.py`, `krs-tool odtworz --ocena-id X`, `krs-tool wyczysc-ladunki`, rule 12.

How to check: open the journal in a text editor — one line per assessment. Then `odtworz` on an old
assessment printing "identical". Then purge payloads and watch `odtworz` answer **"not reproducible
from retained data"**.

Invariant with an observer: reproducibility is a command that **can fail**; the split between journal
and payload exists from day one, because adding it later is a migration.

## Step 7 — gate

A walk over real extracts, a code review, an update of this file naming which "Gate" rows stage 1
closed and which it leaves open and why, and a correction of `../CLAUDE.md` against what actually
stands in the tree.

## Open items carried from ADR-0023

Rows of the "Gate" table that stage 1 cannot close, and does not pretend to: the registry publication
lag; the two unsampled company types; the packaging of a repository document; anything about API
pacing. All of them live in `niezmierzone.md` with the event that closes them.
