# ADR-0013: The identity of a record identifier — a value, not a string

Date: 2026-09-08
Status: **accepted 2026-09-08 by the owner** (decisions 1-4 implemented the same day)
Author: P0w3r223
Related to: docs/decisions.md (returned identifier casing), docs/status.md (phase 5),
            docs/design/phase2_core.md (boundary rule 14), ADR-0004, ADR-0006, ADR-0009

---

## Context

On the night of 2026-09-07/08 the owner ran `aktualizuj` against production. It finished
cleanly: 27 pages, 13 401 changes, 2 681 requests, status `zakonczony`. It also produced
nothing. Every one of its 13 401 records sits in the database as `detail_state='brak'` —
while the details for all 13 401 are in the same database, complete, under a different
spelling of the same identifier.

The register returns one identifier in two spellings:

| Endpoint | Spelling | Evidence |
|---|---|---|
| `/firmy` (list) | UPPER | `probe_out/samples/02_200_firmy.json` |
| `/firma` (details) | UPPER | `probe_out/samples/24_200_firma.json` |
| `/zmiana` | **lower** | `probe_out/samples/26_200_zmiana.json` — `feb789d1-2416-…` |
| `/raporty` | mixed, **not hex** | `probe_out/samples/25_200_raporty.json` — `F3Aj3APe-9rud-…` |

`firma.id` is `TEXT PRIMARY KEY` with no collation, so SQLite compares it case-sensitively.
`store.link_ids` inserted the `/zmiana` spelling verbatim, creating a husk whose
`detail_state` took the column default `'brak'`; `store.save_details` inserted the `/firma`
spelling, creating a second row carrying the data and linked to no run. `stale_detail_ids`
compares `id IN (…)` case-sensitively, so on this path the TTL cache **cannot** hit — every
`aktualizuj` re-buys records it already holds, at ~3 hours per run.

Measured in the owner's production store before the migration:

- 31 860 rows describing 16 310 entries; **15 550 entries present twice**
- all 13 401 identifiers of the overnight run lowercase, all `'brak'`, and for **all 13 401**
  the real details present under the uppercase identifier
- 15 550 orphan rows, i.e. exactly the set `purge_older_than` deletes
  (`DELETE FROM firma WHERE id NOT IN (SELECT firma_id FROM run_firma)`), so the next
  `ceidg-tool wyczysc` would have destroyed the whole night's work

### Why nothing detected it

`client.fetch_details` held the program's only case-tolerant comparison
(`rid.upper() not in got`), and it sat precisely where a mismatch would otherwise have been
reported: `missing` came back empty, so no error, no warning, no red test. The five durable
comparisons were all strict.

The offline suite could not see it either, and that is the sharper half. Every fixture is a
production sample passed through `scripts/anonymize_samples.py`, and the anonymiser
**uppercased every identifier** (`key = real.upper()`, `…upper()`). So `tests/fixtures/zmiana.json`
carried a spelling `/zmiana` has never returned, and 768 offline tests agreed about a world
that does not exist. The doubles agreed too: the `/firma` stub echoed back the identifier it
was asked for, which is the one thing the register does not do.

This is the third time the project has been misled by its own evidence — after the `rokPkd`
line in `conftest.py` and `tests/support.py` building its own `httpx.Client`. The pattern is
worth naming: **a test seam that suppresses the property it exists to expose.**

## The invariant

> A `firma.id` is a value, not a string. Two records denote the same entry iff their
> identifiers are equal **after canonicalisation**; canonicalisation is defined in exactly
> one place, applied at ingress, and every persisted and in-memory comparison operates on
> canonical values only.

## Decision 1 — canonical form: UPPER, and only for hex GUIDs

Uppercase, because it is the spelling the register itself emits on both data-bearing
endpoints — so the tool only ever *sends* a form the API demonstrably produces, and no
production request was needed to justify the choice. It also rewrites the fewest existing
rows (15 550 rather than 16 310) and turns the lenient comparison in `fetch_details` into an
exact one.

Rejected: lowercase (matches RFC 4122 and `_public_link`, but `_public_link` lowercases at the
point of use anyway, so it buys nothing and rewrites more rows); `COLLATE NOCASE` alone
(SQLite cannot alter a column's collation, so it costs the same rebuild while fixing only the
SQL half — the four Python-side comparisons would stay strict); a generated column preserving
the raw spelling (`list_json`/`detail_json` already preserve the payload verbatim, so it adds a
second identity concept for no gain).

**The guard matters more than the case.** `recordid.GUID_WPISU` requires
`[0-9A-Fa-f]` — hex only. Report identifiers from `/raporty` share the 8-4-4-4-12 shape but are
not hex and *are* case-significant, because they go straight into the archive download URL;
report-row identifiers are `NIP:…`, `REGON:…`, `HASH:<lowercase hex>`. Relaxing the pattern to
`[0-9A-Za-z]` would break report downloads and give every hash-keyed report row a second
identity — the same defect on the other source.

## Decision 2 — enforced by the type system, not by another scan

`ceidg_tool/recordid.py` is a pure module owning `KanonicznyId = NewType("KanonicznyId", str)`,
`kanoniczny_id()` and `kanoniczne_id()`. `client._records_of` canonicalises on ingress — it is
the single funnel through which records from all four endpoints pass. `client.fetch_details`
and the four `store` entry points take `Sequence[KanonicznyId]`.

mypy strict already runs over `ceidg_tool` **and** `tests`, so "did someone forget to
normalise?" became a compile-time question at zero runtime cost and with no new AST scan to
maintain. It proved itself immediately: adding the annotations turned up exactly the two
call sites in `pipeline` that needed conversion, and three more in the tests.

`store._record_id` and `store.pending_detail_ids` canonicalise again, on write and on read.
That is deliberate rather than redundant: the database is a boundary like the API, rows
written before the migration may carry the old spelling, and an idempotent function costs
nothing to call twice — unlike skipping it once.

Recorded as **boundary rule 14** in `docs/design/phase2_core.md`.

Rejected: normalising inside `store` on every value in and out (forgiving, but it puts the
invariant out of sight of `pipeline`'s in-memory sets, which is where this defect family
lives); a runtime assertion (it would abort a three-hour unattended run at hour two over a
spelling — the wrong failure mode for this tool).

## Decision 3 — schema v3 merges the doubled rows

`store._upgrade_to_3` rebuilds the identity of every affected entry inside `_migrate`'s single
transaction, so a crash leaves the pre-migration state intact and no manual backup is required.
It **merges** rather than deletes: the two rows are disjoint by construction — the husk carries
nothing but an identifier — so first-non-null loses no field, and where two detail payloads
exist the fresher `detail_utc` wins. The detail block (`detail_json`, `detail_utc`,
`detail_state`) is taken whole from one row, because those three must describe the same fetch.

`run_firma` is re-pointed **before** the old rows are deleted: `firma_id` carries
`ON DELETE CASCADE`, so deleting first would take the run links with it. Re-pointing uses
`INSERT OR IGNORE` because one run may have collected both spellings, which the
`(run_id, firma_id)` primary key would otherwise reject.

`Store.merged_duplicates` reports the count through the `Deps.warnings` seam that
`store.quarantined` already uses, so the operator is told once rather than finding a changed
database.

Rejected: fixing forward only (past `zmiana` runs stay unexportable and 15 550 rows bought with
~2 h 47 min of request time vanish at the next `wyczysc`); a separate repair command (it leaves
a "you must run X first" state in which every result is quietly wrong).

Nothing about resume breaks: `criteria.fingerprint()`, `run.criteria_hash`, `profile_hash` and
`checkpoint` are all independent of `firma.id`, and no other table references it.

## Decision 4 — close the evidence gap with two different mechanisms

They answer different questions, which is why one is not enough.

- **The anonymiser preserves the case shape.** `guid()` keeps the uppercase mapping key (one
  real entry, one fake identifier, whichever endpoint returned it) but emits the fake in the
  *occurrence's* spelling. `/raporty` identifiers are now generated in the production shape —
  8-4-4-4-12 with a non-hex character in every group — instead of `RAPORT-000`, so a test can
  show that canonicalisation leaves them alone. Regenerating the fixtures changed only
  `zmiana.json` and `raporty.json`; every `firmy`/`firma` fixture came out byte-identical.
- **The claim is stored apart from the sample.** `tests/fixtures/api_traits.yaml` states the
  measured per-endpoint properties in words. `tests/test_api_traits.py` asserts the fixtures
  conform (in CI), asserts that canonicalisation touches hex GUIDs and nothing else, keeps a
  detector against re-flattening (the traits must still describe *both* spellings), and audits
  the claim against `probe_out/` when the raw samples are present — skipping honestly when they
  are not, the way `load_pkd` does. The fixture can no longer be regenerated into agreement
  with itself.
- **The doubles stop agreeing with themselves.** `tests/support.registry_id` is the one line
  that makes a `/firma` stub behave like the register: it returns the identifier the register
  would return, not the one it was asked for.

## Consequences

- `aktualizuj` fills the cache it was always supposed to fill; a second run over the same
  window costs no detail requests instead of all of them.
- Records of past `zmiana` runs become exportable, because the run's rows now carry data.
- The operator's database drops from 31 860 rows to 16 310 entries with no field lost.
- One more property of the API is written down rather than assumed. `docs/decisions.md` had
  recorded that *query parameters* are case-insensitive and said nothing about *returned*
  identifiers — half of a question, answered as if it were the whole of it.
