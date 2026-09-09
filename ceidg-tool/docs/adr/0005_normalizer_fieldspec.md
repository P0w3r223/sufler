# ADR-0005: One-to-many normalisation via declarative `FieldSpec` and `id`-linked sheets

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: ADR-0004, INSTRUKCJA_CLAUDE_CODE.md (workbook specification)
Amended by: ADR-0007 (the row-limit consequence below: the exporter splits, it does not refuse)

---

## Context

The instruction mandates sheets `Firmy`, `PKD`, `Spolki`, `Slownik`, `Metadane`,
`snake_case` headers, `nip`/`regon`/`kod_pocztowy`/`terc`/`simc` as text, real Excel
dates, provenance columns in every row and a Polish description per column. The API
has 9 one-to-many relations and a record can carry many PKD codes (the documented
example has 4).

## Options

**A. One wide sheet with repeated column groups (`pkd_1_kod`, `pkd_2_kod`, ...).**
Rejected: unbounded cardinality, PKD filtering breaks, the instruction requires
separate tables anyway.

**B. Separate sheets linked by `id`, columns defined imperatively (a function per sheet).**
Simplest and easy to test per sheet. But `Slownik` is maintained by hand next to the
columns, so descriptions and columns drift within weeks, and "NIP as text" must be
remembered per column. Effort S, risk medium.

**C. Separate sheets plus a declarative `FieldSpec(name, extractor, kind, opis)` list per sheet.**
One place defines column order, Excel type and Polish description, so `Slownik` is
generated; one test checks that every `kind="text"` column lands as text; adding a
column after the probe is one data row. A small mini framework; complex values need
a `Callable` escape hatch. Effort M, risk low.

**D. Long format (id, field, value).**
Rejected: unusable for a human in Excel, and the human is the primary consumer.

## Decision

**C**, with `extractor` being either a dotted path (`"adresDzialalnosci.miasto"`) or
`Callable[[Mapping[str, Any]], Any]` for computed values (`pkd_wszystkie` joined
with `;`, full address). Deciding argument: "NIP as text" and "Slownik" are promises
that the imperative approach breaks silently with every new column.

```python
@dataclass(frozen=True)
class FieldSpec:
    name: str                                    # snake_case, ASCII only
    extractor: str | Callable[[Mapping[str, Any]], Any]
    kind: Literal["text", "date", "int", "bool"]
    opis: str                                    # Polish description for the Slownik sheet

SHEETS: Mapping[str, tuple[FieldSpec, ...]]      # Firmy, PKD, Spolki, Adresy
def normalize(raw: RawRecord, ctx: RowContext) -> NormalizedRecord: ...
def slownik_rows() -> list[tuple[str, str, str]]  # sheet, column, description
```

Details:

- Source merge: `merged = list_json | detail_json` (detail overrides, list fills
  gaps). Records without details are legal (list mode) and get a
  `dane_szczegolowe` boolean column; without it an empty `pkd_glowny_kod` reads as
  "no PKD", which is false.
- Provenance from context, not globals: `RowContext(zrodlo, srodowisko,
  pobrano_utc)` is passed explicitly, keeping the normaliser pure and `Metadane` testable.
- Aliases (`spolki`/`spolka`, `data-utworzenia`/`dataUtworzenia`, `pkdGlowny`
  present/absent) are handled in extractors, not in the profile. They are data
  inconsistencies, not protocol dialect.
- Phase-2 scope: sheets `Firmy`, `PKD`, `Spolki`, `Adresy`. The remaining five
  relations (`zakazy`, `upadlosc`, `zarzadcaSukcesyjny`, `uprawnienia`,
  `kwalifikacjeZawodowe`) stay in raw JSON; adding them later is new `FieldSpec`s
  with zero requests.
- A data sheet is written only when it has rows (`Spolki` "when present"), but
  `Firmy`, `Slownik`, `Metadane` always. Excel rejects a ListObject over an empty
  range, so this must be guarded explicitly.

## Consequences

- Column order is deterministic and `run_firma.position` fixes row order, so two
  exports of the same run produce identical files.
- Excel's row limit is 1 048 576. A whole voivodeship (about 300 k firms) exceeds it
  on the `PKD` sheet. The exporter counts rows before writing and **splits into parts**
  (`_czesc01`, `_czesc02`, …), each with the full sheet set and a `czesc i/N` entry in
  `Metadane` — **amended by ADR-0007**; this ADR said "refuses with a message pointing to
  CSV/JSONL" until 2026-09-09, which no longer describes `exporter.plan_export()`. Silent
  row loss would be the worst possible behaviour, and neither option commits it.
- Large sets use `openpyxl` in `write_only=True` mode, streaming chunks from SQLite;
  the table range is known from `SELECT COUNT(*)`, so ListObjects still work. The
  switch-over threshold is configuration, not a constant.
