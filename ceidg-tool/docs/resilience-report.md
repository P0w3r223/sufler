# Resilience report

Date: 2026-09-07 (first written 2026-09-05)
Status: draft — every scenario has an automated equivalent; §E still wants 1, 2 and 8 run by hand
Author: P0w3r223
Related to: docs/reference/uzupelnienie-01.md §D and §E, docs/adr/0009_boundary_rules_and_resilience.md,
docs/adr/0010_report_link_enrichment.md (decision 8a)

**Two columns, two different claims.** "offline" means an automated suite proves the criterion
against injected faults. "manual" means §E's own requirement: the scenario carried out on a real
machine, dated and described here. Automating a scenario turns its manual run from discovery
into confirmation — it does not tick it off.

---

| # | Scenario | How it is covered | Last result |
|---|---|---|---|
| 1 | `kill -9` mid-page, then `wznow` | `tests/resilience/test_s1_kill_and_resume.py`: the state a killed process leaves is reproduced exactly — page transactions committed, run still `w_toku` (never marked `przerwany`, because no `finally` ran), lock row held by the dead PID — and a fresh `Store` on the same file then resumes it. Nothing is lost relative to what the API served, ids are unique, `count_api` is unchanged and only the missing page is re-requested (the fixture repeats one id across pages, so 15 served rows are 14 stored records). Also pins that the lock expires on its own, so waiting is a real alternative to `--force`, and that forcing a **live** lock warns the operator. Plus the graceful-interruption case in `tests/test_pipeline_e2e.py::test_resume_does_not_refetch_saved_pages`. Manual run: **pending**. | offline: pass (2026-09-06) |
| 2 | Network cut for 2 minutes during a download | `tests/resilience/test_s2_network_outage.py`: the transport is dead for 120 s of clock time (not for a fixed number of failures), the fetch finishes with no intervention, nothing is lost and no id repeats, and the §A sentence "brak połączenia, czekam, postęp zapisany" is asserted where it is produced. A third case pins the §C limit: past 30 minutes the run stops `przerwany` with a resume instruction and an intact checkpoint. **Measured cost**: the 10 → 30 → 60 s rungs end near second 111, still inside the outage, so the fourth attempt lands only after 300 s — a two-minute cut costs the whole ladder and about seven minutes. Manual run with a real cut: **pending**. | offline: pass (2026-09-06) |
| 3 | System clock jump of +2 h during work | `tests/test_review_fixes.py::test_window_survives_wall_clock_jump`, `test_cooldown_survives_wall_clock_jump`: own requests are tracked on the monotonic clock; both limiter windows and the 429 cooldown survive a wall-clock jump. | offline: pass |
| 4 | Hostile names (`=CMD()`, `+1`, `@SUM`, `-2+3`, control chars, 5 000 chars) | `tests/resilience/test_s4_hostile_data.py`: every text cell in every sheet is stored as text with an apostrophe prefix, control characters removed, CSV equally neutralised, JSONL stays valid JSON. Runs in CI with the normal suite. | pass |
| 5 | Truncated JSON, HTML instead of JSON, 204 without body, 500 | `tests/test_client.py`: truncated JSON → `ServerError` (resumable), HTML with 5xx → retries then `ServerError`, 204 → empty page, run marked `przerwany` with checkpoint intact (`test_server_error_marks_run_and_keeps_checkpoint`). | pass |
| 6 | Token expired, empty, wrong environment | `tests/test_config.py`: expired JWT and missing token stop before any request with a message pointing to the token service; wrong environment surfaces as `AuthError` on the first request (`test_401_stops_immediately`). | pass |
| 7 | `grep` of the token in logs, database, output files and messages after a full session | `tests/resilience/test_s7_token_leak.py` (offline full session) plus a real production session on 2026-09-05: 11 requests, log, SQLite and workbook scanned for the token bytes and for `Bearer` — zero hits; log contains endpoint names and status codes only, no query strings. **Since 2026-09-07 the session plants every secret shape, not one** (ADR-0011 F1): the JWT and an `sk-ant-…` key are both written to the log and both greped for across the tree, and masking is asserted in all three channels the documents name — `mask_tokens`, `richtext.safe` and `MaskingFormatter` with a real `LogRecord`. A separate case covers the **opaque, non-JWT** token `inspect_token` supports, which no pattern matches and which is masked by registered value instead. | pass |
| 8 | Disk full during export | `tests/resilience/test_s8_disk_full.py` covers the half `check_free_space` cannot: space sufficed for the estimate and ran out **mid-write**. `ENOSPC` is injected after the temporary file is partially written; no file appears at the destination, no `.tmp` is left behind, and the message names the path. "Database untouched" is checked the way an operator would — the export is repeated successfully once the space is back — rather than by counting rows in the process that just failed. The refusal-before-writing path stays in `tests/test_exporter.py`. Manual run with a full disk: **pending**. | offline: pass (2026-09-06) |
| 9 | `count` = 400 000 | `tests/resilience/test_s9_large_count.py` (12 cases, runs in CI): `flow.prepare_fetch` spends exactly one `count` request, prints the cost table and the split proposal, and starts **no** fetch; every proposed batch carries its own estimate and fits under the threshold; "popraw kryteria" also fetches nothing; through the real CLI, `--tak` exits 3 without a fetch and `--partie` is the only way past the gate. | pass |
| 10 | Two processes on one database | `tests/test_review_fixes.py::test_lock_is_taken_atomically_and_same_pid_does_not_steal`, `tests/test_store_resume.py::test_lock_blocks_second_process_unless_stale`: lock taken in a `BEGIN IMMEDIATE` transaction, second process gets `StoreLockedError`. | pass |

## The §E egress criterion (2026-09-07)

§E asks for one thing the ten numbered scenarios do not cover: "brak połączeń do hostów spoza listy
dozwolonych (test z zaślepką DNS)". It had no test until 2026-09-07, and the gap was not cosmetic.
Host checking existed in two places, `apiprofile` and `client._checked_host`, and both read the
**URL**. `pipeline` built `httpx.Client(verify=True, follow_redirects=False)` with `trust_env` at its
default, so `HTTPS_PROXY` in the environment routed every request — `Authorization` header included,
and the token's payload carries a PESEL — through a host nobody compared against `ALLOWED_HOSTS`.
The string check passed while the socket went elsewhere.

| Claim | How it is covered | Last result |
|---|---|---|
| No connection is attempted to a host outside `config.ALLOWED_HOSTS` | `tests/resilience/test_egress_allowlist.py`: `socket.getaddrinfo` is stubbed to record the names asked for and refuse them, so the assertions are about the *intent to connect* and need no network. **Fifteen cases** (the file had nine when this row was written; corrected 2026-09-09), of which the load-bearing are: the suite-wide guard actually bites; a request to an allowed host resolves exactly that one name (the positive control, without which "no foreign host was asked for" would pass vacuously); with `HTTPS_PROXY`, `HTTP_PROXY` and `ALL_PROXY` all set to a foreign host it still resolves only that one name (the regression); a foreign host is refused with `UntrustedLinkError` **before** any resolution; `http://` to an allowed host is refused too; a `MockTransport` goes through the same gate, so tests no longer travel a different path than production; a gate narrowed to the test host refuses production; `SSL_CERT_FILE` pointing at a non-existent file does not become the CA bundle (with `trust_env=True` httpx fails at construction, so the case discriminates); and `build_deps` is shown to call the factory with no transport and the narrowed host set. | offline: pass (2026-09-07) |
| The policy cannot be bypassed by a second construction site | `tests/test_boundaries.py`, boundary rule 11: only `httpclient.py` builds an `httpx.Client`, scanned across `ceidg_tool/` **and** `tests/`. The scan has its own ten-case test separating building a client from annotating or passing one, covering `httpx.Client`, `from httpx import Client`, both alias forms and `AsyncClient`, with two negative controls for a foreign class of the same name. A separate test pins that `pipeline.build_deps` actually calls the factory and passes no transport of its own — the scan proves there is one factory, not that it is used. | offline: pass (2026-09-07) |

**Which mechanism does what**, because the first version of this change described all of it as
`trust_env` and the review showed that only one of the three claims had a test behind it. Environment
proxies stop applying because `build_http_client` *always* injects a transport
(`allow_env_proxies = trust_env and transport is None`); `trust_env=False` on the client is a second
lock. `SSL_CERT_FILE`/`SSL_CERT_DIR` are ignored because `trust_env=False` is passed to
`httpx.HTTPTransport`, which is what feeds `create_ssl_context` — that single value is the whole
mechanism, and deleting it used to leave all 654 tests green, so it now has its own test. Foreign
hosts are refused by `AllowedHostsTransport`, narrowed by `build_deps` to the host of the selected
environment.

**Deliberate consequences, worth knowing before a run in a corporate network.** The tool ignores a
proxy the operating system is configured to use and ignores a CA bundle named by the environment. On
a network that intercepts TLS it will now refuse to connect rather than hand the token to the
interceptor. That is what §B asks for; if it ever has to change, it changes in
`ceidg_tool/httpclient.py` and nowhere else.

The probes carry the same token and had the same hole through `urllib.request.urlopen`, which builds
a `ProxyHandler` from the environment by default. All three now go through
`probe_support.no_proxy_opener()`. They are developer scripts, not the shipped tool, so they are
outside the boundary scan — the shared opener is the mechanism, the same way the shared request log
replaced the warning in a docstring.

## Gate 2 runs on production (2026-09-05, with the owner's consent)

| Run | Command | Requests | Result |
|---|---|---|---|
| API path | `pobierz -w podlaskie -m Łomża --od 2014-01-01 --do 2014-03-31 --maks 40 --szczegoly --zrodlo api` | 11 | 89 hits, 40 records with details, workbook 42 KB (Firmy, PKD, Adresy, Slownik, Metadane) |
| Report path | `pobierz --wojewodztwo podlaskie --od 2014-01-01 --do 2014-12-31 --maks 300 --zrodlo raport` | 2 (report list + 5 MB ZIP) | 300 records filtered locally from the daily snapshot, workbook 164 KB (Firmy, PKD 2 771 rows, Slownik, Metadane), `zrodlo=CEIDG_RAPORT` |

## Production run used for scenario 7 (2026-09-05)

`ceidg-tool pobierz -w podlaskie -m Łomża --od 2014-01-01 --do 2014-03-31 --maks 40 --szczegoly --zrodlo api --srodowisko prod --produkcja --tak`

| Item | Value |
|---|---|
| Requests | 11 (1 count, 2 list pages, 8 detail batches of 5), all HTTP 200 |
| Records | 40 of 89 hits, 40 with details |
| Workbook | Firmy 40 rows, PKD 200, Adresy 11, Slownik 79, Metadane 16; tables and frozen headers on every data sheet; 0 formula cells |
| Token scan | log, `store-prod.sqlite`, `bramka2_api.xlsx` (all XML parts): 0 hits |
