# Phase 2 core design: `ceidg_tool` package

Date: 2026-09-05
Status: accepted; sections marked below are superseded in part by ADR-0007
Author: P0w3r223
Related to: INSTRUKCJA_CLAUDE_CODE.md, docs/api_notes.md, docs/adr/0001..0007

---

## Superseded by ADR-0007 (docs/reference/uzupelnienie-01.md)

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
inputs: cli.py (typer flags, YAML), ui/wizard.py, phase 4 assistant.py
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
```

Boundary rules — all fourteen enforced mechanically, none resting on review. Rules 1-13 are an
AST scan in `tests/test_boundaries.py` (rules 1-5 joined it in ADR-0009, rule 11 on 2026-09-07,
rules 12-13 with the phase-4 assistant); **rule 14 is carried by mypy strict instead**, and says
so in its own entry — a rule enforced by a different mechanism is still enforced, but pretending
it is the same one would make the scan's coverage look wider than it is:

1. `criteria.py` and `normalizer.py` do not import `httpx`, `sqlite3`, `openpyxl`, `rich`, `os`.
2. `client.py` does not import `store` or `sqlite3`; it receives `RequestHistory` as a protocol.
3. `store.py` does not import `httpx`.
4. `client.py` and `store.py` do not import `rich`; progress goes through the `Events` protocol in `progress.py`.
5. Only `pipeline.py` imports both `client` and `store`.

Extended in ADR-0008 for the phase-3 user layer:

6. `ui/texts.py`, `batching.py`, `estimating.py`, `safetext.py`, `criteria.py`, `pkdmap.py`
   and the pure assistant modules (`assistant/{schema,pkd,prompt,translate}.py`) do not import
   `rich`, `questionary`, `typer`, `httpx`, `httpx2`, `anthropic`, `sqlite3` or `openpyxl`.
   `pkdmap.py` joined the list in ADR-0012: it decides which PKD 2007 codes get added to a
   query, so it has to be answerable without a network or a database, exactly like the
   `criteria.py` it extends.
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
