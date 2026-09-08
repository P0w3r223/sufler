# ADR-0006: Test seam is the `httpx` transport plus an injected clock; no home-grown HTTP abstraction

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: ADR-0003, INSTRUKCJA_CLAUDE_CODE.md (offline tests), scripts/ceidg_probe.py (sample format)

---

## Context

Requirements: offline tests on probe fixtures, limiter tested with a substituted clock,
`mypy --strict`. `respx` is already a dev dependency. The probe writes responses as
`{url, status, headers, elapsed_s, body}`.

## Options

**A. Own `HttpPort` protocol with an httpx implementation and a test fake.**
Formally library-independent, but a second implementation will never exist, the
wrapper copies httpx's API with worse types and hides `Retry-After`, status codes and
report-ZIP streaming. YAGNI. Effort M, risk medium.

**B. Injected `httpx.Client`; `httpx.MockTransport` in tests.**
httpx already exposes a transport seam; tests exercise the real client path including
header parsing; no extra dependency. Effort S, risk low.

**C. `respx` as the primary mechanism.**
Readable URL-pattern matching, but the architecture would depend on a test library
and global interception can be awkward with parallel tests.

## Decision

**B as the architectural seam, C as a convenience in selected tests.** `respx` stays
in dev dependencies for tests where URL matching reads better; no design decision
depends on it.

Also:

- The clock is injected everywhere time matters: limiter (`monotonic`),
  `pobrano_utc` and `Metadane` (`wall`). `time.sleep` exists only inside
  `SystemClock`. Without this the `Metadane` sheet is untestable.
- The client is synchronous. With forced 3.6 s spacing concurrency gains nothing and
  complicates the limiter, SQLite transactions and typer integration. Reversible:
  `ratelimit`, `store`, `normalizer`, `exporter` are I/O-model agnostic; only
  `client.py` would change. Revisit if the API ever allows parallel tokens.
- A fixture loader reads the probe sample format 1:1 from `tests/fixtures/*.json` and
  builds a `MockTransport` from it. Fixtures are literally what the API returned, not a
  hand-written idea of the API.

## Test plan mapped to the instruction

| Test module | Requirement covered |
|---|---|
| `test_ratelimit.py`: 51st request waits; the 1000/60 min window binds independently; 429 leads to exactly 185 s with no requests; cooldown counted from the last attempt; history from the database rebuilds the window after restart; a wall-clock jump does not break the limiter | "limiter with substituted clock", "both windows", "full 180 s" |
| `test_client_paging.py`: `links.next`; the `next == self` guard (the documented `count: 2` example); `numeric` mode with `page_start` 0 and 1 on the same fixtures; 204; 200 with empty list; 404 on `/firma/{id}`; 401 without retry; 5xx with backoff through the limiter | "pagination incl. numbering start", "no results" |
| `test_criteria.py`: NIP checksum; date order; PKD shape; disallowed status; `to_params` for two profiles from one `Criteria`; stable `fingerprint` | "Criteria validation" |
| `test_store_resume.py`: interruption after page N, then resume starts at N+1 and issues no requests for earlier records (counter on `MockTransport`); a crash mid-transaction leaves no checkpoint without records; the detail worklist skips fetched ids | "resume without re-downloading" |
| `test_normalizer.py`: a record with 4 PKD gives 4 rows plus `pkd_wszystkie`; a record without details; `spolka`/`spolki` alias | "flattening a record with many PKD" |
| `test_exporter.py`: NIP/REGON/postal code/TERC/SIMC as text with leading zero; dates as `datetime.date`; `Slownik` covers 100 % of `SHEETS` columns; no `Spolki` sheet at zero rows; refusal above the row limit | "NIP as text", workbook spec |
| `test_pipeline_e2e.py`: fixtures to SQLite to xlsx, zero network, byte-identical with a fixed clock | gate 2 |
