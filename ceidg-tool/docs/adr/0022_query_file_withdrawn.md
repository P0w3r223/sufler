# ADR-0022: The YAML query file is withdrawn; the wizard prints a command instead

Date: 2026-09-10
Status: **accepted** (the owner's decision, given in session 2026-09-10)
Author: P0w3r223
Related to: ADR-0008 (`Criteria` as the only contract, decision 2: one sentence, several entries),
            ADR-0012 (vintage choice recorded for a rerun), ADR-0014 (demo markers),
            docs/research/public-search-parity.md

---

## Context

`pobierz --zapytanie plik.yaml` read criteria from a YAML file. The file existed for a reason that
stopped being true on 2026-09-10: **not every field had a flag**. `imie`, `nazwisko`, `ulica` and
`kod` were reachable only through the file or the assistant, so anyone needing them had to learn a
second input format. The same morning, every filtering field of `Criteria` got a flag.

What the file was actually used for, and what replaced each use:

| Use | Replacement |
|---|---|
| Scheduled runs (`--tak` needs a terminal-free input) | flags: every field has one |
| Repeating a query verbatim next month | the command the wizard now prints |
| Turning an assistant sentence into a repeatable job | the same printed command |
| Recording the PKD vintage choice (ADR-0012) | `--pkd-2007`, which picks wide without asking |

The owner's reason for withdrawing it was the operator, not the code: a second input format that
does nothing the first cannot do is a way to be wrong about what the tool will fetch.

## Decision

**Remove the query file entirely**, and have the wizard print a ready-to-paste command after the
decisions are made (`texts.polecenie_powtarzajace`, `wizard.show_repeat_command`).

Removed: the `--zapytanie/-z` flag, `pipeline.criteria_from_yaml`, `wizard.offer_yaml`, the
`ZAPISZ_YAML` question, and the tests whose subject was the file. **Not** removed: YAML as a format
in this project — API profiles, `pkd2025.yaml`, `pkd2007_2025.yaml` and `tests/fixtures/api_traits.yaml`
are unaffected, and PyYAML stays a dependency.

## Consequences

**One difference, and it is on screen.** The file stored the *codes* of the 2007 vintage; the
command carries the *decision* (`--pkd-2007`) and the codes come from the transition table at run
time. For an unchanged table the population is identical — after the table changes, not necessarily.
`texts.POLECENIE_ROCZNIK` says this next to the command, and
`tests/test_wizard_vintage_command.py` holds it.

**A defect disappeared with its subject.** Audit item A6 — `--lista` unable to switch off
`szczegoly: true` coming from a file, because `False` was indistinguishable from "not given" —
cannot occur without a value underneath. `szczegoly` is a plain `bool` again, and the reason is
recorded in `tests/test_flagi_trojstanowe.py` rather than deleted, so a future source of criteria
(a profile, a remembered query) brings back the tri-state deliberately instead of rediscovering it
through another audit.

**A guarantee got stronger, not weaker.** ADR-0008's decision 2 (the same criteria produce the same
sentences by any route) used to be checked flags-against-file. It is now checked as a loop: the
command the wizard prints is fed back through `CliRunner` and must render the same cost table
(`tests/test_cli_phase3.py`). Quoting is part of that test — "Stara Łomża przy Szosie" has to
survive the shell in one piece, so values needing quotes get single quotes (no interpolation in
bash or PowerShell), and a value carrying an apostrophe or a control character makes the tool say
so instead of printing a command that means something else after pasting.

**The demo marker travels with it.** A command produced under `--demo` carries `--demo`; without
it, a pasted line would reach the register — which is ADR-0014's first marker failing on a channel
it never covered.

## What would reverse this

A need the flags cannot express: criteria too long for one line, a query built once and versioned
in a repository, or a field that never gets a flag on purpose. None of these is present today; the
first that appears should reopen this ADR rather than reintroduce the file quietly.
