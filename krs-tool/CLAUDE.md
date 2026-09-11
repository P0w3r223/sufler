# krs-tool

Python CLI that turns a Polish court-register extract into a risk report for an operator with no
knowledge of APIs. Fifth sub-project of this repository, created 2026-09-10 by
`ceidg-tool/docs/adr/0023`. Work proceeds in steps; each one ends when the owner accepts it, not
when the tests go green — `docs/status.md` is the living plan.

## Facts that change how you work here

**This tool does not connect to anything, and that is a boundary rather than a setting.** No HTTP
client, no networking dependency, no `socket` — absent from the import graph and from
`pyproject.toml`, with three independent observers (an AST scan, a manifest check, and a socket ban
active for the whole suite). The reason is legal, not technical: art. 60a of the KRS act penalises
obtaining register information through network services without an entitlement a private entity
cannot even apply for, no case law exists either way, and the owner chose to wait for a ministerial
position. **The ban covers development and testing.** Do not add a probe, do not "just check one
endpoint", and do not resolve the boundary by adding a configuration flag — see `docs/adr/0001`
decisions 2 and 3.

**The input is a file the operator saved by hand.** One human action per company. Nothing here
fetches it.

**The flagship rule cannot fire, on purpose.** Detecting a missing financial statement requires
excluding six lawful reasons for its absence; two cannot be determined from an extract and one has
not been sampled, so the rule always returns `Nieustalony`. That is the state
`ceidg-tool/docs/adr/0023` requires until the registry publication lag is measured, and it is
visible on the printout rather than hidden. If you find yourself making this rule fire, read the
"Gate" table before writing a line.

**A non-empty division does not tell you which entry is in it, and four rules say so on purpose.**
The read model knows a division is absent, empty or non-empty; the key names *inside* a division have
never been measured (`docs/niezmierzone.md`, row 10). So the four division-4 rules return
`Nieustalony`, and the signal that really follows — the division is not empty, and every entry it may
carry is terminal — is a separate rule, `dzial4_niepusty`, marked `zakres: caly_dzial` in the
catalogue. If you find yourself mapping a rule onto a guessed key such as `zaleglosciPodatkowe`, stop:
that guess is indistinguishable from a measurement once it is in the code, and it produces an
accusation about a named company.

**There is no word available to accuse with.** `Poziom` has no member meaning "late", and a scan
checks the rule catalogue and the report texts against a closed list of accusatory words. This is
paired with the three-valued result: no value to carry the accusation, no vocabulary to phrase it.
A tool that tells a compliant company it filed late is worse than a tool that says nothing.

**The signal layer cannot read the clock.** Every date comes from the extract, primarily
`naglowekA.stanZDnia`. The only sentence the tool can form is "no entry for financial year X
according to the register state as at D". Reproducibility falls out of this for free, which is what
gives `odtworz` something to assert.

**No fixture here may make a claim about the API.** Claims about a saved file live in
`tests/fixtures/odpis_traits.yaml`, each citing the file, its SHA-256, the date and who supplied it.
Claims about the wire live in `docs/niezmierzone.md`, each with the event that closes it. A test
asserts that nothing appears in both. Synthetic extracts may be built and may not be cited as
evidence, and a report produced from one carries markers that make it unmistakable.

**There is no token here.** The secret registry is copied and stays empty; its docstring says so,
because masking that is believed to protect something while protecting nothing is worse than no
masking. If you are looking for a credential, you are in the wrong sub-project.

**Polish output needs `PYTHONUTF8=1`** on this Windows console, exactly as in `ceidg-tool`.

## Commands

```
PYTHONUTF8=1 .venv/Scripts/python -m pytest
PYTHONUTF8=1 .venv/Scripts/python -m mypy krs_tool tests
.venv/Scripts/ruff check krs_tool tests
.venv/Scripts/ruff format --check krs_tool tests
PYTHONUTF8=1 .venv/Scripts/python -m krs_tool
```

This sub-project carries **both** quality-gate sets: `mypy` in strict mode, which only `ceidg-tool`
has, and the function ceiling `C901`/`PLR0915`, which only `ceidg-tool` lacks. The ratio engine and
the parser of later stages are the two places in this tree most likely to grow a three-hundred-line
method, and a ceiling is free on a green field. `scripts` joins the mypy and lint scope in step 2,
together with the first script — unlike in `ceidg-tool`, because the anonymiser is where evidence is
produced and it has to obey the same rules as the package.

CI: the `krs-tool` entry in the root `.github/workflows/ci.yml`.

## Where the design lives

- `docs/status.md` — the living plan: steps, gates, open items. Update at every gate. Steps 0-3 are
  accepted, step 4 is built and awaiting acceptance — a step ends when the owner accepts it.
- `docs/adr/0001_zakres_etapu_1_i_granica_offline.md` — scope of stage 1, the offline boundary, the
  three-valued result, and the recorded deviation from `ceidg-tool/docs/adr/0023`.
- `docs/design/etap1_core.md` — module map and the twelve boundary rules, with the mechanism for
  each and the step in which it gains an observer.
- `docs/niezmierzone.md` — what this project is not entitled to claim, and who can close each row.
- `ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md` — the decision that created this
  sub-project, including the "Gate" table this tool obeys.
- `ceidg-tool/docs/research/` — five documents of evidence behind all of the above.

## Conventions

Code comments, docstrings and user-facing text are Polish; documents under `docs/` are English. Line
length 100, mypy strict over `krs_tool` and `tests` (and `scripts` from step 2). Comments carry the
*why* — where one explains a guard, it is usually load-bearing history rather than noise.

A step that touches behaviour ends with a code review, and a review finding is applied or argued
against explicitly, not silently dropped.

And the sentence that must not be the one that fades as a session fills: **nothing in this
sub-project opens a socket, and the reason is a criminal provision with no case law either way.**
