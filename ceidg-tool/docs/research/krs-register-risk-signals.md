# Risk Signals Available from Public Registers, Without Financial Statements

Date: 2026-09-10
Status: draft
Author: P0w3r223
Related to: `krs-data-access-and-legal.md`, `reporting-deadlines-and-population.md`

---

## Summary

The most valuable and lowest-risk layer of a Polish company-assessment tool does not require
parsing a single financial statement. The free KRS extract carries arrears under enforcement,
creditors holding writs of execution, insolvency proceedings, liquidation, and a complete dated
history of every register entry — hours old rather than months. A bank checks exactly these
things alongside the statement.

**Design consequence: the register-signal layer is built first, the statement parser second.**

## 1. What the KRS extract contains

Structure of the JSON returned by `OdpisAktualny` (paths verified against live responses):

```
odpis.naglowekA.{stanZDnia, dataOstatniegoWpisu, numerOstatniegoWpisu, stanPozycji}
odpis.dane.dzial1.{danePodmiotu, siedzibaIAdres, umowaStatut, kapital}
odpis.dane.dzial2.{reprezentacja, organNadzoru, prokurenci}
odpis.dane.dzial3.{przedmiotDzialalnosci, wzmiankiOZlozonychDokumentach,
                   informacjaODniuKonczacymRokObrotowy}
odpis.dane.dzial4   (see below)
odpis.dane.dzial5   (kurator)
odpis.dane.dzial6.polaczeniePodzialPrzeksztalcenie
```

**Dzial 4 (art. 41 of the KRS act) is in practice a register of operating insolvency:**

- tax and customs arrears subject to enforcement, with date and amount
- social-insurance (ZUS) arrears
- creditors holding writs of execution whose claims were not satisfied in enforcement
- interim orders concerning claims
- petitions for bankruptcy proceedings and their course, receiver details

On a healthy company `dzial4` and `dzial5` come back empty, so **non-emptiness is a binary flag
that costs nothing to detect**.

**Dzial 6 (art. 44)**: opening and closing of liquidation, appointment of a manager, liquidator
or administrator details, dissolution or annulment of the company, mergers and transformations.

**`OdpisPelny`** additionally returns `wpis[]` — every entry with `numerWpisu`, `opis`,
`dataWpisu`, `sygnaturaAktSprawy`, `oznaczenieSadu`. On the sampled company: 175 entries from
2001 to 2026. This is the raw material for derived features — board rotation, frequency of
registered-office changes, clustering of changes in time.

**`dzial3.wzmiankiOZlozonychDokumentach`** carries one array per document type, each element
`{dataZlozenia, zaOkresOdDo}`:

- `wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego`
- `wzmiankaOZlozeniuOpiniiBieglegoRewidentaSprawozdaniaZBadania`
- `wzmiankaOZlozeniuUchwalyPostanowieniaOZatwierdzeniuRocznegoSprawozdaniaFinansowego`
- `wzmiankaOZlozeniuSprawozdaniaZDzialalnosci`
- `wzmiankaOZlozeniuSprawozdaniaZAtestacjiSprawozdawczosciZrownowazonegoRozwoju`

**Parsing trap, observed on two independent samples:** the period format is not uniform even
within one company. "OD 01.01.2023 DO 31.12.2023" appears next to "OD 1 STYCZNIA 2000 ROKU DO
31 GRUDNIA 2000 ROKU". The parser must handle both, must not assume a calendar year, and must
report unparseable entries rather than guess. `dataZlozenia` is `DD.MM.RRRR`.

**Second trap:** the register publishes the **filing date, never the approval date**. See
`reporting-deadlines-and-population.md` — this is what makes an individual "filed late" claim
unprovable.

## 2. Other public sources

| Source | What it gives | Interface | Notes |
|---|---|---|---|
| **KRZ** (debtors register) | insolvency and restructuring proceedings, **enforcement discontinued for lack of assets**, business bans, maintenance arrears | **none found** — SPA, no API, no dataset entry | Public and free by art. 4 of its act. Retention set by art. 11: 10 years generally, 3 years after an arrangement is performed, 7 years for enforcement and maintenance data. Signals therefore have a defined lifetime and can carry a decaying weight |
| **VAT taxpayer register** | VAT status with **dates and legal basis of refusal, removal and restoration**, settlement accounts confirmed via STIR, representatives; state on any chosen day **up to 5 years back** | REST API v1.6.0 + daily flat file | See the limits trap below |
| **CRBR** (beneficial owners) | ultimate beneficial owners | internal API **gated by reCAPTCHA**, no published security scheme | Automation is blocked by design although the register is public by law. A legal decision, not an engineering one |
| **GUS BIR/REGON** | suspension and resumption dates, deregistration date, expected headcount, local units | API, free, requires key | Signals of scale and continuity absent from KRS |
| **Tax lien register** | movable assets encumbered by a tax lien, creditor, date, arrears amount | web form, no API | State as at 24:00 of the previous day. **The operator states its data do not have the force of an official document** — a flag for follow-up, never a basis for a verdict |
| **KNF public warnings** | notifications of suspected offences, with KRS/NIP/REGON | HTML with filter, no CSV/API | 666 warnings at time of check |
| **Sanctions list (MSWiA)** | entities under national sanctions | HTML table, no API | |
| **MSiG** | bankruptcy, liquidation, transformation announcements | new search at `wyszukiwarka-msig.ms.gov.pl`, content in PDF | No longer carries KRS entries since 2025-11-29 |
| **Payment-term reports (MRiT)** | measured payment behaviour of large taxpayers | dane.gov.pl dataset 2747, CSV + JSON-LD, **CC0** | Annual, year N published ~August N+1. **Same year is republished with different as-of dates — bind observations to the as-of date, not the year** |
| **Individual CIT data (MF)** | revenue, costs, income, tax due for large taxpayers | CC0, but the catalogue entry links only to an HTML page | Independent cross-check of statement credibility for large entities |
| **VIES** | validity of an EU VAT number | REST (POST) | |

## 3. Proposed signal hierarchy

This ordering is reasoning from the legal construction of the sources plus foreign literature.
**It is not calibrated on Polish outcome data** — see the epistemic note below.

**Level 1 — near-terminal, latency in hours or days**
- KRZ: bankruptcy petition, opening of restructuring, **enforcement discontinued for lack of
  assets** (the cleanest of all — a court has found there are no assets to cover costs)
- KRS dzial 4: any non-empty entry
- KRS dzial 6: opening of liquidation, dissolution
- VAT register: removal from the register ex officio

**Level 2 — leading, latency in days or weeks, entirely covered by the KRS API**
- no statement filed for the previous year after the statutory backstop
- sudden board rotation or resignations, read from `wpis[]` history
- appointment of a curator (dzial 5)
- change of registered office or name close in time to other changes
- suspension of activity (REGON)

**Level 3 — context and credibility, latency in months**
- payment-term reports, individual CIT data (large entities only)
- tax lien register, registered pledges
- sanctions lists, KNF warnings, VIES

## 4. Two traps worth writing into the design

**The VAT register limit can lock out humans.** The `search` method allows 100 requests per day
(up to 30 entities each), `check` up to 5 000 entities per day. Exceeding it **blocks access
until midnight, including the ordinary web search on the government portal** — from the same
address. A naive batch job will cut colleagues off from a tool they use by hand. The route for
volume is the daily flat file; the API is for pointed, single verification with proof of the
query.

**Sources disagree on the exact limit** (100 vs 300 per day in secondary sources). Verify with
the operator before putting a number in code.

## 5. Epistemic note that should survive into the design

The canonical academic dataset for predicting Polish company bankruptcy (UCI #365, EMIS data,
bankruptcies 2000-2012) contains **65 features, all of them financial ratios from the balance
sheet and income statement**. The literature a team naturally reaches for therefore cannot say
anything about the value of register signals, because it never contained them. **Absence of
evidence is not evidence of absence.**

Foreign work on SME risk (Altman, Sabato and Wilson, Journal of Credit Risk 2010) does examine
non-financial information — auditor opinions, creditor recovery actions, firm characteristics —
and reports improved predictive power. Those categories have direct Polish register equivalents:
creditor recovery actions correspond to dzial 4 and to enforcement discontinued in KRZ; auditor
opinions and reporting discipline correspond to the `wzmianki` block. This is the strongest
available argument for going beyond the statement, and it is an argument transferred from
foreign evidence, not a Polish measurement.

## Open items

- Machine access to KRZ — negatively established only (no dataset entry, no OpenAPI at two
  guessed paths). Requires an e-mail enquiry to the Ministry of Justice, not another search.
- **Rejestr Naleznosci Publicznoprawnych** — not reached at all, every URL returned 404. Public-law
  arrears above a threshold; potentially a Level 1-2 source.
- Public procurement data and the list of excluded contractors — no specification found.
- Polish insolvency statistics for 2024-2026 — not obtained; the search budget was exhausted.
  Without them there is no empirical basis to rank signal strength quantitatively.
- Whether polish research links register data (dzial 4, KRZ) to bankruptcy outcomes — unchecked.
