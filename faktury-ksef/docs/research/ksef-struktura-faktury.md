# KSeF: the structured invoice and the traps in reading it

Date: 2026-09-11
Status: draft — first reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: `ksef-dostep-i-obowiazek.md`, `ksef-kontrola-faktury-kosztowej.md`

---

## Versions

FA(1) until 2023-08-31, FA(2) until 2026-01-31, **FA(3) from 2026-02-01 for every structured invoice
issued**.

**Version detection has a trap, confirmed in the second pass by reading all three schemas: the schema
version field carries the identical value in all three.** Discriminate by the root namespace, the
system code or the variant number — never by the version field. That this is a real trap rather than
a theoretical one is visible in the ministry's own client, where an issue records a session declaring
one version while a document of another was sent.

One consequence reaches further than it looks: **FA(3) is also used to correct invoices originally
issued under FA(1) and FA(2)**, and to settle advances invoiced under the older schemas. One schema
on input, but the semantics of references reach back into documents shaped differently.

## What changed between versions — second pass

**Between the last two versions the paths to line items, amounts and party data are identical.** That
is the good news for a parser, and it means one code path can read both.

Four changes matter:

| Change | Character |
|---|---|
| The attachment element was added | new element |
| Mandatory fields for local-government units and VAT groups in the buyer section | new required fields |
| Payment link and a collective-identifier field added to the payment section | new elements |
| **The payment-term description changed from a simple value to a complex type** | **same name, different content model** |
| The reference to a corrected invoice went from single to repeating, up to 50 000 | cardinality |

The fourth is the dangerous one, and it is the same shape as the income-statement letter collision in
the financial-statement project: **a field keeps its name and stops meaning what it meant.** A reader
written against the older version will not fail — it will read a structure as a value.

**The step before that was a genuine rebuild, not cosmetics.** The line-item wrapper was removed, so
the path to positions differs; the annotations branch went from flat to nested; contact data was
restructured; payment fields were renamed with a name collision, where a term now means something
different from what it meant before; and amount fields were split.

Since the current version is also used to correct invoices originally issued under the older ones,
**code reading only the current schema still has to understand the older correction semantics**, even
though the syntax it sees is current.

## Three levels of optionality where the schema shows two

The ministry distinguishes **obligatory**, **conditional** (mandatory if a statutory condition is
met) and **voluntary** fields. The schema expresses only present-or-absent, and the documentation
warns that conditional and voluntary elements look identical on its diagrams — the category has to
be inferred from the law.

For the data model this means a missing value needs two states: **absent and allowed** versus
**absent and suspicious**. Validating presence against the schema does not capture business
requirement.

Obligation is also inherited: a section may be voluntary while a field inside it becomes mandatory
once the section is used at all.

## Amounts, currencies, rates

- Decimal separator is a dot; no thousands separators; negatives carry a minus.
- **Precision is not uniform.** Most fields carry two decimal places, several carry **six**
  (exchange rates, shares, some tax bases) and several carry **eight**. The numeric type must have a
  scale of at least eight — never a fixed two, never a float.
- **The VAT rate field is a text enumeration, not a number.** Values include `23`, `8`, `0 KR`,
  `0 WDT`, `0 EX`, `zw`, `oo`, `np I`, `np II` — with spaces. Casting to a number fails on a large
  share of real traffic.
- The currency code is mandatory even for the domestic currency. On a foreign-currency invoice the
  net and tax amounts are in the invoice currency and **only the converted tax fields are in zloty**.
- **An exchange rate can appear at four levels**, including per line item and a separate rate for the
  state before a correction. One invoice may carry several rates.

## The total is not the amount to pay

Amounts that are not consideration — re-invoiced costs, compensation — are excluded from the invoice
total. A separate, voluntary settlement section carries charges, deductions and the amount actually
payable.

**Cost analytics built on the invoice total will diverge systematically from the bank transfer.**
This is not an edge case; it is the designed behaviour.

## Corrections — the least determinate area of the schema

The base rule mixes two representations in one document: all fields are filled **as they stand after
the correction**, except tax bases, tax and the total, which are filled **as a difference**.

A second correction to the same original computes its difference against the state after the previous
correction, while still pointing at the **original** invoice. The schema offers no "previous
correction" pointer, so reconstructing a chain requires our own chronological work.

**Three methods of correcting line items are legal and none is mandatory:** by difference; with a
before/after pair where the "before" row counts toward totals with the opposite sign; and by
reversing the original row with a negative quantity or price. Integrators report having to guess
which one an issuer used. The request to standardise is open and unanswered.

The parser therefore has to detect the method heuristically — presence of the before/after marker,
signs, row counts against the referenced originals — and **flag ambiguous documents for review**
rather than pick one reading.

Naming traps that are not typos but genuinely different fields: the number of the corrected invoice;
the *corrected* number where the error was the number itself; a flag meaning "the original was in the
system", which is not a number at all; and the actual system number of the corrected invoice.

Scale: one collective correction may reference **up to 50 000** original invoices, mixing ones inside
and outside the system. The correction type and reason are **voluntary**, so period assignment cannot
rest on them. A wrong buyer tax number is **not corrected** — it requires a correction to zero and a
new invoice.

## Advance, settlement and simplified invoices

Seven invoice kinds exist. Three shapes will break a naive reader:

- **An advance invoice legally has no line items** — the order section carries the detail instead.
- **On a settlement invoice the totals refer only to the remaining amount**, while the line items
  carry the full transaction. Lines not summing to totals is correct here.
- A **final invoice of zero value with non-zero line items** is a normal document.

A simplified invoice has **two alternative legal fillings**, and a margin-scheme invoice carries no
rate breakdown at all.

## The attachment is not a file

The attachment element is **a spreadsheet embedded in the same XML**: blocks of data, key-value
metadata, paragraphs, and tables with issuer-declared column types and headers, up to a thousand rows
each.

It is simultaneously the richest source of cost detail — utility and telecom settlements per
metering point — and the least normalised place in the schema. Without a per-issuer mapping it cannot
be analysed automatically.

Barriers keep it rare: issuing invoices with attachments requires a prior notification through the
tax e-office, works only in batch sessions, and abuse for commercial content costs the right to use
it. Expect attachments to be concentrated among large utility issuers.

## Lines and descriptive data

- The line element is **optional and may repeat up to 10 000 times**, and the item name is itself
  optional — legally empty on some collective corrections.
- The row number is abused in production: systems write their own order numbers there, sometimes very
  large. **Treat it as a string.**
- A voluntary unique row identifier exists and is the only reliable way to bind a correction row to
  an original row. Its use is explicitly optional.
- Goods-and-services group codes and procedure markers are **voluntary and sit at line level**. They
  are not usable as an analytical filter.
- A free key-value bag allows **up to 10 000 pairs** with issuer-defined key names and no dictionary
  at all. The ministry's own examples are "meter number", "supply point address", "number of messages
  sent". This will be the main source of noise in an analytical model and simultaneously the place
  where the detail somebody wants actually sits.
- Annotation flags use **"1" for yes and "2" for no**. This is not a boolean and empty does not mean
  no; separate negative fields exist alongside.

## Things that are not in the invoice but end up in the model

- **The system number of the invoice is not a field of the invoice.** It comes from the interface
  layer. The model must join document and metadata — the XML alone does not identify itself.
- **The issue date is contextual.** Online, the date of transmission counts; in offline modes the
  date on the document does. The mode is declared at the interface, not in the XML.
- A technical re-send of a rejected offline invoice produces documents of identical content — another
  source of apparent duplicates.
- Deduplication cannot rest on the invoice number: the key is seller number, invoice number **and
  invoice kind**. A regular and a settlement invoice with identical numbers have both passed.
- The system stores invoices for ten years from the end of the year of issue, then they are gone.

## What issuers actually do

- **A buyer tax number written into the wrong field means the invoice never reaches the buyer's
  stream.** No error is raised. Missing invoices appear as silence.
- Invoices with disallowed characters were announced as rejected from mid-2026; a report says they
  still are not. Expect them in the stream. Encoding must be UTF-8 without a byte-order mark.
- **The system does not check arithmetic consistency.** The ministry prepared a first business
  validation for foreign-currency invoices and **withdrew it for analysis**, noting in passing that
  no production invoice currently violates it. That is the only concrete public statement about the
  contents of the production set found anywhere.
- Nominal limits are not the effective ones: a collective invoice with around two thousand lines and
  no attachment exceeded the 1 MB ceiling.
- Extra nodes in the XML header, which the system does not reject, break previews elsewhere. Parse
  tolerantly.
- Mass export sometimes hangs at an intermediate status; retries with backoff and idempotency are
  required.

## Open items

- No official list of differences between FA(2) and FA(3) was found; three changes are confirmed.
  A direct comparison of the two schemas is the way to settle it.
- Cardinalities here come from the ministry's brochure and handbook, **not from reading the schema**
  — the file was too large for the tool. Confirm locally before implementing.
- No quantitative data exists on which voluntary fields are actually filled. Only measurement on our
  own corpus will answer it.
- The separate schema for flat-rate farmer invoices was not examined.
- Several behaviours are moving targets: the withdrawn currency validation, the unenforced character
  rejection, and a planned facility for a wrongly-addressed buyer to hide an invoice.
