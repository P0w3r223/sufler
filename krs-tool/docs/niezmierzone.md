# Unmeasured: claims this project is not entitled to make

Date: 2026-09-10
Status: living — a row leaves this file only when something measured it
Author: P0w3r223
Related to: `adr/0001_zakres_etapu_1_i_granica_offline.md` decision 8

---

## Why this file exists

Stage 1 sends no request to the court-register API, by owner decision. It therefore cannot learn
anything about the API, and it must not pretend otherwise. `ceidg-tool` paid for the opposite
mistake: a hand-written line in a fixture was cited by an ADR as a measurement, and a whole detour
rested on it.

The rule that follows is mechanical, not a matter of care:

- a claim about **the file an operator saved** may live in `tests/fixtures/odpis_traits.yaml`, and
  must cite the file, its SHA-256, the date and who supplied it;
- a claim about **the API, the wire or the register's behaviour over time** may live only here;
- a test asserts that no identifier appears in both places.

## Unmeasured

| # | What is not known | Who or what closes it |
|---|---|---|
| 1 | Rate limits, pacing, error shapes and HTTP status vocabulary of the register API | The ministerial answer on art. 60a, then measurement with backoff. Not before |
| 2 | Whether a manually saved file is byte-identical to what the API returns. A browser may reformat; **whitespace in a saved file is not evidence about the wire** | A comparison that cannot be made under the current constraint. Stays open through stage 1 |
| 3 | The lag between a document being filed and its entry appearing in the register | Measurement against the live API. This is row 1 of the ADR-0023 "Gate" table and it blocks the missing-statement accusation |
| 4 | The extract of a **suspended** company | Owner supplies a saved file. Until then the full-year-suspension premise is undeterminable and the missing-statement rule cannot fire |
| 5 | The extract of a **partnership of natural persons** | Owner supplies a saved file. Until then the no-obligation-declaration premise is unsampled |
| 6 | The extract of a company **in liquidation or bankruptcy** | Owner supplies a saved file. Affects the bankruptcy premise and the division-6 rules |
| 7 | Whether `rejestr=P` and `rejestr=S` return different key sets | Two saved files, one of each |
| 8 | Whether a document downloaded from the financial-document repository preserves its signature | One manual download. Belongs to stage 2, recorded here because it was assumed twice already |
| 9 | The real daily quota of the VAT taxpayer register search method (sources say 100, others 300) | Confirmation with the operator. Out of stage 1 scope; recorded so nobody hard-codes either number |

## Deliberately not on this list

Anything above the file: parsing, the rule catalogue, term arithmetic, report rendering, the journal
and replay. Those are fully testable offline and their claims belong in tests, not here.
