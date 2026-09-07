# 0073 — Guards iterate over the protected surface, and tool metadata travels with the tool

Date: 2026-09-07
Status: accepted
Author: P0w3r223
Related to: [ADR 0066](0066-content-trust-classes-and-sticky-conversation-taint.md) (the taint
trigger set, the first instance moved under this rule), [ADR 0067](0067-observability-audit-journal-and-notifier-dead-letter.md)
(the per-call road both concerns ride), [ADR 0068](0068-agent-tool-names-and-the-cost-of-a-wrong-one.md)
(the tool surface this reflects over). The "one source of tools, two surfaces" decision this
leans on is decision 0009 of the `infra-docker-workmate` package, not the local ADR 0009.

---

## Context

A review on 2026-09-07 found the same defect independently in five places, and the project had
already named the class three days earlier while reviewing stage 4 of the GitHub bridge:

> A guard that iterates over the **safeguards** freezes the list of known cases.
> A guard that iterates over the **things being protected** extends itself.

The five instances on `Main`:

1. **`_TAINTING_TOOLS`** (`adapters/inbound/responder/conversational.py`) — a frozen set of five
   *strings*. A tool renamed, or a new builder bringing foreign content, leaves the set silently
   correct-looking and the taint simply never fires.
2. **The audit safe-field allowlist** (`core/domain/audit.py`) — one global list of field names
   shared by every tool; a tool's own structural fields are not its own business.
3. **The tool-description ceiling gate** (`tests/core/test_tool_descriptions.py`) — its own
   docstring confesses it: the probe compares the roster against the *signature* of a test-local
   helper, so a gate that exists in `adapters/inbound` and is not mirrored there is invisible to it.
4. **The stage-4 bridge gate** — asserts the presence of an event kind in `notifier._KIND_LABELS`
   (a safeguard) while the kind is added by a *mapper*; nine kinds emitted in `src` today have no
   label, so the coupling it leans on does not hold.
5. **The prompt redaction gate** — its fixture failed to compose a new field twice.

The root is nameable in one sentence: **reflection over the protected surface exists in this
repository exactly once, and it is trapped in the test layer.** `tests/conftest.py` can enumerate
the whole gate surface by walking the config package; exactly one test file uses it. The prompt,
`preflight.sh` (another repository, in bash), `roadmap.md` and the procedure-catalogue gate derive
the same truth **by hand, each in its own way** — and each drifts separately.

The pattern done right is in the same repository: `tests/test_gates_closed_by_default.py`
discovers settings classes reflectively and keeps an explicit registry whose *removal* requires a
conscious edit. This ADR generalises that shape and applies it to the tool surface.

## Decision

**1. Metadata about a tool lives on the tool's specification, not in a list somewhere else.**
`ToolSpec` gains `taints: bool` — "does this tool's *result* carry content from outside the
capability gates" (the question ADR 0066 R2 asks). The flag is declared where the tool is built,
next to its name, description and function.

**2. The consumer derives, it does not restate.** The conversational door stops holding a literal
set of names. It asks the runtime for its catalogue and unions it with the per-turn `extra_tools`
it built itself, keeping the names whose spec says `taints`. A renamed tool carries its flag with
it; a new tool arrives already classified.

**3. Defaults are ergonomic, but silence is not allowed in the tool package.** `taints` defaults
to `False` so the dozens of `ToolSpec(...)` in tests stay readable. Inside
`core/application/tools/` the default is not enough: an AST guard walks every `ToolSpec(...)` call
in that package and requires the keyword to be **written out**. A new tool cannot be added without
answering the question; the failure names the file and line.

**4. A guard must iterate over the protected things.** When a guard and the thing it protects can
both be enumerated, the guard enumerates the *protected* set and asks a question about each
element. Enumerating the safeguard is allowed only where the protected set genuinely cannot be
discovered — and then the registry is explicit, with the `test_gates_closed_by_default.py` shape:
adding an entry is free, removing one requires deleting a line someone will read.

**5. The mutation check follows the path of the future change.** A guard is validated by making
the change it is supposed to catch — "add an event kind through the mapper", "add a tool without
the keyword" — not by making the change the *measurement* happens to walk ("add a label"). A
positive control pinned to a count works against its author under the wrong iteration direction:
it looks like a completeness guard while guarding a set that never grows.

## Consequences

- The taint trigger set stops being a string list an adapter maintains. `_TAINTING_TOOLS` is gone.
- The tool surface acquires a place for further per-tool metadata. The audit safe-field allowlist
  (instance 2 above) is the natural next tenant and is **not** moved by this ADR: it is fail-safe
  today (an unknown field is redacted, never leaked), so it costs a review it does not yet repay.
  Recorded here so the next reader knows the omission is a decision, not an oversight.
- Doors that wire their own runtime need no change: the derivation asks the runtime, and the
  runtime already owns the base catalogue.
- Test fakes that stand in for the runtime now have to expose a catalogue if the turn is supposed
  to taint. That is a faithfulness gain, of exactly the kind the taint probes needed on 2026-09-07:
  a fake that does not model the contract measures itself.
- Instances 3, 4 and 5 are **not** closed by this ADR. Instance 4 belongs to the stage-4 branch and
  its fix shape is already written down (iterate over kinds emitted in `src`); 3 and 5 live in the
  test layer and follow the same rule when they are next touched.

## Alternatives considered

- **Keep the name list, add a guard binding it to the builders.** Rejected: that is the *fifth*
  instance of the class, not a fix for it. The guard would enumerate builders by hand — a list that
  freezes the same way, one level up. (Today's guard names four builders; there are fifteen.)
- **Make `taints` a required field with no default.** Rejected as a cost with no gain: it forces
  thirty-plus test-local specs to answer a question the test does not ask, and the AST guard
  already makes silence impossible where it matters.
- **Pass the flag through the audit callback (`(name, args, status, taints)`).** Rejected: it
  widens the ADR 0067 contract so the runtime can carry a concern it deliberately does not know
  about. Exposing the catalogue is narrower and reads as what it is.
- **A module-level `TAINTS` declaration per tool module.** Rejected: it is metadata *near* the
  thing rather than *on* it, and it splits in two the moment a module builds two tools with
  different answers.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | The AST guard passes on a call it cannot see (a spec built through a helper or a comprehension). | The guard walks every `Call` whose callee is named `ToolSpec` in the package, regardless of nesting; a spec built outside the package is outside the rule by construction and the door's derivation still reads its flag. |
| R2 | `taints=False` becomes the reflex answer under review pressure. | The flag sits on the same line as the tool's name in a diff, which is where a reviewer already looks; and the question is one sentence long. |
| R3 | Exposing the catalogue invites a caller to mutate it. | The accessor returns a tuple. |
| R4 | The default `False` silently disables taint for a door whose runtime is a test double without a catalogue. | Accepted and made visible: the derivation is a single expression in the door, covered by a probe that fails if it stops consulting the runtime. |
