# ADR-0016: What identifies a report row the register gave no number to

Date: 2026-09-09
Status: **accepted 2026-09-09 by the owner** (implemented and mutation-checked the same day)
Author: P0w3r223
Related to: ADR-0013 (identity of a record identifier), ADR-0010, docs/audit-2026-09-09.md (item A9)

---

## Context

A row in the daily report archive is keyed `NIP:…`, or `REGON:…` when the NIP is blank, or —
when both are blank — `HASH:<sha256 of every column>`. That last fallback includes `Lp.`, the row
ordinal within the download.

`Lp.` changes whenever the register's ordering changes, which is on every download. So the same
sole trader gets a **new identity in every archive**: the store accumulates duplicates that no
`ON CONFLICT` can merge, `run_firma` links a different row each time, and any comparison between
two downloads reports change where there is none. This is ADR-0013's defect on the other source —
one entry, two spellings — with a different mechanism producing the second spelling.

**Measured 2026-09-09**, zero requests, on `probe_out/raport_sample.zip` (287 256 rows,
wielkopolskie):

| | |
|---|---|
| rows with neither `Nip` nor `Regon` | **315** (0.11 %) |
| distinct under `NazwaPodmiotu` + `Nazwisko` + `Imie` + `DataRozpoczeciaDzialalnosci` | **315 — zero collisions** |
| the same, with the full address added | 315 — zero collisions, i.e. the address adds nothing |
| fill rate of those four fields | **100 % each** |
| fill rate of `KodPocztowy` / `NrBudynku` / `NrLokalu` in those rows | 61 % / 69 % / 23 % |

And on the operator's own store, which decides whether a migration is owed:

| | |
|---|---|
| `firma` rows with `id LIKE 'HASH:%'` | **0** |
| `firma` rows with `zrodlo = 'CEIDG_RAPORT'` | 355, **all** keyed `NIP:` |

## Decision

**Identity over a declared, stable subset of the row: name, surname, given name, start date.**

- The field list is explicit and named in one place, not "everything except `Lp.`". A row's status,
  PKD codes, phone and address all change during the life of a business; an identity that includes
  them mints a new entry on every ordinary update — the same defect as `Lp.`, one level rarer, which
  is what makes "all columns except `Lp.`" the tempting wrong answer.
- The address is **not** included. It adds no discrimination on the measured data and its
  components are 23-69 % filled, so it would trade stability for nothing.
- The construction lives in **`recordid.py`** and returns `KanonicznyId`, so boundary rule 14 covers
  the new producer through mypy at no cost to the AST scan. `store.py` already imports `recordid`
  and must not grow an import edge to `reports`.
- It takes **values**, not a CSV row, and normalises them itself (strip, empty means absent). A
  future migration recomputing identity from `firma.list_json` must reach the same digest as ingest
  did; a helper that parses the CSV row would give the two callers two different notions of "empty",
  because `_drop_none` removes empty fields from the mapped record.

### No migration, and that is measured rather than assumed

The operator's store holds **zero** `HASH:` rows, so there is nothing to re-key and no schema v4.
This is a forward-looking fix. The check is one query and belongs in the ADR because "no migration
needed" is the kind of claim that rots: re-run it before shipping this to a store other than the
one measured here.

### The collapse gets an observer

Two different sole traders sharing name, surname, given name **and** start date would now collapse
into one entry. Zero such pairs exist in the measured archive, but zero measured once is not zero
forever, and a silent collapse is precisely the failure this project keeps finding.

`run_report_fetch` already counts `duplicates` and reports them through `on_message`, so they reach
the log and not only the screen. Today that counter cannot fire for identity-keyed rows. After this
change it can, and its message distinguishes the two cases: a repeated `NIP:`/`REGON:` is the
register listing one entry twice, which is ordinary; a repeated content key is **an inference of
ours** that may be wrong, and says so.

## Alternatives rejected

| | Why not |
|---|---|
| **Every column except `Lp.`** | Stable only across a re-download of an unchanged row. A status change, a new phone number or a PKD edit mints a new identity — the same defect, one level rarer, and harder to notice because it needs a real-world change to trigger. |
| **`RAPORT:<report id>:<Lp.>`** — honest "we cannot identify this row" | Duplicates the entry on every download by construction and double-counts it in a multi-run export. It replaces a silent wrong answer with a loud wrong answer. |
| **Drop rows with no NIP and no REGON** | Loses 0.11 % of rows silently. Tier A is about loss of data; this is loss of data. |
| **Include the address to reduce collision risk** | Measured: adds zero discrimination, and its fields are 23-69 % filled, so it would make identity depend on how completely someone filled a form. |

## Consequences

- A report row without NIP or REGON keeps its identity across downloads. On the measured archive
  that is 315 rows per download that stop multiplying.
- The digest changes for any `HASH:` row already stored. Measured exposure on the operator's store:
  zero rows, so no migration ships with this ADR. A store that *does* hold such rows would need one
  before upgrading — and `recordid` taking values rather than a CSV row is what makes writing it
  cheap later.
- Two entries that genuinely share all four fields collapse into one and say so, rather than
  collapsing quietly. Measured frequency on 287 256 rows: zero.
- `HASH:` stays the prefix and stays outside `GUID_WPISU`, so ADR-0013's canonicalisation still does
  not touch these identifiers. What changed is what goes into the digest, not what the digest is.
