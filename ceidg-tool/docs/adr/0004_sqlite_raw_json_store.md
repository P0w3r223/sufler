# ADR-0004: SQLite with raw JSON as the source of truth; export always reads the store

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: ADR-0002, ADR-0003, ADR-0005, INSTRUKCJA_CLAUDE_CODE.md (store.py)

---

## Context

Downloading is expensive (3.6 s per request, so 1000 firms with details take hours).
Normalisation is free and will certainly change: the instruction names 3 one-to-many
relations, the API has 9, and fill rates of `email`/`telefon`/`www` are known only
after a production probe.

## Options

**A. Write straight into normalised tables (`firmy`, `pkd`, `spolki`).**
Small database, plain SQL, export is a `SELECT`. But a normaliser bug or a new column
means re-downloading (hours plus rate budget); every column-set change is a schema
migration; normalisation inside the download path lengthens the transaction.
Effort M, risk high.

**B. Store raw JSON per record; normalise at export.**
Re-export offline for free, new columns and sheets without network, fixtures are
literally cache rows, audit trail for personal data. Database several times larger,
field queries need `json_extract`. Effort S/M, risk low.

**C. Hybrid: raw JSON plus a few extracted, indexed scalars (`nip`, `regon`,
`status`, `wojewodztwo`, `data_rozpoczecia`, `detail_state`).**
All of B plus fast worklist and dedup queries, no migration when Excel columns change.
Two places hold those five fields; resolution: the scalars are an index, never the
export source. Effort M, risk low.

## Decision

**C.** The network is expensive, the disk is not. A design where a typo in a column
mapping costs re-downloading a voivodeship fails the maintenance test.

Second decision that follows: **export reads exclusively from the store, never from
the downloading process's memory.** Side effect: a separate `eksportuj --run-id`
command regenerates the workbook after a normaliser fix without a single request.

Schema (`PRAGMA journal_mode=WAL; foreign_keys=ON; user_version=1`):

- `run`: one row per `pobierz` invocation. `run_id`, timestamps, `environment`,
  `tool_version`, `profile_hash`, `criteria_json`, `criteria_hash`, `mode`
  (`lista`/`szczegoly`), `status` (`w_toku`/`zakonczony`/`przerwany`/`blad`),
  `count_api`, `pages_done`, `records_seen`, `error`.
- `checkpoint`: one row per run. `stage` (`lista`/`szczegoly`/`gotowe`),
  `cursor_mode` (`links`/`numeric`), `cursor`, `page_index`, `updated_utc`.
- `firma`: record cache shared across runs. `id` (GUID) primary key,
  `environment`, `list_json` + `list_utc`, `detail_json` + `detail_utc`,
  `detail_state` (`brak`/`pobrany`/`nieznaleziony`/`blad`), indexed scalars,
  `zrodlo` (`CEIDG_API`/`CEIDG_RAPORT`).
- `run_firma`: membership of a record in a run plus stable Excel row order
  (`page_index`, `position`).
- `request_log`: limiter history that survives restarts. `ts_epoch`,
  `environment`, `token_fp`, `endpoint`, `status`.
- `watermark`: last-update marker for `/zmiana` mode, keyed by scope
  (e.g. `zmiana:test`).

Rules that make resume correct:

1. The checkpoint is updated in the same transaction as the page's records. After a
   crash either the page and its cursor are both in the database or neither is.
   One write per page and one per detail chunk, not per record.
2. The detail worklist is a query, not a table: `run_firma` joined with `firma`
   where `detail_state = 'brak'` or the cached detail is older than the TTL.
3. `firma` is shared between runs: re-fetching the same region reuses cached
   details within `cache_ttl_days` (configuration, default 7).
4. `detail_state = 'nieznaleziony'` for 404, otherwise resume retries the same ids forever.
5. One database file per environment: `.ceidg/store-test.sqlite`,
   `.ceidg/store-prod.sqlite`. Test data is synthetic, production data is personal
   data; "delete production data" is deleting one file.
6. A per-environment lock row (pid + start time): a second concurrent run without
   `--force` is refused.

## Consequences

- The database grows 3-5x versus option A: roughly 300-600 MB per 100 k records.
  Acceptable locally, noted in the README.
- Tension with data minimisation (the instruction: collect only needed fields).
  Mitigated by a configured retention (`ceidg-tool wyczysc --starsze-niz 30d`,
  default 30 days), a `--bez-cache` mode dropping `detail_json` after export, and the
  database being git-ignored. A conscious trade-off, recorded in `docs/decisions.md`.
- `PRAGMA user_version` from day one; migrations are a list of `1->2`, `2->3`
  functions, not alembic.
- Revisit when the Excel column set has been stable for months and the database
  exceeds a few GB.
