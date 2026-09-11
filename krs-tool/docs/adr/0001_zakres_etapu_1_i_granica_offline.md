# ADR-0001: Scope of stage 1 and the offline boundary

Date: 2026-09-10
Status: accepted
Author: P0w3r223
Related to: `ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md` (the decision that created this sub-project), and the five research documents in `ceidg-tool/docs/research/`

---

## Context

`ceidg-tool/docs/adr/0023` decided that a KRS company risk-assessment tool becomes a fifth
sub-project rather than a second command surface inside `ceidg-tool`, and required that this
sub-project restate the decision in its own series before any implementation begins. This is that
restatement, plus the decisions that only make sense once the first stage is actually scoped.

Two owner decisions of 2026-09-10 constrain everything here:

- the address is a separate sub-project, `krs-tool/`;
- **no request goes out to the court-register API before the Ministry of Justice answers on the
  scope of art. 60a of the KRS act** — and, on the owner's instruction, that ban covers development
  and testing, not only production.

## Decision

### 1. Stage 1 is the register-signal layer and nothing else

In scope: reading a register extract supplied as a file, deriving risk signals from it, printing a
report, and journalling the assessment.

Out of scope for stage 1: the financial-statement parser, ratio engine, insolvency-prediction
models, industry benchmarks, and NIP-to-KRS resolution. The last one is out **because it needs the
network**, not because it is hard.

### 2. The offline boundary is structural, not configured

Stage 1 contains no HTTP client and no network dependency. Not disabled by a flag — absent from the
import graph and absent from the dependency manifest.

The reason is the difference between a mechanism and a promise. A configuration flag can be flipped
by a helpful refactor and nobody would see it; an absent dependency cannot. This is the same move
`ceidg-tool` made for its own egress gate, one layer earlier.

Three independent observers, because each misses what the others catch:

| Observer | Catches | Misses |
|---|---|---|
| AST scan over the import graph | a direct import of anything that can open a socket | a transitive dependency |
| manifest check over `pyproject.toml` | a networked package pulled in by declaration | `socket` from the standard library |
| socket ban active for the whole test suite | anything that actually tries to connect at run time | code no test exercises |

Each of the three has a **test of the scan itself**, with a seeded violation. A rule without a
demonstrated failure mode is indistinguishable from an empty set — this project inherits that lesson
rather than rediscovering it.

### 3. Deviation from ADR-0023: the rate limiter and the HTTP client are not copied yet

ADR-0023 decision 1 lists `ratelimit.py` and `httpclient.py` among the modules copied from
`ceidg-tool`. Stage 1 does not copy them.

Copying them would put `httpx` into the dependency manifest, which turns decision 2 from a mechanism
back into discipline: the manifest observer would have to carve out an exception, and an exception is
exactly the shape a future violation hides in. Both modules arrive together with the `OpenApiKRS`
adapter, in the same change that makes them meaningful.

This is recorded as a deviation rather than performed silently, because ADR-0023 is the document a
reader will consult first.

### 4. The result of a signal rule is three-valued

`Sygnal | Wykluczony(przeslanka) | Nieustalony(nierozstrzygniete=[...])`.

A `Sygnal` for the missing-statement rule may only be produced when **all six** lawful reasons for an
absent statement have been answered "does not apply". Two of the six cannot be determined from an
extract at all, and one has not been sampled, so **in stage 1 that rule can never fire — it always
returns `Nieustalony`**.

That is the required state, not a defect. `ceidg-tool/docs/adr/0023`, the "Gate" table, forbids the
accusation until the registry publication lag is measured and the six reasons can be excluded. The
three-valued result is what makes the prohibition structural instead of a comment: there is no code
path from an unanswered premise to an accusation.

### 5. The accusatory vocabulary does not exist in the tree

`Poziom` is an enumeration with no member meaning "late". A scan checks the rule catalogue and the
report texts against a closed list of accusatory words. Combined with decision 4, the tool has no
value to carry the accusation and no word to phrase it in.

### 6. The signal layer cannot read the clock

Every date in a signal comes from the extract, primarily `naglowekA.stanZDnia`. `signals/` may not
import `clock` or call `datetime.now`, `date.today` or `time.time`, and a scan enforces it.

Two consequences, both wanted. The only sentence the tool can form is "no entry for financial year X
**according to the register state as at D**", which is the honest one. And reproducibility comes for
free: running the same extract tomorrow produces the same result, so the `odtworz` command has
something to assert.

### 7. Numeric literals other than 0 and 1 are forbidden in `signals/`

Six months and fifteen days have nowhere to live except the rule catalogue, and "31 December" cannot
be written at all. This is ADR-0023 decision 7 moved one layer earlier, because in stage 1 the
thresholds that matter are statutory periods rather than ratio bands.

### 8. What may be proven by a test, and what must be named as unmeasured

Because decision 2 forbids us from making a single request, no fixture in this project may make a
claim about the API. Fixtures make claims about **a file an operator saved**, and each claim cites
the file, its SHA-256, the date and who supplied it.

Everything above the file — parsing, catalogue, rules, report, journal, replay — is fully testable.
Everything about the wire is recorded in `docs/niezmierzone.md` with the person or event that can
close it. A claim may stand in the traits file or in the unmeasured list, never in both, and a test
enforces that.

Synthetic extracts may be built, and may not be cited as evidence. A report produced from one carries
markers that make it impossible to mistake for a real one — the same doctrine as `ceidg-tool`'s five
demo markers, and for the same reason.

## Consequences

- **The flagship feature ships unable to fire.** Anyone reading the output will see "cannot be
  determined" where they expected a verdict. That is the correct answer given the evidence, and the
  report says why in words.
- **Stage 1 needs no token, no key and no credential**, so the secret registry it copies is empty.
  The module says so in its docstring, because masking that is believed to protect something while
  protecting nothing is worse than no masking.
- **Two rows of the ADR-0023 "Gate" table cannot be closed in stage 1** without extracts of a
  suspended company and of a partnership of natural persons. If those are not supplied, stage 1 ends
  with them open, deliberately.
- **The copied spine is smaller than ADR-0023 anticipated**, per decision 3.

## What would reverse this

- A ministerial position on art. 60a flips decision 2 from a boundary to a configuration value, and
  decision 3 stops being a deviation. The same follows from a favourable answer to the application
  for re-use of public sector information — see `../wniosek-ponowne-wykorzystywanie.md`, which is the
  route with statutory deadlines and an appeal, and therefore the one to use first.

**Correction of 2026-09-11.** An earlier phrasing here and in `ceidg-tool/docs/adr/0023` spoke of a
commercial intermediary "who carries the risk". That is imprecise: Polish law has no licensing
regime for resellers of register data, so commercial providers are ordinary re-users of the same
public sources. They carry **operational and contractual** risk — availability, aggregation, an SLA
— and cannot confer a legal title stronger than the statutory one. Buying data from one does not
answer the art. 60a question; it only moves the querying to somebody else's infrastructure.
- A measured registry publication lag, plus extracts of the two unsampled company types, would let
  decision 4 produce an actual signal — at which point decision 5 needs revisiting, because a
  vocabulary that cannot express a true finding is then in the way.
