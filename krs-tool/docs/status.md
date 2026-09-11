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

## Step 3 — the signal catalogue as data — **done 2026-09-11**

Produced: `signals/katalog.py` (the loader-guard), four rule files plus `zablokowane.yaml`,
`katalog_sygnalow` in `texts.py`, the `katalog` command, `tests/test_katalog.py`, and boundary rules
4, 5, 10 and 11 with their self-tests.

Gates, measured 2026-09-11: **136 passed**, ruff and format clean, mypy strict clean over 35 files.

Nine rules: four on division 4 (tax and social-insurance arrears under enforcement, creditors with
writs, bankruptcy petitions), one on division 5 (curator), three on division 6 (liquidation,
dissolution, and — as context rather than risk — mergers and transformations), and one on missing
financial statements.

**A command, not a script.** The plan called for `scripts/pokaz_katalog.py`; `krs-tool katalog` is
strictly better — the person reviewing the catalogue is not the person who runs scripts, and the
command obeys the output rules the script would have sat outside of.

**Boundary rules 4, 5, 10 and 11 arrived here rather than in step 4**, because they govern the code
and data written in this step, and an observer that arrives early is never worse. Two of them are
worth restating:

- **Rule 10 forced a design choice.** `Poziom` takes its values from `auto()` rather than from
  literals, because the pakiet forbids any number other than 0 and 1. Six months and fifteen days
  have nowhere to live except the catalogue, and "31 December" cannot be written at all.
- **Rule 11 scans values, not files.** The lexicon check parses the YAML and reads the string
  constants of `texts.py`, deliberately skipping comments and docstrings — the file explaining why
  we never accuse anyone has to be able to name the accusation it forbids.

**Two things the catalogue says out loud, and both are the point of it being data:**

- `dzial5_kurator` carries `podstawa_potwierdzona: false` and prints as `[DO POTWIERDZENIA]`. We know
  from the extract that division 5 concerns a curator; the reconnaissance never confirmed which
  article governs it. Writing a plausible article number would have passed every automated check
  while being quietly untrue — which is the exact failure this project tracks in other people's
  literature.
- `brak_wpisu_o_sprawozdaniu` prints `może wystrzelić: nie` and names the three premises that cannot
  be resolved today. That is the required state, visible on the artefact rather than buried in a
  comment.

The loader refuses, rather than defaulting: five premises instead of six, a term counted from
anything but the balance-sheet date, a missing `zywotnosc`, a missing `podstawa_potwierdzona`, an
unknown source of determination, or a code on the blocked list. Each refusal has a test with a seeded
defect.

**A defect the test suite caught in its own test:** the first version of the level-name check asserted
that no member contains "termin", which failed on `TERMINALNY` — a legitimate name meaning
"near-terminal", not "late". The check now looks for accusation stems.

**For review:** run `krs-tool katalog` in a wide terminal — the code column truncates in a narrow
one. The object of the review is the statutory-basis column and the `[DO POTWIERDZENIA]` marks.

Invariant with an observer: six premises cannot quietly become five; `zywotnosc` and
`podstawa_potwierdzona` have no defaults; a blocked rule refuses to load; no number other than 0 and 1
lives in the signal layer; no accusation word reaches a user.

## Step 4 — rules over the read model — **done 2026-09-11**

Produced: `signals/model.py` (the three-valued result, plus `Obserwacja`, `Powod`, `Werdykt` and
`Niewiadoma`), `signals/terminy.py`, `signals/ocena.py` — the plan named no file for "the rule
implementations" and this is it — the `zakres` field with three new loader guards, one new catalogue
rule, `ocena_ryzyka` in `texts.py`, the `ocen` command, boundary rules 8 and 9 with their self-tests,
and `tests/{test_terminy,test_ocena,test_ekran_oceny}.py`.

Gates, measured 2026-09-11 after the code review: **212 passed**, ruff and format clean, mypy strict
clean over 41 files.

**Acceptance was again a demonstration of red**, in real files rather than in fixtures:

| Seeded violation | What fired |
|---|---|
| a second producer of `TerminUstawowy`, in `signals/ocena.py` | rule 9, the producer scan |
| a second producer of `NumerKRS`, in `odpis/czytanie.py` | rule 8, the same scan |
| `zakres: caly_dzial` deleted from the catalogue | the loader guard, plus two catalogue tests |

Each was seeded, observed red, and reverted; the suite returned to 205 passed.

### The measurement that changed the design

The read model knows three things about a division — absent, present-and-empty, present-and-non-empty
— and **does not know which entry sits in it**, because the key names inside a division have never
been measured. Four rules therefore share division 4 and are mutually indistinguishable, which the
catalogue's `moze_wystrzelic` never claimed one way or the other: that property answers "are this
rule's premises resolvable", not "can this rule be told apart from its neighbours".

Collapsing the four into "something is in division 4" would have been the wrong repair, because a
result is per rule and a reader compares it with the catalogue. So the distinction went into the data
instead: a rule now declares `zakres` — `pojedynczy_wpis` or `caly_dzial` — without a default, and

- a `pojedynczy_wpis` rule never concludes anything from a non-empty division — it returns
  `Nieustalony` naming *the reader*, not the register, as the reason;
- the signal that genuinely follows from a non-empty division 4 became **its own rule**,
  `dzial4_niepusty`, at terminal level, because every entry that division may carry is terminal;
- division 6 gets none and should not, because its rules disagree on level — liquidation is terminal
  and a transformation is context, and one number cannot stand for both;
- division 5 is **silent**, and that is the review's doing — see below.

**This is the row for the legal review**, and it is one sentence: does art. 41 enclose division 4 so
that *every* entry it may carry is a terminal-level signal? If not, the new rule changes level or
goes. The loader refuses more than one whole-division rule per division, so the answer cannot be
fudged by adding a second.

### What the code review changed, and it was the centre of the step

The first version derived "is this rule resolvable" from **how many sibling rules happened to share a
division**: a rule alone in its division fired on mere non-emptiness, because it had nobody to be
confused with. The review took that apart with three consequences, each reproduced against the real
catalogue:

- `dzial5_kurator` printed `sygnał` for a non-empty division 5 — a claim that division 5 encloses
  only curator entries, which **nobody declared and nobody reviewed**, on the one rule in the tree
  whose `podstawa_potwierdzona` is `false`;
- deleting three of the four division-4 rules — an ordinary catalogue edit — would have promoted the
  survivor to a rule naming *tax arrears* at a named company, reaching the guess by subtraction
  instead of by writing a key name;
- a single-entry rule on a division that is never empty (1 or 2) would have fired for every company,
  and the loader would not have objected.

The fix is one line of policy: **the right to conclude from non-emptiness is declared and reviewed,
never inherited from the neighbourhood.** Only `zakres: caly_dzial` fires; `_dzialy_z_wieloma_wpisami`
and the sibling-count parameter are gone. Division 5 therefore says nothing until a whole-division
rule is written for it — which needs the same legal question answered first, and that question is the
open `podstawa_potwierdzona: false` on the curator rule. Losing the product's second signal is the
correct outcome: it was never earned.

The review's second finding was the only remaining path from a reading limitation to a statement about
a company. A mention whose period we cannot parse was dropped from the candidate-period computation
(correctly) and then **not mentioned again**, so with the premise gate open the verdict read "no entry
for the candidate period" about an extract that carries a mention we simply could not read. Such a
mention now produces its own `Niewiadoma` and the verdict degrades to `Nieustalony`.

Three smaller repairs from the same review: the mention kind a `brak_dokumentu` rule examines moved
from code into the catalogue (a second such rule would otherwise have been judged against financial
statements and delivered a verdict about the wrong document); the loader now refuses `zakres` on a
rule where it means nothing, instead of ignoring it; and the assumption of financial-year continuity
rides with **every** verdict computed from the shifted limiter, not only with the undetermined one —
it used to disappear exactly where the verdict is strongest.

### Two blockers, not one

Between today and a missing-statement signal stand **two** independent things, and the result now
tells them apart instead of merging them into "unknown":

- the **catalogue** declares three of the six premises undeterminable from an extract — closed by a
  measurement or the ministerial answer, not by us;
- the **reader** does not extract a registration date, so a fourth premise (`rozpoczecie_w_ii_polroczu`)
  is unresolved on our side, although the catalogue says the register carries it in divisions 1 and 3.

A test removes both at once — a seeded catalogue plus a substituted resolver table — and shows the
path to a signal exists. Without it, "the rule cannot fire" would be indistinguishable from "the code
for firing was never written".

### Rule 9 shaped the term arithmetic

`DzienBilansowy` is produced only by `odpis/czytanie.py`, so a *derived* balance-sheet date is not one
— and the candidate year is the one **after** the last filed period, which by definition is not in the
extract. Hence: the candidate is named by the period it follows, and its limiter is the previous
limiter shifted by a financial year. The assumption that the next year is the same length then rides
**in the result** — every verdict, `Sygnal` included, carries `zalozenia`, and the screen prints
them — instead of sitting in a comment.

Two smaller things worth keeping in view. The limiter clamps to the last day of the month
(art. 112 k.c.) — 31 August plus six months is the end of February, and the "obvious fix" of
overflowing into March would move a statutory date. And the number twelve is taken from the calendar
module rather than written down, because rule 10 forbids it here and the exception would be the first
crack in it.

**A confession in the same spirit as steps 2 and 3.** Three of the four dates I wrote by hand into
`test_terminy.py` were wrong and the implementation was right — six months plus fifteen days across a
leap year is not something anyone computes correctly in their head. The golden dates now sit in a
table with the reason for each.

**For review:** `krs-tool ocen --plik <odpis>` on a wide terminal. Three objects, all legal rather
than technical: the `dzial4_niepusty` row with its statutory basis; whether division 5 should get a
whole-division rule of its own (which needs the scope of division 5 confirmed first); and whether the
substitute limiter should move to the next working day under art. 115 k.c. — it does not today, the
error leans toward firing, and it is recorded as row 11 of `pomiary.md` rather than carried silently.
The `brak_wpisu_o_sprawozdaniu` row must read as a list of unresolved premises and never as a
statement about the company.

Invariant with an observer: a marked type has exactly one producer; **only a rule that declared
`caly_dzial` may conclude anything from a non-empty division**; an unreadable mention cannot vanish
from the computation; an unreadable premise cannot become "does not apply"; an assumption reaches the
printout from every branch; the missing-statement rule produces no signal on any extract shape the
builder can make; the level column says whose level it is.

**Still blocked on the owner:** the same real extracts as step 2. Until they arrive, rows 4-7 of
`niezmierzone.md` stay open and the division-attribution gap stays where it is — no amount of code
closes it, only a file.

## Step 5 — the report the owner reads — **done 2026-09-11**

Produced: `raport/texts.py`, `raport/markdown.py`, `marktext.py`, the `raport` command with an
optional `--markdown`, four golden reports in `tests/golden/`, `tests/test_raport.py`, boundary rule 6
extended to pairs and **boundary rule 13**, new.

Gates, measured 2026-09-11: **247 passed**, ruff and format clean, mypy strict clean over 46 files.

**Two deviations from the plan, both deliberate.**

`raport/render.py` does not exist; `render_raport` sits in the existing `render.py`. The modules that
know `rich` are named one by one in the rule-6 test, because each is a separate place where a string
can reach the terminal without a neutraliser. A fourth would have cost exactly what it saved. The
markdown channel did get its own file, because it has its own neutraliser — that is the difference
that earns a module here.

The read model gained `Dzial.klucze`: **the verbatim field names the file carries inside a division**,
transcribed and not interpreted. A signal has to carry a quote from the extract, and for a division
signal there was nothing to quote — the reader keeps three states and nothing else. The names assert
nothing; and the same field is what closes row 10 of `niezmierzone.md` the day a real extract arrives,
because the report prints exactly what the register put there. `signals/` may not read it — that is
rule 13, with a scan and a seeded self-test, because a rule pinned to a guessed key name is
indistinguishable from one pinned to a measured name.

**The channels are a pair, and the scan says so.** `richtext.safe` strips `rich` markup and passes
`](http://…)` through untouched — which in markdown is a link to a foreign address, inside a document
somebody forwards as a report about a company. So `raport/markdown.py` must call `safe_md` and may not
import `richtext`; `render.py` the reverse; and every interpolation in the markdown module must be a
neutraliser call, even one holding program text, because an exception for "our own constants" admits
every name. The hostile-name fixture goes through both channels and the golden file shows the result.

**What the report says that a shorter one would not.** "No signal" is opened with the sentence that it
does not mean the company is sound. The unresolved section names **who closes each item** — a
measurement, one change in this tool, or a different extract. And "what this tool does not claim"
states that it does not see the debtors register: the strongest single signal in this field, which
this tool does not reach. Omitting a source one does not consult is worse in a risk report than
publishing no report, because the reader reads silence as absence of an entry.

**For review:** `krs-tool raport --plik <odpis> --markdown raport.md`, then read the markdown in a
viewer rather than in a terminal — the escaping is visible in the source and invisible once rendered,
and that is the intended state.

Invariant with an observer: every printed signal carries four things and the golden test fails when
one is lost; two channels have two neutralisers, one pairing scan and one hostile fixture; the signal
layer cannot see inside a division.

## Step 6 — journal and replay — **done 2026-09-11**

Produced: `dziennik/{zapis,ladunki,skroty,odtworzenie}.py`, `magazyn.py`, the `odtworz` and
`wyczysc-ladunki` commands, journalling inside `raport` (with `--bez-dziennika`), a top-level error
handler in `__main__.py`, boundary rule 12 with its self-tests, and `tests/test_dziennik.py`.

Gates, measured 2026-09-11: **282 passed**, ruff and format clean, mypy strict clean over 53 files.

**The split between journal and payload is the whole step.** A journal line is tiny and permanent;
the payload — the extract itself, which carries the personal data of everyone sitting on the
company's boards — lives beside it and may be deleted. Adding that split later would have been a
migration of live personal data, which is the operation nobody performs at a good moment.

`dziennik/ladunki.py` is therefore the **only** module in the tree that deletes, and a scan pins that
to exactly one file: deleting a journal line and deleting a payload look nearly identical in code and
mean opposite things — the first erases the fact that an assessment happened, the second is hygiene.

**Replay can say three things and two of them are refusals.** Identical; differs — naming the rules
whose verdict changed and whether the rule catalogue is no longer the same; and *not reproducible from
retained data*, which is what an assessment says once its payload has been purged. Because the
assessment never reads a clock (ADR-0001 decision 6), a difference can only come from the material or
from the catalogue — never from a day having passed. Without that property replay would have nothing
to assert.

Three decisions worth keeping in view:

- **The identifier is derived from the extract's own hash**, so the same file always yields the same
  assessment id and nobody has to remember one. Two assessments of the same material are two journal
  lines and one id, because the journal never overwrites.
- **The line carries the verdict per rule**, not only the result hash. The hash answers "is it the
  same"; the verdicts answer "what changed", and that second question is the only reason anyone runs
  replay. Ten rules keep the line readable in an editor, which is the format's entire point.
- **`wyczysc-ladunki` without `--potwierdzam` deletes nothing** and prints what would go. A deleting
  command that deletes immediately is a command that deletes by accident.

**One thing this step revealed that belonged to step 1.** Nothing translated the error taxonomy into
an exit code: every `KrsError` reached the operator as a traceback, including states that are entirely
foreseen — a purged payload, a file that is not an extract. `__main__.py` now owns that translation,
in one place, and the console script points at it.

**For review:** open `dziennik.jsonl` in a text editor; run `odtworz` on a fresh assessment, then
`wyczysc-ladunki --potwierdzam`, then `odtworz` again and read the refusal.

Invariant with an observer: the journal is opened only to append or to read and never deletes;
deletion lives in exactly one module; replay is a command that can fail, and each of its three answers
has a test.

## Step 7 — gate

A walk over real extracts, a code review, an update of this file naming which "Gate" rows stage 1
closed and which it leaves open and why, and a correction of `../CLAUDE.md` against what actually
stands in the tree.

## Open items carried from ADR-0023

Rows of the "Gate" table that stage 1 cannot close, and does not pretend to: the registry publication
lag; the two unsampled company types; the packaging of a repository document; anything about API
pacing. All of them live in `niezmierzone.md` with the event that closes them.
