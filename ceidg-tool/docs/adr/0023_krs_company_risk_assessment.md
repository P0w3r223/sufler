# ADR-0023: A KRS company risk-assessment tool — address, data model, and the line between signals and documents

Date: 2026-09-10
Status: accepted — owner approved the address (option B) and the art. 60a posture on 2026-09-10; no implementation plan until the sub-project's own ADR-0001 restates this
Author: P0w3r223
Related to: root ADR 0074 (co-tenancy in `Main`), ADR-0001 (protocol facts as data), ADR-0005 (`FieldSpec`), ADR-0011 (assistant, boundary rules 12-13), ADR-0012 (PKD vintage transition), ADR-0013 (one producer of a canonical identity), ADR-0014 (running without the register), ADR-0017 (no operator input ends in a dead end), and the five documents in `docs/research/`

---

## Context

A second product is proposed: given a KRS or NIP number, produce a **report** — not a decision — on
a Polish company's financial position and risk, in the shape a bank uses. The recipient is an
operator with no knowledge of APIs.

Three findings from the reconnaissance shape everything below, and each contradicts the intuitive
design.

**The register and the documents live under two different regimes.** The KRS API is free,
token-less, CC0 and machine-readable. The document repository has no API at all, its operator
states it is protected against robots, and its address changed three times in one year. This split
— not the parser, not the models — is the primary architectural fact.

**The most valuable layer needs no statement.** `dzial4` of the free extract is in practice a
register of operating insolvency: tax and social-insurance arrears under enforcement, creditors
holding unsatisfied writs, bankruptcy petitions. On a healthy company it comes back empty, so
non-emptiness is a binary flag. `dzial6` carries liquidation and dissolution; the full extract
carries a dated entry history. Latency is hours against months for a statement. The canonical
Polish bankruptcy dataset contains 65 features, **all of them financial ratios**, so the literature
a team reaches for by default cannot speak about register signals at all.

**No open-source reader for Polish e-sprawozdania exists.** That is simultaneously the largest cost
of this project and its only genuine differentiator. The good news came from diffing the schemas
rather than reading about them: balance-sheet and income-statement **element names are identical
across schema generations**, so multi-year comparison is feasible.

## Constraints that shaped the decision

Given, not derived. Recorded because several decisions below exist only to make them *structural*
rather than promised.

- The document repository is **not queried automatically**.
- **Numbers are computed by ordinary code.** A language model at most describes the result in
  Polish. Independently confirmed by law: a rule engine written by humans is probably outside the
  AI Act definition, unlike a trained model.
- **Position dictionaries are generated from the official schemas** — never typed by hand, never
  produced from a model's memory.
- **Service addresses, schema versions and thresholds live in configuration.**
- **Every issued assessment is reproducible.** Also a legal requirement: personal-interest
  protection extends to legal persons and **unlawfulness is presumed**.
- **Scope is companies entered in the KRS.** Sole traders are out of scope and the boundary must be
  visible in the design.

## Options considered for the address

**A. A second command surface inside `ceidg-tool`.** Cheapest by a wide margin. **Rejected on legal
grounds, not engineering ones:** the artefact it produces is a register card spanning both
populations, and assessing a natural person is automated decision-making under art. 22 GDPR and a
high-risk category under the AI Act, with no available legal basis in a B2B setting. Engineering
objections are secondary but real — the input contract is a bulk-search contract while this product
is a single-subject lookup, the store schema is CEIDG-shaped, and the allowed-hosts list would grow
from two hosts to five, widening the control this project calls its highest-value one.

**B. A fifth sub-project, `krs-tool/`, with a copied spine.** — **chosen.**

**C. B plus an extracted shared core.** Premature: it refactors a finished product at accepted
gates to serve one that does not exist. No sub-project in this tree shares code today.

**D. A separate repository.** Technically clean, and what the draft of root ADR 0074 recommended —
but the owner decided that question the other way on 2026-09-10 and nothing here is new evidence.

## Decision

**B**, with nine sub-decisions.

### 1. Address: `krs-tool/`, a fifth sub-project; the spine is copied, and the trigger to stop copying is written down

A sibling of `ceidg-tool/` in `Main`: own `pyproject.toml`, own environment, own `docs/adr/`
starting at 0001, a fifth entry in the root CI matrix.

**The name carries the scope boundary** — `krs-tool` says in the directory listing what the tool
does and does not cover.

Copied, with provenance recorded (source path plus date of copy): `errors.py`, `clock.py`,
`ratelimit.py`, `httpclient.py`, `progress.py`, `logsetup.py`, `safetext.py`, `richtext.py`, the
secret-registry half of `config.py`, and the *shape* of the `ui/` layer together with ADR-0017's
no-dead-end rule. Copied as artefact: the PKD transition table, or a regeneration by the same
script.

**Not copied:** `store.py`, `criteria.py`, `normalizer.py`, `exporter.py`, `apiprofile.py`. Each is
CEIDG-shaped in substance, not merely in naming.

**The extraction trigger:** the day a defect is fixed in one copy of the egress gate or the secret
masking and not the other, a shared core gets extracted. Until then, two copies of a rule with one
owner per process is cheaper than a coupling that lets this project redden a finished one.

**Quality gates: both.** `mypy` strict (which only `ceidg-tool` has) *and* the function-length
ceiling (which only `ceidg-tool` lacks). The ratio engine and the parser are the two places most
likely to grow a 300-line method, and a ceiling is free on a green field.

### 2. One identity, one producer, and a named outcome for "not our population"

`identity.py` owns `NumerKRS` as a type with a single producer. A NIP is resolved through the
statistical register, whose legal-person report carries the KRS number; batch work belongs at night,
where the limit is 10 000/h against 6 000/h in office hours. **That route is unaffected by decision
9** — it rests on a different register and a different act.

A NIP with no KRS entry produces `BrakZrodlaPublicznego` — a named, printed outcome, **not** an
empty report and **not** "no financial statement", because statements of payers outside the register
are covered by fiscal secrecy.

The scope boundary is therefore structural: **no `NumerKRS` can be constructed for a sole trader, so
no sole trader can enter the pipeline.** The one thing that could quietly break it is a future
"just look it up in the other register too" convenience, which is named here as the thing that
reopens this ADR.

### 3. The signal layer is a bounded context, built first; the parser owns no socket

Three packages, one direction of dependency:

- `signals/` — the register extract and, later, the other sources. Produces
  `Sygnal(kod, poziom, data_zaobserwowania, zrodlo, tresc_surowa, wazny_do | None)` against a
  catalogue in `signals/reguly/*.yaml`: code, level, statutory basis, lifetime. The debtors register
  gives signals a defined lifetime (10 years generally, 3 after an arrangement is performed, 7 for
  enforcement data), so decay is data, not a constant.
- `sprawozdanie/` — **imports no HTTP library and no database driver.** Its input is bytes the
  operator handed over. "We do not query the repository automatically" becomes a property of the
  import graph, which is a stronger claim than care taken while writing a fetch.
- `ocena/` — pure. No HTTP, no database, no terminal formatting. This is what makes "numbers are
  computed by ordinary code" checkable in one place.

**Staging follows, and stage 1 is a usable product.** Signals need no XSD work and deliver what a
bank checks alongside the statement. An owner who stops after stage 1 has a product, not a torso.

The one crossing between contexts: the filed-documents block tells the assessment layer **which
periods were filed**, so a parsed document is joined to an expected period rather than trusted about
its own. That parser handles both period spellings observed on two independent samples within one
company, must not assume a calendar year, and **reports an unparseable entry rather than guessing**.

### 4. The canonical model speaks in concepts; letters and paths never leave the mapping table

A set of `Pojecie` values, each `Decimal | None`, carried in a `Sprawozdanie` that also holds
template code, detected generation, statement variant, period, amount column, unit, and user-defined
positions attached to their parent.

- **`None` is not zero.** Every statement element is optional; missing means "not reported". No
  defaults at any layer.
- **The mapping key includes the variant, and no path leads from a letter to a concept without it.**
  `J` is financial income in one income-statement variant and **income tax** in the other; `C` is
  gross profit on sales in one and profit on sales in the other. Naive path mapping computes
  profitability from income tax and reports no error. The cash-flow statement has the same problem
  across direct and indirect methods.
- **Map by element name, never by documentation text** — a label phrase occurring 12 times in the
  old schema occurs 0 times in the new one, so label mapping breaks exactly at the generation
  boundary, silently.
- **Detect the generation from the root namespace or root element, never from the year.** The
  2025/2026 vintage is deliberately mixed, and version numbering reset when the variant changed, so
  versions cannot be compared lexicographically. Both spellings of the corrected node name are
  accepted.
- **The unit is part of the value.** "In zloty" and "in thousands" are separate templates, an entity
  may switch between years, and the thousands variant is typed as an integer — rounding is baked in.
  Every derived figure inherits a precision marker, and a year-on-year comparison crossing a unit
  change is labelled.

**The dictionary is generated for all 33 templates at once** by a build script over the vendored
schemas, with a generated header carrying source hash, legal basis and build date. What cannot be
generated is the concept assignment, because the concept vocabulary is ours; that half lives in
reviewable YAML and is the artefact a reviewer reads.

**Schemas are vendored, whole.** The graph spans three hosts and two URL schemes with shared nodes;
the namespace URL redirects to an HTML page; the underlying XML library removed its HTTP client and
never supported TLS, while four of five schema locations are HTTPS. Deduplicate by target namespace,
not by URL. A regression test compares stored hashes against the live URLs.

**Signature order of operations is not negotiable:** read bytes, detect topology, **verify over the
original bytes**, extract payload, then validate. No pretty-printing or re-serialisation before
verification. **A signed document will not validate against the schema** — closed content models
with no signature namespace — so that failure is a consequence of schema design and must not be
reported as a data defect.

Toolchain: `signxml` (the only maintained Python XAdES verifier; single maintainer, a named
bus-factor risk), `xmlschema` for offline resolution, `xsdata` for generated types, `lxml` for
parsing. The EU reference implementation is a container-run oracle for conformance tests, never a
Python dependency. Nothing in Python consumes the EU trusted lists, so the tool states plainly that
it verifies cryptography and does not adjudicate qualification.

**Arithmetic rules are their own versioned declarative artefact**, not conditions scattered through
the parser. No catalogue of validation rules is published for these documents, so ours are authored
— which is exactly why they need a version. Filer-written free text in user-defined positions is
hostile input and passes through the neutralisers before reaching a spreadsheet or a terminal.

**On the third amount column:** prior-year figures come from the prior-year document where we hold
one; the current document's comparatives are the fallback, restated figures winning — with **the
restatement recorded as a fact in the assessment record**, because a corrected prior period is a
signal, not a footnote.

### 5. An abbreviated statement is a penalty, not an "N/A"

Bank scoring cards score reporting quality and completeness **separately**. A bank does not pretend
it has full data; it downgrades for their absence. That is the pattern copied.

- Every ratio declares the concepts it requires; the engine returns a value or
  `Nieobliczalny(brakujace=[...])`. No third path, no defaults, no zeros.
- **Completeness is a scored dimension of the report in its own right**, with an explanation naming
  what was missing and why.
- **No reconstructed cash flow.** The differences between balance-sheet movements and cash-flow
  figures are invisible without the notes, and a labelled proxy is still a number a reader will
  quote. Independent validation weakens the pressure anyway: the only tested model using cash-flow
  data performed poorly. For entities without one, Polish bank practice measures debt-service
  capacity as net profit plus depreciation, which is computable from what we have.
- **Introduction fields are mapped per template with no cross-template default** — the numeric
  statements are stable and the introduction is where names moved, the reverse of the intuitive
  expectation.

### 6. The assessment journal, and the difference between reproducing and recomputing

`dziennik/` is append-only, enforced by an AST scan. One record per issued assessment: identity and
moment; subject and resolution path; **one manifest hash** over the whole configuration bundle;
per-source endpoint as configured, request time, status, response digest and the register's own
as-of; per-document name, hash, signature topology, verification result, detected generation,
validation outcomes; the result; and the Polish prose actually shown with the numeric-token check
result.

`krs-tool odtworz --ocena-id X` re-runs from the **stored inputs** under the pinned methodology and
asserts an identical result, failing loudly when catalogues have moved. A reproducibility claim whose
violation has no observer is not a claim.

**Retention splits the record in two.** European case law prohibits holding register-sourced
information longer than the register does. The journal persists; the **payloads** live in a separate
store under configured retention with a purge command. After a purge the journal says "payload purged
on D, digest H" and `odtworz` answers **"not reproducible from retained data"** rather than quietly
fetching today's register and calling the result a reproduction.

### 7. Thresholds, formulas and benchmarks are versioned data; no licensed benchmark ships

- **`wskazniki.yaml`** — id, formula over concepts, required concepts, **and a definition id**. A
  ratio has more than one legitimate definition, and **a comparison is permitted only where the
  definition id matches the benchmark's** — enforced, not documented, because the stricter
  definitions need splits an abbreviated balance sheet lacks, which is systematic bias, not noise.
- **`modele.yaml`** — every constant carries author, year and page. Variable transforms are data, not
  prose: percentage scaling and the ×360 multiplier are part of the entry, and the two coefficients a
  widely circulated source has swapped are recorded in the corrected order. Each entry carries its
  validation domain and its **independent** accuracy — **no model above 80%**, against 92-98% claimed
  in source papers. Three to five models run in parallel and **disagreement is reported as a signal,
  not resolved**. Contested constants carry a `spor` field; the model whose coefficient differs
  tenfold between sources ships **disabled by default**. Models by the same authors get different
  identifiers, because compilations confuse them.
- **`progi.yaml`** — threshold profiles, each label carrying its provenance, and **every threshold
  printed carries that label**. No regulator publishes threshold values. The five-grade supervisory
  skeleton ships as a named convention **with its applicability date attached**.
- **`benchmarki/`** — a port with a provider directory and **no licensed data in the repository**.
  The tool computes the ratio and points the operator at the source. Only the openly licensed
  aggregates ship, labelled as section-level averages over larger entities — explicitly not a
  distribution and not comparable to a micro entity. Obtaining a licence means adding a provider
  file, nothing else.

**The vintage bridge is required and one-to-many.** All benchmarks use the 2007 classification while
registers carry a mix, and the statement itself lets the filer choose. The tie-breaking rule is
config, and the chosen predecessor is printed rather than assumed.

**Boundary rule: no numeric literal other than 0 or 1 appears in `ocena/`.** An AST scan makes
"thresholds live in configuration" a mechanism instead of a habit.

### 8. The language model describes; the verdict sentence is written by code

`opis/` receives only already-formatted strings and returns Polish prose. **Every digit group in the
returned text must appear in the input view model**; an unknown number means the model computed
something, which triggers refusal, a fall back to deterministic template prose, and a journal entry.
The grade sentence and every threshold comparison are rendered by code — the model may explain, it
may not pronounce. Unfair-competition law penalises disseminating untrue or misleading information
about another business's economic or legal situation for gain, and the commercial nature of the tool
satisfies the purpose element on its own, so the whole defence rests on truthfulness. The prose layer
is an optional extra: a missing key turns description off with a sentence, it does not stop the
report.

### 9. The register channel is a port, and the network adapter is gated on a ministerial answer

`RejestrKRS` is a port with two adapters:

- `OdpisZPliku` — **the default, and for now the only enabled one.** The operator saves the register
  response to a file; the tool never opens a socket to the register.
- `OpenApiKRS` — implemented and tested against recorded fixtures, **disabled by configuration**
  until the Ministry of Justice answers on the scope of art. 60a.

**Owner decision of 2026-09-10:** no automated querying of the court-register API before a
ministerial position is obtained. The request cites Commission Implementing Regulation (EU) 2023/138,
which lists financial statements and management reports among high-value datasets to be made
available free of charge via API and bulk download.

**How the manual path stays cheap.** The distinction that matters is automated querying, not the file
format. A single human action per company — saving the register response from a browser — yields the
full structured extract, so the signal layer keeps working on rich data rather than degrading into a
second document parser. If a stricter reading is preferred later, the adapter is unchanged and only
the input format moves.

This decision does **not** extend to the statistical register used for NIP resolution, nor to the
VAT taxpayer register; both rest on different acts and different access regimes.

**Rate limits: measure, do not trust the declaration.** When the network adapter is enabled, the
default profile is conservative and backoff is present from the first request. The declared absence
of limits is contradicted by commercial providers, and this project has already paid once for
trusting a recorded value over a measurement.

**One trap gets a rule of its own**, because it hurts colleagues rather than the tool: the VAT
register's search method allows a low daily quota and exceeding it **blocks the ordinary web search
from the same address until midnight**. Volume goes through the daily flat file; the API is for
pointed single verification. Sources disagree on the number, so it is configuration and it is
verified with the operator before it is relied upon.

**The store is built for a bulletin that is not used yet.** Version 1 is per-subject and on demand,
but observations are stored **append-only with their observation time**, not as a current-state
cache. That costs almost nothing today and is the difference between adding portfolio monitoring
later and rewriting the store.

## Consequences

- **Two products, two populations, two stores, and no combined card.** The legal boundary and the
  code boundary are the same line. The cost is a wizard, a limiter, an egress gate and an exporter
  that exist twice.
- **A stage-1 product exists before any XSD is read**, and it is the layer with the lowest legal risk
  and the shortest latency.
- **Stage 1 requires one manual operator action per company** until the ministry answers. This is a
  real cost in the wizard's flow and it should be visible in the first design of that flow, not
  retrofitted.
- **The parser is the schedule risk.** The corpus is three layers: freely licensed conformance files
  for the signature path; **generated** synthetic instances signed with a throwaway internal CA for
  the domain path, the only layer that can be published; and a few dozen real documents kept
  **outside the repository**, with CI publishing statistics and hashes only. The ministry states twice
  in writing that it publishes no example XML, and publishing real statements would process the
  signatories' personal data regardless of the download being lawful.
- **Every printed number carries a label** — convention, expert opinion, regulation with its
  applicability date, or measured distribution with its licence. The verbosity is the product's legal
  position.
- **The tool will regularly say "cannot be computed"**, which under decision 5 is a correct answer
  with a reason attached.
- **A model that disagrees with another is shown as disagreeing.** Users will ask for one number; the
  honest answer is that no model exceeded 80% on an independent sample.

## Gate: what must be measured before a claim ships

| Claim | Cannot ship until |
|---|---|
| "No statement filed within the statutory period" | The registry publication lag between filing and the entry appearing is **measured**. It was not. Until then the report states the observation — "no entry for FY X as at date D" — not the accusation |
| Any deadline at all | The rule computes only the statutory backstop, **6 months plus 15 days from the balance-sheet date**, because the 15-day deadline runs from the approval date and the register publishes only the filing date. **"Filed late" is not provable from public data.** The balance-sheet date comes from the register field, never from 31 December |
| Any "missing statement" signal | The **six lawful reasons** are excluded first: full-year suspension (the most common false alarm), start in the second half of the year, bankruptcy or restructuring with an administrator, business in inheritance, a no-obligation declaration, entity outside the entrepreneurs register. The extract of a suspended company and of a partnership of natural persons **were not sampled** — fetch both before building the rule. The pandemic-era extension is exhausted and the tool must be able to say so |
| Any sustainability-reporting signal | An episodic exemption lets most entities skip these obligations for financial years beginning 2025-2026. **A tool that flags absence here would be wrong.** The country-by-country income-tax report is a new document type due 12 months after the balance-sheet date and is easy to mistake for a financial statement |
| Any register API pacing | Empirical measurement with backoff, once the adapter is enabled at all |
| Any claim about a downloaded document's packaging | **No real document has been downloaded.** Whether signatures are preserved is a secondary-source claim; one manual download closes it |
| Any comparison against an industry distribution | A licence answer from the rights holder, or a purchase |

## Open questions, the interim answer, and what changes when each closes

| Open question | Meanwhile | When it closes |
|---|---|---|
| Art. 60a versus the open API | **Network adapter disabled**; operator-supplied extracts; request to the ministry with the high-value-datasets regulation as the lever | Favourable: flip one configuration value. Unfavourable: the product stays on operator-supplied extracts, or moves to a commercial provider who carries the risk |
| Machine access to the debtors register | The strongest single signal — enforcement discontinued for lack of assets — is **not available**, and the report says so rather than silently omitting a level-1 source | A new adapter and new catalogue entries. Nothing in `ocena/` changes |
| Benchmark licences | Ship none; compute and point | A provider file and a provenance line |
| The contested model coefficient, and whether that model has a Polish estimation sample | Present, **disabled by default**, marked contested | Enable or delete — one YAML edit |
| A third-decimal discrepancy in another model | The better-sourced value, with the discrepancy printed in the methodology appendix | One YAML edit |
| Intermediate schema versions, never diffed | Detect generation from the root; an unknown generation is a refusal with a named reason, never a best-effort parse | Add the generation to the mapping table |
| Listed companies and separate IFRS statements | Out of scope; the model carries a **source/format field** so they attach later without a migration | A second parser behind the same canonical model |
| The current consolidated text of the KRS act | Check before acting; legal portals still serve the pre-amendment version | May reopen row one |

## What would reverse this decision

- **A written legal analysis clearing assessment of sole traders** would remove the strongest
  argument against option A — not the others, but it would make the address a genuine question again.
- **The parser proving materially harder than the schema diff suggests** makes a commercial statement
  API the cheaper path, turning this project into the signal layer plus an anti-corruption layer over
  someone else's parsed data. Stage 1 is deliberately built so that discovering this wastes nothing.
- **A defect fixed in one copy of the egress gate and not the other** converts decision 1 from "copy"
  to "extract", per its own trigger.
- **A second consumer of the ratio engine** would justify separating `ocena/` into its own
  installable. Not before.
