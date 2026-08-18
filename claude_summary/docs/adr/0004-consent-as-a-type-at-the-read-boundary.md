# ADR 0004 — Consent as a type at the read boundary, not a convention in the orchestrator

Date: 2026-08-17
Status: accepted
Author: P0w3r223
Related to: [[0001-structure-and-prompt-discriminator]] (§ *Consent* — the convention this ADR
replaces), [[0003-redaction-of-sensitive-content]], `core/consent.py`,
`adapters/transcript_files.py`

---

## Context

ADR 0001 made reading private prompt history fail-closed: without `--consent` or
`CLAUDE_SUMMARY_CONSENT=1` the tool refuses with a clear message. What it did *not* say — because
at the time there was only one caller — is **where** that check lives. It lived in `app.run`, the
orchestrator, as an early return before any adapter was touched.

That placement makes the guarantee a property of the *call site*, not of the *capability*. The
adapter that walks `~/.claude/projects` and yields human prompts accepted a call from anyone:
`scan_prompts(projects_dir, ...)` was a perfectly ordinary function that read private material.
Nothing in its signature, its types, or any test recorded that it must not run without consent.

The failure mode is not malice, it is ordinary maintenance. A second entry point — a scheduled run,
a small script that reuses the scanner for one project, a refactor that hoists the scan above the
consent check to reorder the CLI's error messages — reintroduces the read with no gate and no
signal that anything was lost. The guarantee ADR 0001 stated in prose had no representation in the
code that could break loudly when it stopped holding.

`--consent` is also the switch that ADR 0003's whole redaction argument presumes; a silent bypass
would remove the premise of that decision rather than one of its safeguards.

## Decision

**Consent becomes an argument of the read, carried by a type that cannot be constructed beside the
gate.** `core/consent.py` introduces:

- `ConsentProof` — a frozen dataclass whose `__post_init__` refuses any value except one internal
  sentinel, so `ConsentProof()` raises `ConsentError`;
- `grant_consent(*, flag, env_consent) -> ConsentProof | None` — the single place that sentinel is
  passed, returning `None` when neither the flag nor the environment gave consent;
- `require_consent(proof)` — a runtime guard for untyped callers, rejecting anything that is not a
  real `ConsentProof` (so `None` or `True` cannot pass as "yes").

Every function in `adapters/transcript_files.py` that reads transcripts takes `consent:
ConsentProof` as a **required keyword argument** and calls `require_consent` eagerly — eagerly
because `iter_prompts` returns a generator, and a check deferred to first iteration is a check that
a caller who never iterates never performs. `app.run` keeps its early return, but it is now the
place that *obtains* the proof rather than the place that *is* the gate.

**Scope of the guarantee, stated in the module header and repeated here: this defends against a
mistake, not against an adversary.** Python can defeat any of it — `object.__new__`,
`copy.deepcopy`, importing the sentinel. Someone doing that is doing it deliberately. The value is
narrower and worth having: a plain call to the read without going through the gate does not
type-check under mypy and does not run.

## Consequences

- The gate travels with the capability. A new caller of the scanner cannot compile without first
  deciding, explicitly, where its consent comes from.
- `mypy` becomes part of the privacy contract rather than a style check — a missing argument is now
  a privacy defect the type checker reports.
- Refusal moves to the boundary, so the error message names the actual reason at the point the read
  was attempted, not several frames above it.
- Two test files record the contract directly: `tests/core/test_consent.py` (the type refuses to be
  built beside the gate) and `tests/test_privacy_contract.py` (the read path refuses without proof).
- Cost: one more parameter threaded through the adapter's call chain, and a type whose purpose is
  legible only with the header comment. That is accepted — the alternative was prose in an ADR.
- ADR 0001 § *Consent* remains historically accurate about the *policy* (fail-closed, flag or env);
  its description of the *mechanism* is superseded by this decision.

## Alternatives considered

- **Leave the check in the orchestrator and add a test asserting the order of operations.** Rejected:
  the test pins today's call graph, not the capability, and a new entry point simply is not covered
  by it.
- **A module-level boolean or environment re-read inside the adapter.** Rejected: global mutable
  state, invisible in signatures, untestable without monkeypatching, and it moves the decision away
  from the person who made it.
- **A stronger, unforgeable token (signed, or a capability object handed out by a broker).**
  Rejected as disproportionate: the threat model here is a future maintainer in a hurry, not an
  attacker with local code execution — who, having local code execution, can read `~/.claude`
  directly without this tool.
