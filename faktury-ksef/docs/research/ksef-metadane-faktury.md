# Invoice metadata: the cheap path, and exactly where it stops

Date: 2026-09-11
Status: draft — second reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: `ksef-dostep-i-obowiazek.md`, `ksef-struktura-faktury.md`

---

## Why this document exists

Two questions decide the shape of the data model: **can the source filter purchase invoices for us**,
and **how much analysis is possible without fetching and parsing the full document**. The first pass
left both open — one of them with two of our own agents contradicting each other. Both are settled
here.

## Settled: the source filters purchase invoices for us

A query filter names the **role the authenticated entity plays on the invoice**, with four values:
seller, buyer, third party, authorised party. The second value is our case.

The proof is stronger than documentation: the ministry's own end-to-end test authenticates as the
buyer, queries metadata with that value and asserts the invoice is there. Its comment reads
"purchase metadata". The same filter model is used by the asynchronous export, where naming the
subject type is **required**.

**A pass-1 claim is therefore withdrawn.** The report that said "the export returns everything in the
date range, with no purchase/sale split" over-generalised from an integrator's issue. That issue —
still open — asks for filtering by the **role of a third party**, which matters for local-government
units and VAT groups and is a genuinely missing feature. It is not the buyer/seller split.

Operationally this matters twice over: we get only what we need, and at twenty requests per hour
shared between metadata queries and exports, not paying for the other three roles has measurable
value.

**Risk moved rather than disappeared** — it now sits in completeness at the edges:

- a counterparty who writes an EU VAT number instead of the domestic one into the buyer section
  means the invoice **does not come back** in the buyer query. Two issues, one still open. Silence,
  not an error.
- third-party scenarios — local-government units, VAT groups — carry documents that escape a
  buyer-only query.
- the query window is capped at three months, with month arithmetic that surprises: a range from
  30 November to 28 February was rejected.
- indexing is not instant; the reference client polls.

## The metadata model, field by field

Read from **two independent official SDKs that agree** — not from prose.

Identifiers and dates: the system number, the issuer's own invoice number, and four timestamps —
issue date, acceptance into the system, number assignment, and permanent storage.

Parties: seller as number and name; buyer as name plus an identifier **whose type may be none or
other**, so the buyer key cannot be modelled as a tax number. Third parties as a list with a numeric
role; an authorised party.

Amounts: net, gross, tax, and a currency code.

Flags and classification: issuing mode, invoice kind, form code, self-invoicing, presence of an
attachment, the document hash, and a hash of a corrected invoice.

Envelope: a more-results flag, a truncation flag at the ten-thousand ceiling, and the high-water
mark. The total count was **removed**, so pagination cannot be planned from a count.

## Three traps, each with a concrete case behind it

**The tax amount is always in the domestic currency while net and gross are in the document's
currency.** Confirmed by the ministry in reply to an issue. One record carries **two different
units** — they must never be summed, and a rate must never be derived from them for a
foreign-currency invoice. A practitioner in the same thread: "I already got caught by this when
mapping to internal structures."

**The gross amount is not the amount to pay.** A reported case with deposits: gross 1739.46 against
1815.46 payable. The cheap path returns a number that is formally correct and operationally
misleading. This is the same conclusion `ksef-struktura-faktury.md` reaches from the schema side.

**A correction is recognisable but not linkable.** The invoice kind marks five correcting variants,
so filtering corrections is easy. But the reference to the corrected invoice is **not a metadata
field** — the hash that looks like one belongs to a technical re-send of a rejected offline invoice.
Reconstructing the chain needs the document, or our own matching.

## What metadata cannot answer

| Question | From metadata? |
|---|---|
| Who issued it | yes — number and name only |
| For what | **no** — no line items, descriptions or classifications, ever |
| How much | partly — net and gross yes, payable no |
| In what currency | yes, with the unit trap above |
| When | yes, but **no sale date and no payment term** |
| Is it a correction | yes, without a link to the original |

The missing sale date and payment term are decisive: **period assignment for tax purposes cannot be
done from metadata.** The full document must be parsed always, not only for deeper analysis.

## Operational constraints of the cheap path

Twenty requests per hour, shared with exports, per context and address. A three-month window. A hard
ceiling of ten thousand results with a truncation flag. An export flag exists that returns a package
of metadata without the documents.

**And a known, open pagination defect: duplicate records across pages.** Deduplication by system
number is therefore **mandatory, not an optimisation** — a conclusion the ministry's own incremental
guidance reaches independently.

## Where this is heading

The ministry is consulting on a business-event model, with the stated reason that "synchronising
invoices alone is not sufficient". The proposed events are exactly what cost tooling lacks: invoice
qualification, position markers, payment events, abuse reports, with change history. Horizon: 2027 at
the earliest. Today's metadata model is expected to become that stream's payload.

## Open items

- The interface specification was never read directly — the file was too large for the tools. Field
  names come from two agreeing official clients, but **the required-and-nullable declarations remain
  unverified at source.** Settle before deciding column nullability.
- The meaning of the numeric third-party role is undocumented; take it from the invoice schema.
- Whether the corrected-invoice hash is ever populated for an ordinary correction is an assumption to
  verify with one query on the test environment.
- Maximum page size is stated in one changelog entry and disputed in an issue.

## Seven measurements that close the rest

One run on the test environment, in the buyer's context, settles every remaining doubt: whether an
invoice appears **only** under the buyer filter; whether the export count matches the metadata count;
whether omitting the subject type is an error or a silent default; whether an invoice addressed by EU
VAT number comes back; how many documents escape through the third-party role; how self-invoicing is
seen by the buyer; and how long indexing takes.

This is the move that was impossible on the register project. Here the test environment can fabricate
the entity, its permissions and its invoices.
