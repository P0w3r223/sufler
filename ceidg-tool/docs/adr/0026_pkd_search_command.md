# ADR-0026: `szukaj-pkd` — one command that answers "what will `--pkd` actually reach", including the 2007 half

Date: 2026-09-23
Status: **accepted** (the owner's decision, given in session 2026-09-23)
Author: P0w3r223
Related to: ADR-0011 decision 4 (the dictionary is load-bearing because an unknown `pkd` returns
            **204, not 400** — finding F8), ADR-0012 (vintage coverage, `--pkd-2007`), ADR-0019
            (modules that arrive unplaced grow divergent semantics), ADR-0020 (when the transition
            table may be deleted — *proposed*), ADR-0024 (the envelope), ADR-0014 (why this command
            has no demo mode), CLAUDE.md (the PKD dictionary is generated, never written from
            memory)

**Renumbered from ADR-0025 on 2026-09-24.** This project's remote home moved into the WorkMate monorepo, where `ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md` already holds that number and `tests/test_adr_numbering.py` enforces uniqueness. The commit messages of the work this document describes still say ADR-0025; they were written before the collision was visible and are left as they were, because a commit message is a record of what was known at the time.

---

## Context

The operator has no way to find a PKD code except through the assistant, which needs a key, a
network and a spendable credential — for a lookup in two files that ship with the program.

Meanwhile the register is mid-transition. `9602Z` (hairdressing, 2007) is the most frequent code no
PKD 2025 code reaches; `6201Z` does not exist in PKD 2025 at all and still returns 234 605 records;
and 8.6 % of a 285 026-record sample carries no code the shipped dictionary knows. So the question
an operator actually has is not "what is this code called" but **"if I pass `--pkd X`, what will I
reach, and what will `--pkd-2007` add?"** — and both halves are already in the tree:
`ceidg_tool/data/pkd2025.yaml` (728 subclasses) and `ceidg_tool/data/pkd2007_2025.yaml`, whose
`Poprzednik` values already carry the two shapes of ambiguity (`rowniez`, `dzis`).

There is a second reason, and it is the one that decides the priority. With ADR-0025 the assistant
becomes switchable, and a caller that switches it off loses `AssistantResult.kody_pkd` — the field
that carries **code plus name from the local dictionary, never a name from the model**. CLAUDE.md
names that as the operator's one control: a wrong code is invisible, a wrong industry name is not.
An agent picking codes preserves that control only if it reads the dictionary rather than recalls
it — and this project has measured what recall produces: a list that passed every automated check
here while being quietly wrong. An instruction saying "read the file" is an admonition; a command
that searches the file is a mechanism.

## Decision 1 — one command, one argument, shape decides

`ceidg-tool szukaj-pkd <fraza albo kod>` — a verb, like `sprawdz-nip <nip>`.

| Option | Verdict |
|---|---|
| **A (chosen).** One positional argument; if `criteria.normalize_pkd` accepts it, it is a **code lookup**, otherwise a phrase search | One thing to remember, and the same normalisation as `--pkd`, so `62.01.Z`, `6201z` and `6201Z` all behave exactly as they do in a query. The output states which reading it took, so a surprise is visible rather than silent. |
| **B.** `--kod` / `--fraza` | Rejected: two flags for a distinction the value already carries unambiguously — a canonical PKD shape is never a Polish phrase. |

## Decision 2 — what a hit shows

For a **2025 code**: code, name, and the 2007 predecessors `--pkd-2007` would add, each with what
it drags in (`rowniez`) and what it means today if it is still a live 2025 code (`dzis`). For a
**2007 code**: which 2025 codes it leads to — so `9602Z` visibly covers both `9621Z` (hairdressing)
and `9622Z` (cosmetics), which is the decision `ui/flow.py` asks the operator to make and which
nothing lets them prepare for today.

Search is **substring, case- and diacritic-insensitive, no stemming**. ADR-0011 option B already
named the Polish stemming problem ("budowlane" vs "budownictwo") as a reason not to build a search
tool for the model; the same reasoning applies here, and the honest response is to say so on
screen: the command searches names, not meanings, and a phrase that finds nothing is not proof that
the industry has no code.

Result cap: 40 rows, `obciete: true` in the envelope and one line on screen saying how many more
and that `--wszystkie` lifts it. A 100-row `rich` table scrolls the hit off the top of the
terminal — the same class of defect as the folded path in ADR-0024.

**Every screen carries the transition caveat once**: a code being valid does not make a query
complete, because each record carries one vintage. That sentence is the whole reason this command
is not decoration.

## Decision 3 — where the dictionary lives now that it has a second consumer

| Option | Verdict |
|---|---|
| **A.** The new command imports `assistant.pkd.load_pkd` | Works today (`assistant/pkd.py` imports only `yaml`, `criteria`, `errors`, so an install without the `asystent` extra is fine) and is the cheapest. Against it: the module name is a claim about ownership, and the claim would be false — the dictionary is a fact about the register, not an asset of the assistant. With ADR-0025 making the assistant switchable, a dictionary that lives inside `assistant/` invites exactly the wrong deletion later. |
| **B (chosen).** Move the loader to a neutral pure module (`pkddict.py`), beside `pkdmap.py`; `assistant/pkd.py` keeps `lookup` / `validate_codes` and imports it | Two consumers is exactly when ownership stops being free (ADR-0019: modules that arrive unplaced grow divergent semantics — `reports.matches_criteria` versus `demo/rejestr._pasuje` is the measured case). Cost is small and mechanical: one move, `PURE_MODULES` in the scan gains an entry, ADR-0011's consequences table gains a dated note. |

## Decision 4 — what this command does to `--pkd` validation (ADR-0011 finding F8, open since 2026-09-07)

`--pkd 9999Z` passes `Criteria` today on **shape alone**; the API answers 204 and the operator reads
"Brak firm spełniających kryteria", indistinguishable from an empty register. That is the exact
hole the dictionary was built to close for the model, left open for the flag.

| Option | Verdict |
|---|---|
| **A.** Reject a code present in neither file | Rejected. The 2007 side of our data is a *transition key*, not a full PKD 2007 list, so "absent from both files" is not the same claim as "does not exist" — and a false rejection blocks a query the register would have answered. The tool must not overrule the register on evidence it does not have. |
| **B (chosen).** **Warn**, once, naming `szukaj-pkd`, and fetch anyway | Costs nothing, cannot block a legitimate query, and converts a silent 204 into a sentence. It is also the honest strength of the claim we can actually make. |
| **C.** Nothing | Rejected: F8 has been open since 2026-09-07 and the remedy is now one line away. |

The measurement that would upgrade B to A: a full PKD 2007 subclass list from GUS, generated the
same way `pkd2025.yaml` is — **never written from memory**, for the reason CLAUDE.md gives at
length.

## Decision 5 — no `Deps`, no token, no `--demo`

The command builds **no dependencies at all**: two YAML files, a pure search, a `Block`.
Consequences worth stating, because each is a departure that looks like an omission:

- It runs with **no CEIDG token**. `_settings()` resolves a token and can fail; a dictionary lookup
  that needs a credential would be absurd, and this is the first command anyone runs while setting
  the tool up.
- It makes **zero requests**, and `--help` says so.
- It has **no `--demo`**. This is the one command where demo and production are not distinguishable
  facts, because it touches no register — and ADR-0014's markers exist to separate register data,
  not to decorate. A `--demo` flag here would be a marker with nothing to mark.
- With ADR-0024 it takes `--wynik json`, and `trafienia` is the envelope's per-command field. That
  is why the two ADRs land together: this command is the cheapest possible end-to-end test of the
  envelope — offline, deterministic, no token, no network, no personal data.

## Consequences

| File | Status | Responsibility |
|---|---|---|
| `ceidg_tool/pkddict.py` | new (moved) | **Pure.** `load_pkd` for the 2025 dictionary. |
| `ceidg_tool/pkdszukaj.py` | new | **Pure** (rule 6). Normalisation, folding, ranking, the merge of 2025 names with 2007 predecessors. Zero I/O beyond the two loaders. |
| `ceidg_tool/ui/texts.py` | extended | The result block, the "names not meanings" sentence, the transition caveat. |
| `ceidg_tool/ui/wynik.py` | extended | `trafienia`. |
| `ceidg_tool/cli.py` | extended | `szukaj-pkd`, authoring no sentence. |
| `ceidg_tool/criteria.py` or `cli` | extended | Decision 4's warning, at one site wherever `--pkd` is materialised, so the wizard and the flags share it. |
| `tests/test_boundaries.py` | extended | Two pure modules join `PURE_MODULES`. |

**What we give up:** no synonyms, no stemming, no fuzzy matching. "Fryzjer" finds hairdressing
because the official name contains it; "salon" may not. The screen says which search it ran, so the
operator can widen the phrase rather than conclude the code does not exist.

**Revisit when** the transition table is deleted (ADR-0020, not before 2027-02-01 and only after
the measurement): the command survives, the predecessor column disappears, and the caveat goes with
it. Also when a full PKD 2007 list exists — Decision 4 option A becomes available then, and only
then.
