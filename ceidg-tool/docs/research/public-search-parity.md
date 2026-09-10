# Parity with the public CEIDG search form

Date: 2026-09-10
Status: accepted
Author: P0w3r223
Related to: docs/api_notes.md, docs/decisions.md, ADR-0016 (report row identity), ADR-0018
            (one matching semantics)

---

## The question

Can this tool run every kind of search the public CEIDG form at
`https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/search.aspx` offers?

**No, and most of the gap is not ours to close.** The form drives the register's internal search
engine; we drive `GET /firmy`, whose parameter list is closed. Of what the API *does* expose we
now send everything — four parameters were missing on 2026-09-10 and were added the same day.
Everything else in the gap is absent from the API, not from the tool.

## How the form was established

The page itself could not be fetched: `WebFetch` and `curl` both got **HTTP 403** from the WAF —
the same brittleness `INSTRUKCJA_CLAUDE_CODE.md` names when it rules out scraping. The field list
therefore comes from the vendor's own manual,
[Dokumentacja powykonawcza ST CEIDG](https://pliki.biznes.gov.pl/akademia/Instrukcje/Dokumentacja%20wsp%C3%B3lna%20dla%20wszystkich%20u%C5%BCytkownik%C3%B3w%20wewn%C4%99trznych%20CEIDG.pdf),
§3.1 (browsing without logging in) and §3.4 (advanced search) — read, not remembered. This matters
for one line below: §3.4 is documented for **logged-in** users and carries PESEL, parents' given
names and date of birth, so whether the public page exposes a trimmed version of it is **not
established here**.

The API side comes from `docs/api_notes.md` (the integrator documentation) and from the measured
answers in `docs/decisions.md`.

## Field by field

| Form criterion | API v3 `/firmy` | This tool |
|---|---|---|
| NIP, REGON | yes | yes (OR-ed, up to 25 per request — measured) |
| NIP / REGON of the civil partnership | yes (`nip_sc`, `regon_sc`) | **yes, since 2026-09-10** |
| KRS number | no | no |
| Company name | yes — fragment, case-insensitive (measured) | yes |
| Given name, surname | yes | yes (query file / assistant; no CLI flag) |
| PKD | yes | yes, plus PKD 2007 predecessors (ADR-0012) |
| Voivodeship, county, commune, town, street | yes | yes |
| Building number, flat number | yes (`budynek`, `lokal`) | **yes, since 2026-09-10** |
| Postal code | yes | yes — the form's *basic* section has no such field |
| Address type (main / correspondence / additional) | no | no |
| "Include struck-off entries" | yes, via `status=WYKRESLONY` | yes |
| "Include entries pending verification" | no | no |
| Start date (range) | yes | yes |
| End / suspension / resumption dates | no | no — present in `/firma`, not filterable |
| Short name, accounting-records type | no | no |
| PESEL, second given name, parents' names, maiden name, date of birth | no | no |
| Citizenship | no | no (present in `/firma`) |
| Sheltered workshop, foreign small-manufacturing enterprise | no | no |
| Submitting office, commune TERYT, communal-register entry number | no | no |
| Statuses "Nie rozpoczął działalności", "Przeniesiony niezgodnie z Ustawą" | no (the API knows five) | no |

## What the tool does that the form does not

- **Several values of one field at once (OR)**: multiple towns, PKD codes, NIPs, statuses in one
  request — each measured separately, because the text-field family is not uniform.
- **A start-date range.** The form's basic section has no date field at all.
- **Volume**: 20 results per page in a browser against a whole population into a workbook, the
  daily-report path, and `aktualizuj` driven by `/zmiana`.

## The four parameters added on 2026-09-10

`nip_sc`, `regon_sc`, `budynek`, `lokal` — the API accepted them all along; `Criteria` did not
carry them. Four decisions were made while adding them, and each has a reason worth keeping:

1. **`budynek` and `lokal` are text, not integers.** The register stores "12A", "3/5", "18 m. 2";
   an `int` would reject each of them or, worse, truncate to the leading digits.
2. **Exact matching for both numbers**, in `reports.matches_criteria` and in the demo register
   alike. The manual says to enter the "full building number", so exact agrees with the only
   description that exists — and a fragment would match "12" against "112" and "12A" at once,
   which stops the address filter from narrowing anything. This is a **choice, not a measurement**,
   and it is recorded as such in both docstrings (ADR-0018's table gains two rows).
3. **The report path refuses `nip_sc` / `regon_sc`.** Measured 2026-09-10 on the header of
   `probe_out/raport_sample.zip`: the daily archive has 24 columns and **none about a civil
   partnership**. A filter without a column does not narrow the result — it empties it, silently,
   on the path `--zrodlo auto` prefers because it is four orders of magnitude cheaper. That is the
   A10 defect shape, so `report_covers` declines, `_powod_braku_raportu` gained a fifth reason
   (`FILTR_SPOZA_RAPORTU`), and `pipeline.run_report_fetch` refuses a second time — the wizard is
   not the only caller.
4. **Empty new fields stay out of `canonical_json`.** The fingerprint is the key to resuming an
   interrupted run; a field that entered the digest while empty would have made every pre-update
   run unfindable. `pkd_2007` already had this treatment (ADR-0012); the list is now five names
   long and a parametrised test walks it, so the sixth field fails loudly rather than quietly.

## CLI flags, added the same day

The first version of this document said the new four would travel by query file, "like `--imie`,
`--nazwisko`, `--ulica` and `--kod`, which do not exist either". The owner's answer was to give all
of them flags instead, and that is the better call: a filter reachable only through a YAML file is
a filter the operator cannot discover, because `--help` never mentions it.

Eight flags added — `--imie`, `--nazwisko`, `--ulica`, `--kod`, `--budynek`, `--lokal`, `--nip-sc`,
`--regon-sc` — so every filtering field of `Criteria` now has one. Two things came with them:

- **A guard, not a habit.** `test_kazde_pole_kryteriow_ma_flage_w_wierszu_polecen` compares the
  field list of `Criteria` against the parameter list of `pobierz`, with a named exception list
  carrying a reason per entry (`--pkd-2007` is a three-state switch, `data_od` is `--od`, and so
  on). The next field added without a flag turns the suite red instead of quietly repeating this.
- **Fields now travel as a dict, not as positional arguments.** `_criteria_from_options` used to
  take nine same-typed lists in a row; test call sites read `plik, [], [], [], [], [], [], [], [],
  [], None, None, False, None`, where swapping two neighbours is invisible to a reader and to mypy.
  At seventeen fields that was a matter of time.

One thing fixed in passing, because eight new flags are eight new ways to mistype a value: the CLI
was the last of the four entries into `Criteria` still answering with a raw pydantic dump
(`[type=value_error, input_value=…]`, plus a link to errors.pydantic.dev). It now uses
`bledy_po_polsku`, like the wizard and the assistant — `--kod 15333` answers
*„kod: kod pocztowy '15333' musi mieć postać 15-333"*.

The wizard form still asks its eight questions, ordered commonest to rarest, and gains none of the
new fields — there the cost is the operator's attention, not discoverability.

## Two caveats that outlive this comparison

**Matching semantics differ between the two engines.** The manual says the form matches a fragment
for "Company name" and the beginning of the name for "Street". On our side only `nazwa` and
`miasto` are *measured* as fragments; `powiat`, `gmina`, `ulica`, `imie` and `nazwisko` stay on
exact comparison because two production requests came back inconclusive (ADR-0018). The manual is
a hint about a different engine, not a measurement of ours.

**PKD vintage.** The form's advanced section names a **PKD 2007** code list; we send 2025 codes
with 2007 predecessors attached. 8.6 % of the measured sample is reachable by no code in
`pkd2025.yaml` at all. Any "the form showed X, the tool showed Y" comparison will hit this first.
