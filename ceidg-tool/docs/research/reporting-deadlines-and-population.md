# Filing Deadlines and the Population of Reporting Entities

Date: 2026-09-10
Status: draft
Author: P0w3r223
Related to: `krs-register-risk-signals.md`, `krs-data-access-and-legal.md`

---

## Summary

Detecting a missing or late statement was identified as the flagship feature. This document
establishes that **the statutory filing deadline is not computable from public data**, and that a
naive rule would accuse compliant companies. Sources: the consolidated accounting act
(**Dz.U. 2026 poz. 522**, as at 2026-03-26) and the KRS act, read directly.

## 1. The deadline chain

| Step | Provision | Deadline | Counted from |
|---|---|---|---|
| Prepare the statement | art. 52 sec. 1 | 3 months | **balance-sheet date** |
| Approve it | art. 53 sec. 1 | 6 months | balance-sheet date |
| Audit, where required | art. 53 sec. 1a | before approval | — |
| File with the court register (statement, audit report, approval and profit-distribution resolutions, management report, assurance report) | art. 69 sec. 1 pt 1-5 | **15 days** | **date of approval** |
| Where not approved within the art. 53 deadline | **art. 69 sec. 2** | **15 days after that deadline**, then again 15 days after approval | expiry of 6 months / date of approval |
| Publication in MSiG for entities outside art. 69 | art. 70 sec. 1-2 | 15 days | date of approval |
| Declaration of **no obligation** (general or professional partnerships of natural persons below the threshold) | art. 70a | 6 months | end of the financial year |
| Public country-by-country income-tax report | art. 63n sec. 5 | **12 months** | balance-sheet date |
| Parent not preparing consolidated statements | art. 69 sec. 4 | 30 days from approval, at most 12 months | balance-sheet date |

For a calendar year 2025: prepare by 2026-03-31, approve by 2026-06-30, file within 15 days of
actual approval; if never approved, file by **2026-07-15** and again 15 days after approval.

## 2. Why "filed late" is not provable

The 15-day deadline runs from the **approval date**, and the register does not publish it — the
`wzmianka` carries the **filing date** and the period covered, nothing else.

The only deadline computable from public data is the backstop of art. 69 sec. 2: **6 months plus
15 days from the balance-sheet date**.

**Therefore:**

- The tool may state "no statement filed within the statutory period" only after that backstop has
  passed and no `wzmianka` for that financial year exists.
- The tool may **not** state "filed late" based on the filing date alone for anything filed before
  the backstop.
- Even after the backstop, the six exemptions below must be excluded first.

## 3. Six lawful reasons for an absent statement

| Situation | Provision | Why the absence is correct |
|---|---|---|
| Activity suspended for the whole year | art. 12 sec. 3b | Books need not be closed, absent depreciation or other asset events. Does not apply to securities issuers (sec. 3c). **The most common source of a false alarm** |
| Started in the second half of the financial year | art. 3 sec. 1 pt 9 | Books and statement may be merged with the following year — legally there is no statement for the first period |
| Bankruptcy, or restructuring with an appointed administrator | art. 53 sec. 2a | Approval provisions do not apply at all; only the art. 69 sec. 2 backstop runs |
| Business in inheritance (succession management) | art. 53 sec. 2aa | Approval provision does not apply |
| Partnership of natural persons below the threshold | art. 70a | Files a declaration of no obligation instead of a statement |
| Entity outside the entrepreneurs register | separate rules | Files with the head of the tax administration, into a non-public collection |

Also: the financial year need not be the calendar year (art. 3 sec. 1 pt 9 allows any 12
consecutive months, and the first year after a change **should be longer than 12 months**), so
every deadline hangs off the balance-sheet date, not off 31 December.

Liquidation and bankruptcy additionally produce **two statements within one calendar year** with
unusual balance-sheet dates: books are opened on the day liquidation begins or bankruptcy is
declared (within 15 days), and closed on the day preceding it, with closing within three months of
the event. The addressee of the obligation is then the liquidator, receiver, restructuring or
succession administrator, who counts as head of the entity.

**The pandemic-era three-month extension is exhausted.** The regulation formally remains in force
but limits its application to financial years ending no later than 2022-04-30, and no newer act of
that kind exists. Market memory of "plus three months" is stale, and users will invoke it
defensively — the tool must be able to answer.

## 4. Who must file at all

- **Commercial companies (partnerships and capital companies, including those in organisation) and
  civil-law partnerships: no threshold** (art. 2 sec. 1 pt 1).
- Natural persons, civil-law and general partnerships of natural persons, professional partnerships
  and businesses in inheritance: only from revenue of **EUR 2 500 000** (art. 2 sec. 1 pt 2), raised
  from EUR 2 000 000, applying first to financial years beginning after 2024-12-31.
- **Currency conversion trap:** art. 3 sec. 2 converts amounts at the average central-bank rate on
  the **balance-sheet date**, but art. 3 sec. 3, for this particular threshold, uses the rate on the
  **first working day of October of the preceding year**. Two different rules, easy to confuse.
- Sole traders in the business-activity register file nothing here. **For that population an absent
  entry in the register is not a signal of anything.**

## 5. Entity categories and audit obligation

Category thresholds rose by a uniform 25% from financial years beginning after 2023-12-31, and two
new categories appeared:

| Category | Through FY2023 | From FY2024 |
|---|---|---|
| micro | 1.5m / 3m PLN / 10 people | **2m / 4m PLN / 10 people** |
| small | 25.5m / 51m PLN / 50 people | **33m / 66m PLN / 50 people** |
| medium | did not exist | **110m / 220m PLN / 250 people** |
| large | did not exist | above 110m / 220m PLN / 250 people |
| consolidation exemption | 38.4m / 76.8m before eliminations; 32m / 64m after | **48m / 96m before; 40m / 80m after** |

Status is lost only after exceeding thresholds **in two consecutive years** — a two-year rule, not
a one-year one.

Mandatory audit (art. 64 sec. 1) always covers banks, credit unions, insurers and reinsurers,
capital-market entities, pension funds, payment and e-money institutions, and **joint-stock
companies** (other than those in organisation at the balance-sheet date). Note that the **simple
joint-stock company is not covered** — the provision names joint-stock companies only.

Other entities are audited on meeting **at least 2 of 3** conditions in the preceding year:

| | Through FY2024 | From FY2025 |
|---|---|---|
| employment | 50 | 50 |
| total assets | EUR 2 500 000 | **EUR 3 125 000** |
| revenue | EUR 5 000 000 from sales of goods and products **and financial operations** | **EUR 6 250 000** from sales of goods and products (**financial operations excluded**) |

Not only the amount changed but **the measurement base**.

## 6. New filings that change what the repository contains

- **Public country-by-country income-tax report** (chapter 6b): filed with the court register and
  published on the entity website within **12 months** of the balance-sheet date, kept online for at
  least 5 years. First applies to financial years beginning after 2024-06-21 — for a calendar year,
  the 2025 report is due **2026-12-31**. It is a new document type in the repository and easy to
  mistake for a financial statement.
- **Sustainability reporting** (chapter 6c), with an assurance report added to art. 69 sec. 1 as
  point 5, and penal provisions for missing assurance or filing. **But art. 84a (added 2026-03-14)
  lets entities not perform these obligations for financial years beginning between 2025-01-01 and
  2026-12-31** where they stayed below 1000 people or PLN 1.9bn revenue in that and the preceding
  year. Practically: for FY2025-2026 ESG reporting appears only for the largest entities, and its
  **absence elsewhere is not a breach**. A tool that flags it would be wrong.
- **ESAP**: documents placed in the repository are forwarded to the European single access point
  with metadata (legal entity identifier, size category, sector, whether they contain personal
  data). The accounting-act part took effect 2026-05-29; the KRS part applies from **2028-01-10**.
- **JPK_CIT** books submitted to the tax administration are a separate obligation on a separate
  deadline — do not conflate it with the statement deadline.

## 7. Scale and real consequences

Volumes (official): the repository received **570 565 submissions and 1 544 826 documents in 2025**,
up from 301 122 submissions in 2018. Statements filed with the head of the tax administration
totalled 46 115 in the first five months of 2026 — an order of magnitude smaller, confirming that
the non-register path is niche. MSiG publications run at roughly 1 200-1 300 per year.

Enforcement (2018, the only year found): **105 574 new compulsion proceedings concerning financial
statements** entered the registry courts, plus 41 619 carried over; in the same year 31 094 entities
were struck off. The number of compulsion proceedings about statements was **comparable to the
total number of registration cases** — non-filing is a mass phenomenon, not a margin.

**No official non-filing ratio was found.** The circulating "30-40% of companies do not file" could
not be verified and should not be repeated.

Sanctions escalate in a way that is itself a risk signal:

- art. 77 pt 2: failure to prepare a statement — fine or imprisonment up to 2 years, or both.
- art. 79 pt 4: failure to file with the court register — fine or restriction of liberty.
- art. 24 of the KRS act: compulsion proceedings, a further 7-day deadline, a **repeatable** fine.
- **art. 25a sec. 1 pt 4**: where statements for **two consecutive financial years** are not filed
  despite a summons, the court opens proceedings ex officio to dissolve the entity **without
  liquidation**; art. 25e: property remaining after deregistration passes to the State Treasury free
  of charge, and creditor claims expire after one year.

## Open items

- Registry publication lag between filing and the `wzmianka` appearing was **not measured**. Without
  it, any rule triggering a day after the backstop will produce false accusations. Measure it against
  the live API before the rule ships.
- The extract of a partnership of natural persons and of a suspended company was not sampled — the
  key for the art. 70a declaration and for the new document types is unknown. Fetch both before
  building the rule.
- Tax-act provisions on the non-register filing path were taken from an official portal, not read in
  the statute.
- Legislative pipeline unverified: the sustainability directive has so far been implemented only
  episodically through art. 84a, so a further amendment is almost certainly coming. This is inferred
  from the absence of a permanent provision, not from a confirmed draft.
