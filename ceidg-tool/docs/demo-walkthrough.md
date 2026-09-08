# Demo walkthrough

Date: 2026-09-08
Status: accepted
Author: P0w3r223
Related to: docs/adr/0014_offline_demo_mode.md, docs/audit-2026-09-09.md

This document is in English by project convention; every screen and command below is Polish,
because the tool is.

---

## What the demo is, and what it is not

`--demo` runs the real commands against a **synthetic register generated in the process**. Nothing
is recorded from production, no socket opens to CEIDG, and no CEIDG credential is read — `--demo`
passes `env_file=None`, `use_keyring=False` and a placeholder token, so a demo started inside the
repository does not pick up the real `CEIDG_TOKEN` from `.env`.

**One deliberate exception:** `ANTHROPIC_API_KEY` still reaches demo mode, so `--demo --opis "…"`
really does call `api.anthropic.com` and really does cost money. That is the intended switch for
showing the assistant, and the first screen names it in the `dokąd wysyła asystent` row — but do not
say "nothing leaves this machine" while using `--opis`.

**Real in the demo:** the whole decision sequence, the cost table's arithmetic, paging, the rate
limiter, the lock heartbeat, the progress cadence, `safetext` neutralising a hostile name, the
normaliser, the workbook, the checkpoint, and resume after a killed process.

**Not real:** the data (240 invented sole traders), and the *scale* — the cost table's minutes are
true for this corpus, not for the register, which returned **6 316 121** hits at `limit=1`
(`docs/decisions.md`). Say that out loud when you show it.

**Time is compressed, not faked.** The clock still sleeps, just eight times faster, so the limiter
and the heartbeat run the same paths in the same order. Set `CEIDG_DEMO_TEMPO` to change the pace
(higher = faster); the cost table keeps printing the true minutes either way.

---

## Before you start

```
$env:PYTHONUTF8 = "1"
$env:CEIDG_DATA_DIR = "$env:TEMP\ceidg-demo"      # keeps the demo away from the real store
```

The demo writes to `<CEIDG_DATA_DIR>\demo\`, never to the production directory. Without
`CEIDG_DATA_DIR` it uses `%LOCALAPPDATA%\ceidg-tool\demo\` — still a separate directory from the
real one, but setting the variable makes it obvious to the audience.

To start from a clean slate between rehearsals, delete that directory. **Do not use `wyczysc`** —
and note the audit found `wyczysc --starsze-niz 0` silently keeps 30 days (item A2, not yet fixed).

---

## The walk

### 1. The first screen — what the tool is, and that this is a show

```
.venv\Scripts\python -m ceidg_tool pobierz --demo -w wielkopolskie --szczegoly
```

Point at the two things that matter: the title says **POKAZ**, and the first row of the table is
the warning that the data is invented. The `dane i wyniki` row shows the demo directory.

### 2. The cost table — the honesty anchor

The table offers "lista podstawowa" against "z pełnymi szczegółami" with request counts and
**true minutes**. Say: *these minutes are the real arithmetic — 3.75 s per request is the measured
safe spacing, and the demo compresses only the waiting, not the estimate.*

Answer `szczegoly`.

### 3. The fetch — the machinery that exists for long waits

The two progress bars are the point: the list phase, then details in batches of five. This is the
part that once looked hung and got killed by an operator while it was working correctly.

### 4. The workbook

The summary names the file — note the **`DEMO_` prefix** — the record count, the status breakdown
(active, suspended, struck off) and how many entries carry a phone or e-mail (they are optional in
the register, so some are empty; that is a property of the data, not a fault).

Open the workbook and show two things:

- the `Metadane` sheet, whose **first row is the demo warning** — the marker that travels with the
  file after the show ends;
- in `Firmy`, the entry whose name begins `'=HIPERŁĄCZE(...)`. The leading apostrophe is
  `safetext` refusing to let a registry-supplied name execute as a spreadsheet formula. The
  register is public and anyone can write into it; this is what that costs.

### 5. `aktualizuj` — and the defect fixed today

```
.venv\Scripts\python -m ceidg_tool aktualizuj --demo --od 2026-09-07 --do 2026-09-08 --tak
.venv\Scripts\python -m ceidg_tool aktualizuj --demo --od 2026-09-07 --do 2026-09-08 --tak
```

Run the **same window twice**, and count the requests in
`<CEIDG_DATA_DIR>\demo\logi\ceidg-tool.log`. Measured on 2026-09-08 in a clean directory:

| | `/zmiana` | `/firma` |
|---|---|---|
| first run | 2 | **3** (⌈12/5⌉) |
| second run, same window | 2 | **0** |

The second run still pays for the count and one page of identifiers — those are cheap and
unavoidable — and buys **no details at all**, because every entry's detail is already newer than
the window that reported it.

This is worth narrating: until this morning the freshness rule was a seven-day cache TTL, so an
entry that changed yesterday but was fetched three days ago was *skipped* and kept its pre-change
description while the summary called it refreshed. The fix makes the rule "newer than the change
window", which keeps the zero-cost repeat and stops the silent loss.

### 6. Kill and resume

Start a fetch into a fresh directory, and kill the process mid-way — Task Manager, or close the
window. Then:

```
.venv\Scripts\python -m ceidg_tool runy --demo
.venv\Scripts\python -m ceidg_tool wznow --demo
```

`runy` shows the run still `w_toku`. `wznow` **refuses**, and this refusal is the interesting part:
the killed process left a database lock, and the tool will not write alongside what might be a live
process. It names the PID, the lock's expiry time, and the flag to override. Then:

```
.venv\Scripts\python -m ceidg_tool wznow --demo --force
```

It resumes from the checkpoint and produces the complete workbook.

### 7. The wizard (optional, needs a real terminal)

```
.venv\Scripts\python -m ceidg_tool kreator --demo
```

Same screens, driven by menu. Note: the wizard requires a TTY, so it cannot be exercised from a
script — which is exactly why gate 3 exists as a separate, human-run check.

---

## Result of the run recorded on 2026-09-08

Every step below was executed through the real CLI, not in-process.

| Step | Result |
|---|---|
| `pobierz --demo -w wielkopolskie --szczegoly --tak` | 144 firms of 240, 6 pages, cost table showing 35 requests / 2 min for the details path |
| Workbook | `DEMO_ceidg_wielkopolskie_test_*.xlsx`, 4 sheets (`Firmy`, `PKD`, `Slownik`, `Metadane`), 52 KB |
| `Metadane` first row | `UWAGA | TRYB POKAZU — dane są WYMYŚLONE …` |
| Hostile name in `Firmy` | `'=HIPERŁĄCZE("http://zły.invalid")[red]Fryzjer[/red]` — apostrophe-prefixed |
| Statuses | mixed within the voivodeship: 80 active / 32 struck off / 32 suspended; contacts 41 % phone |
| List mode (no `--szczegoly`) | fills `imie`, `nazwisko`, `ulica`, `budynek`, `kod_pocztowy`, `gmina`, `powiat`, `kraj`, `link` — as the register does; 91 of 240 entries carry no address at all, matching the measured 76/196 |
| `--out moj_raport` | written as `DEMO_moj_raport.xlsx` — the prefix survives an operator-chosen name |
| `aktualizuj --demo` (1st) | 12 changed, 12 details fetched, `stale_details = 0`, 3 `/firma` requests |
| `aktualizuj --demo` (2nd, same window) | 12 changed, **0 `/firma` requests** — the A1 property, measured in a clean directory |
| Kill mid-fetch (`SIGKILL`, exit 137) | run left `w_toku`, checkpoint intact |
| `wznow --demo` | refused, naming the dead PID and the lock expiry |
| `wznow --demo --force` | resumed and exported the complete 144-record workbook |
| Corpus determinism | identical fingerprint across two separate processes |

**Not proven by this run:** that a human can read the screens. Everything above was driven with
`--tak` or from a script, and the wizard needs a TTY. That distinction is the whole reason gate 3
is a separate, human-run check — see step 7.

---

## What the demo deliberately does not include

- **The report path (`/raporty`).** The synthetic register answers with an empty report list, so
  `--zrodlo auto` falls back to the API path. Generating a synthetic ZIP archive would be a second
  corpus; the audit argues the report path deserves attention, and that is its own piece of work.
- **A live assistant call.** The switch exists (`ANTHROPIC_API_KEY` reaches demo mode, because
  `_settings_demo` keeps the real environment for it) but the recorded-response path is not wired
  yet, so the sentence-to-criteria step is not part of this walk.
- **A `DEMO` value in `firma.zrodlo`.** ADR-0014 defers the schema-level marker to a later change;
  today the markers are the screen, the `Metadane` row, the filename and the separate directory.
