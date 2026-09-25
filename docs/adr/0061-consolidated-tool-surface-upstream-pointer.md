# 0061. Consolidated tool surface — the decision lives in the deployment package

Date: 2026-08-09
Status: accepted
Author: P0w3r223
Related to: docs/adr/0054-reduce-jira-to-read-only-my-tasks.md,
  docs/adr/0056-agent-system-prompt-two-blocks.md,
  docs/adr/0057-shell-executor-container-without-network.md;
  upstream: `docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md` in `infra-docker-workmate`

---

## Context

Release 1.6.0 reduced the agent runtime surface from **22 `ToolSpec` registrations across 13
builders** to **five tools** — `Bash`, `Notes`, `GitHub`, `Jira`, `Schedule`. The code for that
change is in this repository. The decision is not: it was taken, measured and written up in the
deployment package (`infra-docker-workmate`), because the criterion it rests on — which barrier
the shell in the executor container cannot cross — is a property of the *deployment*, not of the
application.

That asymmetry is fine on its own. It stops being fine when someone reads this repository alone.
Two neighbouring decisions of the same rebuild, [0056](0056-agent-system-prompt-two-blocks.md)
(prompt) and [0057](0057-shell-executor-container-without-network.md) (executor), exist on both
sides: a package decision and an application ADR. Consolidation existed on one side only, so the
ADR series here jumps from a surface of 22 tools straight to code that registers five, with no
record of why. A reader following `docs/adr/` would find the *consequence* recorded in
[ADR 0054](0054-reduce-jira-to-read-only-my-tasks.md) — and would find it contradicted by the
code, which now accepts a `member` argument that ADR 0054 says will never exist.

## Decision

**The consolidation decision stays upstream; this ADR is the pointer, and it records the two
application-side facts that upstream cannot own.**

We do not restate the trade-off analysis here. It is measured, it is long, and duplicating it
would create two sources to keep in sync — the failure mode this series has hit before
(`docs/reference/tools.md` drifted by seventeen tools while the code moved).

What this ADR does own:

1. **The criterion, in one sentence, because the code cites it.** A typed tool exists only where
   the shell in the executor *cannot* reach: no network (Jira, GitHub, Shifts), no state volume
   (`events.db`), no path into the model's context, or an effect outside the container (file
   delivery, note write). `Skill(name)` and `File(write/list)` are therefore use cases of `Bash`
   and were not built.

2. **Capability gates enter the `Literal`, not the function body.** With writes disabled,
   `create_issue` and `comment` are absent from the schema, so the model never sees an action it
   would have to be refused. `tests/core/test_github_catalog.py` asserts the negative case: a gate
   that lets everything through looks identical to a working one.

3. **The MCP surface was deliberately left alone.** A Claude Code session has no access to our
   executor, so `Bash` and `sufler-search` are unreachable from it. Consolidating there would
   not *move* a capability, it would *delete* it. The golden test freezes that surface in four
   configurations.

## Consequence this repository must carry: ADR 0054 lost a structural guarantee

ADR 0054 states that no parameter can express "whose tasks" — and it was true structurally:
`get_my_jira_tasks` had no parameters at all. After consolidation, `Jira(action=…)` carries a
`member` field in the same schema as the `my_*` actions, and only a dispatcher branch separates
them. The invariant survived, but it changed kind: **structural became procedural.**

The account itself is still never taken from the model — `member_*` actions accept a *name* and
translate it through the trusted identity map. That is the real invariant, and it is the one worth
citing. `tests/core/test_jira_catalog.py` probes both halves: the account is closed over at build
time, and the member-read service is not called for `my_*` actions.

This is the fifth invariant of the consolidation pattern and the most general one:
**consolidation can quietly exchange a structural guarantee for a procedural one.** It is cheap to
miss, because nothing fails when it happens.

## Alternatives considered

- **Copy the upstream decision here.** Rejected: two sources, and the measurements belong to the
  deployment (tool-surface character counts per gate configuration were taken by assembling the
  catalogue from live builders under `config/env.example` gates).
- **Leave the gap.** Rejected: it already cost a reader once — the reference documentation
  described the pre-consolidation surface for a full release, and the contradiction with ADR 0054
  had no visible home.
