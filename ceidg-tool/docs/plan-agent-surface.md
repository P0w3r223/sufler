# Implementation plan — the machine surface (ADR-0024, ADR-0025, ADR-0026)

Date: 2026-09-23
Status: draft (awaiting the owner's read-through before the first commit)
Author: P0w3r223
Related to: docs/adr/0024_machine_output.md, docs/adr/0025_assistant_switch_and_build_cost.md,
            docs/adr/0026_pkd_search_command.md, docs/adr/0020_pkd_sunset_has_an_observer.md,
            docs/design/phase2_core.md (boundary rules), CLAUDE.md

---

Every step below names its **observer** — the test that goes red if the step is undone. That column
is part of the step, not an addition to it: a guarantee whose violation has no observer is not a
guarantee, and this project has closed three of those in one day.

Each numbered step is one commit that passes all four gates alone:

```
PYTHONUTF8=1 .venv/Scripts/python -m pytest
PYTHONUTF8=1 .venv/Scripts/python -m mypy ceidg_tool tests
.venv/Scripts/ruff check ceidg_tool tests scripts
.venv/Scripts/ruff format --check ceidg_tool tests scripts
```

---

## Step 0 — the working tree, ruled on before anything starts

Two loose ends sit in the tree and neither may ride along inside an ADR commit. Both were decided
by the owner on 2026-09-23.

### 0a — `feat: the PKD 2007 table expires on a condition (ADR-0020 accepted)`

The tree already carries the change: `KONIEC_PRZEJSCIA` (the legal fact) split from
`USUNIECIE_NIE_WCZESNIEJ_NIZ = "2027-02-01"` (the removal floor), the generated file's header
rewritten to state the condition, `scripts/build_pkd_transition.py` updated to write it, and both
test files extended. The data file was **rebuilt from the GUS source**, not hand-edited, so its
content is byte-for-byte what it was.

**ADR-0020's status line moves from `proposed` to `accepted` in this same commit** — the owner's
decision. Leaving code that implements a document which says *"diagnosis only; no code changed"*
is the inconsistency CLAUDE.md warns about by name.

Why this must land before step B1, and it is not tidiness:

| Collision | What leaving it dirty costs |
|---|---|
| `tests/test_pkdmap_data.py:21` imports `load_pkd` from `assistant.pkd`, and the uncommitted change rewrites the import block at lines 26-33. B1 rewrites line 21. | Two edits to one hunk, one uncommitted — how half a change disappears in a checkout. |
| `ceidg_tool/data/pkd2007_2025.yaml` is regenerated in the tree; `szukaj-pkd` reads it and its tests will pin what it says. | A `git checkout` of the data file would silently change what the new command asserts, with no test going red. |
| `pkdmap.py` is what `pkddict.py` is placed beside, and both touch `PURE_MODULES` in one edit. | At the B-gate, "did the boundary change break this, or was it already dirty?" stops being answerable. |

### 0b — `docs: the supplement moves to docs/reference/, and its citations follow`

`UZUPELNIENIE_01.md` and `PROMPT_AUDYT_ARCHITEKTURY.md` are deleted in the working tree. The owner
chose deliberate removal **with the references fixed first**, in its own commit, never under
`git add -A` inside an ADR commit — because no gate can see this: nothing opens either file, so all
four stay green whether the document exists or not.

The scale, measured 2026-09-23: **68 citations across 45 files**, including `CLAUDE.md:170` and
`README.md:7` — both of which name the supplement as the document that *wins over the instruction
wherever the two disagree* — eleven documents under `docs/`, and `tests/test_uzupelnienie.py`,
a whole test file named after it. The citations are section references: §B (13), §D (11), §C (5),
§A (5), §E (1).

**Archive, not erase — the owner's decision, 2026-09-23, after the distinction was raised.** "Fix
the references" has two very different readings, because the citations resolve to lettered sections:
if the document leaves the repository entirely, `§B` resolves to nothing and the rule "the supplement
wins wherever the two disagree" becomes unusable. So:

1. `git checkout` both deleted files, then `git mv` each into `docs/reference/` — restored and
   moved in one commit, so git records a rename and the history follows.
2. **The filename keeps its original spelling**, `docs/reference/UZUPELNIENIE_01.md`, not a
   lower-cased one. The 68 in-code citations spell it `UZUPELNIENIE_01`, so the uppercase name is
   what a grep for those citations finds; renaming it would make the file harder to reach from the
   very comments that point at it.

   **Superseded on 2026-09-24, and by measurement rather than by preference.** The same move had
   already been made here, to `docs/reference/uzupelnienie-01.md`, and the citations had been
   repointed with it — so on this branch the argument above described a repository that no longer
   existed. The lower-cased name is the one the code and the link gate now agree on; two spellings
   of one document would have been the actual defect. The paragraph stays because the reasoning is
   still the right reasoning for a file nobody has renamed yet.
3. Repoint the path in `CLAUDE.md:170`, `README.md:7` and the nine documents under `docs/` that
   cite it with the `.md` suffix.
4. The **68 in-code citations stay untouched**: they name a document and a section, not a path, and
   both still exist. So does `docs/design/phase2_core.md`, which cites the name without a suffix.
5. `PROMPT_AUDYT_ARCHITEKTURY.md` is a prompt, not a requirement, and its work is already recorded
   in `docs/audit-architecture-2026-09-09.md`. It goes to `docs/reference/` as well rather than
   being deleted, for one reason: nothing else records what the audit was *asked*, only what it
   found.

The root gets the clearing that motivated the deletion, and nothing that 45 files point at stops
existing.

---

## Part A — ADR-0025, the assistant

Sequenced so the switch lands **after** the plumbing it needs. The ADR's consequences table implies
the reverse; taken literally it means writing a first-screen argument in one commit and deleting it
in the next — a second copy of a rule, which this project audits for.

### A1 — `refactor: the reason the assistant is missing becomes a code, and load_pkd's sentence survives it`

**Where the enum lives, and why not in `texts`.** `PowodBrakuRaportu` sits in `ui/texts.py` because
its producer, `flow._powod_braku_raportu`, is also in `ui/`. Here the producer is
`pipeline._build_assistant`, a layer *below* `ui/` — so `pipeline` importing from `ui/texts` would
invert the direction boundary rule 8 exists to hold. `assistant/__init__.py` is the home: already
pure, already imported by `pipeline`, importable with no SDK installed. It joins `PURE_MODULES` in
the same commit.

**One object, not two fields:**

```python
PowodBrakuAsystenta = Literal["BRAK_KLUCZA", "BRAK_PAKIETU", "BRAK_SLOWNIKA", "WYLACZONY_FLAGA"]

@dataclass(frozen=True)
class BrakAsystenta:
    powod: PowodBrakuAsystenta
    szczegol: str | None = None
```

ADR-0025's consequences table says "`PowodBrakuAsystenta | None` plus `szczegol: str | None`". Two
fields satisfies that literally but leaves a fourth state representable — *reason set, assistant
present* — which mypy cannot rule out and which then needs a test to carry. One object makes
`(assistant is None) == (brak is not None)` a shape mypy narrows for free at the
`collect_from_description` guard. A wording change to the ADR, not a contract change.

**The trap the ADR does not name.** Today's `except CeidgError` branch catches two different facts:
`AssistantUnavailableError(ConfigError)` is raised from inside `AnthropicCaller.__init__` for a
missing package or an empty key, while `load_pkd`'s `ConfigError` means the dictionary is absent.
Mapping "the `CeidgError` branch → `BRAK_SLOWNIKA`" would label a missing package as a missing
dictionary — the B6 defect wearing the new enum. The branch must be:

```python
except AssistantUnavailableError as exc:   -> BrakAsystenta("BRAK_PAKIETU", str(exc))
except CeidgError as exc:                  -> BrakAsystenta("BRAK_SLOWNIKA", str(exc))
```

with `str(exc)` carried in `szczegol` on **both**, never discarded.

Order of edits: `assistant/__init__.py` → `ui/texts.py` (`ASYSTENT_NIEOBECNY` dict + a builder;
**delete `ASSISTANT_UNAVAILABLE`**, which has exactly one reader — deleting it is what proves "with
a closed enum there is nothing left to fall back from") → `pipeline.py` → `ui/flow.py:111-115` →
the four `SimpleNamespace(..., assistant_reason=None)` doubles in `tests/test_assistant.py:489,641,682`
and `tests/test_pomoc_operatorowi.py:71`, or mypy --strict goes red.

| Observer | What makes it red |
|---|---|
| `test_an_unavailable_assistant_reports_the_real_reason` — **keep `match="build_pkd"`**, rebuild only the `Deps` double | Any flattening of `szczegol` into a generic sentence. Do not weaken it to asserting the code; the code is not what B6 was about. |
| **New, end-to-end:** real `_build_assistant`, key set, dictionary path pointed at a missing file → `powod == "BRAK_SLOWNIKA"` **and** `"scripts/build_pkd.py" in szczegol` | Flattening in the producer rather than in the sentence builder. Two observers because either half can lose it. |
| **New:** `set(ASYSTENT_NIEOBECNY) == set(get_args(PowodBrakuAsystenta))` | A fifth code with no sentence. |
| **New:** a `BRAK_PAKIETU` case, asserting a sentence distinct from the dictionary one | The conflation above. |
| `mypy --strict` | A code added to the `Literal` with no entry in the `Final[dict[...]]`. |

**Decision 3 of ADR-0025 lands in this step**, because it needs the enum and nothing else.
`_assistant_destination` branches on `settings.anthropic_key is not None`, so with a key present and
the dictionary missing the §A row promises an assistant that did not build. `build_deps` therefore
appends a sentence to `deps.warnings` — an existing channel `cli` already drains — when a key exists
and the assistant is absent for any reason but `WYLACZONY_FLAGA`. No reordering of `_banner`: it
runs before criteria parsing, so moving it would put a bad `--od` ahead of the first screen, and
that screen is a §A acceptance criterion.

| Observer | What makes it red |
|---|---|
| Key present, dictionary path missing → the warning appears **and** names the dictionary | The false §A claim going back to having no observer on the screen that made it. |
| No key at all → **no** warning | Noise on every run of a tool most people use without an assistant; no key is a normal state (ADR-0011 decision 9) and already has its own row. |

### A2 — `refactor: the caller says whether its path can use an assistant`

`asystent` defaults to `False`. `build_deps` has **82 call sites**; a required keyword would touch
all of them, while a `False` default touches only those that need `True` — and a test that today
proves assistant behaviour goes *red*, not silent, because the code path changes.

`build_deps(..., asystent: bool = False)`. The default is `False` deliberately: omission then costs
nothing rather than silently opening a credentialed path to a second host. Call sites declare
themselves — `kreator` → `True`; `pobierz` → `opis is not None`; `sprawdz_nip`, `wznow`, `raporty`,
`aktualizuj` → omitted. `cli._demo_deps` passes the keyword through, or the demo path diverges from
production on the one question this commit is about.

| Observer | What makes it red |
|---|---|
| A declared `BUDUJE_ASYSTENTA: dict[str, bool]`, each entry driven through `CliRunner` with `build_deps` patched to record its argument | A refactor silently taking the assistant from `pobierz --opis`, or giving one to a new command. |
| **The completeness half:** `set(BUDUJE_ASYSTENTA) == {commands registered on app}` | A new command skipping the rule — what a table without a completeness check cannot see. |
| **The environment observer:** run `raporty` with `ANTHROPIC_LOG=debug` set and assert the variable survives | `caller._wycisz_sdk()` does `os.environ.pop("ANTHROPIC_LOG", None)` at line 91. This is the *second half* of the ADR's objection, and without this test only the 0.2 s has an observer. **Red today, green after this step.** |

### A3 — `feat: --bez-asystenta, and refusing --opis with it rather than ignoring one of them`

`BezAsystentaOpt` on `pobierz` and `kreator`; `_assistant_destination` gains its fourth state and
`first_screen` threads it exactly as `demo=` is threaded;
`build_deps(asystent=(opis is not None) and not bez_asystenta)`.

**The refusal is the first statement inside the `try`, before `_settings()`.** The ADR says "before
anything is built"; after `_settings` a missing token would produce a *different* exit-3 sentence
for an invocation that was wrong at parse time.

**A resolved ambiguity worth recording.** `WYLACZONY_FLAGA` ends up with exactly one reader — the
first-screen row — because `collect_from_description` is unreachable under the flag on both paths:
`pobierz` refuses at parse time, and the wizard does not ask for a description when
`deps.assistant is None` (`wizard.py:47`). That unreachability is a claim, so it gets its own
observer below. Threading the reason into `Deps` for the four commands that never had the flag
would put a label nobody reads claiming a flag was passed.

| Observer | What makes it red |
|---|---|
| `pobierz --opis "…" --bez-asystenta` → exit 3, sentence from `texts`, **and nothing built**: `assert not (tmp_path / "dane").exists()` | A refusal firing after the store and log directory already exist. |
| **The byte-identity test:** the same `pobierz` twice — once with `--bez-asystenta` and a key present, once with the key empty — equal exit codes, equal output | Any behavioural difference between the switch and the no-key path, which ADR-0011 settled as normal. |
| A `test_ui_texts.py` case for the fourth `_assistant_destination` state | The §A acceptance row losing a state. |
| A wizard run under the flag: the description question never appears, `pobierz_menu_item` returns the no-assistant label | The unreachability claim becoming false. |

### A4 — `docs: the cost attributed, and the figure this step was written around retracted`

Docs only, and it turned out to be the step that mattered most. The attribution is in ADR-0025's
Context: **955 ms**, against a 479 ms baseline — building the assistant roughly doubles the startup
of a command that already costs half a second — with `import anthropic` at 806 ms, i.e. 85 % of it.

The figure this ADR was written around, *"roughly 0.2 s"*, is **retracted**. It came from one run
each of `runy` with and without a key, which is a single sample per side inside process-startup
noise — and `runy` passes `online=False`, so it sits outside the `if online:` block that builds the
assistant and **has never built one**. The number measured variance on a command chosen because it
could not use the feature. It had already reached the ADR, this plan and the A2 commit message.
Option D (`lru_cache`) is declined on the same measurement rather than deferred: it would recover
58 ms of 955.

---

## Carried into part B from the part-A review

**`build_deps` takes two booleans where one tri-state belongs.** `asystent` and `wylaczony` admit
pairs with no meaning: `asystent=True, wylaczony=True` resolves silently in favour of the first,
and `wylaczony=True, online=False` drops `WYLACZONY_FLAGA` without a trace, so the wizard's §A row
would fall back to describing the keyring. No caller does either today, which is why the part-A
review flagged it and deferred it rather than blocking on it.

It is worth carrying because it is **the same argument** `BrakAsystenta`'s own docstring uses one
layer down: two fields admit a state the domain does not have, and mypy cannot close it. One
parameter — `Literal["buduj", "nieproszony", "wylaczony"]` — closes the fourth state for free. Part
B touches `build_deps` anyway (nothing in ADR-0024 or ADR-0026 needs it, but `cli` is rewired
throughout), so this is the cheap moment.

## Part B — ADR-0026 first, then ADR-0024

Ordered so the envelope's first consumer exists before the envelope machinery. A rule-15 scan
pointed at a module nothing calls is a scan that always passes; the self-test covers the scan, but
only a real consumer covers the wiring.

### B1 — `refactor: the PKD dictionary is a fact about the register, not an asset of the assistant`

Pure move. `pkddict.py` takes `PKD_RESOURCE`, `DEFAULT_PKD_PATH`, `PKD_VINTAGE`, `load_pkd`;
`assistant/pkd.py` keeps `lookup`/`validate_codes` and imports them. Importers to redirect:
`pipeline._build_assistant:401`, `assistant/prompt.py:19`, `scripts/assistant_smoke.py:27`,
`tests/test_assistant.py:17`, `tests/test_assistant_pkd_data.py:21`, `tests/test_pkdmap_data.py:27`.

| Observer | What makes it red |
|---|---|
| `pkddict.py` joins `PURE_MODULES`; `test_every_pure_module_actually_exists` guards the rename | The file being renamed away without the scan noticing. |
| **New:** `assert "assistant" not in package_targets(PACKAGE / "pkddict.py")` | Exactly what decision 3 is about — a dictionary still depending on `assistant/`, and so still inviting the wrong deletion later. One line, on machinery that exists. |

### B2 — `feat: szukaj-pkd answers what --pkd will actually reach, including the 2007 half`

`pkdszukaj.py` (pure) → `ui/texts.py` → `cli.py` → `PURE_MODULES` → tests. Shape decides the
reading, through `criteria.normalize_pkd`, so `62.01.Z`, `6201z` and `6201Z` behave exactly as they
do in a query, and the screen states which reading it took.

**Output shape — one section per hit, decided by the owner 2026-09-23.** A flat table would have to
fit "what this predecessor also drags in" into a cell, and that clause is the reason the command
exists:

```
9621Z — Fryzjerstwo i pozostałe zabiegi kosmetyczne
  PKD 2007: 9602Z  Fryzjerstwo i pozostałe zabiegi
            └ ten kod prowadzi także do 9622Z, więc --pkd-2007 dołoży również kosmetykę

Szukano w nazwach, nie w znaczeniach.
Kod bywa ważny, a zapytanie i tak niepełne: każdy wpis niesie jeden rocznik.
```

The envelope's per-command field follows that structure rather than the table:

```jsonc
"trafienia": [
  {
    "kod": "9621Z",
    "nazwa": "Fryzjerstwo i pozostałe zabiegi kosmetyczne",
    "rocznik": 2025,
    "poprzednicy": [
      {"kod": "9602Z", "nazwa": "…", "rowniez": ["9622Z"], "dzis": null}
    ]
  }
],
"odczytano_jako": "kod",     // kod | fraza — which reading the argument took
"obciete": false
```

`rowniez` is the ambiguity the screen spells out in prose; `dzis` names the 2025 code a 2007 code
is still live as, or `null`. An agent reading `rowniez` learns the same fact the operator reads in
the `└` line, which is the point of Decision 1B applied to this command.

**The diacritic fold has a trap, measured 2026-09-23.** `unicodedata.normalize("NFKD", "Łódź")`
returns `"Łodz"` — Polish `ł`/`Ł` have no canonical decomposition, so an NFKD-plus-strip-combining
fold leaves them in place and a search for `lodz` silently misses `Łódź`. An explicit `ł→l`, `Ł→L`
pair is required, **and a test named for it**, because this is precisely the class of thing that
passes every automated check while being quietly wrong.

| Observer | What makes it red |
|---|---|
| `test_fold_handles_the_letter_that_has_no_decomposition` — "łódzkie" found by "lodzkie" and by "Łódzkie" | The NFKD-only fold. |
| `szukaj-pkd 9602Z` shows both `9621Z` and `9622Z` | The 2007→2025 merge losing a branch — the decision `flow.py` asks the operator to make. |
| Run in a tmp cwd, `CEIDG_TOKEN` unset, `CEIDG_DATA_DIR` set: exit 0, results printed, **`assert not data_dir.exists()`** | Any `_settings()` / `build_deps` / `setup_logging` creeping in. Three claims — zero requests, no token, no database — in one assertion. |
| 41 matches → 40 rows plus one line naming the rest and `--wszystkie` | The cap scrolling the hit off the top of the terminal. |
| The transition caveat on **every** screen, the zero-hit one included | "A code being valid does not make a query complete" is the whole reason this command is not decoration. |

### B3 — `fix: an unknown --pkd code stops being a silent 204 (ADR-0011 finding F8)`

Site: `flow.prepare_fetch`, guarded by `if criteria.pkd:` — the one place the wizard and the flags
share, beside where `_kandydaci` already reads `deps.pkd_map`. The dictionary loads **lazily there**,
not in `build_deps`: ADR-0025's argument is exactly about not paying for what a path cannot use, and
most invocations carry no `--pkd`.

| Observer | What makes it red |
|---|---|
| `--pkd 9999Z` → exactly one warning naming `szukaj-pkd`, **and the fetch still happens** | Sliding from warn to reject, which the ADR refuses because our 2007 data is a transition key, not a full PKD 2007 list. |
| A known code → no warning; three unknown codes → one warning, not three | Noise that trains the operator to skip warnings. |
| Dictionary absent → the warning is skipped, `prepare_fetch` still works | Absence disabling the *tool* instead of the *check* — the rule `pkd_map` already follows at `pipeline.py:318-323`. |

### B4 — `refactor: every screen, warning and error moves to stderr`

`richtext.make_console(*, stderr: bool = False)`; `cli.console = make_console(stderr=True)` — **one
console, not two**, so the seam stays single and `tests/test_cli.py:46`'s width pin keeps working.
`ConsoleEvents(console)` inherits it, so the progress bar is on stderr by construction, which is the
ADR's answer to the "a stray print corrupts the JSON" risk.

Verified against the installed versions before planning: `rich/console.py:759` resolves
`sys.stderr` at write time when `stderr=True`, so the late-binding property `make_console`'s
docstring depends on survives; `typer/testing.py` builds a `StreamMixer` where `Result.output` is
the mixed stream and `.stdout`/`.stderr` are separate.

| Observer | What makes it red |
|---|---|
| **The whole existing suite, unchanged and green** | This *is* the evidence for "zero test rewrites". If anything goes red, the claim was wrong, and it is worth knowing before six commands depend on it. |
| **New:** a failing command → `result.stdout == ""` and `"Błąd" in result.stderr` | A split that exists only nominally. Uses `.stdout`, not `.output`. |

### B5 — `feat: LineEvents, because off a terminal the bar emitted nothing until it stopped`

`LineEvents` beside `ConsoleEvents`; both expose `.requests`; no `Live`. Cadence: one line per 30 s
of work at the profile's own spacing (8 requests at 3.75 s), one per stage transition, plus the
existing wait announcements — **counted in requests and rows, never pages**, the off-by-a-layer
error that produced four defects on 2026-09-06.

**Selected on `console.is_terminal`, not on `--wynik json` — the owner's decision 2026-09-23.**
Tying it to the flag would leave the measured defect open for everyone who is not an agent: a person
running `pobierz > log.txt`, and every existing schedule, would still get one frame at the end of a
half-hour run. The animated bar survives where it works and nowhere else.

| Observer | What makes it red |
|---|---|
| 24 `on_request` calls → exactly 3 lines; a stage transition → one line | Cadence drift, or a counter that counts pages. |
| `LineEvents.requests` equals the number the envelope's `zadania` reads | "The number in the envelope is the number that drove the heartbeat" becoming untrue. |
| `console.py` is already in `RICH_MODULES`, so rule 10 covers the text for free | A registry name reaching stderr as markup. |

### B6 — `feat: the result envelope as a pure value`

`ui/wynik.py`: `Status`, `kod_wyjscia(status)`, `Wynik`, and the mapping to the envelope dict.

**One thing must not be got wrong.** `ui/wynik.py` is a `PURE_MODULE`, but rule 6's scan checks
*imported roots* — `httpx`, `sqlite3`, `rich`, `openpyxl`. Importing `ExportSummary`/`RunResult`
from `pipeline` would pass the scan while defeating the reason the rule exists. Take the shape
`texts.SummaryInput` already established: **a dataclass of plain values that `cli` fills**, so
`wynik.py` imports `criteria` and nothing else from the package. This is the difference between a
rule enforced and a rule reported as enforced.

| Observer | What makes it red |
|---|---|
| `kod_wyjscia` typed `dict[Status, int]` — mypy carries completeness | A sixth status with no exit code. |
| Table test over all five statuses | The derivation drifting from decision 3. |
| `brak_trafien` vs `przerwano` read from `FetchPlan.count`, asserted separately | Conflating "the query matched nothing" with "an operator read the cost table and quit". |
| **`"demo": true`, and this test belongs in `tests/test_demo_markers.py`** beside the other five | ADR-0014's markers are mandatory jointly; this is the sixth channel and the one where nothing downstream re-reads the first screen. |

### B7 — `feat: jsonout.py owns stdout, and boundary rule 15 says so`

**Rule 15 is stated on `json.dump`, not on `json.dumps` — decided 2026-09-23 after measuring.**
The first draft said "in no other module", which is false on arrival: `json.dumps` already lives in
**six** modules at eight sites (`apiprofile.py:108,118`, `criteria.py:460`, `recordid.py:100`,
`store.py:712,904`, `pipeline.py:931`, `exporter.py:500`). But `json.dumps` returns a string and
writes nothing, and a string is harmless until printed — printing being governed already by rule 9
(no output call in `cli.py`) and rule 10 (only `richtext` hands a foreign string to `rich`). The
dangerous form is the one taking a stream, `json.dump(obj, fh)`, and it occurs **zero times in the
project today**. So the rule is: *only `jsonout.py` calls `json.dump` or writes to `sys.stdout`* —
no exception list, true the moment it is written, and its second half runs on `STREAM_ROOTS`
machinery the rule-9 scan already owns. `phase2_core.md` records the six `json.dumps` sites as a map
of where serialisation happens at all, so the next reader does not rediscover that the rule
deliberately skips them.

**Masking is a different neutraliser from `strip_control`, and they are mirror images.**
`json.dumps` escapes U+0000-U+001F including ESC and masks nothing; `strip_control` strips and masks
nothing — which is why ADR-0009 refused it as a rule-10 neutraliser, the token's payload carrying a
PESEL. So `jsonout` runs `config.mask_tokens` over the whole structure in one recursive walk, and
the scan asserts the argument to `json.dump` *is* a call to that walk. Four traps in the walk:

1. **Keys as well as values** — `uwagi[].kod` and `kryteria`'s keys are strings too.
2. **`Path` before `str`** — `pliki` carries `Path`; masking only `isinstance(x, str)` lets a path
   whose string form contains a token through untouched. Stringify first, then mask.
3. **Recurse into `dict`, `list`, `tuple`; leave `int`/`bool`/`None` alone.**
4. **`ensure_ascii=False` does not disable control-character escaping** — worth a test, because
   "we turned off escaping for Polish" is the plausible wrong reading.

Rule 9 grows too: `json.dump(…, sys.stdout)` joins the shapes `output_calls()` knows. In a program
that just grew a JSON channel, that is the next `typer.echo`.

| Observer | What makes it red |
|---|---|
| Scan (i): `json.dump` and `sys.stdout` writes occur only in `jsonout.py` | Any module acquiring a stream-form serialisation or a direct stdout write. |
| Scan (ii): inside `jsonout.py`, the argument to `json.dump` is the masking walk | Someone dumping the raw envelope — the error message is the one field that can carry a URL. |
| Self-test: five snippets, three rejected | A scan indistinguishable from one that always passes. |
| `mask_tokens` over a secret in a **key**, in a **`Path`**, and two levels deep | All four walk traps. |
| `test_the_cli_output_scan_covers_every_channel` gains the JSON shape | Rule 9 stopping short of the new channel. |

### B8 — `feat: szukaj-pkd --wynik json — every exit path, the one outside the taxonomy included`

**A context manager, not a decorator.** `with wynik_json(polecenie, tryb) as w:` around each
command's body, holding the envelope under construction and writing it in `__exit__` — one place
where the exception classification lives, rather than the same four-way branch copied into every
command and drifting. Four classifications, three of them easy to get wrong:

- **`typer.Exit` is not an error.** Every command raises it on success paths (`kreator` at line
  371). A `finally` turning `raise typer.Exit(code=0)` into `blad` is the obvious bug.
- **`CeidgError`** → `blad` plus its own `exit_code`; `errors.py` unchanged.
- **`KeyboardInterrupt`** → 130, handled at `cli.run():977` outside `app()`; the wrapper must not
  swallow it.
- **Anything else** → `blad`, envelope written, **then re-raised**.
- `kod_wyjscia` is computed, written into the envelope, and *then* exited with — so the printed
  code and the real one cannot drift.
- `events.close()` before the envelope is written, since `LineEvents.requests` feeds `zadania`.

| Observer | What makes it red |
|---|---|
| **Path coverage** over every terminal path — hit, no hit, code reading, phrase reading, capped, both refusals — asserting `json.loads(result.stdout)` succeeds **and** `envelope["kod_wyjscia"] == result.exit_code` | The empty-stdout-exit-0 outcome the ADR calls the worst possible, and any drift between printed and real code. |
| **The `finally` test:** monkeypatch `pkdszukaj.szukaj` to raise `ZeroDivisionError`; assert stdout parses, `status == "blad"`, **and `result.exception` is the `ZeroDivisionError`** | An envelope written from an `except CeidgError` handler instead of a `finally` — identical on every path the taxonomy covers. The third assertion is the one proving nothing was swallowed. |
| `typer.Exit(code=0)` → `status == "ok"` | The classification bug above. |

### B9 — `feat: --wynik json on the six remaining commands`

Split per command, each passing the gates alone, each carrying its per-command field.

**A wiring problem the ADR does not name.** The refusal "`--wynik json` on a command that does not
support it" must be *our* `ConfigError` with *our* sentence and exit 3. If `--wynik` is declared
only on the seven supported commands, typer refuses it itself — English message, exit 2. So
`--wynik` is declared on **every** command, and one `_wynik(...)` helper raises the refusal. The
supported/unsupported split is a declared table compared against `app`'s registered commands, so a
new command must choose — the completeness trick from A2.

**Per-command flag, not a global option on the app callback.** A callback option would have to be
written `ceidg-tool --wynik json pobierz -w wielkopolskie` — the value before the verb, which is a
hostile shape for the caller this whole ADR exists for, since an agent composes the command by
appending flags to a subcommand.

| Observer | What makes it red |
|---|---|
| `kreator --wynik json` → exit 3 with our sentence, not typer's exit 2 | The flag declared only where it works. |
| `pobierz --wynik json` without `--tak` → exit 3 | Implying `--tak` — answering questions the caller did not authorise. |
| Zero hits **with** the flag → exit 4; the identical invocation **without** it → exit 0 | Decision 3B leaking into the human path, which the owner rejected. |
| **The drift test:** one demo `pobierz`, asserting `texts.summary_table`'s rows and the envelope agree on paths, records and run ids | The screen and the envelope computing the same fact twice. |
| `sprawdz-nip --demo --wynik json` → `"demo": true` | ADR-0014's marker missing where it matters most. |
| `--help` for `sprawdz-nip` carries the personal-data sentence | The retention boundary stated in the ADR and nowhere the caller can see it. |

### B10 — `docs: rules 1-15, four new modules on the map, and the gate`

`docs/design/phase2_core.md` (rule 15, its enforcement shape, and `ui/wynik.py`, `jsonout.py`,
`pkddict.py`, `pkdszukaj.py` on the module map); `docs/status.md`; and **`CLAUDE.md` in three
places** — "fourteen enforced mechanically" and "rules 1-13 are an AST scan" both move, and the
"silence is a defect, measured in requests" paragraph gains the machine caller, because "satisfied
for a terminal and for nothing else" is a newly measured fact about this tool.

Then the ADR's own gate, which is a run and not a test: one demo invocation of every supported
command under `--wynik json`, stdout piped through a JSON parser.

---

## The three fragile places, in one view

| Named risk | Where it actually breaks | The observer, and why it must exist |
|---|---|---|
| The envelope must exist on every exit path | A `finally` misclassifying `typer.Exit` as an error, or an envelope written from `except CeidgError` — indistinguishable from correct on every path the taxonomy covers | Path-coverage parametrisation **plus** an injected `ZeroDivisionError` asserting three things at once. Without the third assertion, a handler that swallows passes. |
| Masking in JSON is a different neutraliser from `strip_control` | `json.dumps` escapes ESC and masks nothing; `strip_control` masks nothing either. The walk must cover dict **keys**, and `Path` must become `str` **before** masking | Scan assertion (ii) + the five-snippet self-test + unit tests planting a secret in a key, in a `Path`, and two levels deep. |
| `assistant_reason` must not lose `load_pkd`'s detail | Two places: `_build_assistant` conflating `AssistantUnavailableError` with the dictionary's `ConfigError`, and `texts` building a generic sentence from the code alone | Keep `match="build_pkd"` **and** add an end-to-end test over the real `_build_assistant`. Two observers because either half can flatten it — and B6 was about a guessed reason naming two things that were fine. |

---

## How the work is run

Decided by the owner, 2026-09-23.

**Nothing is pushed until the owner says so.** Every commit stays on the local `master`, which
tracks the remote `ceidg-tool` branch of another team's repository. Two consequences to hold on to:
the organisation's Actions minutes are spent on the owner's signal and not on this plan's schedule,
and sixteen commits therefore live on one machine until then — so the push, when it comes, is worth
doing before anything else competes for attention. `Main` is never a target and `--force` is never
used.

**A code review closes each part, not each commit.** `@tester` then `@code-reviewer` after part A,
and again after part B. The per-step observers in this plan are written as the step goes; the
reviews look at the whole part, because a finding about how A1 and A3 fit together is invisible to
a review of either commit alone. A review finding is applied or argued against explicitly — the
project's own convention.

## After the code

A code review closes the part that touches behaviour, per the project's own convention, and a
review finding is applied or argued against explicitly. Then `skill.md` — which is worth writing
only after `szukaj-pkd` exists, so its most dangerous instruction ("never write PKD codes from
memory") can point at a command rather than at a YAML file.
