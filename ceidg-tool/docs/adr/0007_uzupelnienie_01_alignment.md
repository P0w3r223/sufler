# ADR-0007: Alignment with "Uzupełnienie 01" (security, reliability, resilience tests)

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: UZUPELNIENIE_01.md, ADR-0003, ADR-0004, ADR-0005, docs/decisions.md

---

## Context

`UZUPELNIENIE_01.md` arrived while phase 2 was in progress and takes precedence over
the main instruction. It adds security requirements (§B), reliability requirements (§C),
resilience scenarios (§D) and acceptance criteria (§E). The owner accepted the full
list of changes to already finished elements on 2026-09-05.

## Decisions

| Requirement | Decision | Where |
|---|---|---|
| Registry data is hostile: neutralise `=`, `+`, `-`, `@`, TAB, CR prefixes; strip control chars | `sanitize_text()` applied to every text and text-as-date cell in xlsx and CSV, and to `Metadane`. JSONL keeps raw values (it is not a spreadsheet; JSON escaping is the protection). | `exporter.py` |
| Streaming export, atomic rename, free-space check | `write_only` workbook, rows streamed from a record-source factory, temp file + `os.replace`, `shutil.disk_usage` check before writing. | `exporter.py` |
| Split above 1 048 576 rows instead of refusing | **Amends ADR-0005.** `plan_export()` counts rows per sheet in a first pass and splits firms into parts `_czesc01`, `_czesc02`, … so that no sheet of any part exceeds the limit; each part has the full sheet set and a `czesc i/N` entry in `Metadane`. | `exporter.py` |
| Allowed hosts only | `config.ALLOWED_HOSTS`; `ApiProfile.base_url` validator and `CeidgClient._checked_host()` for every URL including `links.next` and report download links. | `config.py`, `apiprofile.py`, `client.py` |
| Token from keyring, `.env` as fallback, never in logs | Order: argument > keyring > `CEIDG_TOKEN` > `.env`. `MaskingFormatter` replaces any JWT in log lines; `mask_tokens()` on user-facing errors; database stores `sha256(token)[:16]`. POSIX `.env` is chmod 600; on Windows NTFS there is no POSIX mode, so nothing is changed. | `config.py`, `logsetup.py` |
| JWT expiry read locally, warning 7 days before | `inspect_token()` reads `iat`/`exp` without signature verification; an expired token stops the tool before the first request; no `exp` claim shows "brak daty wygaśnięcia w tokenie" plus the issue date (owner decision: no assumed validity period). | `config.py` |
| Data at rest in the user data directory | `platformdirs.user_data_dir("ceidg-tool")`: `store-<env>.sqlite`, `wyniki/`, `logi/`, `raporty/`. Output files are always written to `wyniki/` with a name derived from the criteria and sanitised. | `config.py`, `cli.py`, `pipeline.py` |
| `PRAGMA integrity_check`, quarantine corrupted database | On open; a failing database (or non-database file) is renamed to `<name>.uszkodzony-<stamp>` together with WAL/SHM side files and a fresh one is created. | `store.py` |
| Two processes on one database | Lock row taken in a `BEGIN IMMEDIATE` transaction with a conditional upsert; stale after 600 s without heartbeat; same PID after reboot cannot steal a live lock. | `store.py` |
| 180 s wait after resume | Owner decision: wait the **remainder** of 180 s counted from the last request recorded in `request_log`, not a flat 180 s, because the persisted history already reconstructs both limiter windows. | `ratelimit.enforce_resume_gap()`, `pipeline.run_fetch()` |
| Connection errors retried 10 → 30 → 60 → 300 s, give up after 30 min | Implemented in `CeidgClient._request()`; every retry passes through `RateLimiter.acquire()`; transport errors are `TransportError` (exit code 2, resumable), API errors keep their own types. | `client.py` |
| `link_ceidg` column | `https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/SearchDetails.aspx?Id=<guid>` for API records; `None` for report records (no GUID in the CSV). | `normalizer.py` |
| Resilience tests in CI | `tests/resilience/test_s4_hostile_data.py` (scenario 4) and `test_s7_token_leak.py` (scenario 7) run with the normal suite. Scenarios 1, 2, 8 are manual and go to `docs/resilience-report.md`. | `tests/resilience/` |
| Pinned dependencies | `requirements.lock` generated from the virtual environment (`pip freeze --exclude-editable`). | repo root |
| Report path as a first-class source | New: `reports.py` maps the daily voivodeship CSV to the API record shape (`zrodlo=CEIDG_RAPORT`) and filters locally; `pipeline.run_report_fetch()` stores it as a run so export, resume bookkeeping and metadata are shared. | `reports.py`, `pipeline.py` |

## Consequences

- ADR-0005 "refuse above the row limit" is superseded by splitting; the exporter no
  longer raises for large sets.
- Two new runtime dependencies: `platformdirs`, `keyring`. `keyring` is optional at
  runtime (missing backend means "no token in keyring", not an error).
- The `Deps` object in `pipeline.py` is the single composition root; the CLI never
  builds a client on its own, so the allow-list and consent checks cannot be bypassed
  by a new command.
- Not yet done (phase 3): the interactive wizard and first screen from §A, the
  `count > 50 000` split proposal beyond a text hint, `docs/resilience-report.md` with
  manual scenarios 1, 2, 8.
