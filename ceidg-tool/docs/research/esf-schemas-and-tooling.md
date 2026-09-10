# E-Sprawozdania: Logical Structures, Generation Drift, and the Parser Toolchain

Date: 2026-09-10
Status: draft
Author: P0w3r223
Related to: `krs-data-access-and-legal.md`, `financial-analysis-methodology.md`

---

## Summary

No maintained open-source library for reading Polish electronic financial statements exists —
not on PyPI, not on GitHub; the one public artefact that once existed has been deleted. This is
simultaneously the largest cost of the project and its only genuine differentiator.

The good news, established by diffing the schemas directly rather than reading articles about
them: **element names for the balance sheet and income statement are identical across schema
generations**, so multi-year comparison is feasible.

## 1. Three parallel worlds

| Path | Who | Format |
|---|---|---|
| MF logical structures (XML/XSD) | entities applying the Polish accounting act — KRS filers and CIT/PIT payers keeping books | XML against a CRWDE template |
| ESEF | issuers on a regulated market, consolidated under IFRS | XHTML + Inline XBRL (Reg. EU 2019/815) |
| No structure | separate statements under IFRS | MF publishes no logical structure |

The MF XML parser covers the overwhelming majority of KRS entities but **not** listed companies
or separate IFRS statements. The canonical model should carry a source/format field so the other
two paths can be attached later.

## 2. Template catalogue

33 templates are in force from 2026-01-01, all marked as variant `(2)`. System codes follow a
pattern: `SFJINZ`/`SFJINT` (other entities, in zloty / in thousands), `SFJMIZ`/`SFJMIT` (micro),
`SFJMAZ`/`SFJMAT` (small), `SFJOPZ`/`SFJOPT` (non-profit), plus banks, insurers, brokerages,
credit unions, alternative investment companies, funds, consolidated variants, `SFE` for issuers
and `SOI` for other organisations.

**"In zloty" and "in thousands" are separate templates with separate system codes, not an
attribute.** An entity may switch between years, so unit normalisation is a mandatory pipeline
step. The thousands variant types amounts as `xsd:integer`, not `decimal` — rounding is baked
into the data, and multiplying by 1000 does not recover the lost precision.

## 3. What changed between generations, measured

Comparison of `JednostkaInnaStrukturyDanychSprFin` v1-0 (2018) against v2-0E (2025):

- **1930 element declarations in both files.**
- Named complex types sit at identical line numbers: `BilansJednostkaInna` (60),
  `RZiSJednostkaInna` (2130), `ZestZmianWKapitaleJednostkaInna` (3489),
  `RachPrzeplywowJednostkaInna` (4345).
- Position naming is unchanged and fully positional: `Aktywa_A`, `Aktywa_A_I`, `Aktywa_A_I_1`
  through `Aktywa_A_IV_3_C_4`, `Pasywa_A` and so on.
- The same holds for the small-entity structure: 516 elements in both, complex types at
  identical lines.

**Answer to the load-bearing question: the same balance-sheet position has the same element name
in the old and the new schema.**

Four things did change:

1. **Documentation labels, not names.** In the income statement the phrase "towarów i
   materiałów" occurs 12 times in v1-0 and **0 times** in v2-0E ("produktów i towarów" now).
   Mapping by documentation text breaks at the generation boundary. **Map by element name.**
2. **The document envelope.** The root was `JednostkaInna`; it is now `Dokument` with
   `OpisDokumentu` + `DaneDokumentu` + `TrescDokumentu`. Every path gained a prefix. MF states
   the two description nodes may be left empty.
3. **Renumbering inside the introduction.** The old introduction ran `P_1`..`P_8`, the new one
   `P_1`..`P_10`. **`P_8` used to mean "informacja uszczegóławiająca" (a user-defined position)
   and now means average headcount**; `P_9` is the audit-obligation flag; the user position moved
   to `P_10`. The numeric statements are stable and the *introduction* is where the same name
   means different things — the reverse of the intuitive expectation.
4. **Cash-flow aggregates lost their amounts.** In v1-0 the `A`/`B`/`C` aggregates extended
   `TKwotyPozycji` and carried values directly; in v2-0E that extension is gone. The path
   `RachPrzeplywow/PrzeplywyPosr/A/KwotaA` exists in old files and does not exist in the new
   schema. Compute from sub-positions or mark as missing.

Also: a typo in a node name was fixed between generations
(`DodatkoweInformacjeIObjasnieniaJednstkaInna` gained its missing letters). The parser must
accept both spellings.

**Detect the generation from the root namespace or root element name, never from the year.** The
2025/2026 vintage is deliberately mixed: RDF rejected the new structures with "Nie znaleziono
schemy XSD", and on 2026-02-06 MF permitted statements for 2025 to be prepared under the old
structures. Schema version numbering also reset when the variant changed, so versions cannot be
compared lexicographically.

## 4. Representation traps inside a single document

- **Everything is optional.** Every balance-sheet and income-statement element has
  `minOccurs="0"`. A missing element means "not reported", not zero. The canonical model must
  distinguish `None` from `Decimal(0)`.
- **The same letter means different things in the two income-statement variants.** The element is
  an `xsd:choice` between the by-function and by-nature variants. `J` is financial income in one
  and income tax in the other; `C` is gross profit on sales in one and profit on sales in the
  other. Naive path-based mapping computes profitability from income tax and reports no error.
  **Mapping must be parameterised by variant, and the canonical model must speak in concepts,
  not letters.** The cash-flow statement has the same problem across direct and indirect methods.
- **User-defined positions.** `PozycjaUszczegolawiajaca_N` may appear, unbounded, between every
  pair of standard positions, with a free-text name of up to 250 characters. Children need not
  sum to the parent without accounting for them.
- **A third amount column.** `KwotaB1` holds restated comparatives. Where accounting policy
  changed or prior-period errors were corrected, `KwotaB` and `KwotaB1` differ, and the pipeline
  must decide which is the source of truth for year-on-year comparison.
- **The additional information block is unstructured** — free text plus an optional base64
  attachment, in practice a PDF or even a .docx. Every ratio that needs the notes is out of reach.
  The one exception is the income-tax note, which is structured (`P_ID_1`..`P_ID_11`).
- **`P_x` codes are template-specific.** `P_1C` is the tax number in one template and the activity
  code in another. Do not assume stable meaning across templates.
- **Activity classification is a choice** between the 2007 and 2025 vintages, so a company's
  reported industry can change between years without the business changing.

## 5. The dictionary is generated, never written

Every element in the schema carries an `xsd:documentation` with the exact statutory wording of
the position. The full mapping dictionary can therefore be **generated from the schema for all
33 templates at once**, rather than typed by hand or, worse, produced from a model's memory.

This is the same doctrine the project already applies to the activity-code dictionary, and it
matters more here because there is nothing to copy from — no library, no published mapping.

## 6. Signatures: order of operations is not negotiable

MF accepts a qualified signature in **XAdES in all three topologies** — enveloped, enveloping and
detached — as well as the trusted and personal signature. So a downloaded artefact may be `.xml`
(unsigned or enveloped), `.xades`/`.xsig` (an envelope to unwrap), or XML plus a separate
signature file.

**A signed document will not validate against the schema.** The structures declare closed content
models with no reference to the XML-signature namespace and no wildcard, so an enveloped signature
makes a valid filing fail validation. That is a consequence of schema design, not a data defect.

Required order: read bytes → detect signature topology → **verify the signature over the original
bytes** → extract the payload → then validate against the schema. Whitespace inside a signed
document is significant: no pretty-printing, no re-serialisation, no normalisation before
verification.

## 7. Offline schema resolution is mandatory

The schema graph spans **three hosts and two URL schemes**, with shared nodes reached by more than
one path, and `targetNamespace` on a different domain than the file location. Concretely:

- The namespace URL `http://www.mf.gov.pl/schematy/...` **301-redirects to an HTML page**. Fetching
  the namespace as a URL yields a web page, not a schema.
- **libxml2 removed its built-in HTTP client in 2.15.0 (2025-09-15)**, after deprecating it in
  2.13.0 and dropping FTP in 2.14.0 — and it never supported TLS, while four of the five schema
  locations are `https://`. Remote schema loading through lxml is simply dead.
- One file name contains parentheses; one host serves XSD as `application/octet-stream`.
- Deduplicate by `targetNamespace`, not by URL.
- Template rotation has no API: the catalogue lists template numbers without direct links, and
  the government template repository has no programmatic interface.

**Vendor the whole graph into the repository, validate offline, and add a regression test that
compares stored hashes against the live URLs** to catch a silent replacement upstream.

## 8. Library assessment

| Library | State | Verdict |
|---|---|---|
| **signxml** 5.1.0 (2026-07) | Apache-2.0, active CI, history since 2014, one maintainer | **The reader.** Only actively maintained Python XAdES implementation with a *verifier*. Bus-factor risk. Note: it verifies cryptography, **not** eIDAS qualification |
| **xmlschema** 4.3.2 | MIT, 477 stars, 11 open issues | **Best fit for the distributed-schema problem.** `download_schemas`, `uri_mapper`, `export(save_remote=True)` rewrites locations to local paths |
| **xsdata** | MIT, active | Generates typed dataclasses from XSD — realises the "generated, not hand-written" rule directly |
| **lxml** 6.1.3 | active | Parsing; subject to the HTTP removal above |
| **xmlsec** | MIT, active | Solid crypto engine, undocumented XAdES layer; C build cost on Windows CI. Useful as a second opinion |
| **endesive** | MIT, one maintainer | Good for **producing** signed test material in several topologies |
| **xades** (etobella) | last commit 2023-02, still declares Python 2.7 | Do not build on it |
| **EU DSS** (esig/dss) | maintained by the Commission, ~1000 stars | **Reference oracle**, but Java — run it in a container and query over REST for conformance tests, not as a Python dependency |

**Gap with no Python answer:** nothing consumes the EU trusted lists, so no Python library can
answer "is this certificate from a Polish qualified provider". Either delegate that to DSS or
state plainly that the tool does not adjudicate qualification.

## 9. Test corpus: three layers

MF states twice, in writing, that it **does not publish example XML files**. There is no public
research corpus. Publishing real statements in an open repository would process the personal data
of the signatories, regardless of the download being lawful.

1. **Signature layer — free and clean.** The signxml repository ships `test/xades` with 71
   conformance files under Apache-2.0, including national samples and negative cases, plus W3C
   interoperability vectors. This covers the signature path without touching Polish company data.
2. **Domain layer — generated.** Generate the model and synthetic instances from the vendored
   schemas, then sign them with a throwaway internal CA in all three topologies. Full control over
   edge cases: empty sections, negative values, missing optional blocks, broken cross-sums. This
   is the only layer that can be published.
3. **Reality check — outside the repository.** A few dozen real documents kept locally, never
   committed; CI publishes statistics and hashes only.

## 10. Validation rules must be authored

Unlike the JPK world, **no catalogue of validation rules or error messages is published** for
e-sprawozdania — the only stated rules are those in the schema itself. Syntactic rules come from
the XSD; arithmetic rules (assets equal liabilities, children plus user positions sum to the
parent, net profit in the balance sheet agrees with the income statement) must be derived from the
element hierarchy and the statutory layouts, and kept as a **separate versioned declarative
artefact**, not as conditions scattered through the parser.

One industry analysis claims roughly 30% of filings contain formal errors and a similar share
carry manipulation risk. Single source, unpublished methodology, authored by people connected to a
data vendor — treat as an order of magnitude, not a number. It does justify building cross-checks
in and reporting them, rather than assuming correctness.

## Open items

- Intermediate schema versions (v1-1, v1-2, v1-3) were not diffed — the archive exposes only
  v1-0 and the current page only the 2026 generation. The claim that positions were also stable
  through v1-2 is **unproven**. The method takes minutes once the file is obtained.
- No real document was downloaded from RDF: packaging, whether signatures are preserved, and the
  exact form of accompanying documents remain secondary-source claims.
- Formats of the new RDF categories (ESG, income-tax, group reports) are unverified.
- `pyhanko-certvalidator` status unverified; its predecessor repository is archived.
- Whether lxml resolvers work for `xsd:import`/`xsd:include` is undocumented — test before relying
  on it; `xmlschema` with `uri_mapper` is documented and is the safer default.
