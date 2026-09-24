# ADR-0025: The assistant is built only where it can be used, and its absence always has a named reason

Date: 2026-09-23
Status: **accepted** (the owner's decision, given in session 2026-09-23)
Author: P0w3r223
Related to: ADR-0011 (phase-4 assistant, decision 9 "no key is a normal state", boundary rule 12),
            ADR-0017 (reason codes over guessed sentences), ADR-0014 (demo), ADR-0024 (a machine
            caller never reaches the assistant), run B6 in docs/test-runs-phase4.md

**Renumbered from ADR-0024 on 2026-09-24.** This project's remote home moved into the WorkMate monorepo, where `ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md` already holds that number and `tests/test_adr_numbering.py` enforces uniqueness. The commit messages of the work this document describes still say ADR-0024; they were written before the collision was visible and are left as they were, because a commit message is a record of what was known at the time.

---

## Context

`pipeline.build_deps:384` builds the assistant for **every** online command. Four of the six —
`wznow`, `aktualizuj`, `raporty`, `sprawdz-nip` — have no path to
`flow.collect_from_description:111` and never will; a fifth, `pobierz`, needs it only with
`--opis`, which is known at parse time.

What they pay for, per invocation: `import anthropic`, a 728-entry YAML parse with per-key
`normalize_pkd`, a roughly 40 kB prompt render, an httpx2 client with a fresh SSL context and CA
bundle, a secret registration, and `_wycisz_sdk()` — which mutates `os.environ`.

**The cost, measured 2026-09-23 — and the first measurement of it in this ADR was wrong.**

```
import ceidg_tool.cli (what every command pays)   479 ms
_build_assistant() with a key present             955 ms
```

Attribution inside a warm process, after the CLI is imported:

| Step | Cost |
|---|---|
| `import anthropic` | 806 ms |
| `load_pkd()` — 728 entries | 58 ms |
| `build_system()` — prompt render | 0.2 ms |
| `build_model_http_client()` | 0.6 ms |
| remainder in `AnthropicCaller.__init__` (`register_secret`, `_wycisz_sdk`, `Anthropic(...)`) | ~90 ms |

So building the assistant **roughly doubles** the startup of a command that already costs half a
second to import, and `import anthropic` is 85 % of it — which also settles option D below:
`lru_cache` over the dictionary would recover 58 ms of 955 and is not worth a line.

**The retracted figure, kept because the way it was wrong is the point.** This section first said
*"roughly 0.2 s"*, from one run each of `runy` with and without a key (1,095 s vs 0,879 s). Two
things were wrong with it. It was a single sample per side, inside the noise of process startup.
And `runy` passes `online=False`, so it sits outside the `if online:` block that calls
`_build_assistant` — **it has never built an assistant at all**, with a key or without one. The
number therefore measured nothing but variance, on a command chosen precisely because it could not
use the feature being measured. It reached the ADR, the plan and the commit message for step A2
before being caught by attributing it. Same shape as the `rokPkd` line in `conftest.py` that an ADR
cited as a measurement: a figure repeated because it was written down, not because it was taken.

The time is still the smaller half of the objection: **a command that cannot use the assistant
should not be constructing a credentialed client to a second host, and should not be editing the
process environment.**

There is also no way to turn it off. A key in the keyring makes the wizard ask for a description
and the menu advertise it; the only lever is deleting the key — which turns "run this without the
assistant" into a credential operation.

One fact constrains every option: availability today is **proved by construction**, and the reason
for absence is a *measured* string (`deps.assistant_reason`), because run B6 found the guessed
reason naming two things that were fine while the actual cause — a missing PKD dictionary — went
unnamed. Any design that turns availability into a prediction has to answer that.

## Decision 1 — the switch

`--bez-asystenta` on `pobierz` and `kreator`.

| Option | Verdict |
|---|---|
| **A (chosen).** A flag, plus a **closed reason enum** `PowodBrakuAsystenta` (`BRAK_KLUCZA`, `BRAK_PAKIETU`, `BRAK_SLOWNIKA`, `WYLACZONY_FLAGA`) with an optional `szczegol` string and sentences in `ui/texts.py` | Mirrors `texts.PowodBrakuRaportu` exactly (ADR-0017): the code is computed where the fact is, the sentence is authored by us. The `szczegol` field is not tidiness — it is what keeps `load_pkd`'s own message ("build it with this command") alive instead of being flattened into a generic sentence, which is the B6 defect. |
| **B.** A setting in the config file or an environment variable | Rejected: a switch invisible in the command line is a switch that explains a missing screen nowhere. Flags are the input surface (ADR-0022). |
| **C.** No switch; delete the key | Rejected: it is the current state, and it makes "run this without the assistant" a credential operation. |

Two behavioural rules:

- **The switch changes no exit code by itself.** With `--bez-asystenta` the wizard does not ask for
  a description, the first screen's *dokąd wysyła asystent* row says it was switched off by flag,
  and the run behaves exactly as the no-key path — which ADR-0011 already established as normal,
  not an error. **"Byte-identical to the no-key path" was one word too strong** and is corrected
  here on 2026-09-23, while building it: the §A row is deliberately *different* from the no-key
  row, because decision 1 exists so the operator sees their own decision rather than the state of
  their keyring. Identical in behaviour, distinct on the one line that reports the choice.
- **`--opis` together with `--bez-asystenta` is a `ConfigError`, exit 3, before anything is
  built.** Silently ignoring `--opis` is the same defect as spending a model request on an
  interpretation nobody can confirm (`cli.py:525`, run B5): a cost or a loss incurred to learn
  something that was knowable at parse time.
- No code is removed. The assistant, its tests, the `asystent` extra and boundary rule 12 stay
  exactly as they are.

## Decision 2 — where the construction decision belongs

| Option | Verdict |
|---|---|
| **A.** Leave it; 0.2 s is small | Rejected on the second half of the cost, not the first: `raporty` should not open a credentialed path to `api.anthropic.com`, and `_wycisz_sdk` should not edit the environment of a command that will never call a model. |
| **B (chosen).** `build_deps(…, asystent: bool)` — the **caller** decides, because the caller knows whether its path can reach `collect_from_description` | The cost falls to exactly `kreator` and `pobierz --opis`, which is the set that can use it. No prediction is introduced anywhere: availability is still proved by construction, so `assistant_reason` keeps being measured rather than guessed. The switch from Decision 1 is one more input to the same boolean. |
| **C.** Lazy provider: a cheap predicate now (`find_spec`, key present, file exists), expensive construction on first `interpret` | Rejected **as unnecessary**, which is the interesting part: with B the remaining construction happens only where the answer is displayed or used, so laziness buys nothing and costs the honesty of the reason — "the file exists" is not "the dictionary loads", and the dictionary is what B6 was about. |
| **D.** `lru_cache` the dictionary and the rendered prompt | **Declined on the measurement**: `load_pkd` is 58 ms of 955, and the rendered prompt 0.2 ms. `import anthropic` is 85 % of the cost and no cache reaches it. |

**The guard that keeps B from rotting:** a parametrised test over every command asserting whether it
builds an assistant, against a declared table. A new command that silently acquires one — or a
refactor that silently takes one away from `pobierz --opis` — fails the suite. Same move as
`test_kazde_pole_kryteriow_ma_flage_w_wierszu_polecen`: a list compared against reality, with a
named exception per entry.

**The measurement is taken and it is in Context above.** It was owed because the wizard and
`pobierz --opis` still pay whatever remains, and this project does not keep numbers it has not
measured. Taking it did more than fill a table: it retracted the figure this ADR was written
around. B was correct either way — that is why the ADR did not have to be reopened — but the
argument for it is now five times stronger than the one first written down, and option D is
declined rather than deferred.

## Decision 3 — the first screen claims the assistant works whenever a key exists, and that is not the same fact

Measured 2026-09-23: `texts._assistant_destination` branches on `settings.anthropic_key is not
None`, while availability is decided later by `_build_assistant`. So with a key present and the PKD
dictionary missing, the §A row says *"treść Twojego pytania i słownik PKD do api.anthropic.com"* —
about an assistant that did not build. It is a pre-existing gap, not one this ADR creates, but a
closed reason enum whose sentences the first screen cannot read would leave it half-repaired.

| Option | Verdict |
|---|---|
| **A (chosen).** The banner stays where it is, reading `settings` plus the new flag; **after** `build_deps`, when a key exists and the assistant is absent, `deps.warnings` gains a sentence built from `BrakAsystenta` | `deps.warnings` is an existing channel that `cli` already drains (`for warning in deps.warnings: view.warning(warning)`), so this costs one condition and no reordering. It gives the enum its second reader, and the false claim acquires an observer on the screen where it was made. |
| **B.** Move `_banner` after `build_deps` so the first screen reads construction | One producer, which is the tidier shape — but `_banner` runs before criteria parsing, so a bad `--od` would print **before** the first screen. That screen is a §A acceptance criterion and the first thing the operator sees; reordering it to fix a secondary row is the wrong trade. |
| **C.** Thread only the flag, leave the two producers | What this ADR said before 2026-09-23. Cheapest, and it leaves the operator told the assistant is live until they try `--opis` and learn otherwise. |

The warning fires only when a key exists: no key is a normal state (ADR-0011 decision 9) and
already has its own row, so warning about it would be noise on every run of a tool most people use
without an assistant.

## Consequences

- `pipeline.build_deps` gains one keyword; `_build_assistant` is unchanged apart from the new
  `WYLACZONY_FLAGA` reason.
- The decision-3 sentence is produced in **`ui/flow.show_first_screen`**, not in `build_deps`.
  Decision 3 above argues from `deps.warnings` being a channel `cli` already drains, and that
  argument was right about the channel and wrong about the producer: `pipeline` does not import
  `ui` and must not start, so the code that *builds the sentence* has to sit in the layer that
  printed the promise. `pipeline` keeps the fact (`BrakAsystenta`), `texts` keeps the sentence.
  The visible consequence, recorded rather than discovered later: the correction reaches the
  **wizard only**, not `cli._banner`. That is harmless today because `pobierz --opis` fails with
  the reason as a `ConfigError`, and it is the shape to revisit if another command starts showing
  the section-A row while an assistant was asked for and failed.
- The warning is silent for `BRAK_KLUCZA` and `WYLACZONY_FLAGA` — a switched-off assistant is not
  a surprise worth a warning, and the first screen already says so in its own row.
- `deps.assistant_reason` becomes `PowodBrakuAsystenta | None` plus `szczegol: str | None`;
  `flow.collect_from_description:115` reads the code, `texts` owns the four sentences, and the
  current `deps.assistant_reason or texts.ASSISTANT_UNAVAILABLE` fallback disappears — with a
  closed enum there is nothing left to fall back from.
- `texts.first_screen`'s assistant row gains one state; that row is a §A acceptance criterion and
  its test grows a case.
- Interaction with ADR-0024: machine mode can never use the assistant (`--opis` with `--tak` is
  already refused at `cli.py:539`), so a machine invocation builds none — one more caller for whom
  B is simply correct.

**What we give up:** `ceidg-tool pobierz` without `--opis` no longer proves at startup that the
assistant *would* work. Nothing displayed that fact on that path, so nothing is lost on screen;
`sprawdz-token` remains the place that answers "is my key good", and it does not build a client
either.

**Revisit when** a command other than `kreator` / `pobierz` needs the assistant — the table is then
the one place to change — or when the measurement shows a dominant cost inside a path that still
pays it.
