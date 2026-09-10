# ADR-0019: Every module belongs to a rule, or to a list that says why it does not

Date: 2026-09-09
Status: **proposed** (diagnosis only; no code changed)
Author: P0w3r223
Related to: ADR-0009 (boundary rules and the scan), docs/design/phase2_core.md:86-166,
docs/audit-2026-09-09.md (item C2), docs/audit-architecture-2026-09-09.md (F-A2, F-S1, F-E5)

---

## Context

The scan in `tests/test_boundaries.py` enforces thirteen of the fourteen rules and is unusually
careful about one failure mode: **a rule that checks nothing**. Three tests exist for it —
`test_every_pure_module_actually_exists:203`, `test_every_rich_module_actually_exists:347`,
`test_every_core_module_actually_exists:506` — and the last one ends with

```python
assert all(CORE_FORBIDDEN.values()), "reguła z pustym zbiorem zakazów niczego nie sprawdza"
```

That is the emptiness guard. What the scan has no answer for is the **completeness** question:
*which module is covered by no rule at all?* Three consequences are already measured.

**`ratelimit` is in no rule.** `grep -c ratelimit tests/test_boundaries.py` returns **0**, while
`CLAUDE.md:154` states that `ratelimit` may not import the database — a guarantee with a stated
reason (the limiter takes its lock heartbeat through a callback precisely so it never sees `store`)
and no enforcement. A planted `from .store import Store` passes every boundary test. The module is
in fact a shared kernel: imported by `client.py:44`, `store.py:20`, `console.py:21` and
`assistant/caller.py:31`.

**`reports.py` and `demo/` arrived unplaced, and grew divergent semantics.** Neither appears in the
design document's module map nor in any rule. `reports.matches_criteria` and `demo/rejestr._pasuje`
now disagree with each other and with a recorded measurement (ADR-0018). Nothing structural could
have said "a new record source appeared and belongs nowhere".

**Rule 11 lists a file it cannot analyse.** `test_boundaries.py:725` includes `./ceidg_probe.py` in
the rule-11 scan with a comment explaining why the root copy is covered. The scan looks for
`httpx` / `httpx2` / `anthropic` client factories (`:648`); that file builds its transport with
`urllib.request.urlopen` (`ceidg_probe.py:72`). It therefore passes **vacuously**, while bypassing
both the proxy policy and the shared request log. A gate that names a file and cannot see its defect
is worse than one that never mentioned it, because the name reads as coverage.

## What the ecosystem offers, and why this must be built here

External research (docs/audit-architecture-2026-09-09.md §5.3) settles this direction:

- ArchUnit **rejects by default** a rule executed against an empty set (`failOnEmptyShould`),
  documenting the motivating case as a package rename that left the rule behind. Java only.
- The equivalent ArchUnit request — *report every rule that matched nothing* — has been open since
  2022-07-30 with no maintainer response.
- The only completeness mechanism in the Python ecosystem is import-linter's `exhaustive`, which
  works **solely** for `layers` contracts that declare `containers`. Rules 9, 10, 11 and 12 here are
  call-shape rules, not graph contracts, so `exhaustive` cannot reach them.
- import-linter's answer to a rule it cannot enforce is the transferable one: since 1.9.0 it
  **refuses to start**, raising `Invalid forbidden module …`. Unenforceable is a configuration
  error, not a silent pass.

So there is no tool to adopt for this rule shape. The instrument has to be the project's own — and
the project already built its narrow half.

## Options

### A. A coverage test plus an explicit exemption list

One test enumerating every module under `ceidg_tool/` and asserting each appears either in at least
one rule's named set or in a `POZA_REGULAMI` tuple carrying a **reason string**. Adding a module
forces a one-line decision; deleting a rule's last member fails loudly.

- Cost: small; one test, one tuple, and a reason for each of today's uncovered modules
  (`reports.py`, `demo/*`, `records.py`, `exporter.py`, `ratelimit.py`).
- Effect: answers the question no tool answers, in the same shape as the three emptiness guards
  already present.
- Risk: the exemption list becomes a dumping ground if reasons are not reviewed. Mitigation is that
  a reason is a sentence somebody has to write, which is the same mechanism the project uses for
  every other guard comment.

### B. Adopt import-linter alongside the scan for the graph rules only

Move rules 1-8 and 13 to import-linter `layers`/`forbidden` contracts with `exhaustive`, keep the
AST scan for 9, 10, 11, 12.

- Cost: medium; two mechanisms, two configuration surfaces, two failure formats in CI.
- Effect: `exhaustive` answers the completeness question for the graph half only.
- Against it, measured: rules 1-6 and 8 have **no recorded catch** in this project's history, while
  9, 10 and 12 do. Moving the rules that never caught anything to a second tool, and keeping the
  ones that did in the first, buys completeness for the half that has not needed it. Research also
  records that import-linter's `include_external_packages` defaults to **off**, so a rule naming an
  external library silently covers nothing until it is switched on — the same defect class, in the
  tool brought in to fix it.

### C. Do nothing; treat the gap as acceptable

- Against it: C2 has been open across two audits, `CLAUDE.md` claims a guarantee that nothing
  enforces, and two of this audit's Tier-1 findings (F-S1, and F-E5 via F-A1) are downstream of it.

## Decision

**Proposed: A, plus two immediate entries that make today's claims true.**

1. Add the coverage test with `POZA_REGULAMI` carrying reasons.
2. Add `ratelimit.py` to `CORE_FORBIDDEN` with `{"sqlite3"}`, which makes `CLAUDE.md:154` checkable.
3. Make the rule-11 scan **refuse** a file it lists but cannot analyse — import-linter's pattern —
   so `ceidg_probe.py` reports itself instead of passing vacuously. This is separable from the
   question of whether that file should exist in its current form (Tier-1 item 2).

Option B is rejected on measurement, not on taste: the rules it would move are the ones with no
history of catching anything, and it doubles the maintenance surface for the half that needs it
least. If the graph rules ever outgrow the scan, B remains available.

## Consequences

- `docs/design/phase2_core.md` gains a fifteenth entry describing the coverage rule, and the module
  map has to be completed — it currently names 13 of 41 modules (F-A1).
- The exemption list is a visible inventory of what nobody has thought about, which is the point.
- Rule 11's refusal will fail on `ceidg_probe.py` the moment it is added, which is the correct
  first result and should be landed together with, or immediately before, that file's fix.
