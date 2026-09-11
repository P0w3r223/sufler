# What actually went wrong in the first months of mandatory e-invoicing

Date: 2026-09-11
Status: draft — second reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: all four documents of the first pass

---

## Why this document exists

Every agent in the first pass worked without a search engine. The material was excellent — statute,
ministry documentation, live probes — and entirely missing the layer that says what breaks in the
field. This closes that gap for February to September 2026.

## The outage everyone remembers was not the invoicing engine

The February failure was **the authentication layer**. The national trusted-profile service stopped
serving unrelated state services because invoicing traffic overloaded it; users saw "you have reached
your query limit". The minister later spoke of a distributed attack and apologised. Invoice
acceptance kept working throughout.

And the counterintuitive result: **the much larger wave went far more smoothly.** February onboarded
around 320 thousand firms with hours-long failures; April onboarded **2.7 million** and the system
took over three million invoices overnight, with 55 outage reports against thousands in February.
Stabilisation is dated to the second half of April.

The lesson for us is not about capacity. It is that **the fragile part was the login path**, which is
precisely the part our harvester depends on and cannot retry its way out of.

## Production behaviour the documentation did not describe

**A defect that silently lost data.** An export returned ten thousand and one invoice headers in a
single package while declaring it had not truncated — **the system did not know it had cut the
result**. A client trusting that flag lost invoices. Fixed in June, four months after the obligation
started.

The design consequence is permanent regardless of the fix: **never trust a single completeness flag.**
Cross-check the count, and deduplicate regardless.

**Limits bit earlier than documented** — refusal after 32 single-invoice fetches against a documented
64 per hour, after hours of inactivity. **The test environment did not mirror production limits until
May**, three months into the obligation.

**The protective layer returns HTML where the client expects JSON**, and error responses do not always
conform to the specified error format. Timeouts and gateway errors were still being reported in
August, inside the "stable" period. The HTTP client must survive a non-JSON, non-conforming error on
every call.

**The ministry's own incremental guidance lists traps it cannot remove**: no ordering guarantee across
windows, windows that must abut or documents fall between them, and duplicates possible even with a
correct implementation. Deduplication is structural, not defensive.

## Receiving cost invoices: where the surprises actually are

**Nothing notifies anyone.** There is no push. Payment and settlement deadlines run whether or not
anybody looked. Polling is entirely the receiver's problem — by design, not by omission.

**Ghost invoices.** Anything issued to your tax number lands in your account and cannot be rejected.
They look exactly like real ones, because every invoice has the same format and the same
visualisation. Previously invoice spam was filtered at the mailbox; now it arrives through a channel
the company treats as trusted. Available remedies are an abuse report and a planned facility to hide
a document from search results — which does not delete it. The ministry's position is that a taxpayer
**need do nothing**: the appearance of an invoice has no tax effect, booking it does.

**Detecting ghosts is a real feature with real value**, because the trust layer does not exist in the
system: unknown counterparty, absent from the taxpayer register, an imposed short payment term, no
prior trading relationship.

**The system is not a complete cost repository, and will not be for years.** Outside it: sellers below
the monthly threshold, VAT-exempt sellers with receipts, foreign counterparties. A concrete,
separately reported case: **missing fuel invoices**, because some sellers use the exemptions.
Companies must actively chase what is absent.

**So the tool must be able to point at a gap** — an invoice we expect and do not have — rather than
only process what arrived. On the register project this shape appeared as "missing documents show up
as silence"; here it recurs at the level of whole cost categories.

**Duplicates between e-mail and the system are the most-described accounting problem**: purchasing
registers the invoice from an e-mail, accounting pulls the same one from the system, and it is booked
twice, sometimes paid twice. The repeated recommendation is one source of truth, identified by the
system number.

**The document carries nothing for routing.** In a multi-branch company an invoice arrives at head
office and nobody knows which branch, person or cost account it belongs to; documents circulate for
weeks awaiting approval. The advice given by advisers is to build one's own categorisation rules,
because the system will not supply them.

**That is this product's core, not its garnish.**

Two edge cases nobody designed for: **employee expenses**, where the supplier issues only into the
system and the employee receives no document at all; and **goods receipt**, where stock arrives, the
invoice is in the system and the warehouse has no access, so receipt blocks.

**Four dates get confused in practice.** For a domestic buyer the receipt date is the date the number
was assigned — regardless of when the document was actually fetched. The month-boundary trap follows:
an invoice issued on the last day of a month can be "received" in the next one.

## Data quality is worse than the documentation assumes

From a single source, so directional rather than quotable: **18%** of correcting invoices contain
errors; **58.6%** of issuers bind correction lines by row number and only **0.5%** by the systemic
identifier, with around 7% binding nothing; roughly **10%** of documents have incomplete content —
3.5% without a unit price, 2.5% without a line total.

Two consequences survive even if the numbers are wrong by half. **Matching a correction to its
original must work on the row number**, because the reliable mechanism is effectively unused. And
**the parser must tolerate a missing unit price and a missing line total** rather than treating them
as corruption.

## Legal findings to keep behind configuration, not in code

**An invoice received outside the system does not lose the right to deduct.** A consistent line of
tax rulings, the most recent from September 2026: the statutory list of exclusions does not cover
issuance outside the system, and the right arises on actual receipt. Later transmission changes the
record marker, not the moment of deduction.

**One dispute is open.** Three court judgments of September 2026 rejected the authority's position on
deduction timing in one of the offline modes, calling it unjustified differentiation. They are **not
final** and no case references were obtained. So deduction timing belongs behind a configurable rule.

**Eliminating the buyer's own correcting note had the largest side effects.** Every formal slip now
requires a correcting invoice from the seller. Reported consequences: a sharp rise in corrections,
counterparties withholding payment until discrepancies are resolved, settlement cycles lengthening by
days and weeks.

**If the tool ever renders a PDF, be careful.** A ruling held that a PDF differing from the document
may count as a **separate invoice**, with the tax payable a second time on the same transaction. The
interpretive line here first hardened and then softened — it is the least stable legal area found.

## Things that were feared and did not happen

The capacity collapse; chaos among micro-businesses in April; a data breach during the outage; and
the loss of deduction for invoices received outside the system. Penalties do not apply through 2026
and start in 2027 — with a side effect noted by practitioners: the absence of pressure weakens the
motivation to tidy up procedures.

## Open items

- Several findings came only from search-result summaries rather than full articles, including the
  February attack and the volume statistics.
- The September court judgments were not verified — no references, no finality check. This is
  load-bearing for date logic and must be checked before any rule ships.
- The data-quality percentages have one source and unknown provenance. Do not quote them in project
  documentation without reaching the underlying report.
- Several issues were closed without a visible public answer, so "closed" does not mean "cause
  explained".
- No international comparison was made. A more mature foreign system has been through the same stages
  — duplicates, notifications, ghost invoices — and would be a good source of patterns.
