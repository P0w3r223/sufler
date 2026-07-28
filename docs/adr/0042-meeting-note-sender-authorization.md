# 0042 — Meeting-note sender authorization (Entra identity → membership gate)

Date: 2026-07-28
Status: proposed
Author: P0w3r223
Related to: [ADR 0041](0041-production-m3-meeting-note-write-from-teams-door.md) (production `/notatka`),
[ADR 0009](0009-meeting-note-flow-and-write-surface.md) (M3, §3 caller-controls-location),
[ADR 0006](0006-write-capability-gate-2.md) (write gate),
[ADR 0035](0035-weekly-timesheets.md) / [ADR 0036](0036-timesheet-issue-attribution.md) (identity directory),
`roadmap-v1-gap-analysis.md` (B2)

---

## Context

ADR 0041 wired the production `/notatka` write from the Teams door, gated OFF, and left an explicit
follow-up: *"confirm channel-member authorization expectations."* Today that gate authorizes **by a
single global flag** (`enable_meeting_note_write`): once on, **any** sender in the channel can file a
note into **any** project. For a shared knowledge base that is a real integrity/confidentiality risk —
a note landing in another company's project — flagged CRITICAL by the M4 design pass.

The inbound message already carries the sender's Entra identity: `sender_id` = the AAD object id, is
normalized in `teams_graph/selection.py`, flows through the handler into `InboundMessage`, and today is
used **only** as the 1:1 delivery target (ADR 0027). It never reaches the `/notatka` write path.

The project already has the primitive we need: `AadIdentityLookup.resolve_by_aad_user_id(aad) → Person
| None` (`core/ports/timesheets.py`), implemented fail-closed by `YamlIdentityDirectory` /
`GraphIdentityDirectory` (`adapters/outbound/graph_identity_directory.py`) — built for worklogs
(ADR 0035/0036), already tested, already rejecting shared identifiers. What is missing is a notion of
"who may write" and the plumbing of the resolved actor to a core decision.

Hard constraints (hexagonal + governance):

1. **Identity enters the core through a port; the decision is a pure core function.** Resolution
   (Graph/YAML) stays in the adapter; the core receives a resolved actor, never a raw AAD id + a
   directory. Authorization is not decided in the adapter.
2. **Fail-closed.** Unknown / unresolvable sender → refuse the write. No name matching.
3. **The read-only dispatcher (ADR 0017) stays read-only.** Authorization attaches to the *separate*
   `MeetingNoteRouter`, not to `CommandRouter`.
4. **Additive seams.** Responder/wiring changes carry defaults so existing callers are untouched.

## Decision

1. **`Actor` value object + pure decision in the core.** New `core/domain/authorization.py`:
   `Actor(aad_user_id, display_name)`, `actor_from_person(Person) → Actor`, and
   `can_write_meeting_note(actor: Actor | None, *, project: str) -> bool`. B2-A policy is a membership
   gate: **a resolved actor → allowed; `None` → denied.** `project` is already in the signature as the
   **seam for B2-B** (per-project/company policy) — today the membership gate does not differentiate by
   project, but callers already pass it, so a permission matrix drops in without changing call sites.

2. **`MeetingNoteAuthorizer` core application service.** New `core/application/meeting_authz.py` takes
   the existing `AadIdentityLookup` port: `authorize(requester_aad_id, *, project) → Actor`, resolving
   the sender and raising a new `NoteAuthorizationError` (core error) when denied. Pure orchestration
   over a port + the pure decision — unit-tested on a fake lookup, no Graph, no network.

3. **The Teams door enforces at its boundary.** `MeetingNoteRouter` gains an optional
   `authorizer: MeetingNoteAuthorizer | None`; `CommandContext` gains an additive `sender_id: str = ""`
   (the read-only `CommandRouter` ignores it). `dispatch` reads `ctx.sender_id`; when an authorizer is
   present the router authorizes **before** the slow transcript+summary chain and, on
   `NoteAuthorizationError`, returns a readable refusal. The `MeetingNoteService` stays **pure** (compose
   note); the operator CLI (`workmate-meeting`, OS-authenticated, single trusted user) keeps calling the
   service directly with no authorizer.

4. **Authorization is intrinsic to the write gate — not a second toggle.** Enabling
   `enable_meeting_note_write` now **requires** an identity list: new
   `WORKMATE_TEAMS_GRAPH_IDENTITIES` (may reuse the worklog `identities.yaml`), validated fail-fast —
   the write cannot be turned on without an authorization source. A membership gate that defaulted OFF
   would mean "anyone writes" by default, which is exactly the CRITICAL risk; so the *capability* stays
   OFF by default (ADR 0006), but once on, membership is mandatory. This ADR stays `proposed` until the
   team accepts it.

## Alternatives considered

- **B2-B now: per-project/company permission matrix** (`can_write_note(actor, company, project)` driven
  by a person→projects map). Deferred: for a dozen trusted people with writes already create-only + ADR-
  gated, the membership gate closes the "anyone writes" risk; the per-project map is maintenance and
  over-fit until a real need to restrict specific people to specific projects appears. The signature
  seam keeps it a later additive gate.
- **Authorize inside the adapter (router) with inline policy.** Rejected: the decision must be a pure,
  testable core function that a future second write door reuses; the router only orchestrates and formats.
- **Authorize inside `MeetingNoteService` (defense-in-depth, all callers gated).** Rejected as the
  default: it would force `requester_aad_id` onto the trusted operator CLI path too. The Teams door is
  the multi-user trust boundary; it owns the check. `MeetingNoteService` stays a pure compose use case.
- **`GraphIdentityDirectory` (Graph-membership-gated) as the wired default.** Not the default: it adds a
  `TeamMember.Read.All` scope and a startup Graph fetch. `YamlIdentityDirectory` is fail-closed with no
  extra scope. Because both implement the same `AadIdentityLookup` port, switching to the Graph variant
  (adds "still currently in the team" currency) is a drop-in wiring hardening, documented in the runbook.

## Consequences

- **Buildable/verifiable now (gated OFF):** the `Actor` + pure decision, the `MeetingNoteAuthorizer`,
  the `CommandContext.sender_id` seam, router authorization + refusal text, config gate + fail-fast on a
  missing identity file. Unit-tested on fakes. Golden MCP surface unchanged.
- **Not verified live (parked 2026-07-28):** transcript scopes are admin-consented, but end-to-end
  refusal/allow with real AAD ids still needs the enabled gates, a populated `identities.yaml`, and a
  real meeting to smoke against; the operator confirms on live-smoke.
- **CRITICAL risk closed:** a note write now requires a resolved, listed member; an unknown sender is
  refused before any transcript fetch.
- **B3 depends on this.** Async fire-and-forget (ADR 0043) must capture the resolved `Actor`
  synchronously at enqueue; authorization stays synchronous/fail-fast even when execution moves to the
  background.

## Follow-ups

- On acceptance: flip to `accepted`; populate `WORKMATE_TEAMS_GRAPH_IDENTITIES`; live-smoke an allowed
  and a refused sender.
- B2-B (later, additive): per-project/company policy behind the same `Actor` seam, its own ADR.
- Optional hardening: switch the wired directory to `GraphIdentityDirectory` for team-membership currency.
