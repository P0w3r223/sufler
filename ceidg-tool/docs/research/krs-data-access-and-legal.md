# KRS Financial Documents: Access Channels and Legal Standing

Date: 2026-09-10
Status: draft
Author: P0w3r223
Related to: `krs-register-risk-signals.md`, `esf-schemas-and-tooling.md`

---

## Summary

Register data and financial documents are served under two entirely different regimes. The
register has a free, open, machine-readable API. The document repository has none, and its
operator states it is protected against robots. This split is the single most important
architectural fact for any tool built on KRS.

## 1. The register: Open API KRS

- `https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr=P&format=json` — current extract, JSON
- `.../OdpisPelny/{krs}` — full extract with a dated `wpis[]` history (175 entries on the sampled company, 2001-2026)
- `.../Biuletyn/{YYYY-MM-DD}` — daily change feed, an array of KRS numbers changed that day
  (~2k changes/day declared; 10k+ entries with repetitions observed on one sample).
  An hourly bulletin is also listed by the operator. Note the exact path: a `/api/Biuletyn/`
  variant returns 404.

Terms, as declared by the publisher on the national open-data portal (dataset 27606, Ministry
of Justice): licence **CC0 1.0**, public domain, "API nie wymaga tokenu i nie posiada limitów
pobrań". Legal basis: the 2021 open-data act; service launched 2022-03-08. Personal data in the
open extract are anonymised (initials, first digit of PESEL), unlike the PDF extract.

Verified live on 2026-09-10 — both extract endpoints and the bulletin returned valid JSON with
no credential.

**Caveat that must not be skipped:** the declared absence of limits is contradicted by
commercial providers, who describe query limits and no stability guarantee. The official API
documentation is a single-page app and could not be read. One press source cites 100 requests
per 15 minutes. Measure empirically and implement backoff; do not trust the declaration. This
project has already paid once for trusting a recorded value over a measurement.

Since **2025-11-29 KRS entries are no longer announced in Monitor Sądowy i Gospodarczy**. The
daily bulletin is now the monitoring channel, and it changes the architecture from "query on
demand" to "watch a portfolio".

## 2. The document repository (RDF): no API

- Rebuilt from scratch and relaunched **2026-02-23**: new architecture, integration with the
  central e-signature service and the national identity service, new document categories
  (sustainability reports, income-tax reports, group management reports).
- Address changed. `ekrs.ms.gov.pl/rdf/pd/search_df` now 302-redirects to
  `rdf-przegladarka.ms.gov.pl`; the whole `ekrs.ms.gov.pl` host redirects to `prs.ms.gov.pl/krs`.
  Government pages and every third-party guide still cite the old URL. The MSiG search moved
  as well, to `wyszukiwarka-msig.ms.gov.pl`. **Three address changes in one year: endpoints
  belong in configuration, never in code.**
- Coverage starts **2018-03-15**; older material sits in court files.
- Legal basis: art. 9a of the KRS act — every person may inspect the repository, and documents
  are made available free of charge over public networks. The 2025 amendment did **not** touch
  this article, which is an argument that RDF sits outside art. 60a entirely.
- The operator states the search is "zabezpieczone przed działaniem robotów pobierających
  dokumenty". **That statement dates from 2018 and was not updated after the 2026 rebuild.**
  Whether the new front end actually enforces anything was not verified.
- No terms-of-use document was found on the new portal; `robots.txt` returns the SPA shell
  rather than directives.

**Decision taken regardless of the above: we do not scrape RDF.** The stated intent of the
operator is sufficient. Two honest paths remain — the tool hands the operator a ready link and
accepts a downloaded file, or automation goes through a commercial API (MGBI, Transparent Data,
rejestr.io) whose provider carries the risk.

## 3. The unresolved legal question: art. 60a

The act of 2025-09-26 (**Dz.U. 2025 poz. 1556**, in force **2025-11-29**) added art. 4 sec.
4c-4m and a new art. 60a:

> Kto bez uprawnienia uzyskuje z Rejestru informację za pośrednictwem usług sieciowych, podlega
> grzywnie, karze ograniczenia wolności albo pozbawienia wolności do lat 2.

The new channel is closed by subject: only public entities, or entities performing public tasks
under separate provisions or by delegation, may apply, and only by decision of the Minister of
Justice. **A private commercial entity has no standing to apply at all** — so under an
unfavourable reading there is no path to legalisation.

Arguments that the open API falls outside art. 60a:

- "bez uprawnienia" is a negative element of the offence. The entitlement flows from art. 4b
  sec. 1-2 of the KRS act, which states that register data are made available for re-use "także
  za pośrednictwem interfejsu programistycznego aplikacji (API)" under the open-data act. The
  amendment **left art. 4b untouched** while surgically editing fifteen other places.
- The act uses two distinct terms: "API" for the universal channel, "usługi sieciowe" for the
  new gated one.
- The Ministry told the press that Open API is available to everyone without ministerial
  consent and permits mass download of anonymised data, while Full API is restricted.
- The API is still open and answering 9.5 months after the provision took effect.
- Criminal law requires strict construction; an undefined term resolves in favour of the accused.

Argument to the contrary: the explanatory memorandum to the bill **equates "usługi sieciowe"
with API**, quoting the open-data act definition verbatim. On that reading "bez uprawnienia"
means "without the consent introduced by the same amendment".

Status: **no case law, no ministerial interpretation, no commentary analysing the relation
between art. 60a and art. 4b.** There is no delegation to issue implementing regulations, so no
clarifying regulation should be expected; the regime is exhausted by the statute plus individual
administrative decisions.

The risk concerns the **channel**, not the data — KRS data remain public and free under art. 4
sec. 4a and art. 9a.

**Caveat:** the KRS act was amended at least six further times within a year of poz. 1556. Only
one of those (Dz.U. 2026 poz. 119) was verified as not touching art. 60a. Check the current
consolidated text before acting; legal portals still serve the pre-amendment version.

## 4. Leverage for a request to the Ministry

Commission Implementing Regulation **(EU) 2023/138** on high-value datasets lists the
"companies and company ownership" category and expressly includes **financial statements and
management reports**, to be made available free of charge, **via API and bulk download**, under
CC BY 4.0 or less restrictive terms, applicable since roughly mid-2024.

Poland has not operationalised this for financial statements — the national open-data portal
holds no dataset of KRS financial documents. This is an argument, not a licence. But it turns a
request for a favour into a question with a legal basis.

## 5. Resolving NIP to KRS

The KRS API takes the KRS number only; there is no NIP lookup. Two routes:

- **GUS BIR/REGON API** — free, requires a registered key (commercial applicants register by
  e-mail, declaring source IP addresses). Query by REGON, NIP or KRS; the legal-person report
  carries `praw_numerWRejestrzeEwidencji` with the KRS number. Rate limits vary by time of day:
  6 000/h between 08:00-16:59, 8 000/h in shoulder hours, **10 000/h between 22:00-05:59**.
  Batch work belongs at night — roughly 67% more throughput.
- **VAT taxpayer register** — returns KRS among other fields, but see the limits warning in
  `krs-register-risk-signals.md`; it is not a bulk route.

For a NIP with no KRS entry the correct answer is **"no public source"**, not "no financial
statement": statements of CIT payers outside the register go to the head of the tax
administration and are covered by fiscal secrecy.

## Open items

| Item | Who can close it |
|---|---|
| Scope of art. 60a versus the open API | Written position from the Ministry of Justice, or accepted risk documented in writing |
| Actual rate limits of the KRS API | Empirical measurement with backoff |
| Whether RDF returns documents with signatures preserved | One manual download |
| Whether the rebuilt RDF front end enforces anti-automation | Manual inspection in a browser |
| Current consolidated text of the KRS act | ISAP lookup before any decision |
| Machine access to the debtors register (KRZ) | E-mail enquiry to the Ministry of Justice |
