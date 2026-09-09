# ADR-0017: The assistant asks one clarifying question instead of returning nothing

Date: 2026-09-09
Status: **accepted 2026-09-09 by the owner** (implemented, verified on the demo register
with the live assistant and on production for the report path the same day)
Author: P0w3r223
Related to: ADR-0011 (phase-4 assistant; this reverses its "no clarification round trip in v1"),
ADR-0008 (the decision sequence), ADR-0012 (PKD vintage)

---

## Context

A UX pass on 2026-09-09 drove the real wizard against the synthetic register with the **live**
assistant, playing an operator who types little and does not know exactly what they are looking
for — the operator this tool exists for. Seven interpretations, zero CEIDG requests. The assistant
itself did well: two words (`fryzjer poznań`) became `miasto: Poznań; PKD: 9621Z`; two typos
(`fryzjezy w poznaiu`) changed nothing; an impossible filter (`duże firmy IT w Warszawie`) came
back with four PKD codes and three limitation sentences rather than a refusal.

Three paths failed, and they failed with the same shape: **the operator got a message, lost what
they had written, and landed back in the menu.**

| Input | What happened | Where |
|---|---|---|
| `wszystkie firmy` | Empty interpretation screen (*"rozumiem jako: tylko lista podstawowa"*), confirmation question defaulting to **"tak, szukaj"**, then `Błąd: Podaj przynajmniej jedno kryterium` | the `is_empty` guard in `prepare_fetch` |
| any query returning 0 | One sentence, then `wyjdz` — so the `Co dalej?` question, whose only useful option is *"popraw kryteria"*, **never appeared at a zero result** | the `count == 0` branch in `prepare_fetch` |
| menu item 3 with criteria no report covers | `ConfigError: … Użyj --zrodlo api albo auto` — a command-line flag named at an operator who is inside the wizard and has no flags | the report-source guard in `prepare_fetch` |

The first is the worst, because the screen actively misleads: it asks "is this what you meant?"
about an empty result, with the affirmative as the default. Pressing Enter — the whole point of a
tool for a lazy operator — leads to an error.

ADR-0011 Decision 6 had ruled this out: *"No clarification round trip in v1. Ambiguity comes back
as a limitation code and a partially-filled `Criteria`, not as a question."* That reasoning held
while ambiguity meant *partially* filled. It does not cover **nothing** filled, where there is no
partial result to show and the limitation codes have nothing to attach to. The owner reviewed the
measured runs and reversed the decision, with the explicit instruction that the assistant should
help rather than hand the operator off somewhere else.

## Decision

**When the assistant produces criteria with no filter at all, the flow enters a clarification
round instead of failing.** Three properties make it more than a retry:

**1. The trigger is the empty criteria, not the model's question.** `AssistantAnswer` gains two
fields, `pytanie` and `propozycje`, and the prompt instructs the model to fill them when it cannot
build a single filter. But `flow.collect_from_description` enters the round on
`wynik.kryteria.is_empty()` — whether or not the model asked anything. A model that forgets the
question gets a code-authored one (`texts.DOPYTANIE_ZAPASOWE`). This follows the project's own
rule that a guarantee whose observer depends on the thing being guarded is not a guarantee.

**2. A proposal is an input, not a result.** The model's suggestions are *sentences*, and picking
one feeds it back to `interpret` as a new description. It therefore travels the identical path as
anything the operator types: PKD validated against the local dictionary, `Criteria` built by
`translate.to_criteria`, and the confirmation screen authored by code with PKD **names from
`pkd2025.yaml`**. The model gains no shortcut to `Criteria` merely because the text came from it,
so ADR-0011 Decision 6's core property — *the one text the operator acts on is written by the
code* — survives intact. This is also why proposals are not structured criteria: that would have
been the shortcut.

**3. There is always a route that does not need the model.** Two options are present in every
clarification round: **choose a voivodeship from a list of sixteen** and **write it differently**.
The voivodeship route returns `Criteria` directly — no second model request, because there is
nothing to translate and paying the model to read back a value the operator just picked from a
closed set would be spending on our own answer. It is the only question in this tool that cannot
be answered wrongly, which is exactly what an operator who knows nothing needs.

**Zero hits become a question with concrete options**, computed by code.
`Criteria.poszerzenia()` returns candidates that each drop **one** filter, ranked by how often
that filter is the culprit: literal-matching text fields first (`nazwa`, `ulica`, `kod`), then
`miasto` (the register stores the official name; a district or colloquial form misses), then
`pkd` — a code can be valid, alive in PKD 2025 and still absent from every record in the region,
because 58.6 % of the register is still on the 2007 vintage (ADR-0012) — then the closed-set
filters. Dropping `pkd` clears `pkd_2007` too, since the latter is an extension of the same
filter; a candidate keeping it would still filter by industry and would not be a widening.
Candidates that would empty the criteria are never produced. Each screen row carries **why that
filter is a suspect**, because "spróbuj bez miasta" without a reason teaches nothing.

**A report that does not cover the criteria states the reason and offers the API path.** The
reason is a code (`WIELE_WOJEWODZTW`, `BRAK_WOJEWODZTWA`, `STATUS_SPOZA_RAPORTU`,
`BRAK_DZISIEJSZEGO`) that `flow` computes and `texts` names — `texts` may not import `reports`
(boundary rule 6), the same split as `STATUS_BRAK_W_RAPORCIE`. Reasons are checked in order of
permanence: the ones inherent to the criteria before the one that passes by itself overnight.

## What this costs, and what it must not cost

**One extra model request per accepted proposal.** The clarification round itself is free — the
question and the proposals arrive in the answer that was going to be made anyway.

**Non-interactive runs are unchanged, deliberately.** All three new questions declare their
behaviour under `--tak`:

| Question | `safe_default` | Why |
|---|---|---|
| `brak_trafien` | `True`, default `wyjdz` | Widening drops a filter, i.e. changes the population somebody asked for. A schedule ends at zero rather than fetching something else. |
| `raport_na_api` | `False` | A schedule that asked for the two-request path must not silently spend one request per page because no report existed that morning. |
| `dopytanie` | `False` | A description with no filter cannot become anything without a person. Schedules have the YAML query file. |

**The 50 000 threshold, the cost table and the split machinery are untouched.** A clarification
that produces a very broad query (a whole voivodeship) is not a new risk: it meets `count`, the
cost table and the threshold exactly like any other query, and now also the zero-hit path.

## Alternatives rejected

| Option | Verdict |
|---|---|
| **A.** Keep ADR-0011 as it stands; the operator rewrites the description | This *is* the measured defect. The description is lost and nothing is learned about which filter was wrong. |
| **B.** Proposals as structured criteria rather than sentences | Saves one model request and gives the model a path to `Criteria` that skips `to_criteria`, the PKD dictionary and the code-authored screen. That path is the whole safety argument of ADR-0011. |
| **C.** A conversational session with the model | Rejected for the reason ADR-0008 Decision 2 rejected a session state machine: the assistant plugs into `Criteria`, not into the session. One round, then the ordinary sequence. |
| **D.** Code-authored proposals only, no model question | Cheaper and weaker: code cannot know that "wszystkie firmy" is a person who has not decided on an industry. Kept as the **fallback**, not the mechanism. |
| **E.** Ask whenever the query looks too broad, not only when it is empty | "Too broad" is a count, and counts are what the cost table and the 50 000 threshold already gate — with a real number instead of a guess. Asking earlier would duplicate that decision with less information. |

## Consequences

* `assistant/schema.py` gains two fields; both are in `NIE_SA_FILTRAMI` in the test that pairs
  schema fields with `Criteria` fields, so a future **filter** field still trips it.
* `translate.py` caps the question at 200 characters and proposals at four × 120, de-duplicated.
  These are the only fields where the model writes a sentence the operator reads; without a
  ceiling one verbose answer turns the menu into a wall of text.
* `Criteria` gains `poszerzenia()` — a statement about the register (which filter is usually at
  fault), so it lives with the validators and is testable without a terminal.
* Three screens join `ui/texts.py`: `clarification`, `zero_hits`, `report_unavailable`.
* `tests/test_pomoc_operatorowi.py` holds the round trip, the safety net, the request counts and
  the key-parity checks. Two older tests reversed their assertions, and both said so in their
  docstrings: `test_a_zero_hit_query_stops_before_the_choice` (now `…offers_a_way_out_instead_of_
  ending`) and `test_asking_for_the_report_source_when_none_exists_is_a_configuration_error`
  (now `…offers_the_api_instead`, with a second test pinning the `--tak` refusal).

## Verification

Re-run on the demo register with the live assistant, 2026-09-09:

* `wszystkie firmy` → *"Zapytanie o wszystkie firmy jest zbyt szerokie — jaki region lub rodzaj
  działalności Cię interesuje?"* with four concrete proposals; picking one produced
  `miasto: Kraków; PKD: 9621Z` and a normal confirmation screen.
* `nie wiem czego szukam` → same shape, plus the voivodeship route reachable without the model.
* `fryzjerzy w Krakowie` → 0 hits → two ranked widenings with reasons → dropping `miasto` gave
  36 records and a workbook.
* A bad NIP now reads `nip: NIP '1234567890' ma błędną sumę kontrolną` instead of a pydantic dump
  with a link to errors.pydantic.dev.

**Production run, 2026-09-09, with the owner's consent — exactly two requests.**
`pobierz --zrodlo raport -w wielkopolskie --miasto Gniezno --pkd 9621Z --pkd-2007 --tak`
against `dane.biznes.gov.pl`: `GET raporty -> 200` (1.11 s) then `GET raport -> 200` (7.12 s),
with the server's own quota counter going 1000 → 999 → 998. 21 MB archive of 2026-09-08,
282 records for Gniezno.

That run immediately caught a defect this very change had introduced. The summary said
*"telefon i e-mail: nie pobrano — lista podstawowa ich nie zawiera"* for a report export whose
rows **do** carry contacts (63 phones, 68 e-mails — 22 % and 24 %). The predicate asked
"were details fetched", and contacts have two sources: `detail_json` on the API path, and the
CSV row itself on the report path, which has no details and can have none. The fix for a silent
untruth on the commoner path had produced a loud one on the rarer path — the same shape as
A1/ADR-0013 in the audit, where repairing one silent loss activated another. Corrected to ask
whether the *source* can carry contacts at all, verified by re-exporting the same run from the
store at **zero further requests**, and pinned by
`test_the_report_path_has_contacts_without_any_details_fetched`.
