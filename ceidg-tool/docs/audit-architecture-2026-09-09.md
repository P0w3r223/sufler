# Architecture, code and test audit with external reconnaissance

Date: 2026-09-09
Status: draft. The audit itself changed no production code; after it closed, the owner authorised
two of its findings (Tier 1 items 1 and 6) and those are applied — see the note under the ranked
list. Every remaining decision belongs to the owner.
Author: P0w3r223
Related to: docs/audit-2026-09-09.md (the 2026-09-08 audit this one builds on), CLAUDE.md,
docs/design/phase2_core.md, docs/status.md, ADR-0012, ADR-0014, ADR-0015, ADR-0016, ADR-0017

---

## 1. Method

Two waves. Wave 1 ran eight agents in parallel with **deliberately overlapping scopes**, because
the previous audit's most valuable output came from the two places where two passes looked at the
same artefact from different questions. Wave 2 ran the architect afterwards, with wave 1's findings
supplied as measured input so it could not spend its budget rediscovering them.

| Agent | The one question it was given | Overlapped with |
|---|---|---|
| tester | does the suite *observe* what it declares — would it pass with the property removed | code-reviewer on `tests/fixtures/`, `tests/support.py` |
| tester (2nd) | do the doubles agree with the register, `api_traits.yaml` as reference | code-reviewer (2nd) on the anonymiser |
| code-reviewer | correctness and security of everything built **after** the 2026-09-08 audit | tester on the doubles |
| code-reviewer (2nd) | is the layer that manufactures evidence and reference data trustworthy | tester (2nd) |
| researcher ×4 | four external directions, §5 below | — |
| researcher ×2 (pass 2) | targeted: the two pass-1 claims that were load-bearing and not closed | pass 1 |
| architect | do the layers, `Criteria`, and the fourteen rules still describe this program | all of them |

**Contract given to every agent.** Input: goal, scope as a path list, and a sentence naming what
no longer needed checking. Output: findings with file and line, ≤ 2 000 tokens, each declaring the
scope actually reviewed and the sampling rule. Boundary: no agent saw another's work, none wrote
to a repository file, none sent a request to CEIDG. Mutations were permitted and had to be
reverted; `git diff --stat` was empty at the end and `git status --short` showed only the
untracked prompt file.

**Research passes.** One pass on four perspectives, then a *targeted* second pass on the two
questions where pass 1 left a claim that would change a decision without fully closing it. Pass 2
settled both — see §5.

**No worktrees, and it cost something.** `.env`, `.venv`, `probe_out/` and `PKD/` are
git-ignored, so a worktree would contain neither the gates nor the samples against which claims
get checked; that outweighed the default isolation rule. The price was real: two agents mutating
the same tree produced two transient test failures in a third agent's run, which that agent
correctly diagnosed as a concurrency artefact rather than a defect. Worth knowing before
repeating this shape.

**No request reached CEIDG, stated structurally.** Every spawn carried the prohibition verbatim;
`--srodowisko prod --produkcja` was never uttered in this session; demo runs answer from an
in-process `MockTransport`, so no socket opens. This is **not** read from `request_log`:
`store.py:31` keeps entries for 7 200 s and `trim_request_log()` deletes them, so a half-day audit
would read a zero there that means nothing.

**Two errors of my own, both instructive.** I twice measured the raw archive against a shape I had
assumed rather than checked: once splitting `PozostaleKodyPkd` on commas when the separator is
`$##$` (`reports.py:135`), once reading `links` at the top level of a probe sample when the sample
wraps the response as `{url, status, headers, body}`. Both produced confident wrong numbers; both
were caught by comparing against the project's own artefact — the production parser, then the real
sample. That is defect shape 2 applied to the auditor, and it is the strongest argument in this
document for the doctrine it was auditing.

### Volume, and which ratio this is

| Artefact | Lines | Files |
|---|---|---|
| `ceidg_tool/` | 13 046 | 41 |
| `tests/` | 22 337 | 71 (69 with content) |
| `scripts/` | 2 790 | 13 |
| `docs/` | 7 713 | — |
| root prose (`CLAUDE.md`, `README.md`, the two requirement documents) | 925 | 4 |
| `.claude/sessions/` | 1 273 | 6 |

**Prose : code = 9 911 : 13 046 = 0.76 : 1**, counting `docs/` + root prose + session briefs
against `ceidg_tool/` only. Counting `scripts/` as code gives **0.63 : 1**.
**Tests : code = 22 337 : 13 046 = 1.71 : 1.**
The previous audit computed 0.68 : 1 and 1.67 : 1 on a smaller tree and, judging by the figure,
a narrower definition of prose. The test-to-code ratio is essentially unchanged; prose grew
slightly faster than code. The largest artefact here is still the test suite, so the owner's old
hypothesis ("over-invested in documentation") stays refuted as stated.

---

## 2. The numbered items: all still standing, four of them sharper

These were measured and numbered by the previous audit. They were not rediscovered — only checked.

| # | Standing | Refinement measured today |
|---|---|---|
| C1 opaque secret masked | open | `config.py:456,460` are the only production calls; every test (`resilience/test_s7_token_leak.py:157-179`) calls `register_secret` directly. Seam still untouched. |
| C2 `ratelimit` may not see the database | open | `grep -c ratelimit tests/test_boundaries.py` → **0** |
| C3 `approx` without `abs=` | open | **37** call sites; no scan persisted |
| C4 registry text cannot steer the terminal | open | `safetext.py:16` is `ord(ch) >= 32`; `\x7f`, `\x85`, `\x9b` pass |
| C5–C8 | open | unchanged; C7 is now sharper — see F-E1, F-E5 |
| D2 `datetime.now` outside `Clock` | open, **11 not 13** | 13 grep hits in 5 files, of which `cli.py:111` and `pipeline.py:818` are *comments about the rule*. Eleven calls. |
| D5 demo database cannot label itself | open **by decision** | `store.py:76` `CHECK (zrodlo IN ('CEIDG_API','CEIDG_RAPORT'))`; ADR-0014:111 defers it deliberately at a stated price (schema v4 + migration). Not a backlog item. |
| D8 only `pobierz` resumes | open | unchanged, and §3.4 shows why it now matters more |
| F12 exact match on unmeasured fields | open, **5 fields not 7** | `nazwa` and `miasto` were measured 2026-09-09 and corrected to `_contains_ci` (`reports.py:280-300`). `powiat`, `gmina`, `ulica`, `imie`, `nazwisko` remain. |
| B4 trim the retrospective | open, **and it grew** | `status.md:114-1376` is **1 263** lines, not 1 076. The item about removing content gained 17 % since it was named. |

---

## 3. Findings

Each carries file and line, the consequence, and the evidence. Findings are labelled by the
project's own six defect shapes where one applies.

### 3.1 The evidence layer

**F-E1 — `api_traits.yaml` excludes a measured property, on a premise that is false.**
*Shape 2 and 6.* The file's stated purpose is that only measured properties go into it. Its header
comment excludes `links.next == links.self` because "the probe never reached the last page of
`count = 6 316 121`". You do not need to reach the last page of a six-million-row result: when
`count = 1`, the only page *is* the last one, and the probe captured exactly that. Scanned all 35
raw sample files; **23** carry a `links.next`/`links.self` pair, and of those **two** have
`next == self` — `probe_out/mini/06_200_firmy.json` (count = 1) and
`probe_out/nip_ids/1_200_firmy.json` (count = 2) — while 21 have `next != self`. The mechanism
by which the measurement was lost is one line: `api_traits.yaml:16` says
`zrodlo_surowe: "probe_out/samples/"`, naming one of the three directories the probe wrote to.
**Consequence:** this is the *only* property that terminates paging on this API — `decisions.md:347`
records that guard 2 (a missing `next`) never fires. No fixture carries a terminal page; ten test
sites hand-build one, so the parser is checked against a double built from the parser's own
contract.

**F-E2 — half of `api_traits.yaml` has no audit against raw samples.**
`test_api_traits.py:109` is parameterised by `ID_PARAMS` alone. The entries under `odpowiedzi`
declare `probki: "11, 15"` and `probki: "01"`, and `grep -rn "probki" tests/` returns **zero**.
For identifiers the file's claim (a fixture cannot be generated into agreement with itself) holds;
for `204` and `count` the loop closes on the fixture and the profile with no link to a sample.

**F-E3 — the anonymiser truncates two lists without declaring it, and nothing observes the size.**
`scripts/anonymize_samples.py:199` (`identyfikatoryWpisow[:10]`) and `:202` (`raporty[:12]`).
Measured: the raw `/zmiana` sample holds 100 identifiers against a fixture of 10; the raw
`/raporty` sample holds 816 against 12 (`decisions.md:34`). `count` stays full, so the fixture body
has a shape the register does not return — a 10-item page at `limit=100`. **Mutation, reverted:**
trimming `tests/fixtures/zmiana.json` from 10 identifiers to 2 leaves **1258 passed, 1 skipped**.
**Consequence:** `/firma` batching at 5 exercises 2 batches instead of 20, so the request-counted
heartbeat rhythm — the doctrine `CLAUDE.md` calls load-bearing — is tested at a tenth of scale.

**F-E4 — the demo corpus is ~4.7× harsher than the register, justified by a retracted number.**
`ceidg_tool/demo/korpus.py:17-18` cites 58.6 % vintage-2007 as the reason a 2025 code misses part
of the register — the exact conflation `CLAUDE.md` retracted on 2026-09-08 (58.6 % is the vintage
share; **8.6 %** is "no code the entry carries is in the dictionary"). Measured on the built corpus
(`ile=240`, default seed): vintage 2007 = 62.1 %, unreachable by any 2025 code = **40.0 %**, because
`korpus.py:307` gives an entry codes only from its own vintage and only one of the five 2007 codes
exists in 2025. **Consequence:** the operator learns a trap five times larger than the real one
from the screen the project built to teach it.

**F-E5 — the demo double contradicts a measurement wherever the measurement lives only in prose.**
*Shape 2.* `demo/rejestr.py:77-87` puts `miasto` in the `proste` list and the loop at `:89-96`
compares it by `casefold()` equality; `decisions.md:430` records the zero-request measurement that
it matches by **fragment** (run `eb1df3a8`, `miasto=Łomża` returned entries from
`Stara Łomża przy Szosie`). **Evidence, run:** `pobierz --demo -m "Łomża"` → 48 firms;
`pobierz --demo -m "Łomż"` → 0 firms and a "nothing found" screen. The register answers that
non-empty. Separately, `demo/rejestr.py:100-102` matches `ulica` by fragment while
`reports.py:321-329` matches it exactly, under a docstring at `:293` saying it is unmeasured — two
doubles written a day apart, disagreeing, with nothing confronting them.

**And a third, worse than either: `demo/rejestr.py:81` reads `("gmina", wpis.miasto)`.** The demo
compares a `gmina` filter against the entry's **city**, so on the demo register `--gmina Poznań`
matches entries in the city of Poznań rather than in the commune. Nothing measured says the two
fields are interchangeable, and `reports.py:321-329` treats `gmina` as its own address key.

The pattern across all three is sharp: the double is faithful on every property somebody wrote into
`api_traits.yaml` and unfaithful on every property that lives only in `decisions.md` or nowhere.

**F-E6 — the demo double is lenient where the register returns 400.** `demo/rejestr.py:170,207`
apply `min(limit, LIMIT)` instead of refusing. Measured (`decisions.md:26`,
`fixtures/firmy_400_limit50.json`): `limit=50/100/500` → 400 `NIEPOPRAWNY_ROZMIAR_STRONY`, and
rejected requests still consume quota. On the double, `limit=500` returns 200. Narrow blast radius
(`client.py:354` always sends `profile.max_limit_firmy`), but a regression of that clamp passes the
demo green and burns quota on production.

**F-E7 — a cross-process claim asserted in-process.** `tests/test_anonymizer_traits.py:147-148`
asserts `Anonymizer().guid(X) == Anonymizer().guid(X)` under a docstring about fixtures not churning
the diff on regeneration. **Mutation:** `seed: int = 42` → `int(time.time())` leaves the file green
while two runs 1.2 s apart produce different identifiers. Determinism rests on the literal, not the
test.

### 3.2 Built after the previous audit

**F-P1 — the assistant's evaluation is structurally blind to the field its own repair stood on.**
*Shape 6.* `scripts/eval_asystenta.py:170-182` exports **8** fields. `AssistantAnswer`
(`assistant/schema.py:82-97`) declares **19**, of which **16 carry criteria** — fifteen filters plus
`szczegoly`; the other three (`ograniczenia`, `pytanie`, `propozycje`) are the clarification
channel. So **eight filters — `powiat`, `gmina`, `ulica`, `kod`, `imie`, `nazwisko`, `nip`,
`regon` — never reach even the raw JSON**, and cannot be read back by hand either. `STABILNE` omits `nazwa`. `ocen_ziarno`
begins each field with `if pole not in ocz: continue`, so it compares only what the gold answer
names — and S01–S15 name only `miasto`. **Evidence, run on the harness's own functions:** an answer
for S01 carrying `miasto=[Gniezno]` **plus** `wojewodztwo=[wielkopolskie]` **plus** `nazwa=[fryzjer]`
scores `ok=True, powody=[]`. **Consequence:** the `wojewodztwo` defect that cost 980 firms was fixed
on 2026-09-09 **in the prompt only**; its regression would be reported as 100 % invariance.
`docs/eval-asystenta.md` names this mechanism in words — the harness was never changed, and the
"0/120" re-run figure was read off the JSON by hand.

**F-P2 — ADR-0014's third marker has no observer on the `--out` path.** *Shape 1.*
**Mutation:** `cli.py:267`, `prefiks = "DEMO_" if deps.demo else ""` → `prefiks = ""` leaves the
whole suite green. **Real run under the mutation:** `pobierz -w podlaskie --demo --tak --out
moj_raport` wrote `moj_raport.xlsx`; unmutated it writes `DEMO_moj_raport.xlsx`. The behaviour is
correct today. `CLAUDE.md` states the five markers are mandatory *jointly*, and this is the marker
on the file an operator deliberately named — i.e. the one most likely to be forwarded.

**F-P3 — the §B output-confinement guarantee has no assertion at all.** *Shape 1.*
**Mutation:** `cli.py:268`, `return deps.settings.output_dir / safe_filename(...)` → `return out`
leaves the suite green; under it, `--out "../../ucieczka.xlsx"` from a nested cwd wrote two
directories above the working tree and `wyniki/` was never created. Unmutated, the same argument
lands in `…/dane/demo/wyniki/DEMO_ucieczka.xlsx`. `grep -rn "_sanitised" tests/` → 0;
`grep '"--out"' tests/` → 0; all eight `out=` uses call `flow.export_and_report` **beneath the
seam**. This is the one place the operator's own filename meets hostile registry text.

**F-P4 — zero hits on the report path is the dead end ADR-0017 was written to close.**
*Shape 3.* `ui/flow.py:397-399` returns `"raport", FetchPlan(...)` **before** the widening loop that
begins at `:443`, so the report path can never reach it. `pipeline.py:752-771` then finalises with
`set_run_count(run_id, 0)`, `set_stage "gotowe"`, `update_run_status "zakonczony"`, and
`export_and_report` sees a non-empty run list and writes the workbook. **Consequence:** an operator
whose criteria match nothing in the archive gets a finished, empty file and no offer to widen —
while the identical situation on the API path opens the menu ADR-0017 built.

**F-P5 — the report path is admitted by default on a coverage test that ignores the fields it
matches wrongly.** *Shape 3.* `reports.py:357-367` decides `report_covers` on voivodeship count and
statuses alone. Criteria carrying `ulica`, `powiat`, `gmina`, `imie` or `nazwisko` pass, and
`matches_criteria` then compares them exactly against server semantics that
`scripts/ceidg_probe_match_semantics.py` did not settle (both groups returned 204). `--zrodlo auto`
plus `UZYC_RAPORTU(default="tak")` makes this the default route, and `texts.report_offer:765-783`
lists what the report lacks in *content* — nothing about matching.

**F-P6 — `recordid.POLA_TOZSAMOSCI_RAPORTU:58` has no consumer.** Its only occurrence in the
repository is its definition, so changing the field list inside `id_z_tresci` would not diverge from
it loudly. One floor down, `PREFIKS_TRESCI` was deliberately given a reader for this reason.

### 3.3 `scripts/` — the layer that manufactures evidence

**F-S1 — the root probe bypasses two policies, and the gate that names it cannot see it.**
*Shape 4.* `ceidg_probe.py:72` uses `urllib.request.urlopen` instead of the proxy-free opener, and
`:55-57` sleeps on its own instead of taking the shared gate. Measured without opening a socket:
the shared opener carries `ProxyHandler.proxies = []`, plain `urlopen` carries the environment's
proxy. **Two consequences:** on a corporate network `python ceidg_probe.py --env prod` hands the
PESEL-bearing token to an interceptor; and it writes no `request_log`, so a concurrent `pobierz`
cannot see it — the exact scenario `scripts/probe_support.py:3-12` describes as closed. **And the
rule-11 scan lists this file by name** (`tests/test_boundaries.py:725`) while looking for
`httpx`/`httpx2`/`anthropic` client factories (`:648`), so it passes **vacuously**. A gate that
names a file and cannot see its defect is worse than one that never mentioned it.

**F-S2 — the production-consent guard exists in 1 of 8 probes.** Only
`scripts/ceidg_probe_nip_ids.py:207` takes `--produkcja`, with the guard at `:180`. The other seven
send on `--env prod` behind one gate, and their usage lines teach that form. `ceidg_probe_repeated_city.py:28`
says "requires the owner's consent in session" in prose while a sibling file shows it could be code.
The existence of one implementation makes the absence in seven an oversight, not a decision.

**F-S3 — the PKD transition generator drops codes silently, and the test that should see it is true
by construction.** *Shape 3 and 1.* `scripts/build_pkd_transition.py:144-146` routes a 2025 code
absent from `pkd2025.yaml` into `spoza`, taking its 2007 predecessors with it. The only trace is a
one-off `print` at `main():310-311`; `grep spoza docs/decisions.md docs/adr/0012*` returns nothing.
`tests/test_pkdmap_data.py:141-143` then asserts `brakujace == []` — true **because the generator
removed those rows**. **Consequence:** rebuilding `pkd2025.yaml` from an incomplete GUS export would
silently delete ADR-0012's expansions for those industries, leave the suite green, and hand the
operator the 2025-only subset that ADR-0012 exists to prevent.

**F-S4 — a cross-constant invariant with no observer, next to its twin that has one.**
`store.py:31` `REQUEST_LOG_KEEP_S = 7200` against the longest limiter window of 3 600 s
(`profiles/prod.yaml:33`). The relation is held by a comment; `grep REQUEST_LOG_KEEP_S tests/` → 0.
The probe calls `trim_request_log()` on every gate open (`probe_support.py:101`), so raising the
profile window above 7 200 would turn opening a probe into deleting history a running fetch's
limiter needs. The waiting doctrine's equivalent **is** an assertion:
`tests/test_ratelimit.py:538-539`, `assert WAIT_SLICE_S < DEFAULT_LOCK_STALE_S`.

**F-S5 — `scripts/assistant_smoke.py` cannot report failure.** `:64` sets `niepowodzenia = 0` and
nothing increments it; `:90` returns `1 if niepowodzenia else 0`; the refusal branch at `:72-75`
prints and continues. Exit code is always 0. Group A's results in `docs/test-runs-phase4.md` came
from a tool whose exit code measures nothing — they had to be read off the screen.

**F-S6 — one reference value enters from memory, 50 lines under a docstring forbidding it.**
`build_pkd_transition.py:67` hardcodes `PODSTAWA_PRAWNA = "Dz.U. 2024 poz. 1936 …"` while
`build_pkd.py:169-171` refuses the same thing explicitly ("we do not guess the legal basis… it is
entered by whoever has the source document, via `--podstawa`"). The value is currently correct — and
§5 shows the legal picture has since gained a second act.

**F-S7 / F-S8 — smaller.** All eight probes accept `--token`, so a PESEL-bearing credential can
reach `argv` and shell history; the default path (`CEIDG_TOKEN`/`.env`) is safe and the flag's cost
is nowhere named. `scripts/` is outside mypy — 27 errors, of which one matters by position:
`probe_support.py:97` types `environment: str` rather than a literal, and that value selects which
`request_log` is shared; today only each caller's `argparse choices=` protects it. Measured:
`mypy scripts --explicit-package-bases` reports **27 errors in 5 files** — and mypy cannot check
that directory at all without the flag — while the root `ceidg_probe.py` adds **21 more** on its own.

### 3.4 Architecture

**F-A1 — the module map describes about a third of the program.** *Debt.*
`docs/design/phase2_core.md:65-84` names 13 modules. Unplaced but load-bearing: `reports.py`
(a second record source with its own identity scheme), `demo/` (3 modules), `records.py`,
`recordid.py`, `httpclient.py`. `reports.py` landed nowhere and grew its own filter semantics —
which is F-P5, i.e. the map's cost is not cosmetic.

**F-A2 — no rule answers "which module is covered by no rule".** The scan already has emptiness
guards (`test_boundaries.py:203, :347, :506, :509` — the last reads
`assert all(CORE_FORBIDDEN.values()), "reguła z pustym zbiorem zakazów niczego nie sprawdza"`).
It has no completeness guard. Uncovered and weight-bearing: `reports.py`, `demo/*`, `records.py`,
`exporter.py`, `ratelimit.py`. This is the mechanism behind C2 and behind F-E5: `ratelimit` passes
every boundary test because no rule names it, and `reports.py` acquired divergent semantics because
nothing placed it.

**F-A3 — rule 5 is why `pipeline.py` is 1 615 lines.** *Decision whose cost came due.* "Only
`pipeline` imports both `client` and `store`" makes every new source land in one file: it now holds
five operations, the composition root, `LockHeartbeat` and `_LogEvents`. The invariant rule 5
protects — page records and checkpoint in one transaction — does not require one *file*.

**F-A4 — `console.py` is a view outside rule 8's reach.** The rule-8 scan walks `PACKAGE/"ui"` only
(`test_boundaries.py:189`); `console.py` importing `store` alone would break no rule, because rule 5
fires only on *both*.

**F-A5 — `aktualizuj` sits outside the `Criteria` contract, with a measurable consequence.**
`pipeline.run_update:917` takes `(since, until)` only. It calls `store.link_ids(...)` and then
`stale_detail_ids` on **every** identifier `/zmiana` reports (`pipeline.py:967-989`) — no
intersection with the population the store already holds. So "update my data" buys details for
everything that changed nationwide in the window. `update_cost_table` (`ui/texts.py:461-479`) prices
it honestly and calls the number "zmienionych wpisów", which is true of the register and not of the
operator's store. No ADR addresses the scope. This is a product decision, not a bug — but it is
undocumented, and the command's name implies the narrower reading.

**F-A6 — the PKD sunset surface is larger than ADR-0012 says, and one part of it is irreversible.**
Measured two ways, because the two answer different questions. The **field** `pkd_2007` appears in
**4 production modules and 9 test files**: `criteria.py` (`:82`, `:256`, validator `:283`,
`poszerzenia` at `:329` with the reset at `:376`, `wszystkie_pkd:420-422`,
`canonical_json:424-431`, `:460-463`), `cli.py:448,524-531`, `ui/flow.py` in **two** functions
(`_z_rocznikiem:222-231`, `_kandydaci:234-273`) and `ui/texts.py:876`. The **layer as a whole** —
what removal would actually touch — is **7 production modules and 13 test files** plus the data
file, adding `pkdmap.py`, `demo/korpus.py`, and `pipeline.py`, which owns the map's seam
(`:53` import, `:103` the `Deps` field, `:318` the load) though it never names `pkd_2007` itself.
Against ADR-0012:374's "one data file, one module, one flow step, one field". The `canonical_json`
carve-out
is the sharp part: **deleting it after the transition invalidates every stored run fingerprint**,
which is resumability. Removing this layer is not a deletion; it is a migration.

**F-A7 — a self-imposed ceiling nobody has connected to the split threshold.**
`profiles/prod.yaml:30` `max_pages: 10000` × `limit=25` = **250 000 records per query**, enforced
loudly (`client.py:397`, `PagingRunawayError`) — this is *not* a silent subset, and that is worth
recording as a positive. But the batch-split threshold is 50 000 and, under `--tak`, splitting
requires an explicit `--partie`. A 300 000-record query run with `--tak` and no `--partie` therefore
fails after 10 000 requests, roughly 10.4 hours at 3.75 s spacing. The API declares no such
ceiling — the probe recorded `links.last = page 1263224` at `count = 6 316 121`.

### 3.5 Found by the orchestrator

**F-O1 — `.gitignore`'s exception licenses exactly the defect the rule exists to prevent.**
*Shape 4, with personal-data exposure.* `.gitignore:23` carries `!tests/fixtures/**`, which
un-ignores the **whole** directory, including the extensions listed above it precisely because they
carry personal data. Measured with `git check-ignore`:

```
tests/fixtures/raport.csv      NOT ignored — would be committed
tests/fixtures/dane.sqlite     NOT ignored — would be committed
tests/fixtures/zrzut.json      NOT ignored — would be committed
tests/fixtures/probka.ndjson   NOT ignored — would be committed
probe_out/x.json               ignored
demo/recordings/a.json         ignored
```

`*.sqlite` is globally ignored because a store carries PESELs. `tests/fixtures/` is the directory
the anonymiser **writes to**, and the natural place somebody drops "a quick sample". The remote is
another team's repository. The guard (Tier D3) exists; its exception undoes it.

**F-O2 — the project has no sentence about GDPR.** `grep -rEi "\brodo\b|\bgdpr\b|art\. ?14|
obowiązek informacyjny|administrator danych"` across every `.md`, `.py` and `.yaml` returns
**zero**. (My first attempt returned 227 — all of them the substring inside "ś**rodo**wisko", which
is defect shape 6 committed by the auditor.) §5 supplies why this matters.

**F-O3 — the project stands on API documentation from its first day.** `docs/api_notes.md:6` and
`INSTRUKCJA_CLAUDE_CODE.md:26` cite integrator documentation **v1.0, dated 2024-10-21**; the copy in
`docs/reference/` is that file. The operator publishes **v3.7, dated 2026-04-22** at a date-versioned
URL. The probe measurements are current (2026-09) and remain the authority on behaviour — but
anything the operator *documented* between v1.0 and v3.7 is invisible here, including any capability
that would change §5's direction 4. The pass-2 researcher could not read the `.7z`, so what changed
is unknown; establishing it costs zero CEIDG requests.

**F-O4 — "each record carries one vintage" has 141 counterexamples, and is unfalsifiable in the
other direction.** *Shape 6.* The report CSV has one `RokPKD` column and two code columns, so a
mixed-vintage record cannot be expressed in it. In the direction that *is* decidable — a row labelled
`RokPKD=2025` carrying a code GUS lists as a 2007 predecessor and absent from `pkd2025.yaml` — there
are **141 of 117 887** (0.12 %). The reverse direction cannot be settled from these files at all,
because `pkd2007_2025.yaml` records **zero** identity relations (0 of 357 entries map a code to
itself), so "in the 2025 dictionary and not a listed predecessor" does not mean "2025-only".

**F-O5 — the corrected headline figures reproduce exactly.** Measured over
`probe_out/raport_sample.zip` with the project's own parser: main code outside the dictionary
**71 818 = 25.20 %** (`CLAUDE.md`: 71 817 / 25.2 %, a one-row difference); no code the entry carries
in the dictionary **24 494 = 8.59 %** (`CLAUDE.md`: 24 494 / 8.6 %, exact); rows with no main code
**2 230**, all `RokPKD=2007` (exact). The 2026-09-08 correction is independently confirmed.

**F-O6 — `CLAUDE.md` carries two stale gate figures, and the second one is the load-bearing one.**
`CLAUDE.md:136` says "1247 pass + 1 skip"; the suite reports **1258 passed, 1 skipped** (1259
collected). Ten lines below, `:146` says a suite reporting "**1248 passed**" would mean the report
path got a double rather than that the skip was tidied away — and that sentence is the one the
document offers as a *signal*, so today it names a number ten below the real count. The paragraph
warning that a figure must be corrected in the same commit that moves the code is two screens away.

**F-O7 — the report archive's server-side lifetime is nowhere recorded.** "Retencja" in this project
(`config.py:1`, `cli.py:134`) means *our* retention. `docs/decisions.md` — the register of measured
API facts — records neither the publication cycle (~05:00) nor that report files are removed after
about seven days. Zero-cost to close.

---

## 4. Where the agents disagreed, and what only the synthesis can say

**On the demo double, the two testers and a code-reviewer are complementary and together say
something neither said.** The doubles tester found the double faithful on identifier spelling across
all four endpoints, on non-hex report identifiers, on 204 semantics, on `count`, and on `pkd`
matching all of an entry's codes. The post-audit reviewer found it *wrong* on `miasto` and `ulica`.
Both are right, and the pattern is the finding: **the double is faithful to every property somebody
wrote into `api_traits.yaml`, and unfaithful to every property that lives only in
`docs/decisions.md`.** The instrument works; its coverage is the defect. That is the same sentence
the previous audit wrote about doubles and report paths, now with a second instance.

**On the report path's unmeasured fields, the architect and the code-reviewer prescribe opposite
remedies, and this is the owner's call.** Both found it. The architect (R1) would make
`report_covers` **refuse** the report path when an unmeasured field is set, until it is measured —
a one-line predicate with a behavioural effect. The reviewer would **disclose** it: add these fields
as a fifth reason in `_powod_braku_raportu`, or one sentence in `report_offer`. Refusing protects
the operator from a silent subset at the cost of sending them down the three-hour path for a query
the report might have answered correctly; disclosing keeps the cheap path and moves the judgement to
someone who cannot verify it. ADR-0018 below states both.

**On `test_pkdmap_data.py:122`, this audit partially contradicts the previous one.** The 2026-09-08
audit listed it as an assertion that cannot fail (`n == n`). The tautology is still on that line —
but the test *as a whole* now discriminates, because the following line anchors `(51, 306)` in
constants. What has got worse instead is the prose around it: the module docstring says "55 / 209",
a comment says "264 codes", and the constants say 51 / 306 / 357.

**Between research pass 1 and pass 2, both targeted questions closed, in opposite ways.** On EV code
signing, pass 1 flagged its own claim as resting on vendor sources; pass 2 found the primary source
and **confirmed** it verbatim. On the register's self-service path, pass 1 could not establish
whether the report application exports XLSX; pass 2 found it exports six formats **and** that no
PKD criterion exists in any of the 41 reports — which turns a gap into the strongest available
argument for this tool's existence. This is what the targeted second pass was for, and it earned
its cost on both questions.

**Between the two research passes on the PKD sunset, the finding converged from independent
readings of the same primary act** — two agents, neither seeing the other, both reached
Dz.U. 2025 poz. 1792 and both read art. 11 and art. 12 the same way. Pass 2 added what pass 1 did
not have: that the transition period is now **delegated to a regulation** and is therefore movable
by a sub-statutory act.

---

## 5. External reconnaissance

Four perspectives, one pass each, plus a targeted second pass on two of them. Sources are listed
per finding. Where the evidence is absent, that absence is the finding.

### 5.1 The PKD 2007 sunset — the date in our documents is wrong, and the mechanism is not a date

**The operative date on the data side is 31 January 2027, not 31 December 2026.** Ustawa z 21.11.2025
o zmianie ustawy o statystyce publicznej (Dz.U. 2025 poz. 1792, promulgated 16.12.2025, art. 12
in force from 31.12.2025), read in the original by both research passes: entities that have not
changed their entry by 31.12.2026 have their activity codes replaced automatically **"w terminie do
dnia 31 stycznia 2027 r., jeżeli jest to możliwe"** (art. 12 ust. 1) — a window, not a moment. Where
automatic replacement is impossible, **CEIDG deletes the entry ex officio after 31.01.2027**
(art. 12 ust. 2); the KRS equivalent (art. 20e ust. 1 pkt 3) removes only the activity subject and
gives five years to supply a new one. Our `pkd2007_2025.yaml` header says `wygasa: 2026-12-31` —
a date after which the old vintage is still in the data for at least a month.
Sources: [Dz.U. 2025 poz. 1792](https://api.sejm.gov.pl/eli/acts/DU/2025/1792/text.pdf),
[Dz.U. 2024 poz. 1936](https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20240001936).

**Part of the migration already happened, on 1 January 2026.** Art. 11: entries carrying `93.29.Z`
that were not changed by 31.12.2025 had it replaced with `93.29.B`, and art. 11 ust. 2-3 changes
**the entry's other PKD 2007 codes at the same time**. So the vintage distribution moved for a
selected sub-population eight months ago. Our archive was downloaded after that date, so our 58.6 %
is post-event — but nothing in the project records that the event occurred or was checked.

**The date is now movable by a regulation.** Art. 40 ust. 1 pkt 2 and ust. 7 delegate the
"okres równoczesnego stosowania" to a Council of Ministers regulation; art. 18 keeps 1936 in force
until a new one is issued. Neither researcher found such a regulation or a draft as of 2026-09-09.
A hard constant in a file header is therefore an assumption carrying risk, not a durable fact.

**The operator's own pages contradict the statute.** biznes.gov.pl and the GUS FAQ (last updated
26.02.2025) give 1.01.2027; the act says *by* 31.01.2027. The act has precedence and the pages
predate it. Do not average them.

**Nobody has said what happens to `rokPkd` or the `pkd=` filter afterwards.** Pass 2 searched the
integrator pages, the Akademia documentation index and the changelogs: there is no release note, no
changelog and no communication tying the API to the PKD transition. That is an established absence,
not an unsearched one.

**The engineering half: a header comment nothing reads is a known-ineffective pattern, and the
better trigger is not a date.** ArchUnit-style expiring-TODO linters (eslint-plugin-unicorn,
SwiftLint `expiring_todo`) break the build on a date, and their own maintainers treat that as a
liability — unicorn ships `checkDatesOnPullRequests: false` by default so the build does not break
for whoever happens to open a PR that day. The academic work on "on-hold" self-admitted technical
debt (Maipradit et al., EMSE 2020) found that 58 % of commits removing such a comment did not fix
the problem, and motivates itself with the observation that developers do not track the external
event. The strongest available variant is the one unicorn also supports: an expiry **condition**
rather than a calendar date. ADR-0012's "Revisit when" section already half-states it ("or earlier,
if the register's share of 2007 codes drops far enough"); what is missing is an observer that checks
and **shouts**. Ericsson/O2 (6.12.2018, ~32 million subscribers, an expired certificate whose date
was known to the second) is the strongest evidence that "the deadline is written down" and "somebody
will act" are different propositions.
Sources: [expiring-todo-comments](https://github.com/sindresorhus/eslint-plugin-unicorn/blob/main/docs/rules/expiring-todo-comments.md),
[Maipradit et al.](https://arxiv.org/html/1901.09511v3),
[The Register on Ericsson/O2](https://www.theregister.com/2018/12/06/ericsson_o2_telefonica_uk_outage/).

**Not settled:** whether art. 12 ust. 2's "wykreśla się wpis" means the whole entrepreneur entry or
only the activity subject. The text says "wpis" while the parallel KRS provision deliberately says
"przedmiot działalności", which argues for the broader reading. The consequence is severe enough to
warrant asking the operator rather than inferring.

### 5.2 Delivery — and the register already ships a GUI, which reframes the question

**The warehouse's report application exports to Excel, and has no PKD criterion.** The operator's own
user manual shows export to **XML, PDF, Excel, Word, CSV, JSON**; the parameter panel for the daily
report offers *Stan na dzień, Data od, Data do, Województwo, Powiat, Gmina, Miejscowość* and nothing
else. The catalogue of 41 ready reports lists parameters of voivodeship, place, activity status and
period. **PKD appears as a criterion in none of them.** This is the first evidence-based answer this
project has to "why does this tool exist": its advantage is **selectivity by industry and
multi-voivodeship cross-sections**, which is exactly where the most machinery went (`Criteria`,
`pkdmap`, the vintage step) — not the volume, where the register's own bulk files win outright.
Sources: [Podręcznik użytkownika Hurtowni danych v1.3](https://pliki.biznes.gov.pl/akademia/Hurtownia_danych/Instrukcja_uzytkownika_Hurtowni_danych.pdf),
[Rodzaje raportów](https://akademia.biznes.gov.pl/hurtownia-danych-raporty/).

**Access is not open.** A Biznes.gov.pl account, then a **separate application for permissions**, a
personal-data declaration, and login (Profil Zaufany among them). The procurement annex also
documents a **"Log RODO"** module recording who viewed which personal-data report and when — the
reports contain personal data and every view is logged.
Source: [Annex 3 to the OPZ, gov.pl](https://www.gov.pl/attachment/46395518-322a-47ba-9669-1cb95e90e08c).

**The delivery paths, and where they break.** PyPI + `uvx`/`pipx` is the cheapest channel and does
not solve this problem — it removes `venv` and `pip install -e`, not the terminal. Single-file
binaries remain contested: PyInstaller's antivirus false-positive issue (#6754) is open and
maintainers attribute it to the shared bootloader; `--onedir` triggers fewer heuristics. On code
signing the primary source is now unambiguous: Microsoft Learn states verbatim that **"EV
certificates no longer bypass SmartScreen"**, places OV and EV in the same table row, gives "several
weeks and hundreds of clean installs" as the reputation threshold, notes reputation does not carry
across versions, and records that no consumer-facing manual review exists. Microsoft Store is the
only path with zero warnings. Windows 11's Smart App Control can block unsigned files regardless of
origin. Non-admin installation is feasible (MSIX is per-user by design; the Python Install Manager
installs per-user), and the embeddable package is documented as **not** for end users.
Sources: [SmartScreen reputation for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation),
[PyInstaller #6754](https://github.com/pyinstaller/pyinstaller/issues/6754),
[Using Python on Windows](https://docs.python.org/3/using/windows.html).

**The Polish precedent says the desktop channel breaks on updates, not on installation.** The
Ministry of Finance withdrew the desktop *e-mikrofirma* and *Klient JPK 2.0* over a Log4j
vulnerability in a bundled library and told users to delete them and move to the web versions.
Source: [podatki.gov.pl](https://www.podatki.gov.pl/komunikaty-techniczne/wylaczenie-mozliwosci-pobrania-desktopowych-aplikacji-e-mikrofirma-i-klient-jpk-2-0/).

**GDPR is a cost shared by every delivery path, and highest for the hosted ones.** UODO fined
Bisnode 943 000 PLN for failing art. 14's information obligation toward more than six million people
whose data came in part from CEIDG; the NSA dismissed the cassation appeal on 19.09.2023. Public
availability of the register is not consent to arbitrary processing. Hosting the tool moves the
controller/processor role onto whoever hosts it; "somebody else runs it for you" creates a
processing entrustment. This project says nothing about any of it (F-O2).
Sources: [UODO](https://uodo.gov.pl/pl/138/2827),
[Niebezpiecznik on the NSA judgment](https://niebezpiecznik.pl/post/pierwsza-polska-i-milionowa-kara-za-rodo-byla-zasadna-wyrok-nsa/).

**No evidence found** for where non-technical users actually drop off when installing desktop
software; the only numbers available are SaaS-onboarding benchmarks from an interested source, which
measure a different funnel. Treat any "X % fail at installation" claim as unsupported.

### 5.3 Enforcing boundaries — the instrument exists here, applied narrowly

**The class of defect "a rule that covers nothing" is recognised, and solved only in Java.** ArchUnit
rejects a rule executed against an empty class set by default (`failOnEmptyShould`), documenting the
motivating case as a package rename that left the rule behind. No Python tool has a general
equivalent. The analogous ArchUnit request for "report every rule that matched nothing" has been open
since 2022 with no maintainer response. **This project already built the narrow version** —
`test_boundaries.py:203, :347, :506, :509`. What it lacks is the completeness question (F-A2), and
the only ecosystem answer to that is import-linter's `exhaustive`, which works **solely** for
`layers` contracts with `containers`.

**Rules named after one import form, or one library name, are a documented failure mode in two
independent tools.** import-linter #111: `forbidden_modules = astropy.units` reports KEPT against
`from astropy import units as u`. Ruff #16692 (open): TID251 catches a direct import but not a
re-export chain. **The counterintuitive and most transferable part:** import-linter's maintainer did
not add support for the unenforceable form — since 1.9.0 the tool **refuses to start**, raising
`Invalid forbidden module …: subpackages of external packages are not valid` and requiring
`include_external_packages=True` when a rule names an external module. A rule it cannot enforce is a
configuration error, not a silent pass. That is the pattern F-S1 needs.

**Call-shape rules are essentially only expressible in Semgrep, at a price.** Deply goes furthest in
YAML beyond imports (decorators, inheritance, file patterns) but does not inspect call arguments.
Semgrep expresses "every SDK call passes `api_key=`" directly — but Semgrep CE analyses each file in
isolation, cross-file analysis is a paid tier, and the December 2024 rules relicensing produced the
Opengrep fork. **Rules 9, 10 and 12 — the ones with recorded catches — are therefore not portable to
a declarative tool**, which makes keeping the custom AST scan a reasonable choice rather than
technical debt. Ruff has no plugin system (meta-issue #283 still open; a September 2025 discussion
asked whether anything had changed and nothing had).

**No evidence found** of any published Python architecture-testing rollback, of the maintenance cost
of running two mechanisms side by side, or of any project reporting how many violations a tool caught
versus let through. Every available report is a demonstration.
Sources: [ArchUnit advanced configuration](https://github.com/TNG/ArchUnit/blob/main/docs/userguide/010_Advanced_Configuration.adoc),
[ArchUnit #927](https://github.com/TNG/ArchUnit/issues/927),
[import-linter #111](https://github.com/seddonym/import-linter/issues/111),
[import-linter contract types](https://import-linter.readthedocs.io/en/stable/contract_types/),
[ruff #16692](https://github.com/astral-sh/ruff/issues/16692),
[Semgrep OSS licensing update](https://semgrep.dev/blog/2024/important-updates-to-semgrep-oss/).

### 5.4 Bulk versus API — the canonical shape is three parts, and we have all three

**The boundary in this class of system is set by pagination depth, not by rate limits.** Norway's
Brønnøysundregistrene caps `(page+1)*size ≤ 10 000` and says outright that a caller wanting all
units should use the download service; Companies House advanced search hard-stops at 10 000. Our API
declares no such cap (the probe recorded `links.last = page 1263224` at `count = 6 316 121`), so this
does not bite us — but **our own** `max_pages` does, at 250 000 records (F-A7), and loudly.

**The canonical pattern is snapshot → change stream → point API, not a binary choice.** Companies
House ships the snapshot **carrying a `timepoint`** so the stream attaches exactly where the snapshot
ended; Brønnøysund publishes a nightly full file plus an updates endpoint; GLEIF publishes three
golden copies a day plus 8-hour, 7-day and 31-day deltas. **We have all three endpoints** —
`/raporty`, `/zmiana`, `/firma` — and treat the report as an *alternative* rather than the
foundation. What is missing is not the change path but the **consistency marker**: CEIDG publishes no
`timepoint`, so the snapshot↔changes seam has to be closed with an overlapping time window.

**The counterintuitive one: bulk is also the reconciliation channel, not only the bootstrap.**
Companies House users report roughly two million lost PSC timepoints over a single day boundary, and
AA/CS01 events visible in REST and on the website but absent from the stream. Documentation says the
stream is fresher; measured experience says the stream loses events in the tail. The "poorer and a
day older" channel is simultaneously the higher-completeness one.

**Nobody enriches the whole population through the API.** Companies House's bulk company file is a
narrow monthly CSV; PSC is separate and daily; officers only on request. Enrichment goes to the
subset a user's query names, or to the delta — which matches our asymmetry exactly (287 256 rows in
one request versus 25 per request).

**Legally, bulk is sometimes freer than the API, not more restricted.** The EU High-Value Datasets
implementing regulation (2023/138) makes company registers a mandatory category, free and available
**both via API and as bulk download**. Estonia inverts the usual asymmetry — bulk download is free
and formality-free while the API requires an agreement with RIK; Denmark's system-til-system bulk is
free but requires a written application and acceptance of advertising-protection terms. Germany's
Handelsregister is the counterexample: free access since 2022, no official REST API, 60 requests/hour
in the terms, and no bulk at all. On the Polish side, UODO has publicly questioned the model of
universal publicity for entrepreneurs' personal data, noting that the address is often the home
address — an argument for the bulk path defaulting to a **narrower** column set, not a wider one.
Sources: [Companies House Streaming API](https://developer-specs.company-information.service.gov.uk/streaming-api/guides/overview),
[Brønnøysundregistrene](https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html),
[GLEIF Golden Copy](https://www.gleif.org/en/lei-data/gleif-golden-copy),
[Regulation (EU) 2023/138](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A32023R0138),
[UODO on CEIDG publicity](https://uodo.gov.pl/pl/138/3841).

**No evidence found** of any team publishing a documented API↔bulk migration together with what it
cost them. Descriptions of target states exist; descriptions of transitions do not. If this project
measures its own, that measurement is probably stronger than anything public.

### 5.5 Where we are different, and whether it is a decision or debt

| Difference | Verdict |
|---|---|
| Custom AST scan instead of a declarative boundary tool | **Decision.** Rules 9, 10 and 12 — the ones with recorded catches — are call-shape rules that no declarative Python tool expresses. |
| No completeness guard over the rule set | **Debt.** The instrument is already here for emptiness; the ecosystem has no answer for this rule shape, so it must be built (ADR-0019). |
| Report path as an alternative rather than the foundation | **Open product decision** (ADR-0021), and the only one the previous audit left open. |
| Expiry written as a date in a file header | **Debt.** The literature and this project's own doctrine agree that a guarantee with no observer is not a guarantee (ADR-0020). |
| Installation via `pip install -e .[dev,asystent]` from a branch of another team's private repository | **Debt**, and the register's own GUI now defines the bar it must clear. |
| Nothing recorded about GDPR obligations | **Debt with legal weight**, given the Bisnode judgment. |

---

## 6. Ranked remediation list

Ordered as instructed: loss or misrepresentation of data first, then what blocks deployment, then
the rest. Nothing here was applied — `CLAUDE.md` requires a review after any behaviour change, and
the reviewers ran in wave 1, before any fix could exist.

### Tier 1 — data loss, misrepresentation, or personal-data exposure

| # | Finding | Where | Fix, as far as it is unambiguous |
|---|---|---|---|
| 1 | ~~`.gitignore` exception un-ignores personal-data extensions in the directory the anonymiser writes to~~ **APPLIED 2026-09-09** | `.gitignore:23` | **Small and unambiguous.** Replace the blanket `!tests/fixtures/**` with explicit un-ignores for the extensions that belong there — `!tests/fixtures/*.json` is what the current fixtures need — leaving `*.csv`, `*.sqlite`, `*.ndjson` ignored everywhere. Verify with `git check-ignore` on the four paths in F-O1. |
| 2 | Root probe bypasses the proxy policy and the shared request log; rule 11 names it and passes vacuously | `ceidg_probe.py:55-57,72`; `tests/test_boundaries.py:648,725` | Two separable halves: make the file use `probe_support`, and make the scan refuse a file it lists but cannot analyse (import-linter's pattern, §5.3) |
| 3 | Report path with zero matches finishes as a completed run and exports an empty workbook, with no widening offer | `ui/flow.py:397-399`; `pipeline.py:752-771` | ADR-0017's mechanism exists; the report branch returns before reaching it |
| 4 | Report path admitted by default while matching five unmeasured fields exactly | `reports.py:293-300,357-367`; `ui/flow.py:384` | **Two opposed remedies — owner's call.** ADR-0018 |
| 5 | PKD transition generator drops codes silently; the test that should observe it is true by construction | `scripts/build_pkd_transition.py:144-146`; `tests/test_pkdmap_data.py:141-143` | Assert against the source key's code set, not against the generator's output |
| 6 | ~~`api_traits.yaml` excludes the only paging-termination property, on a false premise~~ **APPLIED 2026-09-09** | `tests/fixtures/api_traits.yaml:16,71-74` | **Small and unambiguous.** Widen `zrodlo_surowe` to all three sample directories and add the property with `probki: mini/06, nip_ids/1` |
| 7 | Assistant evaluation cannot see 8 of 15 fields and compares only gold-named ones | `scripts/eval_asystenta.py:170-205` | Export all `Criteria` fields; compare the full set; add `wojewodztwo: []` to gold S01–S15 |

### Applied on 2026-09-09, after the owner authorised the two unambiguous items

**Tier 1 item 1 — `.gitignore`.** The blanket `!tests/fixtures/**` became
`!tests/fixtures/*.json` and `!tests/fixtures/*.yaml`, with the reason recorded above them.
Verified in both directions with `git check-ignore`: all twelve tracked fixtures stay visible,
and `.csv`, `.sqlite`, `.ndjson`, `.db`, `.xlsx`, `.jsonl` dropped into that directory are now
ignored again. No program behaviour is involved.

**Tier 1 item 6 — `api_traits.yaml`.** Three changes. `zrodlo_surowe` now names all three sample
directories, with a note that the single-directory pointer is what erased the measurement. The
false premise in the `odpowiedzi` header ("the probe never reached the last page") is replaced by
the correction and by the general lesson — *a justification for an omission is a claim like any
other and is subject to checking*. And the property itself is recorded as
`koniec_stronicowania`, carrying `probki_probe_out`, `next_rowne_self: true`, and an explicit
`fixture: null` with the reason: `probe_out/samples/` holds 18 paginated responses and **none** is
a terminal page, which is precisely why the anonymiser could not produce a fixture for it.

**The entry did not ship without an observer.** Adding a claim that nothing reads is the defect
shape this audit is about, so two tests came with it:
`test_koniec_stronicowania_zmierzony_na_probkach_spoza_samples` (reads the entry, confronts the two
named raw samples, skips honestly without `probe_out/` exactly like its sibling) and
`test_atrapa_demo_konczy_stronicowanie_tak_jak_rejestr` (the CI-visible half — the demo register
answers a one-hit query and its only page must be terminal). **Mutation check, reverted:** flipping
`next_rowne_self` to `false` fails **both**. Gates after the change: **1260 passed, 1 skipped** —
two more than before, the skip still in place.

### Tier 2 — blocks deployment

| # | Finding | Where |
|---|---|---|
| 8 | No delivery path for a non-technical operator; the documented install needs a clone of another team's private branch | `README.md:19,37-45`; §5.2 |
| 9 | Production-consent guard exists in 1 of 8 probes | `scripts/ceidg_probe_nip_ids.py:207` versus seven siblings |
| 10 | PKD sunset: the date is wrong (31.01.2027), now movable by regulation, the surface is 7 modules + the data file + 13 test files (4 modules for the field alone), and the `canonical_json` carve-out cannot be deleted without invalidating resumability | `pkd2007_2025.yaml:15`; ADR-0012:374; `criteria.py:426-431` — ADR-0020 |
| 11 | ADR-0014 marker 3 and the §B output confinement both unobserved on `--out` | `cli.py:267,268`; no test names `--out` |
| 12 | Demo corpus is 4.7× harsher than the register and cites a retracted figure | `demo/korpus.py:17-18,307` |
| 13 | Demo double disagrees with measured `miasto` semantics and with `reports.py` on `ulica` | `demo/rejestr.py:77-102` |
| 14 | Project stands on API documentation v1.0 (2024-10-21); v3.7 (2026-04-22) exists | `docs/api_notes.md:6` |

### Tier 3 — the rest

C1, C3, C4, C5–C8, D2 (eleven sites), D8, F12 (five fields), B4 (now 1 263 lines); F-E2, F-E3, F-E6,
F-E7; F-P6; F-S4, F-S5, F-S6, F-S7, F-S8; F-A1, F-A2 (ADR-0019), F-A3, F-A4, F-A5, F-A7; F-O2, F-O6,
F-O7; the `test_pkdmap_data.py` prose drift (55/209 versus 51/306/357); no GDPR sentence anywhere.

---

## 7. What cannot be settled without requests

Each with its cost, and the decision left with the owner.

| Question | Cost | Note |
|---|---|---|
| F12: which of `powiat`, `gmina`, `ulica`, `imie`, `nazwisko` match by fragment | **5 production requests** (one per field; the 2026-09-09 probe's two group requests proved only that at least one field per group is not a fragment match) | Settles Tier-1 item 4 with a measurement instead of a choice |
| Does the API cap deep pagination | **2 production requests** (`page` far beyond any real result at `limit=1`; a 200 or a 400 settles it) | Would confirm F-A7's assumption that only our own ceiling exists |
| Exact `ids` batch size between 5 and 9 | 4 requests | Already judged not worth it unless detail fetching becomes the bottleneck |
| `link_ceidg` enrichment batch size | 1 request | Precondition recorded in `status.md` |
| Current vintage shares after the 1.01.2026 automatic reclassification | 2 requests (list a fresh daily report, download it) | §5.1; also re-tests F-O4 on post-event data |

**Zero-request and available now:** reading integrator documentation v3.7 (F-O3); every measurement
in §3 that used `probe_out/`; the `git check-ignore` verification of Tier-1 item 1.

---

## 8. Gates

Run three times: at the start of this session, at the end of the audit with no code changed, and
again after the two authorised fixes.

| Gate | At the audit's close | After the two fixes |
|---|---|---|
| `pytest -q` | **1258 passed, 1 skipped** (1259 collected) | **1260 passed, 1 skipped** — two new observers for the `api_traits.yaml` entry |
| `mypy ceidg_tool tests` | Success, 112 source files | Success, 112 source files |
| `ruff check ceidg_tool tests scripts` | All checks passed | All checks passed |
| `ruff format --check ceidg_tool tests scripts` | 124 files already formatted | 124 files already formatted |

The skip is `test_api_traits.py::…[raporty]`, the demo's open report edge, and it is present in
every one of the three runs.

During the audit `git diff --stat` was empty. Afterwards it carries exactly the two authorised
fixes — `.gitignore`, `tests/fixtures/api_traits.yaml`, `tests/test_api_traits.py` — plus this
session's brief. **No production module under `ceidg_tool/` was touched at any point.** No agent
sent a request to CEIDG, and this is asserted structurally, not read from `request_log` (§1).
