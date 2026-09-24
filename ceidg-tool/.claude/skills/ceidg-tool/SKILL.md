---
name: ceidg-tool
description: |
  Driving this repo's CEIDG CLI from flags, without its interactive assistant:
  the `--wynik json` result envelope on stdout, the exit-code taxonomy, PKD code
  lookup and the production-consent rule. Use whenever a task means fetching,
  updating, resuming, exporting or inspecting Polish sole-trader (JDG) records
  with `ceidg-tool` / `python -m ceidg_tool`, or reading its JSON output.
  Triggered by: pobierz, aktualizuj, wznow, eksportuj, szukaj-pkd, sprawdz-nip,
  raporty, runy, CEIDG, JDG, PKD, --wynik json, --demo.
---

# ceidg-tool from an agent

You are the second caller this tool was built for. The first is a person at a terminal, and
almost every screen exists for them. **Do not read the screens.** Pass `--wynik json`, parse
stdout, branch on the exit code.

## Invocation

```bash
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool <polecenie> [flagi] --tak --wynik json
```

`PYTHONUTF8=1` is mandatory — without it the *screens* on stderr mangle Polish diacritics on this
Windows console. (The envelope itself is pure ASCII with `\uXXXX` escapes, so it survives either
way; that is deliberate.) `.venv/Scripts/ceidg-tool` is the same thing if the venv is on PATH.

**stdout is the envelope and nothing else. stderr is the whole human narrative** — first screen,
cost table, warnings, errors, progress. Nothing is suppressed; read stderr when you need to explain
what happened to a person, but never parse it. Screens are free to be reworded; the envelope is
the contract.

## The repository ships no credentials

**There is no CEIDG token and no assistant API key in this checkout.** The author's token is not
distributed and was never committed: measured 2026-09-24 over all 4681 blobs of a clone fetched
that day, by value and by shape, across its every branch and tag. That `.env` was never tracked is
a separate measurement — `git log --all -- .env` is empty.

So on a fresh clone, assume the credential is missing until something proves otherwise. The rule
the code applies is narrower than "commands that reach the register":

- **Everything needs a token** except `szukaj-pkd`, `token zapisz`, `token usun`, and anything run
  with `--demo`. That includes commands that touch no network at all — `eksportuj`, `runy`,
  `wyczysc`, `kreator`, `sprawdz-token` — because settings resolve before the work starts.
- Without one they exit **3** with a sentence beginning `Brak tokenu:`. For the commands that
  carry an envelope that arrives as `"status": "blad"`, so branch on it rather than on the text.
  For the ones that do not — `eksportuj`, `kreator`, `wyczysc`, `sprawdz-token`, `token *` —
  `--wynik json` is refused first and **stdout stays empty**, so do not call `json.loads` on it.
  The command table below is the authority on which is which.
- `szukaj-pkd` and everything under `--demo` work with no credential at all. Start there.
- The assistant key is a **separate, optional** credential. Its absence disables `--opis` only —
  and you should not be using `--opis` anyway (see below).

Obtaining a token is a human errand, not a step you can take: it needs Profil Zaufany at
<https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00>. If one is missing, say so and stop; do not
attempt to source a credential from the environment, another repository or a previous session.

## Before anything reaches production

`--srodowisko prod --produkcja` sends requests about **real people** and the token's payload carries
a PESEL. Never pass it unless the owner has consented **in the current session** — not in a previous
one, not "presumably". This is the one action here that cannot be taken back.

Develop and demonstrate against `--demo`: a synthetic register generated in-process, no socket, no
token, its own data directory. The `test` environment is dead (the host is unreachable from this
network), so `--demo` is the only register-free path.

## The envelope

One JSON document, one line, printed once, at the end — on every exit path, including an
unexpected exception.

```jsonc
{
  "wersja": 1,              // bumped only on a removal or rename
  "polecenie": "pobierz",
  "status": "ok",           // ok | brak_trafien | nic_do_zrobienia | przerwano | blad
  "kod_wyjscia": 0,         // equals the process exit code, always
  "demo": true,             // ← check this. Nothing else tells you the data is invented.
  "zapytania": 3,           // requests spent in this invocation
  "uwagi": [{"kod": "", "tekst": "…"}],
  "srodowisko": "test",     // absent where the command has none (szukaj-pkd)
  "kryteria": { },          // Criteria as sent
  "run_ids": ["…"], "rekordy": 1234,
  "pliki": ["C:\\…\\wyniki\\CEIDG_prod_wielkopolskie_20260923_1203.xlsx"],
  "blad": {"typ": "AuthError", "komunikat": "…"}   // only when status == "blad"
}
```

`wersja`, `polecenie`, `status`, `kod_wyjscia`, `demo`, `zapytania` and `uwagi` are **always**
present. The rest appear when the command produces them.

**`uwagi[].tekst` is not a contract.** This project rewrites sentences whenever it finds a defect.
Branch on `kod` where one exists; treat `tekst` as prose for a human.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | `ok`, or `przerwano` |
| **4** | `brak_trafien` (the query ran and matched nothing) or `nic_do_zrobienia` (nothing to resume / no changes / no finished run) |
| 1 | unrecoverable in this run |
| 2 | **resumable** — retry makes sense, or `wznow` |
| 3 | configuration, authorisation, or a decision the tool refuses to take for you |
| 130 | Ctrl+C. **No envelope** — 130 is outside the taxonomy, so it is not invented. |

**Code 4 exists only under `--wynik json`.** Without the flag those same outcomes exit 0, so
existing schedules do not start alerting on a legitimately empty day. Distinguish
`brak_trafien` from `nic_do_zrobienia` by `status`, not by the code.

## Three refusals, all exit 3

Each of them is a `ConfigError` with a Polish sentence on stderr. Ignoring a flag is how a machine
caller ends up parsing a human screen, so none of them is silent.

1. `--wynik` with anything but `ekran` / `json`.
2. `--wynik json` on a command that has no envelope: `kreator`, `eksportuj`, `wyczysc`,
   `sprawdz-token`, `token *`.
3. `--wynik json` **without `--tak`** on any command that can ask a question. `runy` and
   `szukaj-pkd` ask nothing and do not require it.

`--tak` is not consent to production. That stays with `--produkcja`.

## Decisions `--tak` will not take for you

`--tak` answers every question with its declared default — except the ones marked as *not
yours to answer*, which raise `ConfigError` (exit 3) with a sentence naming the flag to pass.
Expect these:

| Situation | What happens | What to pass |
|---|---|---|
| more than **50 000** hits | refuses; a split table is printed to stderr first | `--partie` (accept batching) or `--maks N` |
| `--zrodlo raport`, no report covers the criteria | refuses rather than silently falling back to the API | `--zrodlo auto` if the API path is acceptable |
| `--opis "…"` together with `--tak` | refuses **before** building anything | use flags instead (see below) |
| zero hits | **not** a refusal: exits with `brak_trafien` / 4, never widening the query on its own | broaden the criteria yourself |

## Do not use `--opis`

`--opis` hands a sentence to a language model that turns it into criteria. It exists for a person
who does not know the API. For you it is strictly worse: it refuses under `--tak` anyway, it costs
~955 ms of construction (806 ms of that is `import anthropic`), it opens a credentialed path to a
second host, and it mutates `os.environ`. **Build `Criteria` with flags.** Every filtering field
has one.

Pass `--bez-asystenta` when you want that stated explicitly — it also keeps the first screen honest
about why no assistant is present.

## PKD is the trap worth knowing

Two facts, both measured, both on by default:

1. **The dictionary shipped here is PKD 2025**, and `6201Z` — the classic software code — does not
   exist in it at all. The API still returns 234 605 records for it, because the filter matches the
   code *as stored on the record* and PKD 2007 stays legal until 31.12.2026.
2. **8.6 % of a 285 026-record sample is unreachable by any 2025 code** — measured on a
   *wielkopolskie* report archive, which is the honest scope; do not restate it as "the register".
   A PKD-filtered fetch therefore returns a subset, silently. That is a property of the register,
   not of the tool, and it applies to `--pkd` exactly as it applies to every other input.

So: **resolve codes with `szukaj-pkd` before you fetch.** It costs zero requests, needs no token, no
database and no network.

```bash
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool szukaj-pkd 9602Z --wynik json
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool szukaj-pkd fryzjer --wynik json --wszystkie
```

It takes a code (`6210B`, `62.10.B`, `6210b` — same normalisation as `--pkd`) or a phrase, and says
which it read the argument as:

```jsonc
{ "odczytano_jako": "kod",        // or "fraza" — a typo in a code reads as a phrase
  "wszystkich": 1, "obciete": false,
  "trafienia": [{
    "kod": "9602Z", "nazwa": "Fryzjerstwo i pozostałe zabiegi kosmetyczne", "rocznik": 2007,
    "poprzednicy": [],            // for a 2025 code: what --pkd-2007 would add
    "nastepcy": [                 // for a 2007 code: where it leads today
      {"kod": "9621Z", "nazwa": "Działalność fryzjerska"},
      {"kod": "9622Z", "nazwa": "Działalność w zakresie pielęgnacji urody…"}]}]}
```

Read `poprzednicy[].rowniez` before passing `--pkd-2007`: it names the *other* industries that
would come along with the old code. Phrase search matches **names, not meanings** — "budowlane" will
not find "budownictwo".

An unknown `--pkd` code no longer produces a silent empty result; it warns on stderr and proceeds.

## Commands

All take `--wynik json`; the last four refuse it.

| Command | What it does | Envelope adds | `--tak`? |
|---|---|---|---|
| `pobierz` | fetch by criteria → xlsx (`--format xlsx,csv,jsonl`) | `kryteria`, `run_ids`, `rekordy`, `pliki` | yes |
| `aktualizuj` | changes since the last run (`/zmiana`) + refresh details | `zmienione`, `szczegoly`, `nierozwiazane`, `przeterminowane` | yes |
| `wznow` | resume an interrupted fetch from its checkpoint | `run_ids`, `rekordy`, `pliki` | yes |
| `sprawdz-nip` | one company by NIP; checksum verified locally first | `firma` (**one person's data** — see below) | yes |
| `runy` | fetches recorded in the local database | `runy` | no |
| `raporty` | ready-made CEIDG report archives; `--pobierz <id>` downloads one | `raporty`, `pliki` | yes |
| `szukaj-pkd` | PKD lookup, offline | `trafienia`, `odczytano_jako`, `wszystkich`, `obciete` | no |
| `eksportuj` · `kreator` · `wyczysc` · `sprawdz-token` · `token *` | — | no envelope (exit 3) | — |

`nierozwiazane` and `przeterminowane` on `aktualizuj` matter more than they look: **zero is their
only correct value.** Anything else means work was done and lost.

## Cost, time and interruption

Requests are spaced **3.75 s** apart and the tool counts in requests, never pages. A real fetch can
run for half an hour. Two consequences:

- The tool counts hits **before** asking for consent and prints a cost table to stderr; the numbers
  in `zapytania` are what it actually spent.
- Exit **2** means resumable. `wznow` picks up from the checkpoint; re-running `pobierz` starts over.

Off a terminal you get one progress line per 8 requests (~30 s) on stderr, plus one per stage
transition — timestamped to the second, so a captured log shows where a stall began.

## Personal data

- `sprawdz-nip`'s `firma` is one identified person: name, address, phone. The tool writes no
  envelope to disk, so retention is **yours**. Do not paste it into anything that keeps logs.
- Output files land in `wyniki/` only, with a `DEMO_` prefix in demo mode even under `--out`.
- The CEIDG token never appears in any output: everything on stdout passes one masking walk.

## Worked example

```bash
V=".venv/Scripts/python -m ceidg_tool"

# 1. Which codes actually cover hairdressing?
PYTHONUTF8=1 $V szukaj-pkd fryzjer --wynik json          # → 9621Z, and 9602Z is its 2007 source

# 2. Try it against the synthetic register first.
PYTHONUTF8=1 $V pobierz --demo --tak -w wielkopolskie --pkd 9621Z --szczegoly --wynik json
echo "exit=$?"

# 3. Production — only with the owner's consent, given in this session.
PYTHONUTF8=1 $V pobierz -s prod --produkcja --tak \
  -w wielkopolskie --pkd 9621Z --pkd-2007 --maks 5000 --wynik json
```

Branch on the exit code first, then on `status`; read `pliki` for the workbook and `zapytania` for
what it cost. On 2, run `wznow`.

## If you are changing the code, not calling it

Read `CLAUDE.md` first — it is unusually load-bearing, and most of its paragraphs record a defect
that was found and closed. Then `docs/adr/0024_machine_output.md` for this surface, and
`docs/design/phase2_core.md` for the fifteen boundary rules (rule 15: only `jsonout.py` writes to
stdout). Gates, all four, no `-q`:

```bash
PYTHONUTF8=1 .venv/Scripts/python -m pytest
PYTHONUTF8=1 .venv/Scripts/python -m mypy ceidg_tool tests
.venv/Scripts/ruff check ceidg_tool tests scripts
.venv/Scripts/ruff format --check ceidg_tool tests scripts
```

The house standard for a new guarantee is an observer: **a guarantee whose violation has no
observer is not a guarantee.** Before adding a guard, ask what would print if it were violated —
if the honest answer is "nothing", that is the defect.
