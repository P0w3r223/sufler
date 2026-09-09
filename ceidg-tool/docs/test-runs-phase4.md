# Test runs for phase 4 — functionality and resilience

Date: 2026-09-07
Status: groups A, B and C run 2026-09-07 (results below); **group C blocks group D** — see its results; D, E and B4 outstanding
Author: P0w3r223
Related to: docs/adr/0011_phase4_language_assistant.md, docs/resilience-report.md, UZUPELNIENIE_01.md §D/§E, docs/status.md

---

## Why a runbook and not more tests

768 offline tests cover everything that can be known without leaving the machine. What is left is
exactly the set of questions a test cannot answer, and each run below exists to answer one of them:

- Does the model, on a real call, produce PKD codes and criteria that a person would recognise as
  their own sentence? Nothing offline can tell us — the mock hands back whatever we wrote.
- Does `max_tokens` + `effort` actually leave room for the answer under Opus 5's adaptive thinking?
  The review predicted the previous setting would truncate **every** call; the fix is unverified.
- Does the `pkd` query parameter index PKD 2025? Records carry 2025 codes, but that is an inference.
- Does a person without API knowledge reach a finished file? That is gate 3, and it is the one
  requirement no test can stand in for.

## Order, and why it is this order

Cheap before expensive, and anything that would **invalidate** a later run first.

1. **A — assistant alone.** Model tokens only, **zero CEIDG requests**. If the interpretation is
   wrong, every later run is wasted effort against a wrong query.
2. **B — assistant under failure.** No CEIDG requests, no successful model calls; several cost
   nothing at all.
3. **C — PKD vintage probe.** 3 CEIDG requests. Must precede D: if the `pkd` parameter indexes 2007,
   the shipped dictionary is wrong and a PKD-filtered fetch would return nothing that means anything.
4. **D — gate 3, end to end.** ~6 CEIDG requests to a finished workbook.
5. **E — §E manual resilience scenarios 1, 2, 8.** Deliberate damage; run last because they leave
   the database and the disk in states the earlier runs would rather not meet.

Production consent is required from group C onwards (`--srodowisko prod --produkcja`), per CLAUDE.md.
Groups A and B spend no CEIDG request at all, so they need no consent for the register — only the
API key, which is already in `.env`.

---

## Group A — does the assistant understand Polish? (model only, 0 CEIDG requests)

**How to stop before spending anything.** Run the wizard, choose *"Pobrać firmy według kryteriów"*,
type the sentence, read the **Interpretacja** screen, then answer **"wróć do menu"**. The wizard
returns to the menu having spent zero CEIDG requests — the assistant sits upstream of `count`.

```
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool
```

Cost per run: one model call, a fraction of a grosz. The PKD dictionary in the prompt caches for
five minutes, so runs done back to back are cheaper still — and **A7 checks that the cache reads**.

| # | Sentence to type | What it probes | Pass |
|---|---|---|---|
| **A1** | `firmy budowlane w Białymstoku założone w zeszłym roku` | the ordinary case end to end | Criteria show `miasto: Białystok`, a construction PKD code **whose name on screen is construction**, and dates `2025-01-01 – 2025-12-31` — absolute, derived from today, not left as words |
| **A2** | `salony fryzjerskie w Łomży, potrzebuję telefonów` | does a contact request set `szczegoly`? | `ze szczegółami` in the criteria line, and `KONTAKTY_OPCJONALNE` among the limitations — the register may have no phone, and saying so is the point |
| **A3** | `firmy budowlane z przychodem powyżej miliona` | does it invent a filter the API lacks? | **No** revenue filter anywhere in the criteria; `BRAK_DANYCH_FINANSOWYCH` stated. A silent drop here would be the worst outcome: the query would look answered |
| **A4** | `spółki z o.o. w Warszawie` | does it claim to answer a question about a different register? | `SPOLKI_W_KRS` and `TYLKO_JDG` stated. If it returns Warsaw sole traders without saying they are not what was asked, that is a defect |
| **A5** | `firmy z PKD 62.01.Z` | the vintage boundary, and whether the 2007→2025 mapping is worth building | Either a refusal naming **PKD 2025** (today's behaviour, correct but blunt), or the code mapped to `6210A`/`6210B`. Whichever happens, **record it** — it decides the open item in `docs/status.md` |
| **A6** | `kilka firm fryzjerskich w Krakowie` | can the model set a record cap? | `maksymalnie N rekordów` **absent** from the criteria. It cannot — the schema has no such field — and resilience scenario 9's guard depends on that |
| **A7** | repeat **A1** verbatim, within five minutes | prompt caching | Second interpretation noticeably faster. Caching failing is silent by construction, and it is the difference between ~9 gr and ~1 gr per question |
| **A8** | `chcę wszystko` | a sentence with no usable filter | Refusal before any request, with the "bez żadnego filtra" sentence — not an empty query against the whole register |

**Also worth doing once, in A1:** answer **"tak, szukaj"**, then at the YAML question say yes. That
writes a query file from the assistant's output and costs nothing — it is the route a scheduled job
takes, since `--tak` refuses to confirm an interpretation nobody read.

### Group A — results, 2026-09-07

Run with `scripts/assistant_smoke.py` (a probe, not a test: it costs model tokens and needs the
network, so a person starts it deliberately — like the three CEIDG probes beside it). Eight runs,
**zero CEIDG requests**, about 40 seconds of wall clock and a few grosze.

**All eight met their stated criterion.** The headline is that the `max_tokens` + `effort` fix
works: every response was complete JSON, none truncated. The review's prediction — that the previous
`max_tokens=2048` would have consumed the ceiling on thinking before emitting the object — was
correct in kind, and the fix is now confirmed against the real API rather than argued.

| # | Result |
|---|---|
| A1 | `miasto: Białystok`, `województwo: podlaskie` (inferred, and unambiguous), PKD 4100A/4100B/4391Z/4399Z with **construction names on screen**, dates `2025-01-01 – 2025-12-31` — absolute, derived from today. 8.9 s cold. |
| A2 | `9621Z — Działalność fryzjerska`, `ze szczegółami` (the contact request set it), `KONTAKTY_OPCJONALNE` stated. Also added `status: AKTYWNY`, which the sentence did not ask for — see finding 3. |
| A3 | **No revenue filter invented.** `BRAK_DANYCH_FINANSOWYCH` and `BRAK_FILTRA_WIELKOSCI` stated. This was the most dangerous case and it is clean. |
| A4 | Warsaw criteria returned, with `SPOLKI_W_KRS` and `TYLKO_JDG` stated — the designed behaviour: partial criteria plus limitation codes, not a silent pretence of having answered. |
| A5 | **The model translated `62.01.Z` to `6210A`/`6210B` by itself.** No refusal was needed. See finding 1. |
| A6 | `max_rekordow: None` — "kilka" produced no cap. It cannot: the schema has no such field, and resilience scenario 9 depends on that. |
| A7 | **Cache read: 24 854 tokens** on every call after the first. Caching works. But the same sentence produced **15 PKD codes instead of 4** — see finding 2. |
| A8 | `is_empty()` true, so `prepare_fetch` refuses before any request. |

### Three findings from group A

**1. The model already maps PKD 2007 → 2025, and does it silently.** `62.01.Z` came back as
`6210A`/`6210B` with correct programming names — the dictionary's refusal path was never reached.
This largely answers the open item about building a mapping from `KluczePKD_2007_2025.xls`: the
capability exists without it. What is missing is the *telling*: an operator who typed a code they
have used for years is not told it no longer exists, only shown different codes. The names make it
visible to a careful reader; a sentence would make it visible to everyone.

**2. The same sentence gives materially different queries on different runs.** A1 chose four
construction codes; A7, verbatim the same sentence, chose fifteen — the whole of division 43. This
is not a formatting difference: `to_params` renders repeated `pkd=`, so the two are different
queries with different counts, different costs and different fetch times. The cost table still shows
the operator what they are about to spend, so nothing runs away unannounced — but "ask twice, get
two different jobs" is worth deciding about rather than discovering later. The lever is the prompt:
today it says nothing about how *broad* a code set should be.

**3. The model adds filters the sentence did not ask for.** A2 set `status: AKTYWNY` for "salony
fryzjerskie". Defensible — one usually wants operating businesses — but it is an addition, and
`WYKRESLONY` entries are excluded from the result without the operator having said so. It is visible
on the confirmation screen, which is the control working as designed.

None of the three is a defect in the code. All three are prompt-level decisions that only a real
run could surface, which is the argument for group A existing at all.

### Group A — prompt refined and re-run, 2026-09-07

Findings 1 and 2 were prompt-level, so the prompt was the fix. Both are confirmed against the real
model; finding 3 went away as a side effect.

**Finding 1 — the silent vintage translation now speaks.** A new limitation code
`KOD_PKD_Z_INNEGO_ROCZNIKA` carries it, which is the architecture's own mechanism: the model
chooses *which* limitations apply, and the Polish sentence is authored in `ui/texts.py`. On the
re-run, `62.01.Z` produced `6210B` **plus** the sentence *"Podany przez Ciebie kod PKD pochodzi ze
starszej klasyfikacji i dziś nie istnieje…"*. The operator is now told, instead of quietly shown
different codes.

**Finding 2 — the code set narrowed, though it is not deterministic and cannot be.** One
instruction ("podawaj **najwęższy** zestaw… nigdy nie wypisuj wszystkich podklas działu") with the
*reason* attached — each code is a separate filter, so breadth changes the request count.

| Run of the identical sentence | Before | After |
|---|---|---|
| A1 | 4 codes | 4 codes |
| A7 | **15 codes** (all of division 43) | **2 codes** |

The spread went from 4–15 to 2–4. The blow-up to a whole division is gone; run-to-run variation is
not, and will not be — it is a property of the model, not a defect a prompt can close. What makes
that acceptable is unchanged: the cost table prices the query before anything is fetched, so
whichever set comes back, the operator sees what it will cost and can decline.

**Finding 3 resolved itself.** A2 no longer adds `status: AKTYWNY` unasked — the same "narrowest"
instruction seems to discourage unrequested filters. One observation, not a proof.

**No regressions in the safety-critical runs**, re-checked after the prompt change: A3 still invents
no revenue filter, A6 still cannot set a record cap.

**A trap closed on the way.** Nothing pinned the limitation codes to their texts, so adding
`KOD_PKD_Z_INNEGO_ROCZNIKA` could have failed three silent ways — no description for the model (it
never learns the code exists), no sentence for the operator (`interpretation` skips it in
`if kod in …`, so a limitation the model *did* notice is swallowed), or a sentence with no code
(dead text). One test now pins all three key sets equal, another pins that every code reaches the
prompt, and the prompt's limitation list is **generated from the enum** rather than transcribed.
Verified by mutation.

---

## Group B — what happens when the assistant fails (0 CEIDG requests)

| # | How to induce it | Pass |
|---|---|---|
| **B1** | Temporarily rename `ANTHROPIC_API_KEY` in `.env` to `ANTHROPIC_API_KEY_OFF`, run the wizard | First screen says *"asystent wyłączony"*; the wizard goes **straight to the eight questions** with no description prompt and no error. The tool must be fully usable without the assistant |
| **B2** | Set the key to `sk-ant-api03-nieprawidlowy-klucz-do-testu`, run the wizard, type any sentence | A sentence pointing at `sprawdz-token`, exit to the menu, and **the key value nowhere in the output or in `logi/ceidg-tool.log`** |
| **B3** | `set ANTHROPIC_BASE_URL=https://example.com` then run the wizard with a sentence | Refusal naming the allowed host — **not** "brak połączenia". This is the fix from the last review: the SDK wraps our gate refusal in a connection error, and reporting it as a network problem would mislead exactly when it matters |
| **B4** | Disconnect the network, run the wizard, type a sentence | One announced pause, then a sentence saying the assistant is unavailable and offering questions one by one. Never a traceback, never an unbounded wait |
| **B5** | `PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool pobierz --opis "firmy w Łomży" --tak` | Exit code 3 with the "tryb nieinteraktywny" sentence. A schedule must not act on an interpretation nobody read |
| **B6** | Move `ceidg_tool/data/pkd2025.yaml` aside, run the wizard | Assistant unavailable, message names `scripts/build_pkd.py`. Restore the file afterwards |

`sprawdz-token` after each of B1–B3 should describe the state truthfully — source and fingerprint,
never the value.

### Group B — results, 2026-09-07

Run in a sandbox directory with its own `.env`. `DEFAULT_ENV_FILE` is relative to the working
directory, so a different cwd gives full control over the credentials without touching the real
file — which holds two secrets and is no place to experiment.

**Five of six run; B4 needs a real network cut and stays with the owner.** Two of the five found
defects, which is what this group is for: both were *messages*, and both had passed their stated
criterion while being wrong about something the criterion did not ask about.

| # | Result |
|---|---|
| B1 | `Deps.assistant is None`, first screen says *"asystent wyłączony"*, `sprawdz-token` truthful. The wizard's description branch is guarded on `deps.assistant is not None`, so the eight questions come as before |
| B2 | Rejected key → *"Klucz asystenta został odrzucony. Sprawdź go: `ceidg-tool sprawdz-token`."*, exit 3, and **zero occurrences of the key** anywhere under the data directory including the log |
| B3 | `ANTHROPIC_BASE_URL=https://example.com` → *"Odmowa połączenia z 'example.com': dozwolone są tylko hosty api.anthropic.com"*, exit **1** (the documented code for `UntrustedLinkError`), refused in 2.2 s — i.e. before any network. The last review's fix, confirmed end to end |
| B4 | **Not run** — needs the network adapter actually disabled |
| B5 | Exit 3 and the right sentence, but **7.4 s and a rendered interpretation**: the model was asked *before* the refusal. Fixed; now 2.2 s, no call. See below |
| B6 | Dictionary moved aside → the message blamed *"brak klucza API albo pakietu `anthropic`"*, naming two things that were both present. Fixed; now names the missing file, the command to rebuild it and the GUS source |

### Two defects found by group B

**B5 — paying to learn something already known.** `--opis` with `--tak` cannot possibly be
confirmed: the confirmation question carries `safe_default=False`, so the refusal is certain from
the first line of the command. The program nevertheless spent a model call and rendered an
interpretation before refusing. That is the same mistake as sending a request for a NIP whose
checksum is wrong locally — a cost paid to discover something knowable for free. The guard moved to
`cli.pobierz`, where consent-shaped decisions already live (ADR-0008), and the message now also says
what to do instead: run without `--tak`, or let the wizard write a query file for the schedule.

**B6 — a message that blamed the wrong thing.** `_build_assistant` caught `CeidgError` and returned
a bare `None`, so the specific reason died there and the caller fell back to a sentence enumerating
causes it had not checked. With the dictionary absent, an operator was told to look at their key.
`Deps` now carries `assistant_reason`, and `load_pkd`'s own sentence — which names the file, the
command and the source — reaches the screen. The default sentence stays, but only for the case
where the reason genuinely is unknown.

Both are pinned by tests: one asserts the model is never called when `--tak` is present, the other
that a known reason reaches the operator instead of the default.

---

## Group C — which PKD vintage does the filter index? (3 CEIDG requests)

```
PYTHONUTF8=1 .venv/Scripts/python scripts/ceidg_probe_pkd_vintage.py --env prod
```

Three `limit=1` requests, counts only, no personal data fetched or written. The script states its own
verdict. `4933Z` is a positive control: it appears in real records, so a zero there means the run is
inconclusive and the other two numbers must not be read.

**Pass**: `6210B` returns a non-zero count and `6201Z` returns zero → the parameter indexes PKD 2025
and the shipped dictionary is right. Any other combination is a finding, and the "SŁOWNIK ASYSTENTA
JEST ZŁY" branch means stopping before group D.

### Group C — results, 2026-09-07

Run on production with the owner's consent given in session. Three requests, ~22 s of wall clock.

| `pkd=` | Exists in | HTTP | `count` |
|---|---|---|---|
| `6201Z` | PKD 2007 only | 200 | 234 605 |
| `6210B` | PKD 2025 only | 200 | 142 294 |
| `4933Z` | PKD 2025 only (positive control) | 200 | 31 402 |

**The run did not meet the pass criterion above — it landed on the third branch**, and following
that branch to the end turned group C from a formality into the finding of the day. The criterion
demanded `6201Z` return **zero**; it returned 234 605. Both vintages are accepted, because the
filter matches the code **as stored on the record** — and while the transition to PKD 2025 runs (to
31.12.2026) each record carries one vintage or the other.

**Settled offline, at zero further cost.** `probe_out/raport_sample.zip` — the 21 MB production
report already on disk — carries `RokPKD` per record. Across **285 026 real records** (one
voivodeship, wielkopolskie): 58.6 % still carry PKD 2007 codes, and **8.6 % carry no code at all
that the shipped `pkd2025.yaml` knows** — corrected 2026-09-09 from 25.2 %, which counts records
whose *main* code is absent and so answers a different question. The full measurement is in `docs/decisions.md`.

### Group C blocked group D — unblocked again by ADR-0012 (2026-09-07)

The walk sentence below is *"salony fryzjerskie w Łomży"*. `9602Z`, PKD 2007's hairdressing code, is
the **single most frequent unreachable code** in the sample (6 811 of 285 026); PKD 2025 splits it
into `9621Z`/`9622Z`. The assistant would answer `9621Z`, the register would return only the
migrated salons, and the workbook would look complete. A gate-3 walk in that state would be
measuring the wrong thing: the operator cannot judge a result whose incompleteness is invisible.

**ADR-0012 was designed, accepted and built the same day**, so the walk below stands as written —
and the hairdressing sentence is now the *right* one to use, because it exercises the new step
rather than dodging it. Its own gate item, never measured before, was settled with three more
requests: repeated `pkd=` is OR-ed, exactly (38 201 + 187 149 = 225 350). That measurement also put
a national figure on the gap: a PKD 2025 hairdressing query reaches **17 %** of hairdressers.

**What to watch in group D, added by ADR-0012.** Walking *"salony fryzjerskie w Łomży"* now reaches
a screen that did not exist before:

| What to watch | Why |
|---|---|
| A screen appears naming `9602Z` and saying it **also covers beauty salons** | That trade-off cannot be removed — a `9602Z` record carries no evidence of which side of the split it is on. The screen is the whole remedy |
| It carries **two counts**, and the wider one is roughly six times the narrower | Both are measured, one request each. If a number looks invented, it is a defect |
| Choosing *narrow* spends no further `count`; the cost table shows the number already taken | The restated invariant: at most two before consent, none after |
| Choosing *wide* on a national query crosses the 50 000 threshold and offers the split path | Designed behaviour, not a surprise |
| `Metadane` and the saved query file record which population was chosen | A rerun has to reproduce the same result, not today's default |

---

## Group D — gate 3, end to end (~6 CEIDG requests, production)

The acceptance requirement: *a person without API knowledge reaches a finished file*. Now it also
exercises the assistant, so this run answers two questions at once.

```
PYTHONUTF8=1 .venv/Scripts/python -m ceidg_tool --srodowisko prod --produkcja
```

Walk: first screen → *"Pobrać firmy"* → describe it in a sentence (e.g. *"salony fryzjerskie
w Łomży założone w 2014 roku, najwyżej 20 firm, z telefonami"* — note the cap has to be given in the
follow-up, since the assistant cannot set one) → confirm the interpretation → cost table → *lista*
or *szczegóły* → purpose → export.

| What to watch | Why |
|---|---|
| The cost table appears **before** any fetch, and its numbers match what then happens | The whole consent model rests on it |
| The progress bar moves; no stretch of silence longer than a few seconds | Phase 3e closed thirteen defects of this shape; the assistant added a sixth channel that has never run against a real model |
| The closing summary is **visible** — not overwritten by a live bar | That was the defect the owner found walking gate 3 the first time |
| The workbook opens, `Firmy` leads with `nip`/`nazwa`, PKD names match the industry asked for | The dictionary and the register agreeing on names, at last, on a real record set |
| `Metadane` carries the purpose, and `link_ceidg` is filled (API path) | Provenance |

Then, in the same session: **`aktualizuj`** — it must ask before starting, with a cost table
(phase 3e), and **`eksportuj`** — it must reproduce the workbook from the database with no request.

---

## Group E — §E manual resilience (deliberate damage, run last)

These three have automated equivalents that pass; §E asks for real runs, dated in
`docs/resilience-report.md`.

| # | Scenario | Do | Pass |
|---|---|---|---|
| **E1** | kill mid-fetch | Start a fetch of a few hundred records, kill the process (Task Manager → End task) mid-page, then `ceidg-tool wznow` | Resumes without re-fetching saved pages; final record count equals `count`; no duplicate ids. If the lock blocks, the message must name the expiry time and `--force` |
| **E2** | network cut | Start a fetch, disable the network adapter for two minutes, re-enable | Continues with no intervention, nothing lost. Expect ~7 minutes of waiting: the 10 → 30 → 60 s rungs end while the network is still down, so the fourth attempt lands after 300 s. That is measured, not a fault |
| **E3** | full disk | Fill a small volume (or point `CEIDG_DATA_DIR` at a nearly full one), run `eksportuj` | No partial file at the destination, no `.tmp` left behind, database intact, message names the path. Repeat the export once space is back — it must succeed |

---

## Group F — what could be verified without the owner (2026-09-07)

The owner asked for as much verification as possible to be done without them. The test API host
settles what that can be: `test-dane.biznes.gov.pl` resolves to 158.66.4.154 and **times out at TCP
level after 8 s**, while `dane.biznes.gov.pl` (184.86.103.7) connects in 0.03 s. Measured again
today, so groups D and E cannot run against the test environment at all, and production needs the
owner's consent for personal data. Everything below therefore costs **zero CEIDG requests**.

### F1 — every new screen rendered through the real `rich`, and a defect only rendering could show

Offline tests assert `Block` view models; nobody had ever *rendered* the vintage screens. Done
through `ui/render.ConsoleView`, the same path `cli.py` uses. Polish diacritics, column widths and
wrapping are correct. One real defect surfaced:

> `1086Z  Produkcja artykułów spożywczych homogenizowanych i żywności dietetycznej`
> `       …ten kod istnieje też dziś i znaczy: Produkcja artykułów spożywczych homogenizowanych…`

For codes whose PKD 2007 and PKD 2025 names are identical, the "what it means today" line repeated
the column beside it word for word and read like a bug in the program. The view model was correct,
which is why no assertion caught it. Fixed with a second phrasing ("…ten kod jest nadal w użyciu,
więc dojdą też dzisiejsze firmy z tym kodem") and pinned by a test that reads the rendered **text**
rather than the fields.

### F2 — the model's real code choices land on the transition table

Four real model calls, zero CEIDG requests, roughly a grosz. The question was whether the vintage
step is theoretical: does the model, for ordinary Polish sentences, actually pick codes that have
PKD 2007 predecessors?

| Sentence | Model chose | Predecessor | Question asked? |
|---|---|---|---|
| *salony fryzjerskie w Łomży* | `9621Z` | `9602Z` | yes — also reaches `9622Z` (beauty) |
| *kluby fitness w Białymstoku* | `9313Z` | `8551Z` | yes — `8551Z` is still live today, meaning something else |
| *piekarnie w Suwałkach* | `1071Z` | `1086Z` | yes — also reaches `1061Z`, `1062Z`, `1072Z`, `1073Z` |
| *warsztaty samochodowe w Ełku* | `9531A`, `9531B` | `4520Z` | yes — also reaches `9531C` |

All four fire the new screen. The last row is the context-sensitivity working against real model
output: the model picked two of the three repair subclasses, so only `9531C` is named as coming
along — the other two were asked for.

Note that three of these four trades are exactly the ones the code review found orphaned by the
generator's dropped mappings. Before that fix, fitness clubs and bakeries would have got no
expansion at all and no screen.

### F3 — the workbook records which population was fetched

Verified on a real `.xlsx` read back with `openpyxl`, not on the criteria model: `Metadane`
carries both the human sentence (`dodatkowo kody PKD 2007: 9602Z`, phrased as an addition rather
than as the operator's own choice) and `kryteria_json` with `pkd_2007`, so the run can be
reproduced exactly. Pinned as a test in `tests/test_pipeline_e2e.py`.

### F4 — the flag/file contradiction refuses, end to end through the real CLI

`pobierz -z zapytanie.yaml --bez-pkd-2007 --tak` exits **3** with a Polish sentence naming both
sides and what to do, before any request. The criteria block above it shows
`dodatkowo kody PKD 2007: 9602Z`, i.e. `describe()`'s distinction survives into the real terminal.

### F5 — three stale PKD hints, all teaching a code the tool itself refuses

`hints_block` (wizard), `prompts.PKD_PYTANIA` (wizard question) and `--pkd`'s typer help all offered
`62.01.Z` as *the* example. That code does not exist in PKD 2025 and `assistant/pkd.validate_codes`
rejects it, so the program was teaching the operator a code its own assistant refuses. All three now
say `62.10.B`. Found by reading the rendered help, not the source.

### F6 — a real CLI run against a host that never answers

`pobierz --miasto Łomża --pkd 14.23.Z --pkd-2007 --tak --srodowisko test`, i.e. the real command
against the dead test host. Two things came out of it.

**The clean-expansion block renders in the real terminal, before any request.** The sequence reads
as intended: the criteria block shows what the operator asked for (`PKD: 1423Z`), then a separate
block says what the program is adding and why (`1412Z Produkcja odzieży roboczej`, "znaczą dokładnie
to samo… więc dokładam je bez pytania"). Nothing is silently widened.

**The retry ladder is bounded, and the run only *looked* unbounded.** Observed rungs: 10 s, 30 s,
60 s, then 300 s repeating — cut off at 900 s by the harness timeout, not by the program. That is
correct behaviour, not a defect: `CONNECTION_MAX_OUTAGE_S` is 30 minutes of **accumulated delay**,
so the ladder gives up around the ninth failure with a `TransportError` naming the resume command.
`tests/resilience/test_s2_network_outage.py` already pins both halves. Worth recording because the
first reading of the log suggested an infinite loop, and it is not one.

What the operator sees while waiting is exactly what phases 3e and 3g were for — every rung prints
its own line with the wall-clock time it will resume at and the words *postęp zapisany*:

```
brak połączenia, czekam do 17:58, postęp zapisany
czekam 300 s (ponowienie), dalej o 18:05
```

Note for group E2: real wall time to give up is longer than 30 minutes, because each attempt also
spends up to the 60 s connect timeout before its delay starts. Expect roughly 40 minutes against a
host that blackholes TCP, rather than one that refuses fast.

### What still cannot be done without the owner

- **Group D (gate 3)**: needs production and needs a person who does not know the API. Both halves
  are the owner's by definition.
- **Group E (scenarios 1, 2, 8)** and **B4**: need a real killed fetch, a real network cut and a
  real full disk, all against a live API.

The automated equivalents of E1/E2/E3 pass (66 tests under `tests/resilience/`), and the full suite
is at 854.

---

## What to record, and where

- Group A: the interpretation screens (a screenshot or copy-paste is enough) — especially **A5**,
  which decides whether the 2007→2025 mapping is worth building.
- Group C: the three counts, into `docs/decisions.md` beside the other measurements.
- Group D: into `docs/status.md` as the gate-3 result, and the request count into
  `docs/resilience-report.md`.
- Group E: into `docs/resilience-report.md` with dates, which is what §E asks for.

## Estimated cost

| Group | CEIDG requests | Model calls | Wall clock |
|---|---|---|---|
| A | **0** | 8–9 | ~10 min |
| B | **0** | 3 (all failing) | ~10 min, plus the network cut |
| C | 3 | 0 | ~15 s |
| D | ~6 | 1 | ~5 min, plus reading the workbook |
| E | ~10-20 | 0 | ~30 min, mostly waiting in E2 |

Groups A and B are the cheapest information in this table by a wide margin: they cost nothing from
the register, need no consent for personal data, and answer the question that currently has no
evidence at all — whether the assistant works.
