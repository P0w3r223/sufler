# Bank-Style Analysis: Metrics, Bankruptcy Models, Benchmarks, and Output Constraints

Date: 2026-09-10
Status: draft
Author: P0w3r223
Related to: `esf-schemas-and-tooling.md`, `reporting-deadlines-and-population.md`

---

## Summary

Three findings shape the analytical layer:

1. **No regulator publishes threshold values.** Supervisory guidance lists metrics; the thresholds
   belong to each bank. Every number a tool prints is a convention and must be labelled as one.
2. **The honest anchor is position within an industry distribution**, not a fixed threshold — but
   every source carrying such distributions forbids redistribution or is silent on licensing.
3. **Polish bankruptcy models are far weaker than their authors claim**, and the secondary
   literature circulates mutually contradictory coefficients for the same model.

## 1. What banks actually compute

Three independent sources converge closely: European supervisory guidance for lending to
enterprises, Polish academic descriptions of bank practice, and the Polish loan-loss provisioning
regulation.

The recurring set: equity ratio, debt-to-equity, EBITDA, interest-bearing debt to EBITDA, total
debt service coverage, cash debt coverage, current and quick ratios, net working capital, interest
coverage, ROA, ROE, ROCE, net margin, turnover evolution, receivable and payable days, inventory
turnover, cash conversion cycle.

Two details from Polish practice that textbooks omit:

- the current ratio is computed with **the current instalment of long-term debt added to the
  denominator** — a bank deliberately makes the ratio harsher than the standard formula;
- debt-service capacity is measured through **"nadwyżka finansowa" = net profit + depreciation**,
  rather than EBITDA. For entities without a cash-flow statement the second is computable and the
  first often is not.

**Thresholds quoted in the literature** (current ratio 1.5-2.0, quick 1.0-1.2, debt service
coverage above 1, DSCR covenant at 1.2-1.3, golden balance-sheet rule at 1): all are expert
opinion. One of the cited authors states outright that assigning points to ratios is "w zasadzie
arbitralne" and calls it a weakness of the method.

## 2. The one source of hard numbers

The Polish regulation on loan-loss provisions (**Dz.U. 2026 poz. 311**, issued 2026-02-18)
provides a ready five-grade skeleton, classifying an exposure by the worse of two criteria —
payment timeliness and the debtor's economic and financial situation:

| Grade | Timeliness | Economic situation |
|---|---|---|
| normal | ≤ 30 days | no concerns |
| watch | > 30, ≤ 90 | early-warning signals (region, country, industry, customer or product group) |
| substandard | > 90, ≤ 180 | may threaten timely repayment |
| doubtful | > 180, ≤ 365 | **materially deteriorated, especially where losses materially impair net assets** |
| loss | > 365 | repayment unlikely |

Minimum provision rates: 0.5% / 7% / 30% / 60% / 100%. Exposures to one debtor are all classified
to the highest risk grade. The economic criterion may be applied annually rather than quarterly
only where the exposure is normal, the total below EUR 1m **and** below 10% of the bank Tier 1 —
which is a useful marker of where "light" and "full" analysis part company for SMEs.

The regulation also enumerates what must be measured: profitability, liquidity, turnover of current
assets and current liabilities, balance-sheet structure including debt and debt-service capacity;
plus qualitative measures — management quality, dependence on the market, on subsidies or public
contracts, on a few large suppliers or customers, and on group entities.

**Important status caveat: this regulation applies for the first time to financial years beginning
after 2026-12-31.** Until then the 2008 regulation applies, whose text was not compared.

The one hard numeric rule usable immediately: **losses materially impairing net assets imply the
"doubtful" grade.**

## 3. How a bank handles an abbreviated statement

Scoring cards used in practice score **the quality and completeness of the reporting separately**
(3 points out of 20 in the balance-sheet section of a documented example). A bank does not pretend
it has full data — it downgrades the assessment for their absence.

**This is the pattern to copy: a missing cash-flow statement is a penalty, not an "N/A".**

The other two documented responses are unavailable to us: demanding data from outside the
statement (aged debtor reports, business plans, projections), and shifting to qualitative criteria.

Reconstructing cash flow from two balance sheets is possible in form but not in substance — the
national accounting standard devotes a section to explaining why balance-sheet movements differ
from cash-flow figures (contributions in kind, acquisitions, exchange differences, reclassification,
write-offs), and those differences are invisible without the notes. Any such figure must be labelled
a proxy of unknown error, never "cash flow".

## 4. Discriminant models: verified forms

Coefficients below reflect a published audit of errors in the Polish literature, which corrects
named authors by name. **None of the original publications was read directly** — all five are
print-only. The Poznan model original is freely downloadable and should be fetched manually to
close that gap.

**Hołda (2001)** — 40 failed + 40 healthy companies, activity classes 45-74 (construction, trade,
hospitality, transport, real estate and business services — **not manufacturing**), data 1993-1996.

```
Z = 0.605 + 0.681*PWP - 0.0196*SZ + 0.00969*ZM + 0.000672*WOZ + 0.157*RM
```
- PWP = current assets / current liabilities
- SZ = total liabilities / total assets, **×100**
- ZM = net result / average total assets, **×100**
- WOZ = average current liabilities / cost of products, goods and materials sold, **×360**
- RM = total revenue / average total assets

Threshold 0, with a no-decision band from -0.3 to +0.1.

**Corrections established by the audit** — each is an error found in published papers:
0.157 belongs to asset turnover and 0.00969 to return on assets (a widely circulated source has
them swapped); the WOZ denominator is cost of goods sold, **not** operating costs; the multiplier
is 360, not 100 and not 365; SZ and ZM **must** be expressed as percentages — omitting that shifts
the function by orders of magnitude; the sign on PWP is positive.

**Poznan model (Hamrol, Czajka, Piechocki 2004)** — 100 companies, 50/50, data 1999-2002. Six
independent sources agree; the only one of the five that can be treated as certain without the
original.

```
FD = 3.562*X1 + 1.588*X2 + 4.288*X3 + 6.719*X4 - 2.368
```
X1 = net result / total assets; X2 = (current assets - inventory) / current liabilities;
X3 = permanent capital / total assets; X4 = result **on sales** / sales revenue. Threshold 0.

Implementation risk: "permanent capital" is not a statutory term — define it explicitly (equity
plus long-term liabilities) and test it; a published worked example got it wrong.

**INE PAN model G (Mączyńska, Zawadzki 2006)** — 80 listed companies, data 1997-2001.

```
Z = 9.498*(operating result/assets) + 3.566*(equity/assets)
  + 2.903*((net result + depreciation)/liabilities)
  + 0.452*(current assets/current liabilities) - 1.498
```
Threshold 0, no grey zone. One source gives 3.5566 rather than 3.566 — a third-decimal discrepancy,
unresolved. Also define whether the first variable is operating result or EBIT; Polish literature
disputes their identity.

**Gajdka and Stos (2003)** — 34 companies, data 1998-2001. Note there are **two different models by
these authors**, from 1996 and 2003, routinely confused in online compilations.

```
Z = -0.3342 - 0.0005*X1 + 2.0552*X2 + 1.7260*X3 + 0.1155*X4
```
X1 = average current liabilities / cost of production sold, **×360 (days)**; X2 = net profit /
average assets; X3 = gross profit / sales revenue; X4 = assets / liabilities. Threshold 0, grey
zone -0.49 to +0.49. The audit establishes that omitting the intercept and failing to express X1
in days are **errors**, resolving a contradiction between two secondary sources.

**Mączyńska (1994)** — implement last.

```
Z = 1.5*W1 + 0.08*W2 + 10*W3 + 5*W4 + 0.3*W5 + 0.1*W6
```
W1 = (gross profit + depreciation) / liabilities; W2 = total assets / liabilities; W3 = gross
profit / total assets; W4 = gross profit / sales revenue; W5 = inventory / sales revenue;
W6 = sales revenue / total assets (**asset turnover — an inverted version circulates**). Grades:
below 0 threatened; 0-1 weak; 1-2 good; above 2 very good.

**Unresolved:** the W2 coefficient is 0.08 in four sources and 0.0085 in one. The scale test
favours 0.08 — at 0.0085 the variable would contribute ~0.013-0.04 against thresholds of 0/1/2 and
would be effectively dead. Not verified against the 1994 original. A further unconfirmed lead
suggests this model may be an adaptation of a foreign function with **no Polish estimation sample
at all** — verify before documenting.

**Attribution trap:** a widely cited compilation presents the Mączyńska formula under a sample
description belonging to the INE PAN models. They are different objects and need different
identifiers in code.

## 5. Independent validation: expect 70-78%, not 92-98%

Ten models tested on one independent sample of 50 companies (25 bankruptcies 2007-2015):

| Model | Accuracy |
|---|---|
| Hołda | 78% |
| Poznan | 78% |
| INE PAN G | 74% |
| Appenzeller-Szarzec | 72% |
| Mączyńska | 70% |
| Maślanka | 66% |
| Prusak | 62% |
| Gajdka-Stos | 56% |

**No model exceeded 80%**, against 92-98% claimed in the source papers — a systematic effect of
in-sample validation on balanced 50/50 samples.

Three counterintuitive results: older models did not perform worse than newer ones; the only model
using cash-flow data performed poorly, which weakens the concern about abbreviated statements; and
discriminant models beat logit models here, contrary to the methodological literature.

Running three to five models in parallel and **treating disagreement as a signal rather than a
defect** is the repeatedly recommended practice.

**Every constant in code carries a citation with author, year and page.** The audit literature
exists precisely because authors copy each other instead of reading originals — and it notes that
certified auditors and court experts propagate the same errors, producing mutually exclusive
expert opinions in insolvency cases. This is the same failure mode this project already paid for
once with the activity-code dictionary.

**Domain limit to state in the interface:** none of these models was validated on
micro-enterprises or sole traders. Every estimation sample consists of companies.

## 6. Industry benchmarks: distributions exist, licences do not

| Source | Distribution? | Micro? | Licence |
|---|---|---|---|
| **BACH** (ECCBSO, hosted by Banque de France; Polish data computed by NBP from GUS microdata) | **Q1 / median / Q3, ~80 NACE divisions, 4 size classes** | no — 9+ employees | free of charge, but **"redistribution of data is prohibited, even when this is done without charging"** |
| **SKwP + InfoCredit** sector ratios | **median, quartiles, whiskers, sd, 16 ratios per division** | **yes — 90% of the 211 070 sample are micro and small** | reprint above 25% needs written consent; database rights sit with the data producer, not the publisher |
| **GUS** published aggregates | no — section-level averages | no — 10+ employees | **CC BY 4.0** |
| **Bank sector report (PONT data)** | deciles D1/D5/D9, 79 divisions | no — 9+ employees | no reuse licence, PDF only |

So the source with a clean licence has no distribution, and the sources with distributions have no
licence. **This is a decision for people, not for code:** ask the two rights holders, buy the data
at source, or design the tool to compute the ratio and **point the user at the source** rather than
shipping someone else's table — which removes the problem entirely at the cost of convenience.

Two further constraints:

- **Definitions differ between benchmarks.** One source computes current liquidity excluding trade
  receivables and payables due beyond 12 months; another uses the plain formula; one annualises ROE
  by a quarter-dependent multiplier. A ratio must be computed with the formula of the benchmark it
  is compared against, or the comparison is worthless. Worse, the stricter definitions require
  splits the abbreviated balance sheet does not contain — which introduces **systematic bias, not
  noise**.
- **All benchmarks use the 2007 activity classification** while registers now carry a mix of the
  2007 and 2025 vintages. A mapping layer using the official transition keys is required, and the
  keys are one-to-many in places, so a tie-breaking rule is needed.

The transfer-pricing field offers a mature, ready convention worth copying: selection by activity
code, revenue within a band around the subject, three to five years, and outlier removal by the
interquartile filter.

## 7. Legal constraints on the output

These constrain how the assessment is produced and presented, and they are architectural.

- **Scope boundary.** Automated creditworthiness assessment of a **natural person** is automated
  decision-making under art. 22 GDPR, and the responsibility rests on the producer of the score
  where the recipient decision depends on it decisively. It is also a high-risk category under the
  AI Act. Companies in KRS are not natural persons, so neither applies to our scope — **but both
  apply the moment the tool assesses a sole trader**, and there is no available legal basis for
  that in a B2B setting. A combined register card covering both populations is therefore
  disqualified without separate legal analysis.
- **Reproducibility is a legal requirement, not a nicety.** Personal-interest protection extends to
  legal persons and **unlawfulness is presumed** — the defendant must prove otherwise. Every issued
  assessment must be reproducible: method version, sources, and data state at the moment of issue.
- **Unfair-competition law has a provision written for this product**: disseminating untrue or
  misleading information about another business, including its **economic or legal situation**, for
  gain. The commercial nature of the tool satisfies the purpose element on its own, so the whole
  defence rests on truthfulness and on not misleading. This argues for cautious language over
  striking verdicts.
- **Retention must follow the register, not our cache.** European case law prohibits private
  bureaus from holding information sourced from a public register longer than the register does.
- **A rule engine written by humans is probably outside the AI Act definition**, unlike a model
  trained on data. This independently confirms the decision taken on other grounds: figures are
  computed by plain code, and a language model at most describes the result.
- **Staying outside the credit-bureau regime** depends on using only public registers and not
  accepting creditor-supplied data. The tempting product extension — letting clients add their own
  information about counterparties — moves the product into a regime where operating without an
  approved set of rules is a criminal offence.

## Open items

- Mączyńska W2 coefficient, and whether the model has a Polish estimation sample.
- Poznan model original — free download, blocked only by a content-type quirk.
- INE PAN G third-decimal discrepancy.
- Sector-specific models for four of the five sectors in the central-bank study were not read.
- Licence answers from the two benchmark rights holders.
- Polish case law and data-protection decisions on register-sourced data were unreachable
  (anti-bot blocks); in particular the first Polish penalty concerning bulk processing of business
  register data without an information notice remains unverified at source.
- The pre-2027 provisioning regulation was not compared against the new one.
