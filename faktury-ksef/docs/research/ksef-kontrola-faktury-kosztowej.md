# Verifying a cost invoice that came from the state's own system

Date: 2026-09-11
Status: draft — first reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: `ksef-dostep-i-obowiazek.md`, `ksef-struktura-faktury.md`

---

## The whole thesis of the product in one table

| The system guarantees | The system does not guarantee |
|---|---|
| Authenticity of origin — only an authenticated entity with write permission can issue | That the transaction happened, or that the amounts are real |
| Integrity of content — number plus hash, publicly verifiable | That the issuer is an active VAT payer |
| Conformance to the schema — obligatory elements are enforced | That the indicated account is on the taxpayer register |
| Uniqueness of the system identifier | **That the invoice is yours at all** |
| Dates with legal force — receipt is the moment a number is assigned | Arithmetic consistency |
| Completeness of the pull, provable through the high-water mark | Absence of an economic duplicate |
| Ten-year archival on the state's side | Correct rates, classification, prices against a contract |

**The value of the tool is exactly the right-hand column.**

## A risk class that did not exist in the world of PDFs

There is **no acceptance step**. Anyone who types your tax number puts an invoice into your stream,
and from the moment it receives a number it is **legally received**. The buyer cannot reject it in
the system.

So "did we even order this from them" stops being a controlling formality and becomes the **first
line of defence**. In the paper world a fraudulent invoice had to reach you somehow; here it arrives
by itself.

## Checks required by law, with the cost of skipping them

| Check | Why | Cost of omission |
|---|---|---|
| Payee account in the taxpayer register, for transactions above PLN 15 000 paid by transfer to an active VAT payer | tax and income-tax provisions | **No deductible cost**, plus joint liability for the supplier's VAT on that transaction |
| Notification if payment went to an account outside the register anyway | **7 days from ordering the transfer**, to the payer's own tax office | Loss of both remedies above |
| Mandatory split payment above PLN 15 000 for listed goods and services | VAT act | Additional tax liability, no deductible cost, and evidence against due diligence |
| Checking the document before entering it in the books | accounting act — records must be reliable, complete and free of arithmetic error | Unreliable books; liability of the head of the entity |
| Due diligence toward the counterparty — registration, VAT status, licences, authority of signatories | ministry methodology | Challenge to VAT deduction. Note the methodology is expressly **non-binding guidance for officials**, not legal protection |
| Sanctions screening | EU regulation and the national list | Asset freeze, penalties, exclusion from public procurement |
| **From 2027-01-01: the system number in the payment order** | new VAT provision | Penalty, effective the same date |

One detail matters for architecture: the new payment provision determines the payee's VAT status
**as at the day of payment**, by reference to the taxpayer register. The statute itself forces a
register query on the payment date.

## Good practice, automatable from the invoice and its history

- **Three-way match** against order and receipt. The methodology treats the absence of a contract or
  order as an indicator against due diligence, so the controlling practice has tax support.
- **Economic duplicate** — the key is seller number, invoice number, gross amount and sale date, not
  the system identifier, which only catches technical duplicates.
- **Change of bank account against the counterparty's history.** The system removes the classic
  "swapped PDF in an e-mail" vector; it does not remove "an e-mail asking to change the account".
  Business e-mail compromise remains one of the largest loss categories in international crime
  reporting.
- **Price against the market or a contracted price list** — an indicator named in the methodology.
- **Payment term shorter than the industry norm** — likewise.
- **Invoice subject against the counterparty's registered activity**, available from the court
  register.
- **Authority of signatories and composition of governing bodies**, which the methodology requires to
  be checked regularly rather than once.
- **Share capital grossly low against the transaction's scale.**
- **Completeness of the pull** — the accounting equivalent of "we have not lost a cost invoice".

## What has become ceremony

For an invoice fetched from the system: verifying an electronic signature, OCR, re-keying, checking
that obligatory fields are present, scanning the verification code, calling the issuer to confirm
they issued it, and keeping one's own archive **as evidence**.

The verification code matters **only** for invoices received outside the system — issued offline, or
handed to a buyer who gave no tax number. For a fetched invoice it is double work. An own archive
still makes sense for operational continuity and analytics; it no longer makes sense as proof.

## Two counterintuitive findings

**Paying through split payment is stronger protection than checking the register.** The methodology
states that where payment was made through the mechanism, due diligence is met provided the formal
conditions were verified — and it protects against the additional liability, against the income-tax
sanctions **and against joint VAT liability even when paying to an account outside the register**.
The exception is knowing the invoice is fictitious. Practically: a "paid by split payment" flag can
**substitute** for the account check rather than merely supplement it.

**The state does not join its own registers on our behalf.** The e-invoicing system queries neither
VAT status nor the account. The only statutory link between it and the taxpayer register is the
payment provision, and it starts on 2027-01-01.

## Public sources for verifying the issuer and the account

| Source | Access | Limits, confirmed |
|---|---|---|
| **Taxpayer register (the white list)** | no registration | Search methods: **100 queries per day per IP, up to 30 entities each**. Check method: **5 000 entities per day**. After that, blocked until midnight. Every response carries a confirmation identifier. History reaches five years back. **Flat files published daily at midnight** allow checking number-account pairs without limits |
| **EU VAT validation** | no registration, REST | Returns validity, name, address and a request date; a confirmation number appears to require identifying the enquirer — unconfirmed |
| **Court register** | no registration, JSON | Returns seat, capital, governing bodies, activity codes. Requires knowing the register number — mapping from the tax number must be built separately |
| **Business activity register** | requires an account | Limits and scope unverified |
| **Sanctions lists** | published as tables | No confirmed machine feed on the national side |

**The cheapest mass verification is the flat file, not the interface.** The search method yields
around three thousand entities per day at best, the check method five thousand, and the daily files
effectively no limit. Practitioners call the search limits absurdly low.

A structural point for the checks: **the bank account in the invoice schema is optional and may
repeat up to a hundred times.** "Check the account from the invoice" is therefore sometimes
impossible. What must be checked is the account actually used in the payment.

## Offline mode breaks date alignment

An invoice issued offline must reach the system by the next working day, or within seven working days
of the end of an outage. **An invoice issued in January can receive its number in February.** Period
assignment must follow the number-assignment date, not the date printed on the document — which
aligns with fetching by permanent-storage date rather than issue date.

## Open items

- Three VAT provisions underpinning deduction rules could not be retrieved in their literal wording
  and must be quoted from the official source before any rule is written into code.
- The ministry's own pages disagree on the deadline for the notification about paying outside the
  register — seven days appears to be the rule and fourteen a lapsed pandemic-era exception, but this
  needs confirming in the current text.
- No Polish data was found on the proportion of cost invoices containing errors, or on the scale of
  duplicate payments.
- Whether the system validates the existence or activity of the buyer's tax number on acceptance is
  not documented.
- The claim that the account field repeats up to a hundred times comes from a practitioner's report,
  not from the schema. Confirm before implementing.
- **No tax rulings, no case law and no implementation experience from 2026 informed this document.**
