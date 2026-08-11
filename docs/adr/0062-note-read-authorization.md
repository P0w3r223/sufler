# 0062 — Note read authorization (Entra identity → membership gate, opt-in)

Date: 2026-08-11
Status: proposed
Author: P0w3r223
Related to: [ADR 0042](0042-meeting-note-sender-authorization.md) (write-side membership gate — the template),
[ADR 0006](0006-write-capability-gate-2.md) (write capability gate),
[ADR 0035](0035-weekly-timesheets.md) / [ADR 0036](0036-timesheet-issue-attribution.md) (identity directory),
[ADR 0057](0057-shell-executor-container-without-network.md) (executor mount boundary),
infra `docs/decyzje/0006` (workspace write model), `docs/decyzje/0010` (conversation isolation in the shell),
`docs/przebudowa-harnessu.md` §6 (stage 2)

---

## Context

Writing a meeting note is authorized (ADR 0042): the capability flag `enable_meeting_note_write`
rides on a sender **membership gate** — a resolved, listed member may write, an unknown sender is
refused fail-closed. **Reading a note has no equivalent.** Every read path reaches the whole
division knowledge base with no filter on the sender, channel, or membership:

- agent tools `search_notes` / `get_note` / `list_projects` (`core/application/tools.py:91-162`) —
  `get_note` returns the full body of any note by id;
- the `/szukaj` command (`adapters/inbound/commands.py:149-152`) — has `ctx.sender_id` in scope but
  ignores it;
- the `workmate-search` CLI (`adapters/inbound/cli/search.py:117-145`) and shell `cat` over the
  read-only mount `/mnt/system/notes` (`core/agent/prompt.py:72-88`).

They converge on one `NotesService` (`core/application/services.py:50`) over one
`MarkdownNotesRepository.all()` (`adapters/outbound/markdown_notes_repo.py:58-73`) which walks the
entire notes tree; `search_notes`/`get_note` take **no** requester identity. So today **any** sender
in a watched channel — including a guest who is not a member of the division — gets the whole
vertical's institutional memory. The infra ADR 0006 already names this as the wider leak that its own
residual scratchpad risk "fits inside": *"notatki są dziś współdzielone na cały pion bez żadnej
autoryzacji odczytu"*.

The primitive is already there and identical to the write side: the inbound `sender_id` (AAD object
id) is normalized in `teams_graph/selection.py`, flows into `InboundMessage`, and is **already
delivered onto the read path** in `CommandContext.sender_id` (`adapters/inbound/responder.py:312-314`)
— it is simply not consumed. `AadIdentityLookup.resolve_by_aad_user_id(aad) → Person | None`
(`core/ports/identity.py`, fail-closed `YamlIdentityDirectory`) already resolves it. What is missing
is a notion of "who may read" and the plumbing of the resolved actor to a core decision — exactly the
shape ADR 0042 built for writes.

### The read-vs-write asymmetry that shapes this ADR

Two structural differences from ADR 0042 must be stated up front, because they change the design:

1. **Read has no capability flag to ride on.** Writing is a capability that is OFF by default
   (`enable_write`, ADR 0006), so ADR 0042 could make authorization *intrinsic to that flag* — "not a
   second toggle". **Reading is the default behavior**; there is no `enable_read` to attach to.
   Introducing read authorization therefore *needs its own toggle*. Per the owner's decision
   (2026-08-11) it is **opt-in, default OFF** (`WORKMATE_ENABLE_NOTE_READ_AUTHZ`): a safe rollout that
   does not risk cutting off legitimate readers while `identities.yaml` is still incomplete. The gap
   stays open by default — but consciously, and closed the moment the operator populates identities
   and flips the flag.

2. **Read has many entry points, and one of them bypasses the application layer.** Writes enforce at a
   single door (`MeetingNoteRouter`) before the slow chain. Reads arrive through typed tools, the
   `/szukaj` command, the CLI, **and the shell over the `ro` mount**. A membership gate in
   `NotesService` is enforceable on the *typed* paths, where the sender identity is known — which is
   **exactly today's production configuration** (układ A, shell OFF: the agent reads only through the
   typed `search_notes`/`get_note`). It is **not** enforceable on the shell/CLI path: the executor
   mounts the whole base `ro` and `cat` reads it directly, with no sender identity (infra ADR
   0006/0010, ADR 0057). This is the same boundary those ADRs already draw. Closing the shell path
   requires a **per-conversation / per-sender mount** — the container rebuild infra ADR 0010 defers
   until the tool surface stabilizes. This ADR does **not** pretend to close it; it closes the typed
   paths and records the shell path as the same known residual risk, to be lifted with the
   per-conversation executor. A gate that claimed otherwise would be, in ADR 0006's words,
   *"zamkiem w drzwiach obok wyrwanej ściany"*.

Hard constraints (unchanged from ADR 0042):

1. **Identity enters the core through a port; the decision is a pure core function.** Resolution stays
   in the adapter; the core receives a resolved `Actor`, never a raw AAD id + a directory.
2. **Fail-closed.** Unknown / unresolvable sender → refuse the read. No name matching.
3. **The read-only dispatcher (ADR 0017) stays read-only in shape.** The authorizer is injected; the
   dispatcher orchestrates, it does not decide policy inline.
4. **Additive seams.** Wiring changes carry defaults so existing callers (operator CLI, tests) are
   untouched when the flag is OFF.

## Decision

1. **`can_read_note` pure decision in the core.** Extend `core/domain/authorization.py` (reusing the
   existing `Actor` and `actor_from_person(Person) → Actor` from ADR 0042) with
   `can_read_note(actor: Actor | None, *, project: str | None = None) -> bool`. Policy is a **membership
   gate**: a resolved actor → allowed (reads the whole base, preserving the cross-team culture the
   prompt relies on); `None` → denied. `project` is in the signature as the **seam** for a future
   per-project/company read policy — mirroring `can_write_meeting_note`'s `project` seam — but is not
   consulted today. Read and write get **separate** pure decisions rather than one shared function, so
   the two policies can diverge later (e.g. per-project read while writes stay membership-wide) without
   touching call sites.

2. **`NoteReadAuthorizer` core application service.** New `core/application/note_read_authz.py` taking
   the existing `AadIdentityLookup` port: `authorize(requester_aad_id) → Actor`, resolving the sender
   and raising the existing `NoteAuthorizationError` (reused from the write side) when denied. Pure
   orchestration over a port + the pure decision — unit-tested on a fake lookup, no Graph, no network.

3. **Enforcement on the typed read paths, where identity is known.**
   - **Agent tools.** The note-read catalog (`build_notes_read_catalog`, and the read tools folded into
     `build_notes_catalog`) is built with an optional `read_authorizer` and the turn's `sender_id`
     closed in the tool closure (the same way `WorkspaceScope` is closed per ADR 0006, and the same
     per-turn identity injection responder already does). When the authorizer is present and the sender
     is unresolved, the read tools return a readable refusal (`"Brak uprawnień do odczytu bazy wiedzy —
     nadawca nierozpoznany (fail-closed, ADR 0062)"`) instead of results — the model relays it, exactly
     as tool errors already flow.
   - **`/szukaj` command.** `_search` passes `ctx.sender_id` (already present) through the same
     authorizer before calling `search_notes`; on denial it returns the refusal string. The
     `CommandRouter` stays read-only in shape — the authorizer is injected, not decided inline.

4. **Shell / CLI path is explicitly out of scope for this gate.** `workmate-search` and `cat` over
   `/mnt/system/notes` run inside the network-less executor with **no** sender identity (ADR 0057); the
   whole base is mounted `ro`. Under `WORKMATE_ENABLE_SHELL=true` (trusted channels only, infra ADR
   0010) the membership gate is **not** enforced on this path — recorded as residual risk, closed only
   by the per-conversation mount that infra ADR 0010 defers. This is acceptable precisely because the
   shell is already restricted to mutually-trusting channels.

5. **Own capability toggle, fail-fast on identities.** New `WORKMATE_ENABLE_NOTE_READ_AUTHZ`
   (default `False`). When `True`, startup **requires** an identity map (reusing
   `WORKMATE_TEAMS_GRAPH_IDENTITIES` / `meeting_note_identities`), validated fail-fast — symmetric to
   the write fail-fast at `config.py:930-938`. Turning read authorization **on** without an
   authorization source is a startup error, not a silent open door. Unlike ADR 0042 (authorization
   intrinsic to the write capability), this is a standalone toggle because reading has no capability
   flag of its own; the trade-off (gap open by default) is the owner's chosen safe-rollout stance.

## Alternatives considered

- **Per-company/project read scoping now** (`can_read_note(actor, company)` driven by a person→companies
  visibility map). Deferred: it needs a **new visibility dimension** in the identity directory (which
  today only maps AAD ↔ Jira, no readable scope) and it **contradicts the cross-team culture** the
  prompt actively encourages (*"that answer usually sits in another team's notes"*). The membership gate
  closes the exact stated gap ("anyone in the channel reads everything") with zero new data model; the
  `project` signature seam keeps per-project scoping a later additive ADR.
- **Enforce inside `NotesService` / `MarkdownNotesRepository` (defense-in-depth, all callers gated).**
  Rejected as the default: it would force `requester_aad_id` onto the operator CLI and every test that
  reads notes, and it still would not reach the shell/mount path (the repo is bypassed by `cat`). The
  Teams door is the multi-user trust boundary; the typed read paths there own the check. `NotesService`
  stays a pure read use case.
- **Build-time fail-closed like Jira (ADR 0054): don't offer read tools to an unresolved sender.**
  Considered and folded in as the tool behavior's sibling — but a call-time readable refusal is more
  informative than a silently absent tool (the prompt still references the knowledge base), so the
  tools are offered and refuse on use. Build-time omission remains available if a measurement later
  shows the refusal text costs turns.
- **Make read authorization intrinsic / default-on (symmetric to ADR 0042).** Rejected per the owner's
  rollout decision: with `identities.yaml` holding ~2 people today, a default-on fail-closed gate would
  cut off legitimate readers not yet in the map. Opt-in first; make intrinsic once the map is complete.

## Consequences

- **Buildable/verifiable now (gated OFF):** the `can_read_note` pure decision, the `NoteReadAuthorizer`,
  the tool-closure + `/szukaj` enforcement with refusal text, the config toggle + fail-fast on a missing
  identity file. Unit-tested on fakes. With the flag OFF, every existing read path behaves exactly as
  today; golden MCP surface unchanged.
- **Gap closed for production układ A (shell OFF) once enabled:** with the flag on and identities
  populated, an unrecognized sender's turn can no longer read the base through the typed tools or
  `/szukaj`. This is today's production configuration (powłoka OFF).
- **Residual risk, unchanged and now documented in code:** under `WORKMATE_ENABLE_SHELL=true` the shell
  reads the `ro` mount directly, unauthorized — the infra ADR 0010 residual risk, lifted only by a
  per-conversation mount. Enabling read authz does **not** justify enabling the shell on untrusted
  channels.
- **Three decisions must be re-read on acceptance** (their arguments partly stood on the *absence* of
  read authorization): infra `docs/decyzje/0006` §"Czego to NIE załatwia" (hard scratchpad isolation
  "has sense only together with read authorization"), infra `docs/decyzje/0010` §residual risk, and
  `przebudowa-harnessu.md` stages 1 & 3. Each loses part of its "the leak is already larger" argument
  once this gate is on.

## Follow-ups

- On acceptance: flip to `accepted`; decide the operator rollout (populate/verify `identities.yaml`,
  then flip `WORKMATE_ENABLE_NOTE_READ_AUTHZ=true` in the deploy env); live-smoke an allowed and a
  refused sender through `search_notes` and `/szukaj`.
- Per-company/project read scoping (later, additive): behind the same `project`/`company` seam, its own
  ADR — only when a real need to restrict specific people to specific clients appears.
- Shell-path closure: per-conversation / per-sender mount, tracked with the container rebuild in infra
  ADR 0010; only then is the membership gate enforced under the shell.
- Once `identities.yaml` is complete and stable: consider promoting read authz from opt-in to intrinsic
  (default-on fail-closed), retiring `WORKMATE_ENABLE_NOTE_READ_AUTHZ`.
