# ADR-0001: Phase-1 findings live in an `ApiProfile` (data), not in code constants

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: INSTRUKCJA_CLAUDE_CODE.md (phase 1 questions), docs/api_notes.md, docs/design/phase2_core.md

---

## Context

Eight facts about the API dialect are unknown before the probe runs (page numbering
start, max `limit`, `ids[]` batch size, `nazwa` semantics, `pkd` format, value casing,
report coverage, field fill rates). The official documentation contradicts itself on
page numbering (argument table says `page=1`, the example `links` use `page=0`).
Test and production environments may differ. The instruction requires
`Criteria.to_params()` to produce a query "consistent with phase-1 findings", so
`Criteria` touches the dialect by definition.

Every unknown gets exactly one of three tactics, never "we will see after the probe":

| Tactic | When | Cost of a change after the probe |
|---|---|---|
| Eliminate: accept both shapes | 204 vs empty list, `spolka` vs `spolki`, `data-utworzenia` vs `dataUtworzenia` | zero |
| Parametrise: a value in `ApiProfile` | numbers or enums: `page_start`, `max_limit`, `ids_batch_size`, `pkd_format` | edit YAML + add a fixture |
| Defer: keep raw JSON, decide at export | which fields are populated, how many 1:N relations become sheets | edit a `FieldSpec` list, no network |

Anti-pattern to avoid: `if batching: ... else: ...`. One code path parametrised by
chunk size; `ids_batch_size = 1` is a valid special case, not a separate branch.

## Options

**A. Constants in `config.py`, edited after the probe.**
Simplest, but one value for both environments, users cannot override without editing
code, tests must monkeypatch globals (leaks between tests), data changes look like
logic changes in git. Effort S, risk medium.

**B. `ApiProfile` (frozen pydantic) loaded from `profiles/test.yaml` and
`profiles/prod.yaml`, injected into the client and into `to_params(profile)`.**
Per-environment values, CLI/env override without code, tests build a profile
explicitly, `profile_hash` stored per run detects resume on a changed dialect.
One more concept, risk of growing into a flag bag. Effort S/M, risk low.

**C. Auto-detection at start-up (client probes `limit`, `page`, `ids[]`).**
4-6 requests out of the 50/3 min budget on every run, non-deterministic, a detection
failure blocks the tool. Effort M, risk high.

## Decision

**B**, with C available as a separate explicit command (`ceidg-tool profil --wykryj`)
that runs the probe logic once and writes the result to YAML.

Responsibility split that keeps the probe from forcing a rewrite:

- `Criteria` validates **semantics** (date order, NIP checksum, PKD shape, allowed
  status). Independent of the dialect.
- `to_params(profile)` **renders** (casing, dots in PKD, date format, parameter
  names). Dependent on the dialect.

Signature: `def to_params(self, profile: ApiProfile) -> list[tuple[str, str]]`
(a list of pairs because `status=A&status=B` needs repeated keys; canonically sorted
so `fingerprint()` is stable). A `to_params()` reading a global profile singleton is
rejected: hidden global state, `criteria.py` stops being pure.

Profile content (protocol facts only; conservative defaults until the probe runs):

```yaml
base_url: "https://test-dane.biznes.gov.pl/api/ceidg/v3"
paging_mode: links            # links | numeric
page_start: 0                 # used only when numeric
send_page_on_first_request: false
default_limit: 25
max_limit_firmy: 25
max_limit_zmiana: 500
count_semantics: total        # total | page
ids_batch_size: 1             # 1 = no batching; the probe raises it
max_url_length: 4000          # 100 GUIDs in a query is about 4.1 kB; split by length too
detail_mode: query            # query (?ids=) | path (/firma/{id})
list_root_key: firmy
detail_root_key: firma
changes_root_key: identyfikatoryWpisow
empty_result_statuses: [204]
param_case: lower
pkd_format: compact           # compact (6201Z) | dotted (62.01.Z)
date_format: "%Y-%m-%d"
rate:
  windows: [[48, 180], [960, 3600]]   # 4 % headroom under 50/1000
  min_spacing_s: 3.6
  cooldown_s: 185                     # 180 + clock-drift margin
```

## Consequences

- A change after the probe is a YAML edit plus a fixture. No structural change.
- The profile describes the protocol only. Behaviour switches ("fetch details")
  belong to `Criteria` or `Settings`, never to the profile. Every profile field must
  map to a question in `docs/decisions.md`; if it cannot, it does not belong there.
- Resuming a run whose `profile_hash` differs from the active profile is refused
  (results would mix two dialects).
- Revisit when test and prod profiles turn out identical and the API gains versioning.
