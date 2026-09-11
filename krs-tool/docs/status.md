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

## Step 2 — the extract becomes a read model — **done on synthetic material 2026-09-11**

Produced: `identity.py`, `odpis/{model,czytanie,zrodlo}.py`, `anonimizacja.py`,
`scripts/anonimizuj_odpisy.py`, `tests/budowniczy.py`, `tests/fixtures/odpis_traits.yaml`,
`docs/pomiary.md`, the `pokaz --plik` command and the subject card, plus
`test_odpis.py`, `test_identity.py`, `test_karta.py`, `test_anonimizator.py`,
`test_odpis_traits.py`.

Gates, measured 2026-09-11: **103 passed**, ruff and format clean, mypy strict clean over 32 files.
`scripts/` is now inside both scopes.

**The owner chose to build on synthetic extracts, with real ones to follow.** That is a legitimate
way to build a reader and an illegitimate way to learn what a register returns, so the separation is
mechanical rather than a matter of care:

- `tests/fixtures/odpis_traits.yaml` holds claims about **files an operator supplied**, each citing
  file, SHA-256, date and supplier. It is **empty**, and that is the true state.
- `docs/pomiary.md` declares `zmierzonych-wlasnosci: 0` and lists the **nine assumptions** the reader
  now encodes, with what breaks if each is wrong. A test keeps that number equal to the number of
  entries in the traits file, so the document cannot drift by being forgotten.
- A synthetic extract may never be cited as evidence — a test rejects any traits entry naming one.
- Every synthetic extract carries a key the register will never emit, and a card built from one wears
  a marker in its title and its first note. Verified by eye and by test.

Three design decisions worth keeping in view:

- **A division has three states, not two.** Absent from the file, present-and-empty,
  present-and-non-empty. Collapsing the first two would make the division-4 flag — the cheapest
  level-1 risk signal there is — rest on an ambiguity.
- **An unreadable period is reported, never guessed.** The model keeps the raw text alongside
  `okres=None`, the card prints it as unreadable, and the count of unreadable entries appears in the
  notes.
- **The anonymiser lives in the package, not in the script.** It produces evidence, so it obeys the
  package's rules. Its list of preserved properties is a tuple of codes checked against the test
  module in both directions: a property added without a test fails the gate, and so does the reverse.

Two defects the gates caught while writing this step, both worth recording because they are the kind
that pass a code review: the NIP checksum zipped nine weights against ten digits (`strict=True`
caught it), and the word-form period branch reused the names bound in the numeric branch, which mypy
rejected as a type change rather than the copy-paste it was.

**Still blocked on the owner:** manually saved extracts — a healthy company, one in liquidation, one
**suspended**, a **partnership of natural persons**, and one with a non-calendar financial year.
Until they arrive, rows 4-7 of `niezmierzone.md` stay open, the nine assumptions stay assumptions,
and the missing-statement rule of step 4 will be structurally unable to fire.

Invariant with an observer: a claim about the register cites a file with a hash; a synthetic extract
cannot become evidence; the measurement count in the document equals the number of claims; the
anonymiser cannot delete a property it declares it preserves.

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
