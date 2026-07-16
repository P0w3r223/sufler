# Multi-team Shifts support

Date: 2026-07-16
Status: proposed
Author: P0w3r223
Related to: PLAN.md (single-team pilot), CLAUDE.md (safety invariants), ADR 0006 spirit (write-gate)

---

## Context

`Powiadomienia_teams` today drives the weekly Shifts-reminder cycle for **one** team.
Single-team is baked into three places in the code:

- **Config** (`config.py`): `team_id` and `scheduling_group_id` are scalars. Different teams
  have different scheduling groups, and the write path requires the correct one.
- **State** (`state.py`): the pending-reminder map is keyed by AAD `user_id` alone, and
  `PendingReminder` carries no `team_id`. A person present in two teams collides on one key.
- **Orchestration** (`app.py`): `run_once` and the reply/write path read `settings.team_id` /
  `settings.scheduling_group_id` globally, so a reply cannot be routed to the member's actual team.

The pure layer (`reminders/detect`, `reminders/propose`, `scheduler/weekly`, `messages`, the
interpreter internals) is already team-agnostic — it operates on injected members/shifts/`now`.
So multi-team is almost entirely a **config + state + orchestration** change.

Target scale: a few teams, dozens of people, still **one Python process, no new infrastructure**
(no queue, no database). The deliberately synchronous Graph client stays synchronous.

### Hard constraints (must not be violated)

- Write to Shifts only after the employee's explicit "tak" (spirit of ADR 0006).
- Cross-user guard `ensure_single_owner` — a reply can never write another person's schedule.
- Reply content is data, not commands (prompt-injection containment in the interpreter).
- Hexagonal split (pure logic, no I/O in the core).
- **Backward compatibility**: the existing single-team `.env` and the existing
  `powiadomienia_state.json` must keep working without manual migration.

## Decision

Adopt a three-part design. Each part keeps the single-team run working via a compatibility shim.

### Decision 1 — Team list in configuration: **structured YAML file + in-memory `TeamConfig` list**

Introduce `teams: tuple[TeamConfig, ...]` on `Settings`, where `TeamConfig` is a frozen value
object `{team_id, scheduling_group_id, only_user_ids?}`. Populate it from an optional YAML file
(`POWIADOMIENIA_TEAMS_FILE`, reusing the existing `roster.py` YAML+validation pattern); when the
file is absent, synthesize a **one-element list from the existing scalar** `team_id`/
`scheduling_group_id`. All downstream code depends only on `settings.teams`, never on the scalars.

**Relation to the already-shipped `TeamContext` (Layer A).** `TeamContext {team_id,
scheduling_group_id}` is the *runtime* slice the orchestration already reads per cycle;
`TeamConfig` is its *configuration source* and additionally carries per-team `only_user_ids`.
Keep exactly **one** type flowing through the loop: materialize a `TeamContext` from each
`TeamConfig` at the start of a run (or fold `only_user_ids` into `TeamContext`) — do not create
two parallel value objects. `Settings.team_context` (the single-team shim) becomes the degenerate
one-element case of iterating `settings.teams`.

Alternatives considered:
- **Parallel delimited env lists** (`TEAM_IDS=a,b` + `SCHEDULING_GROUP_IDS=g1,g2`, positional):
  rejected — positional pairing silently misaligns team → scheduling group (a correctness hazard),
  and cannot express per-team overrides.

### Decision 2 — State partitioning: **composite key `"{team_id}:{member_id}"` in the single file**

Add `team_id: str = ""` to `PendingReminder` (the default means old records deserialize cleanly;
the loader already tolerates missing/unknown fields). New writes use the composite key. On load, a
**bare key (no colon)** is back-filled to the primary configured team. This fixes the two-team
collision while preserving the single atomic-file guarantee (`os.replace`) that underpins the
at-most-once write safety. The `team_id` is set on the pending **at nudge time**, so the reply/
write path reads the team from the pending — not from global settings — and routes to the correct
schedule and scheduling group.

Alternatives considered:
- **Nested `{team_id: {member_id: …}}`**: cleaner long-term shape, kept as the fallback if
  composite-key handling feels awkward; requires a shape-migration branch on load.
- **One file per team**: hard isolation but over-engineered at target scale.

### Decision 3 — Cycle over teams: **sequential per-team fan-out under the existing single timer**

Keep `next_run` and the one Sunday-16:00 wakeup. In `run_once`, loop `for ctx in settings.teams:`
running today's exact steps per team, tagging each `PendingReminder` with `ctx.team_id`.
`poll_replies` already iterates all pendings; resolve each pending's team context from its
`team_id` to pick the right `scheduling_group_id` and time-off reasons. Cache
`list_time_off_reasons(team_id)` per cycle (changes rarely).

Alternatives considered:
- **Concurrent per-team (asyncio/threads)**: rejected — contradicts the synchronous-by-design
  client, introduces races on the shared JSON state and MSAL token cache, and buys nothing at
  target scale.

## Consequences

- **Enabler**: this design plugs into the `TeamContext` seam introduced by the preparatory
  refactor (Layer A / A4). Once orchestration reads a `TeamContext` parameter instead of global
  settings, Decision 3 becomes literally wrapping `run_once` in a `for ctx in settings.teams:` loop.
- **Safety unchanged**: explicit "tak", `ensure_single_owner`, and prompt-injection containment are
  per-pending / per-interpreter and orthogonal to team count. One new consideration worth noting:
  the write team comes from the pending (set when the member was in that team at nudge time); a
  stale pending after a member leaves is bounded by `reply_window_hours` and can still only ever
  write that member's own id. Optional belt-and-suspenders: re-check `list_members(ctx.team_id)`
  membership at apply time.
- **Backward compatible**: no YAML file → single-team behavior identical; old state file loads via
  the bare-key back-fill.
- **Cost**: N sequential Graph round-trips per wakeup (seconds at target scale); two config sources
  during the transition (documented, temporary).

## Implementation sequence

1. Land Layer A preparatory refactors first (especially `TeamContext` and orchestration split).
2. Decision 2: `team_id` on `PendingReminder` + composite-key loader with bare-key back-fill.
3. Decision 1: `TeamConfig` list + YAML loader + single-team shim.
4. Decision 3: per-team loop in `run_once`; resolve `TeamContext` per pending; cache reasons.
5. `@tester` (two-team collision, old-file migration, per-team scheduling-group routing) +
   `@code-reviewer` + a two-team live dry-run smoke.
