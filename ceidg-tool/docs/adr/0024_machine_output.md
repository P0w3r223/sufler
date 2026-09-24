# ADR-0024: A machine caller gets an envelope on stdout, a human keeps the screen, and neither can silence the other

Date: 2026-09-23
Status: **accepted** (the owner's decisions, given in session 2026-09-23)
Author: P0w3r223
Related to: ADR-0008 (one decision sequence, injected `Prompter`), ADR-0009 (boundary rules 9 and
            10, `richtext.py` as the single rich seam, and its "revisit when a second output
            channel appears"), ADR-0014 (the five demo markers), ADR-0017 (`safe_default` per
            question), ADR-0022 (flags are the only input), ADR-0025 (the assistant is never built
            for a machine caller), ADR-0026 (`szukaj-pkd`, the first command wired to the
            envelope), docs/design/phase2_core.md (rules 6-15), CLAUDE.md ("silence is a defect,
            measured in requests")

**Renumbered from ADR-0023 on 2026-09-24.** This project's remote home moved into the WorkMate monorepo, where `ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md` already holds that number and `tests/test_adr_numbering.py` enforces uniqueness. The commit messages of the work this document describes still say ADR-0023; they were written before the collision was visible and are left as they were, because a commit message is a record of what was known at the time.

---

## Context

The tool has one caller in mind — a person at a terminal — and is acquiring a second: an agent
driving it with flags and `--tak`. Four defects were measured on 2026-09-23, and they are the same
defect at four layers: **every fact the tool produces is encoded for a screen, and a screen is
allowed to change its wording.**

| Claim | Evidence |
|---|---|
| The summary folds the output path | `ui/render.py:31-35` builds the key-value table with `add_column("wartość", overflow="fold")`; `texts.summary_table:757-759` puts `f"{path} ({format_size(path)})"` in that column. Measured at 80 columns (what `rich` assumes off a terminal): `…\demo\wyniki\DEMO` on one row, `_ceidg_wielkopolskie_test_20260923_1304.xlsx (17 KB)` on the next, with a border between. The one datum a machine caller always needs is the one the rendering breaks. |
| Errors go to stdout | `cli._fail:171` → `ConsoleView.error` → `richtext.make_console()`, which is built with no `file=` and therefore resolves to `sys.stdout` at write time. Measured: `pobierz --demo --tak --format xml` exits 3 with the message on stdout and stderr empty. |
| Zero hits exit 0 | `flow.prepare_fetch:445-461` returns `"wyjdz"` at `count == 0`; `cli.pobierz:566-567` returns → exit 0, identical to a successful fetch. Measured with `--pkd 9999Z`. |
| There is no machine output at all | `--format xlsx,csv,jsonl` exports *records* (`exporter.write_jsonl:488`). Nothing reports *what happened* — paths, run ids, requests spent, why it stopped. A search for `--json` or `machine` across `ceidg_tool/` returns nothing. |

**A fifth defect was found while designing the fix, and it decides the liveness question.** `rich`
15.0.0 (`live.py:269-276`) renders intermediate frames only when `console.is_terminal`. Off a
terminal nothing is emitted until the bar stops, so a caller that pipes the tool today sees **no
per-request rhythm at all** — only `ConsoleEvents.on_message` lines reach a pipe. The doctrine
"silence is a defect, measured in requests" is currently satisfied for a terminal and for nothing
else, which means the machine caller's silence is *worse* than the operator's, not better.

Four constraints bound every option below:

- The human default does not change, except where this ADR says it does and why.
- Input stays flags. ADR-0022 withdrew the query file because a second input format is a second
  way to be wrong about what will be fetched; nothing here reopens it. This ADR is **output only**.
- Rule 9 (`cli.py` authors no sentence and has no output channel) is the precondition that makes
  rule 10 checkable by a syntactic scan. A second channel must not weaken either.
- Silence is a defect for the agent too.

ADR-0009's closing line named this moment: *"Revisit when a second output channel appears (TUI,
GUI, web) — `richtext.py` is then the seam to generalise."* The generalisation taken here is not
"teach `richtext` to speak JSON"; it is **every channel has exactly one owning module and one
named neutraliser**.

## Decision 1 — what the machine contract contains

| Option | Verdict |
|---|---|
| **A.** Serialise `Block` (title / headers / rows / notes) to JSON | **Rejected**, and it is the tempting answer. It is screen-scraping in JSON clothing: an agent would key on Polish row labels, and `texts` would silently become an API — exactly the freedom this project uses constantly. |
| **B (chosen).** A typed **result envelope** built from the values `flow` already returns (`ExportSummary`, `RunResult`, `FetchPlan`, `ExecuteResult`) | The facts an agent needs already exist as typed values; the screen is one rendering of them and the envelope is another. Nothing new is computed, so the two cannot disagree about what happened. |
| **C.** A JSONL event stream as the only output | Rejected: no single place to read "what happened", and record data already has `--format jsonl`. |

One JSON document, printed once, at the end, with Polish keys, ASCII-folded (`blad`,
`kod_wyjscia`) — the same vocabulary as the workbook (`nip`, `rekordy`, `run_id`), because the
operator and the agent should not learn two names for one column.

**`zapytania`, not `zadania` — corrected 2026-09-23 while implementing.** The first draft folded
`żądania` to `zadania`, which is not a fold at all: `zadania` is an ordinary Polish word meaning
*tasks*, so the one key an agent reads to learn what the call cost said something else entirely.
The cost table has called these `zapytań` since phase 3 (`texts.cost_table:581`), the word folds
to ASCII without collision, and this ADR's own rule is that the operator and the agent do not
learn two names for one thing. `LineEvents` prints the same word.

```jsonc
{
  "wersja": 1,                       // bumped only on a removal or a rename
  "polecenie": "pobierz",
  "status": "ok",                    // ok | brak_trafien | nic_do_zrobienia | przerwano | blad
  "kod_wyjscia": 0,
  "demo": false,
  "srodowisko": "prod",
  "zapytania": 37,                   // requests spent in this invocation
  "kryteria": { },                   // Criteria.model_dump(mode="json")
  "run_ids": ["…"],
  "rekordy": 1234,
  "pliki": ["C:\\…\\wyniki\\CEIDG_prod_wielkopolskie_20260923_1203.xlsx"],
  "uwagi": [ {"kod": "RAPORT_BEZ_LINKU", "tekst": "…"} ],
  "blad": {"typ": "AuthError", "komunikat": "…"}   // present only when status == "blad"
}
```

Per command: `sprawdz-nip` adds `firma`; `runy` adds `runy`; `raporty` adds `raporty`;
`aktualizuj` adds `zmienione` / `szczegoly` / `nierozwiazane` / `przeterminowane` (the four numbers
`texts.update_summary` already receives); `szukaj-pkd` adds `trafienia` (ADR-0026).

**Sub-decision — `uwagi` are codes where a closed set already exists, prose where it does not.**
`PowodBrakuRaportu`, `OgraniczenieKod` and `DLACZEGO_PUSTO` are this project's own pattern: a code
in `flow`/`criteria`, a sentence in `texts`. The summary notes (`texts.summary_notes:790`) are
prose only. Converting all of them is the largest single piece of work here and it is **not** on
the critical path: v1 ships codes for the sets that exist and prose for the rest, with `"tekst"`
documented as *not* a contract — an agent matching on it is matching on a sentence this project
rewrites whenever it finds a defect. Codes arrive for a note when a caller needs that note.

## Decision 2 — which stream carries what

| Option | Verdict |
|---|---|
| **A.** Envelope to a file named by `--wynik-plik` | Rejected on a concrete ground, not on taste: §B confines written files to `wyniki/`, and an envelope from `sprawdz-nip` carries one person's name, address and phone. That is a personal-data artifact with no retention story — `wyczysc` would not touch it. The envelope is a message, not an artifact. |
| **B (chosen).** `--wynik json`: stdout carries the envelope and nothing else; **every** screen, warning, error and progress line goes to stderr. | One parseable stream, and nothing is suppressed — the agent still gets the whole human narrative, on the channel where prose belongs. |
| **C.** JSON to stdout, human output suppressed | Rejected: a half-hour run with no output is the defect this project has fixed four times. |

**Errors and warnings move to stderr on the human path too — the owner's decision, 2026-09-23.**
This is the one place a human-visible default shifts, and it was taken deliberately against the
alternative of routing errors by mode. Three reasons: it is invisible on a terminal (`rich` flushes
per print, so interleaving is preserved); it costs **zero test rewrites**, because
`typer.testing.Result.output` is the mixed stream (`typer/testing.py:45-60, 102-107`) while
`result.stdout` becomes newly available as the sharper assertion; and two error paths would mean a
rule in two copies, a shape this project has twice paid for (`richtext.safe` in ADR-0009, the lock
heartbeat in phase 5b). Who it affects: anyone redirecting stdout to a file today and reading the
error there. That is not an existing caller of this tool — it is a caller this ADR creates.

## Decision 3 — the exit code for "finished, and there is nothing"

| Option | Verdict |
|---|---|
| **A.** New code `4` always, human path included | Rejected by the owner: a scheduler treating non-zero as "investigate" would start alerting on a legitimately empty day, and no existing job asked for that. |
| **B (chosen, with C).** `4` only when `--wynik json` is on | The flag *is* the caller's declaration — nobody asks for JSON at a terminal for fun. One flag cannot disagree with itself, whereas a separate `--kody-rozszerzone` could. |
| **C (chosen, with B).** `status` in the envelope, exit stays 0 | Alone it is too weak — the exit code is the first thing an agent branches on — but as the **observer** of the exit code it is essential: a guarantee whose violation has no observer is not a guarantee. |
| **D.** `--kod-przy-zerze N` | Rejected: a knob that makes the contract per-invocation. |

`kod_wyjscia` is derived from `status` by one function, and the envelope prints the value it exited
with, so the two cannot drift. That function is a `match` with `assert_never`, **not** the
`dict[Status, int]` the plan named: mypy does not check a dict literal against the members of a
`Literal`, so a sixth status would join the set silently and surface as a `KeyError` at runtime —
which is the shape of guarantee this project calls unenforced. With `assert_never` the new member
cannot be added without a red type check.

```
ok, przerwano        -> 0
brak_trafien         -> 4      "the query ran and matched nothing"
nic_do_zrobienia     -> 4      "no resumable run / no changes / no finished run"
blad                 -> the existing taxonomy: 1, 2, 3 (errors.py unchanged)
KeyboardInterrupt    -> 130 (unchanged)
```

`brak_trafien` and `przerwano` are different facts and the code says so: at `count == 0` under
`--tak` the `brak_trafien` question resolves to its safe default `wyjdz` (ADR-0017 — widening must
never happen for a schedule), which is *zero hits*; an operator who read the cost table and quit is
*przerwano*. `flow` already distinguishes them via `FetchPlan.count`; the envelope reads that
rather than re-deriving it.

**`przerwano` is unreachable under the flag today, and that is a consequence of Decision 6, not an
oversight — measured 2026-09-24.** `--wynik json` requires `--tak`, and `--tak` answers every
question with its declared default: "what next" defaults to `lista`/`szczegoly` and never to
`wyjdz`, while every give-up question above the threshold carries `safe_default=False` and therefore
raises `ConfigError` (exit 3, status `blad`) rather than returning. So the only reachable give-up is
the zero-hits one, whose `count == 0` is genuinely measured. The status stays in the closed set
because it describes a real human outcome and because the human path computes no envelope at all —
but a reader must not take it as evidence that a caller can receive one.

This matters more than it reads, and `tests/test_wynik_json.py` pins the mechanism so the assumption
cannot rot quietly: **three other paths return `FetchPlan(count=0)` as a placeholder** — the report
path's vintage question cancelled, the same question on the API path, and a declined `RAPORT_NA_API`
— where `count` was never measured at all. Under `--tak` none of them is reachable. If the `--tak`
coupling is ever relaxed, `plan.count == 0` stops meaning "the query ran and matched nothing", and
`pobierz` would report `brak_trafien` for an operator who simply declined a fallback.

## Decision 4 — liveness for a caller that is not a terminal

| Option | Verdict |
|---|---|
| **A.** Keep `ConsoleEvents`, point its console at stderr | Rejected on the measurement above: no intermediate frame off a terminal, so the agent gets one bar at the end — the exact silence CLAUDE.md calls a defect — and on a terminal the ANSI animation is noise in a captured log. |
| **B (chosen).** `LineEvents` in `console.py`: one line per cadence threshold to the **stderr rich console**, no `Live` | No new output channel (it goes through `richtext.safe`, so rule 10 covers registry text for free), readable in a captured log, and the cadence is counted in **requests and rows**, never in pages. |
| **C.** JSONL events on stderr | Deferred. It is the richest option and it creates a second schema nobody versioned; diagnostics that look parseable get parsed, and then they are a contract. Revisit when a caller states a need the envelope cannot meet. |

Cadence: at least one line per **30 s of work at the profile's own spacing** (8 requests at
3.75 s), plus one at every stage transition, plus the existing wait announcements — the rhythm
`store.touch_lock()` already keeps, for the same reason. `LineEvents` carries the request counter
the envelope's `zadania` reads, so the number in the envelope is the number that drove the
heartbeat.

**Who gets it: every caller that is not a terminal, not only `--wynik json` — the owner's decision,
2026-09-23.** Tying it to the flag was the narrower option and it leaves the measured defect open
for everyone else: every existing schedule would still get one frame at the end of a half-hour run.
The selection is therefore made on `console.is_terminal`, the same property `rich` itself branches
on, and the flag does not enter it. The animated bar survives where it works — on a terminal — and
nowhere else.

**Read that as "stderr is not a terminal", because Decision 2 moved the console there.** This ADR
first wrote the example as "a person running `pobierz > log.txt`", and that stopped being true in
the same change: at a terminal, redirecting stdout takes a stream the screens no longer use, so the
bar stays animated. The callers `LineEvents` actually serves are the ones where **stderr** is a
pipe — a scheduler, a CI container, an agent's subprocess — which is the population the decision
was about. Corrected 2026-09-24 after a code review; the mechanism was right, the sentence naming
it was not, and it is the sentence the next change will trust.

## Decision 5 — who owns the new channel, and boundary rule 15

`jsonout.py` is to stdout what `richtext.py` is to `rich`: one module, one writer, one neutraliser.

**Rule 15.** *Only `jsonout.py` calls `json.dump` or writes to `sys.stdout`, and everything it emits
passes `config.mask_tokens` through one recursive walk.*

**The rule is stated on `json.dump`, not on `json.dumps`, and the distinction is the whole design.**
The first draft said "`json.dump`/`json.dumps` appears in no other module", which is false on
arrival: measured 2026-09-23, `json.dumps` already lives in **six** modules at eight sites —
`apiprofile.py:108,118` and `criteria.py:460` (fingerprint hashing), `recordid.py:100` (the
ADR-0016 report-row digest), `store.py:712,904` and `pipeline.py:931` (values bound into SQL),
`exporter.py:500` (`write_jsonl`, to a file under `wyniki/`). A scan red on its first run is a scan
nobody trusts, and the obvious repair — a module set with a named exception per entry, the shape
`HTTP_CLIENT_MODULES` uses — would have carried six entries to re-read at every future change.

It is not needed, because `json.dumps` **returns a string and writes nothing**. A string is
harmless until it is printed, and printing is already governed: rule 9 forbids every output call in
`cli.py`, rule 10 lets only `richtext` hand a foreign string to `rich`. The dangerous form is the
one that takes a stream — `json.dump(obj, fh)` — and that form **occurs zero times in the project
today**, so the sharp rule has no exception list at all and is true the moment it is written. Its
second half, `sys.stdout`, is already machinery the rule-9 scan owns through `STREAM_ROOTS`.

`docs/design/phase2_core.md` records the six `json.dumps` sites as a map of where serialisation
happens at all, so the next reader does not have to rediscover that the rule deliberately does not
cover them.

The reason is specific and easy to get wrong: **`json.dumps` escapes control characters
(U+0000-U+001F, ESC included) but does not mask a secret.** So JSON encoding is a genuine
neutraliser for the terminal half of §B and none of the credential half — the mirror image of
`strip_control`, which ADR-0009 deliberately refused to accept as a rule-10 neutraliser *because*
it does not mask, and the token's payload carries a PESEL. The vector is real: an error message is
the one envelope field that can carry a URL.

Enforcement follows the shape the scan already uses: the masking walk is one function, so no scan
has to prove anything about leaves; the scan asserts (i) exactly one module serialises, (ii) inside
it the argument to `json.dump` *is* a call to the masking walk, and (iii) `cli.py` still has no
output call — with `json.dump(…, sys.stdout)` added to the shapes the rule-9 scan knows, because in
a program that just grew a JSON channel that is the next `typer.echo`. It ships with a self-test in
the established style: five snippets, three rejected.

## Decision 6 — which commands, and what an unsupported command does

Supported: `pobierz`, `aktualizuj`, `wznow`, `runy`, `raporty`, `sprawdz-nip`, plus `szukaj-pkd`
(ADR-0026). Not supported: `kreator` (a wizard has no machine caller), `wyczysc`, `token *`,
`eksportuj` — the last is the obvious next one and is left out only because nobody asked for it.

Two refusals, both `ConfigError` (exit 3), both for the same reason — **ignoring a flag is how a
machine caller ends up parsing a human screen**:

- `--wynik json` on a command that does not support it.
- `--wynik json` without `--tak`. Implying `--tak` would be the tool answering questions the caller
  did not authorise; refusing costs one sentence and one flag. (`--tak` never implies production
  consent; that stays with `--produkcja`, resolved in `cli._settings` above the ui layer.)

**Every exit path emits exactly one envelope**, written in a `finally`, so an unexpected exception
outside the `CeidgError` taxonomy still produces `status: "blad"` on stdout *and* lets the
traceback propagate — nothing is swallowed. A path that returns without an envelope gives the agent
empty stdout and exit 0, which is the worst possible outcome, so it is pinned by a test that walks
every terminal path of every supported command and asserts stdout parses.

## Consequences

| File | Status | Responsibility |
|---|---|---|
| `ceidg_tool/ui/wynik.py` | new | **Pure** (rule 6). `Wynik`, the closed `Status`, `kod_wyjscia(status)`, and the mapping from `ExportSummary`/`RunResult`/`FetchPlan` to the envelope dict. |
| `ceidg_tool/jsonout.py` | new | The single writer. Masking walk, **`ensure_ascii` on** (see below), deterministic key order, one trailing newline. Rule 15's owner. |
| `ceidg_tool/richtext.py` | changed | `make_console(*, stderr: bool = False)`. The seam stays single. |
| `ceidg_tool/console.py` | changed | `LineEvents` beside `ConsoleEvents`; both expose `.requests`. |
| `ceidg_tool/cli.py` | changed | Picks view, events and exit map at the composition root — the same move ADR-0008 made for `Prompter`. Authors nothing, prints nothing. |
| `ceidg_tool/ui/render.py`, `ui/texts.py` | unchanged | The screen layer is untouched; that is the point of Decision 1B. |
| `docs/design/phase2_core.md` | changed | Rule 15; the module map gains two files. |
| `tests/test_boundaries.py` | extended | Rule 15 + self-test; rule 9 gains the JSON shapes. |

**`ensure_ascii` stays on — this ADR said `False` and the measurement says otherwise.**
Measured 2026-09-23 on the project's own machine: without `PYTHONUTF8=1`, `sys.stdout.encoding`
is **cp1250**, so `ensure_ascii=False` emitted `ł` as byte `0xb3` — valid cp1250, not valid
UTF-8, and therefore a document `jq` refuses, since RFC 8259 requires UTF-8 for interchange. The
escape `ł` is pure ASCII, survives every stream encoding, and every parser turns it back
into `ł`. Readability of raw stdout was the only argument for `False`, and it stopped applying
the moment Decision 2 moved the human narrative to stderr. CLAUDE.md's "Polish output needs
`PYTHONUTF8=1`" is a note for the operator's screen; a machine channel that needs an environment
variable to be well-formed is the kind of silent trap this project keeps closing.

**The envelope carries the demo marker, and that is a sixth channel for an existing rule.**
ADR-0014's five markers cover screens, the workbook and the filename; ADR-0022 had to add the
printed command as a sixth. An agent that cannot tell a demo envelope from a production one is the
same failure on the channel where it matters most, because nothing downstream of an agent re-reads
the first screen. `"demo": true` is not decoration.

**What the stderr move costs the commands that have no envelope.** Decision 2 is stated above as
affecting "a caller this ADR creates", and that is true of `--wynik json` but not of the move
itself: `eksportuj`, `sprawdz-token`, `wyczysc` and `token *` also send everything to stderr and get
no envelope in exchange, so `ceidg-tool sprawdz-token > out.txt` now writes an empty file. Nothing
in the repository reads this tool's stdout and CI runs only the four gates, so no known caller
breaks — but the honest statement of the change is "stdout is now empty unless you ask for JSON",
not "errors moved". Noted 2026-09-24 from a code review. `--help` is the one exception and stays on
stdout, because click writes it; usage errors stay on stderr, so neither can corrupt an envelope.

**What we explicitly give up:**

- stderr is **not** a contract. Screens keep the freedom to be reworded; anything an agent must
  depend on goes in the envelope or it does not exist.
- No streaming envelope. One document at the end; liveness is stderr's job.
- No machine *input*. ADR-0022 stands.
- `uwagi[].tekst` is prose in v1 for the notes that have no closed set yet.

**Risks**

| Risk | Response |
|---|---|
| A stray print corrupts the JSON document | Rule 15 + rule 9 scans; in machine mode nothing but `jsonout` can reach stdout, and the bar is on stderr by construction. |
| Exit 4 breaks an existing schedule | It cannot: no existing invocation passes `--wynik json`. |
| An envelope-less exit path | `finally` + a path-coverage test over every supported command. |
| `sprawdz-nip --wynik json` puts a person's data into an agent's log | Stated in the ADR and in `--help`; the tool writes no envelope to disk, so retention stays with the caller — the same boundary as the screen today. |
| The envelope and the screen drift | Both derive from the same returned values; a test asserts the summary block and the envelope agree on paths, records and run ids for one fixture run. |

**Gate.** Before merge: rule 15 enforced with its self-test; `result.stdout` assertions (not
`result.output`) for the envelope, so the split is actually proven; one demo run of every supported
command under `--wynik json` with its stdout piped through a JSON parser.

**Revisit when** a caller needs events on stderr as JSONL (Decision 4C), `eksportuj` joins, or a
second machine format is asked for — at which point `--wynik` already takes a value and
`jsonout.py` is the one module to extend.
