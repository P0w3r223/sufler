# Phase 2 core design: `ceidg_tool` package

Date: 2026-09-05
Status: accepted; sections marked below are superseded in part by ADR-0007
Author: P0w3r223
Related to: INSTRUKCJA_CLAUDE_CODE.md, docs/api_notes.md, docs/adr/0001..0007

---

## Superseded by ADR-0007 (uzupelnienie-01.md)

- "Refuse above the Excel row limit" → the exporter splits into parts instead.
- "Token only from `.env`/env" → keyring → `CEIDG_TOKEN` → `.env`, JWT expiry read locally.
- `--bez-cache` flag → not implemented; retention is `wyczysc` plus TTL/retention settings.
- Data directory → `platformdirs` user data dir, output only in `wyniki/`.
- The module contracts below are the design sketch; the implemented signatures differ
  in details (see "Implemented contracts").

## Implemented contracts (as of 2026-09-05)

```python
# pipeline.py
def estimate(count: int, profile: ApiProfile, *, max_rekordow: int | None = None) -> Estimate
#   Estimate(count, page_limit, ids_batch, spacing_s, requests_list, requests_details)
#   spacing_s = max(min_spacing_s, tightest window span/limit)  -> 3.75 s by default
def run_fetch(criteria, deps, *, resume_run_id=None, known_count=None, force_lock=False) -> RunResult
def run_report_fetch(criteria, deps, report, *, force_lock=False) -> RunResult
def run_update(deps, *, until=None, since=None) -> RunResult      # watermark per 5-day window
def run_export(run_id, dest, deps, *, cel_pobrania=None, formats=("xlsx",)) -> ExportSummary

# client.py
Page(records, count, next_cursor: Cursor | None, index, self_url)
def fetch_details(ids) -> tuple[list[dict], list[str]]             # (records, missing ids)
def download_report(report: Report, dest: Path) -> Path
def iter_changes(od, do, start=None) -> Iterator[Page]

# store.py (schema v3: run.kind in firmy | raport | zmiana; identifiers canonicalised, ADR-0013)
def find_resumable_run(criteria_hash, *, profile_hash, kind="firmy") -> RunInfo | None
def link_ids(run_id, *, page_index, ids) -> int                     # /zmiana without list JSON
def stale_detail_ids(ids, *, cutoff: datetime) -> list[KanonicznyId]
def outdated_details(ids, *, older_than: datetime) -> list[KanonicznyId]
```

`stale_detail_ids` takes the freshness threshold from its caller rather than computing one from
a cache TTL. `/zmiana` *is* the staleness signal, so on the `aktualizuj` path the threshold is the
end of the change window; a TTL there silently kept pre-change data (audit 2026-09-08, A1).
`outdated_details` is that rule's observer — the entries that kept a detail older than the window
that reported them. `count_run_unresolved` cannot see those: they are in state `pobrany`, resolved
and untrue.

## Deviations from the module table in the instruction

Three files are added next to the six mandated modules. Responsibilities from the
table are unchanged.

| File | Why separate |
|---|---|
| `ratelimit.py` | "limiter with a substituted clock" is testable in isolation only when the limiter depends on two protocols (`Clock`, `RequestHistory`). Keeping it inside `client.py` would put HTTP, limiter, retry, paging and five endpoints into one 400+ line file. |
| `apiprofile.py` + `profiles/*.yaml` | Phase-1 findings are data, not code; test and prod may differ (ADR-0001). |
| `pipeline.py` | Without it the download loop lives in the CLI, and the wizard, YAML and the phase-4 assistant get three copies of the logic. |

Helper files without architectural weight: `errors.py` (exception taxonomy),
`clock.py` (protocol + `SystemClock`), `progress.py` (event protocol).

## Module map and dependency direction

```
inputs: cli.py (typer flags), ui/wizard.py, phase 4 assistant.py
        |  all go through ui/flow.py -> one decision sequence (ADR-0008)
        v
   criteria.py   PURE: pydantic, no I/O; the only input contract
        |
        v
   pipeline.py   orchestration; the only module that knows both network and database
     |        |          |
     v        v          v
  client.py  store.py  exporter.py
     |  |       |          |
     v  v       |          v
ratelimit  apiprofile <---+     normalizer.py  PURE: FieldSpec
   clock       ^
               |
           config.py   token, environment, paths, production consent

outputs: one owning module per channel (ADR-0009, ADR-0024)
   richtext.py  -> rich, on **stderr**: every screen, warning, error and progress line
   jsonout.py   -> sys.stdout: the result envelope, and nothing else (rule 15)
        ^
        |
   ui/wynik.py  PURE: Wynik, Status, kod_wyjscia — the envelope as a value

classification data, PURE, no owner above them (ADR-0026):
   pkddict.py   the PKD 2025 dictionary (was assistant/pkd.py until 2026-09-23)
   pkdmap.py    the 2007 -> 2025 transition table
   pkdszukaj.py the search `szukaj-pkd` runs, over both
```

**The YAML query file is gone (ADR-0022, 2026-09-10).** Flags, the wizard and the assistant are
the three inputs; `texts.polecenie_powtarzajace` replaced the file by printing a ready-to-paste
command after the decisions are made.

**Four modules joined the map on 2026-09-23**, three of them because the tool acquired a second
caller (ADR-0024: `ui/wynik.py`, `jsonout.py`) and one because the PKD dictionary acquired a
second consumer (ADR-0026: `pkddict.py`, with `pkdszukaj.py` built on it). `console.py` gained
`LineEvents` beside `ConsoleEvents` in the same breath: `rich` renders intermediate `Live` frames
only on a terminal, so everything that is not one used to get a single frame at the end of a
half-hour run.

Boundary rules — all fifteen enforced mechanically, none resting on review. Rules 1-13 and 15
are an AST scan in `tests/test_boundaries.py` (rules 1-5 joined it in ADR-0009, rule 11 on
2026-09-07, rules 12-13 with the phase-4 assistant, rule 15 on 2026-09-23); **rule 14 is carried
by mypy strict instead**, and says so in its own entry — a rule enforced by a different mechanism
is still enforced, but pretending it is the same one would make the scan's coverage look wider
than it is:

1. `criteria.py` and `normalizer.py` do not import `httpx`, `sqlite3`, `openpyxl`, `rich`, `os`.
2. `client.py` does not import `store` or `sqlite3`; it receives `RequestHistory` as a protocol.
3. `store.py` does not import `httpx`.
4. `client.py` and `store.py` do not import `rich`; progress goes through the `Events` protocol in `progress.py`.
5. Only `pipeline.py` imports both `client` and `store`.

Extended in ADR-0008 for the phase-3 user layer:

6. `ui/texts.py`, `ui/wynik.py`, `batching.py`, `estimating.py`, `safetext.py`, `criteria.py`,
   `pkdmap.py`, `pkddict.py`, `pkdszukaj.py` and the pure assistant modules
   (`assistant/{__init__,schema,pkd,prompt,translate}.py`) do not import
   `rich`, `questionary`, `typer`, `httpx`, `httpx2`, `anthropic`, `sqlite3` or `openpyxl`.
   `pkdmap.py` joined the list in ADR-0012: it decides which PKD 2007 codes get added to a
   query, so it has to be answerable without a network or a database, exactly like the
   `criteria.py` it extends. `pkddict.py` and `pkdszukaj.py` joined in ADR-0026 for the same
   reason one layer down — `szukaj-pkd` needs no token, no database and no terminal, and that
   is a property of the command, not a convenience of its test.

   **`ui/wynik.py` (ADR-0024) carries a trap this scan cannot see.** The scan reads imported
   *roots* — `httpx`, `sqlite3`, `rich`, `openpyxl` — so `from ..pipeline import ExportSummary`
   would pass it without a murmur while removing the reason rule 6 exists: the envelope would
   then only be constructible where `pipeline` is, i.e. where there is a network and a database.
   A separate AST test in `tests/test_ui_wynik.py` pins that module's relative imports to exactly
   `{criteria}`. This is the difference between a rule enforced and a rule reported as enforced.
7. Only `ui/prompts.py` imports `questionary`; only `richtext.py`, `ui/render.py` and
   `console.py` import `rich` (`cli.py` left that list in ADR-0009).
8. `ui/*` does not import `client` or `store` — it goes through `pipeline`.
9. `cli.py` prints no user-facing sentence of its own; every block comes from `ui/texts.py`.
10. Every string that reaches `rich` from outside the program goes through `richtext.safe`,
    which strips control characters and prevents markup parsing (ADR-0008, decision 7).

Rule 9 is what makes rule 10 checkable at all: while `cli.py` printed through `rich`
itself, "every outside string passes through `safe`" would have needed dataflow analysis across
the package (ADR-0009). The rule-10 scan is syntactic and says so — it rejects
`console.print(f"…{n}")` even for an integer, because such a sentence belongs in `ui/texts.py`
under rule 9; the remedy is to move the sentence, never to relax the scan.

`richtext.py` (new in ADR-0009) is the only module that may hand `rich` an outside string:
it owns `make_console()` and `safe()`, which `ui/render.py` and `console.py` import instead of
each holding its own copy of the neutralisation.

**Since 2026-09-23 that console writes to stderr** (ADR-0024, decision 2): `make_console(stderr=True)`
is asked for once, in `cli.py`, and `ConsoleEvents`/`LineEvents` inherit the stream rather than
being told about it in seven places. One console, not two, so the seam rule 10 guards stays single.
The flag rather than `file=sys.stderr` because `rich` resolves the stream at every write
(`console.py:757`) — freezing it at import time would break output capture in the CLI tests, the
same reason the console never had a `file=` at all.

Extended on 2026-09-07 for the egress policy (docs/reference/uzupelnienie-01.md §B/§E):

11. Only `httpclient.py` **builds** an HTTP client; every other module receives one. The rule
    is about the client, not about one library: the scan covers `httpx`, `httpx2` (which
    `anthropic` 1.x is built on) and the SDK's re-exported client factories. Passing a client
    around, and annotating a parameter with its type, stay allowed — the scan looks at calls,
    not at names.

Extended on 2026-09-07 for the phase-4 assistant (ADR-0011):

12. Only `assistant/caller.py` constructs `anthropic.Anthropic`, and every such call passes
    `api_key=` and `http_client=` explicitly (enforced since 2026-09-07).
    The SDK otherwise resolves an ambient credential chain (env, then an `ant auth login`
    profile on disk) and builds its own transport — so a bare `Anthropic()` would spend a
    credential this tool never asked for, over a socket nobody governs. Deliberately **not**
    folded into rule 11: rule 11 asks "who builds it", rule 12 also asks "with what". The scan is
    syntactic and **rejects `Anthropic(**kwargs)`** — a client whose credentials and transport are
    assembled elsewhere is exactly what the rule exists to stop, so that is not a false positive.
13. `assistant/*` imports none of `client`, `store`, `pipeline`. This is the structural form of
    §B's phase-4 sentence — "what reaches the model is the question text and the PKD dictionary,
    fetched records never". Records cannot travel there because there is no import edge along
    which they could, which is a stronger claim than care taken when building the prompt (and
    complements the test that pins the request's actual bytes).

Extended on 2026-09-08 for the identity of a record identifier (ADR-0013):

14. Only `recordid.py` produces a `KanonicznyId`; `client.py`, `store.py` and `pipeline.py`
    accept nothing else as an entry identifier. The register returns one identifier in two
    spellings — UPPER from `/firmy` and `/firma`, lower from `/zmiana` — and `firma.id` is a
    case-sensitive primary key, so treating a spelling as an identity wrote every changed
    entry to the database twice and made the detail cache unhittable on the `aktualizuj` path.
    Unlike rules 1-13 this one is carried by mypy strict rather than by the AST scan: `NewType`
    turns "was this normalised?" into a compile-time question, and mypy already covers both
    `ceidg_tool` and `tests`. The canonical form applies to hex GUIDs only — `/raporty`
    identifiers share the 8-4-4-4-12 shape, are not hex, and are case-significant because they
    go into the download URL.

    **Extended 2026-09-09 (ADR-0016).** `recordid.py` now has a *second* producer,
    `id_z_tresci`, which mints the identity of a report row the register gave no number to.
    The rule is unchanged in substance — one module owns the construction — but the sentence
    "only `recordid.py` produces a `KanonicznyId`" is now doing more work than it looks: two
    functions, one module, and the reason the second one takes **values** rather than a CSV row
    is precisely so that a future migration cannot become a third producer with a third digest.

Extended on 2026-09-23 for the machine output channel (ADR-0024):

15. Only `jsonout.py` calls `json.dump` or writes to `sys.stdout`, and everything it emits passes
    `config.mask_tokens` through one recursive walk. This is rule 10's shape applied to the second
    channel: one owning module, one named neutraliser, so "can a secret leave this way" is answered
    by reading one file.

    **The rule names `json.dump`, not `json.dumps`, and that distinction is the whole design.**
    Measured 2026-09-23: `json.dumps` already lives in **six** modules at eight sites —
    `apiprofile.py:108,118` and `criteria.py:460` (fingerprint hashing), `recordid.py:100` (the
    ADR-0016 report-row digest), `store.py:712,904` and `pipeline.py:931` (values bound into SQL),
    `exporter.py:500` (`write_jsonl`, to a file under `wyniki/`). A rule naming `dumps` would have
    been red on its first run, and a scan red on arrival is a scan nobody trusts; the obvious
    repair — a module set with a named exception per entry — would have carried six entries to
    re-read at every future change. It is not needed, because `json.dumps` **returns a string and
    writes nothing**, and a string is harmless until it is printed: printing is already governed by
    rule 9 (no output call in `cli.py`) and rule 10 (only `richtext` hands a foreign string to
    `rich`). The dangerous form takes a stream, and it occurs **zero times** in the project, so the
    sharp rule has no exception list and was true the moment it was written. Those six sites are
    listed here rather than in the scan so the next reader does not have to rediscover that the
    rule skips them deliberately.

    **Masking and escaping are different neutralisers, and they are mirror images.**
    `json.dumps` escapes control characters (U+0000-U+001F, ESC included) and masks nothing — so
    JSON encoding covers the terminal half of §B and none of the credential half, exactly opposite
    to `strip_control`, which ADR-0009 refused as a rule-10 neutraliser *because* it does not mask
    and the token's payload carries a PESEL. The vector is concrete: an error message is the one
    envelope field that can carry a URL.

    Rule 9's scan grew the same shape — `json.dump(…, sys.stdout)` joined the calls it counts as
    output — because in a program that just acquired a JSON channel, that is the next `typer.echo`.
    A bare `print` counts too, package-wide rather than in `cli.py` alone: measured 2026-09-24, one
    `print` in `pipeline.run_update` made `json.loads(stdout)` fail on a real `aktualizuj` run with
    all 122 boundary tests green. `console.print` carries the same name and is deliberately **not**
    counted — it writes wherever its console points, which is stderr, and that is rule 10's
    territory; a scan modelling it would be a scan about somebody else's library.

    **One stdout write outside `jsonout.py` is known and left alone: `ctx.get_help()`**
    (`cli.py`, the no-subcommand callback). Under `rich_markup_mode="rich"` typer renders the help
    to stdout as a side effect and returns `""`, so neither scan sees it — the comment beside the
    call has known this since ADR-0009. It cannot corrupt an envelope: the callback declares no
    `--wynik`, and `kreator` is refused with exit 3 before anything is written. Recorded here so the
    next reader finds it in the rule rather than by measuring (code review, 2026-09-24).

**The log file is a second terminal-bound channel, and only one seam neutralises it.**
Rule 10 governs `rich` and nothing else, so `log.*` calls pass every gate — the boundary scan
explicitly allows `log.error("%s", exc)`. What keeps registry text safe in
`~/AppData/Local/ceidg-tool/logi/ceidg-tool.log` is that `pipeline._LogEvents.on_message` is the
only place outside text reaches the logger, and it runs `safetext.strip_control` first; the
token is covered independently by `logsetup.MaskingFormatter`. Two consequences worth knowing
before adding a log line: `strip_control` deliberately keeps `
`, so a registry name carrying a
newline can still forge a log row; and a new `log.info("firma %s", nazwa)` anywhere else would
walk straight past every gate. If that ever becomes more than a convention, extend the AST scan
to `log.*` in the modules that touch registry text rather than relying on this paragraph.

Rule 11 stands to §B as rule 9 stands to rule 10: it is what makes "no connection leaves for a
host outside `ALLOWED_HOSTS`" checkable by reading one module. `httpx` with the default
`trust_env=True` and no injected transport takes `HTTPS_PROXY` from the environment, so every
request — carrying a token whose payload is a PESEL — went to a host nobody compared against the
allowlist, and `client._checked_host` could not see it, because it inspects the URL rather than
the socket.

`httpclient.py` therefore owns `build_http_client()`, and three distinct mechanisms close three
distinct holes — the first review of this change found them conflated into one, which mattered
because only one of them had a test:

- **Environment proxies** stop applying because a transport is *always* injected
  (`allow_env_proxies = trust_env and transport is None`). `trust_env=False` on the client is a
  second lock, not the mechanism; it also disables `.netrc`.
- **`SSL_CERT_FILE`/`SSL_CERT_DIR` replacing the CA bundle** is stopped by `trust_env=False`
  passed to `httpx.HTTPTransport`, which is what reaches `create_ssl_context`. This one value is
  the whole mechanism behind half of §B's "TLS with certificate verification", so it has its own
  test.
- **A foreign host** is refused by `AllowedHostsTransport`, at the layer where the socket opens.
  `build_deps` narrows it to the host of the selected environment, so a `links.next` naming
  production cannot leave a run started against test.

The test seam is part of the reason the defect survived: `tests/support.py` built its own client,
and httpx skips environment proxies whenever a transport is injected, so the production
construction line had no coverage at all.

**Widened on 2026-09-07** (ADR-0011, finding F2). The scan matched the literal module name
`httpx`, so a second HTTP stack would have walked straight through a rule that reported itself
closed — `anthropic` 1.x is built on `httpx2`, a different distribution from the pinned
`httpx==0.28.1`. That is the shape ADR-0009 found when the `cli.py` scan saw only `rich` while
`typer.echo` was open. The factory and module sets are now data, so a third library joins the rule
rather than appearing beside it, and the scan reaches `scripts/` as well — the probes carry the same
production token.

**One shape is deliberately outside rule 11**: `Anthropic(...)` / `AsyncAnthropic(...)`, which also
build an HTTP client internally. They belong to **rule 12** (ADR-0011, decision 2), which asks for
something rule 11 cannot express — one owning module *and* an explicit `api_key=` and `http_client=`
on every call — and which cannot be checked before that owning module exists. Until phase 4 ships,
nothing enforces that shape; ADR-0011's Gate section is where it is tracked.

## Data flow

```
Criteria (validated, frozen, fingerprint)
   | to_params(profile)            <- API dialect enters HERE, not in Criteria
   v
client.count(criteria)   -- 1 request (limit=1) -->  count
   v
estimating.estimate(count, profile) --> Estimate(requests, seconds)   (ui/flow asks the user)
   v
store.start_run(...) --> run_id
   |
   +- LIST stage:    for page in client.iter_pages(criteria, cursor):
   |                     store.save_page(run_id, page, next_cursor)     <- ONE transaction: records + checkpoint
   +- DETAIL stage:  for chunk in store.pending_detail_ids(run_id, ttl):   <- worklist is a QUERY
   |                     store.save_details(run_id, client.fetch_details(chunk))   <- one transaction per chunk
   v
store.finish_run(run_id)
   v
EXPORT (separate command, offline):
   store.iter_run_records(run_id) --> normalizer.normalize(raw, ctx) --> RowSets
   exporter.write_workbook(...)   --> Firmy, PKD, Spolki, Adresy, Slownik, Metadane
```

Three properties that follow:

- Resume is free: the only path from network to Excel goes through SQLite, so the
  process boundary sits where durable state already is.
- Export is repeatable without network: a normaliser fix is `ceidg-tool eksportuj --run-id ...`.
- The estimator is a pure function `(count, profile) -> numbers`, so the phase-3
  message ("~N requests, about M min") changes with the profile, not with CLI code.

## Module contracts

```python
# criteria.py  (PURE)
class Criteria(BaseModel, frozen=True):
    nazwa: tuple[str, ...] = (); nip: tuple[str, ...] = (); regon: tuple[str, ...] = ()
    imie: tuple[str, ...] = (); nazwisko: tuple[str, ...] = ()
    wojewodztwo: tuple[str, ...] = (); powiat: tuple[str, ...] = ()
    gmina: tuple[str, ...] = (); miasto: tuple[str, ...] = (); ulica: tuple[str, ...] = ()
    kod: tuple[str, ...] = (); pkd: tuple[str, ...] = ()
    status: tuple[StatusJdg, ...] = ()
    data_od: date | None = None; data_do: date | None = None
    szczegoly: bool = False
    max_rekordow: int | None = None          # BUSINESS limit, not a transport parameter
    def to_params(self, profile: ApiProfile) -> list[tuple[str, str]]: ...
    def fingerprint(self) -> str: ...
```

Deliberately outside `Criteria`: `page`, `limit`, `base_url`, token. They are
transport parameters; inside `Criteria` the phase-4 assistant could generate them and
user YAML would start describing protocol instead of the business question.

```python
# client.py
@dataclass(frozen=True)
class Page:
    records: list[dict[str, Any]]; count: int | None; cursor: Cursor | None; index: int

class CeidgClient:
    def __init__(self, http: httpx.Client, profile: ApiProfile,
                 limiter: RateLimiter, token: str, events: Events) -> None: ...
    def count(self, c: Criteria) -> int: ...
    def iter_pages(self, c: Criteria, start: Cursor | None = None) -> Iterator[Page]: ...
    def fetch_details(self, ids: Sequence[str]) -> list[dict[str, Any]]: ...  # splits by ids_batch_size AND max_url_length
    def list_reports(self, od: date | None, do: date | None) -> list[Report]: ...
    def download_report(self, report_id: str, dest: Path) -> Path: ...
    def iter_changes(self, od: datetime, do: datetime) -> Iterator[Page]: ...

# pipeline.py
@dataclass(frozen=True)
class Estimate: requests_list: int; requests_details: int; seconds: float
def estimate(count: int, profile: ApiProfile, szczegoly: bool) -> Estimate: ...   # PURE
def run_fetch(c: Criteria, deps: Deps, resume_run_id: str | None = None) -> RunResult: ...
def run_export(run_id: str, dest: Path, deps: Deps) -> Path: ...
```

## Errors, security, exit codes

```
CeidgError
+-- ConfigError             missing token, bad path                       exit 3
+-- ProdWithoutConsentError                                               exit 3
+-- AuthError               401/403, no retry                             exit 3
+-- BadRequestError         400, no retry, URL without token in message   exit 1
+-- UntrustedLinkError      links.next on a foreign host                  exit 1
+-- PagingRunawayError                                                    exit 1
+-- ProfileMismatchError    resume on a different profile/criteria        exit 1
+-- ServerError             5xx after retries (resumable)                 exit 2
+-- TransportError          timeout/DNS (resumable)                       exit 2
+-- ExportError                                                           exit 1
```

- No `except Exception`. Resumable errors set `run.status = 'przerwany'`, commit the
  checkpoint and print "resume with: `ceidg-tool wznow --run-id ...`". Exit code 2
  tells a scheduler that a retry makes sense.
- **Exit code 4 exists only under `--wynik json`** (ADR-0024, decision 3): `brak_trafien` ("the
  query ran and matched nothing") and `nic_do_zrobienia` ("no resumable run, no changes, no
  finished run") are both 4, and both are 0 without the flag. The flag is the caller's own
  declaration — nobody asks for JSON at a terminal for fun — so one flag cannot disagree with
  itself, whereas a separate switch could. A scheduler that reads non-zero as "investigate" would
  otherwise start alerting on a legitimately empty day, and no existing job asked for that.
  The envelope's `status` is the observer of the exit code, and `ui/wynik.kod_wyjscia` derives one
  from the other in a `match` with `assert_never` — not a `dict[Status, int]`, because mypy does
  not check a dict literal against the members of a `Literal`, so a sixth status would have joined
  the set silently and surfaced as a `KeyError`. `KeyboardInterrupt` stays 130 and writes **no**
  envelope: 130 is not a code `kod_wyjscia` can produce, and inventing a status for it would create
  exactly the drift that function exists to prevent.
- Production consent is a mechanism, not a convention:
  `config.resolve_environment(requested, consent: bool)` raises
  `ProdWithoutConsentError`; `consent` is a constructor argument passed from the CLI
  after confirmation, never a global flag.
- Token only from `.env`/env; config `__repr__` redacts; logs and exceptions redact
  headers; `request_log` stores `token_fp`, never the token. The
  `x-gravitee-transaction-id` header is logged: it is the only identifier the API
  operator will ask for.

## Build order and probe dependency

| # | Step | Blocked by the probe? |
|---|---|---|
| 1 | `errors`, `config`, `apiprofile` + `profiles/*.yaml` with conservative values | no |
| 2 | `criteria` + tests | no (semantic validation is dialect-independent) |
| 3 | `clock`, `ratelimit` + tests with `FakeClock` | no |
| 4 | `store` + schema + resume tests (synthetic data) | no |
| 5 | `client` + tests on fixtures | yes: needs fixtures and profile values |
| 6 | `normalizer` + tests | partly (PDF examples suffice to start) |
| 7 | `exporter` + tests | no |
| 8 | `pipeline` + minimal CLI + e2e test (gate 2) | yes |

## Risks

| Risk | Impact | Response |
|---|---|---|
| `links.next` present on the last page too (documented example: `next == self == last` at `count=2`) | infinite loop, budget exhausted | five termination guards (ADR-0002); a test reproducing the documented example |
| `ids_batch_size` too large, URL over the gateway limit | 400 only in production | split by count and by `max_url_length`; 100 GUIDs is about 4.1 kB |
| `count` drifts between resume sessions | misleading `Metadane` | store `count_api` from the first page and report the discrepancy |
| `PKD` sheet above 1 048 576 rows | corrupted/truncated file | count rows before writing, refuse and point to CSV/JSONL |
| Personal data in raw JSON in SQLite | data-minimisation principle | configured retention, `wyczysc`, one file per environment, `--bez-cache` |
| Two processes on one token | 429 and a 180 s stall | shared `request_log` read on every `acquire()` plus a per-environment run lock |
| Profile changed between download and resume | mixed dialects in one file | `profile_hash` in `run`, refuse to resume |

## Decisions taken by the project owner (2026-09-05)

- Three additional modules accepted.
- Default `min_spacing_s` = 3.75 s (3.6 s was measured unsafe on 2026-09-06).
- `Adresy` sheet is in phase-2 scope.
- ADRs written in English (global knowledge-docs convention).
- Cache retention defaults chosen by Claude: `cache_ttl_days = 7`, cleanup of raw
  data older than 30 days; both configurable.
