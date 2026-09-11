# The tooling ecosystem around KSeF, and what it means for building here

Date: 2026-09-11
Status: draft — first reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: `ksef-dostep-i-obowiazek.md`, `ksef-struktura-faktury.md`

---

## The headline, and it is the opposite of the register project

For Polish electronic financial statements the answer was "no maintained open-source library exists,
anywhere, in any language" — and that single finding decided both the cost of that project and
whether it had a differentiator at all.

**Here a living ecosystem exists.** Two maintained Python libraries, both MIT, both tracking the
interface contract, one released within a week of this document. **We do not write the HTTP client.**
We pin a version — of the library and of the contract it implements.

## Official tooling

The ministry maintains exactly **two official SDKs: C# and Java**. There is no official Python
client. The artefact we can rely on safely is the **interface specification** plus the narrative
documentation; everything else is somebody else's code.

Worth noting for calibration: the healthiest unofficial client in the whole ecosystem is **PHP** —
more stars than any Python one and no open issues. Python is a second-tier language here, despite
being the natural language for cost analytics.

Other things the ministry publishes and that are worth using:

- **Documentation as a repository with an issue tracker.** Over a hundred open issues is not a sign
  of collapse but a live channel of integrator experience — the best available source on traps,
  better than any tutorial.
- **A published algorithm for incremental download**, with the high-water-mark mechanism. See
  `ksef-dostep-i-obowiazek.md`.
- **Published hard limits.**
- **An official PDF visualiser** under a permissive licence, in TypeScript. Its fork-to-star ratio is
  unusually high, which suggests visualisation is a painful and unfinished problem that people keep
  taking away and reworking.
- **A public outage interface**, without authorisation, publishing planned maintenance and failures.
  Cheap and sensible in any harvester: it separates "our bug" from "the system is down".

## Python libraries

| Project | Health | Scope |
|---|---|---|
| The larger one | 35 releases, latest within a week of this document; automated pull requests tracking contract versions; an author-run pre-release audit; an open sign-off issue for 1.0 | Claims full endpoint coverage, sync and async, sessions, exports, tokens, permissions, certificates. Python 3.12+ |
| The smaller one | 27 releases, latest two weeks before; zero open issues, which at this size reads as low traffic rather than maturity | Contract-aligned, several authentication routes, metadata queries, invoice and receipt download, batch and interactive sessions, builders with schema validation, a command line. Python 3.10+ |

Both are **0.x**. Pin the version and pin the contract version.

**A generational cull is visible in the data.** Everything built for the previous generation of the
interface is dead or archived, including a well-starred Java client. The living projects were almost
all created after the current interface went live, which makes **repository age an inverse signal of
quality here** — and means no project has a year of production history.

One caveat that does not show in a readme: the larger library's own pre-release audit closed a list
of issues worth knowing before building on it — secrets leaking through logging and representations,
faulty batch-session resumption, a one-time token vulnerable to middleware retries, transfer errors
misclassified as authentication errors, unversioned persisted state, builder output not validated
against its own schema, and reversed date ranges accepted. Most were closed. It shows the class of
problem in this layer and that the library is still walking toward 1.0.

Two dead projects are still worth reading as references rather than dependencies: a command-line
harvester implementing exactly our incremental flow in a couple of hundred lines, and a script
collection that is the clearest available reference for the cryptography and authorisation.

## What practitioners say is missing

**Filtering purchase invoices.** An integrator's report states that the role of an entity cannot be
used as a filter, and concludes that selecting only purchase invoices is impossible. **This is in
tension with the documentation**, which describes a subject-type filter whose second value means
received invoices. The report's issue appears to concern a narrower case — third parties on the
invoice — but this is **load-bearing for the whole product** and is the first question of the second
reconnaissance pass. It is recorded here unresolved rather than settled in the convenient direction.

**Metadata are not enough for cost analysis.** Recurring reports: no tax amount in currencies other
than the domestic one, divergence between metadata and the amount payable, requests to extend
metadata with amounts and party descriptions. The consequence for architecture is that the full
document has to be parsed — which is the same conclusion `ksef-struktura-faktury.md` reaches from the
other direction.

**Limits at scale.** An issue open since December 2025 with dozens of comments: the documentation
does not say how limits add up across several entity roles and several clients. An accounting office
with hundreds of clients hits a wall, and the answer amounts to "queue on your side".

**Visualisation**, **stability of long-running exports**, and **a schema that is still moving** —
open requests around decimal precision, missing discount fields, item-count ceilings and absent
validation patterns for classification codes. Write the parser tolerantly and version it.

## Commercial landscape

Essentially unexamined, and said plainly rather than inferred. The search budget was exhausted, so
accounting systems and commercial integrations were not verified; a ministry page that might have
listed certified applications returns a not-found error.

Indirect signals from public code repositories — gateways wrapping the official SDKs, synchronisation
to cloud drives and document tools, agent-style assistants over accounting platforms — suggest a glue
layer is forming between the system and everyday tools, which would imply that ready-made accounting
systems do not cover the buyer-side download-and-analyse scenario. **This is a hypothesis, not a
finding.**

## Operational conclusions

1. Do not write the HTTP client. Choose one of the two libraries, pin both versions, and run a short
   spike on the test environment — one real package export through each — before committing.
2. Implement the high-water-mark loop ourselves regardless of the library: it is state logic we must
   control and test, including idempotency, deduplication and truncation handling.
3. Plan for the hourly limits and for the outage interface from the start.
4. Parse the full document; metadata will not carry the analysis.
5. Treat the schema as a moving target and version the parser.

## Open items

- Commercial systems and whether they expose anything usable on the buyer side.
- Actual download figures for the two libraries — the statistics service refused the request, so star
  counts are the only proxy, and a poor one.
- Whether a Python port of the official visualiser exists, if visualisation ever enters scope.
- Library readme claims were not verified against code. Release metadata proves activity, not
  quality.
