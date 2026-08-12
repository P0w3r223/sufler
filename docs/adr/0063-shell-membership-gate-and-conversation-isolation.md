# 0063 — Shell tool membership gate + per-conversation scratchpad isolation

Date: 2026-08-12
Status: accepted
Author: P0w3r223
Related to: [ADR 0057](0057-shell-executor-container-without-network.md) (the executor this gates),
[ADR 0062](0062-note-read-authorization.md) (note-read membership gate — the template applied here to the shell),
[ADR 0042](0042-meeting-note-sender-authorization.md) (write-side membership gate),
[ADR 0054](0054-reduce-jira-to-read-only-my-tasks.md) (build-time fail-closed omission),
infra `docs/decyzje/0010` (conversation isolation in the shell — the deferred per-conversation mount),
infra `docs/decyzje/0006` (workspace write model)

---

## Context

Every **typed** path that reaches the division's data enforces a **membership gate** keyed on the
inbound `sender_id` (AAD object id) resolved against `identities.yaml`, fail-closed on an unknown
sender: note write (ADR 0042), Jira `/moje-zadania` (ADR 0054), note read (ADR 0062). **The shell
tool has no such gate.** `build_shell_catalog` (`core/application/tools.py:573`) is attached in the
responder whenever the shell factory is present — i.e. whenever `WORKMATE_ENABLE_SHELL=true`
(`adapters/inbound/responder.py:389-390`, `if self._shell_catalog_factory is not None`) — for
**any** sender the bot answers on a watched channel. There is no `sender_id` in that path.

Two structural facts, **confirmed empirically on production** (image `workmate:1.8.0-deploy`,
trusted channel, shell temporarily enabled, 2026-08-12):

1. **No membership gate on the shell.** A sender **not** in `identities.yaml` (a second account
   sharing the display name of a mapped member, AAD `20d8b521…`) was refused by `/moje-zadania`
   ("could not resolve your Jira account", ADR 0054) **and** by note write ("sender is not a
   recognised member", ADR 0042) — yet the **same sender** received the shell and ran
   `ls /home/scratchpad`, seeing another conversation's scratch file (a planted `tajne.txt`) and the
   `teams-graph/<hash>` tree of every conversation. The shell is a **side door around** 0042/0054/0062.

2. **Cross-conversation scratch read.** The executor mounts the **whole** scratchpad volume. `cwd` is
   the per-conversation subdir — an isolated *start*, confirmed `pwd` →
   `/home/scratchpad/teams-graph/<sha256(conversation)[:32]>`, created by the `CommandRunner.run`
   mkdir decorator (`agent_wiring.py:349-364`) — **but not a jail**: an absolute path reaches any
   conversation's subdir. `build_shell_catalog`'s own docstring states this ("Nie jest to
   zamknięcie… granicą jest brak sieci, nie domknięcie katalogu").

### Why this is broader than the residual risk 0057/0062 already recorded

ADR 0057 and ADR 0062 accepted the shell's cross-read as tolerable **"because the shell is
restricted to mutually-trusting channels."** That premise conflates two different boundaries:

- **Channel participation** — who can post on the watched channel. This is what actually decides
  whether the shell is offered today. A watched channel routinely contains guests and non-mapped
  accounts (the finding's second account was exactly one).
- **`identities.yaml` membership** — who every typed data gate (0042/0054/0062) checks.

The shell rides the **looser** of the two. So a guest whom 0062 refuses on the typed note-read path
simply `cat /mnt/system/notes/...` through the shell over the `ro` mount **and** reads other
members' scratch. The executor's missing network (ADR 0057) blocks exfiltration to the outside — but
**not** the leak that matters here: the agent relays what it read into its **Teams reply to the
requester**. "No network" does not close a confidentiality leak whose exit is the legitimate reply
channel. ADR 0062 even *widens* to the shell on purpose — with the shell on it removes the typed
note-read tools and lets `cat` serve reads — which is safe **only if** the shell itself carries the
same membership boundary. It does not. This ADR closes that gap.

The primitive is already present, exactly as in ADR 0062: per-turn `sender_id` on the responder,
`AadIdentityLookup.resolve_by_aad_user_id(aad) → Person | None` (fail-closed `YamlIdentityDirectory`),
and a per-turn tool factory precedent — `user_push_tool_factory: Callable[[str], …]` keyed on
`sender_id` (`responder.py:201, 405`). What is missing is consuming it for the shell catalog.

## Decision

Two parts, at two layers. §1 (app) closes the sharper vector — non-members — now. §2 (infra) closes
member-to-member cross-read, and is handed to a new infra ADR.

### §1 — Membership gate on the shell tool (app layer, buildable now)

1. **`can_use_shell` pure decision in the core.** Extend `core/domain/authorization.py` (reusing
   `Actor` and `actor_from_person(Person) → Actor` from ADR 0042/0062) with
   `can_use_shell(actor: Actor | None) -> bool`: a resolved actor → allowed; `None` → denied. A
   **separate** pure decision from `can_read_note` / `can_write_meeting_note`, so shell policy can
   diverge later without touching call sites.

2. **Per-turn, sender-keyed shell factory.** The shell catalog moves from a scope-only factory
   (`Callable[[WorkspaceScope], …]`, invoked unconditionally at `responder.py:389`) to a per-turn
   factory that also receives `message.sender_id` — the same shape ADR 0062 gives the note-read
   factory and the 1:1 push factory already uses. It resolves the sender via `AadIdentityLookup`; a
   resolved member → `build_shell_catalog(scope, …)` as today; an unresolved sender → **omit the
   shell tool** (empty list).

3. **Build-time omission, not runtime refusal** (per ADR 0054's reasoning): a guest does not need to
   learn the shell exists. Omit it silently rather than offer a tool that refuses on use. (A refusal
   string stays available if a later measurement shows omission confuses the model.)

4. **The gate is intrinsic to the shell flag — no second toggle.** Unlike read authorization (ADR
   0062 needed its own `WORKMATE_ENABLE_NOTE_READ_AUTHZ` because reading is default-on), the shell is
   already opt-in OFF (ADR 0057, `WORKMATE_ENABLE_SHELL`). So membership gating is baked into "shell
   on" exactly as ADR 0042 baked authorization into the write capability — there is no window in
   which the shell is on **and** ungated. When `enable_shell` is true, startup **requires** an
   identity map and validates it fail-fast (symmetric to the write fail-fast at `config.py:930-938`
   and ADR 0062 §5): turning the shell on without an authorization source is a startup error, not a
   silent open door to everyone.

### §2 — Per-conversation scratchpad isolation (infra, new ADR)

§1 restricts **who** gets the shell to members; two mutually-listed members still cross-read each
other's scratch via absolute paths. Closing that requires the executor to **see only the requesting
conversation's subdir** — the per-conversation / per-sender mount that infra ADR 0010 defers. This
ADR records the requirement and hands the **mechanism** to a new infra ADR; candidates for it to
weigh: (a) an ephemeral executor per conversation, mounting only that subdir; (b) a per-conversation
bind-mount into the shared executor; (c) mount-namespace / subtree confinement per command.
Consistent with ADR 0057 ("the executor does not inspect command content"), the fix must come from
what the container can **see**, never from filtering shell strings.

### §3 — Production stance

The shell is **OFF** on production as of 2026-08-12 (`WORKMATE_ENABLE_SHELL` removed from `.env`,
`teams-graph` recreated). Re-enable only after §1 ships. §2 is required before the shell is offered
on any channel whose members should not see each other's scratch.

## Alternatives considered

- **Keep the status quo — "trusted channel" as the only control.** Rejected: the finding shows the
  watched channel admits non-members, so the boundary the shell actually rides is looser than every
  typed data gate. Recording a residual risk is not the same as leaving the **widest-capability**
  tool ungated while the **narrow** typed tools are gated.
- **Own toggle `WORKMATE_ENABLE_SHELL_AUTHZ` (mirror 0062).** Rejected: the shell is already
  default-OFF, so the gate is intrinsic to the shell flag — a second switch would only re-create a
  shell-on-but-ungated window.
- **Runtime refusal (offer the shell, refuse on use).** Rejected as default per ADR 0054 — omit
  rather than advertise. Available as a fallback if measured to matter.
- **Command-content filtering in the executor (block `..`, absolute paths outside cwd).** Rejected,
  consistent with ADR 0057: judging shell strings is unreliable — `cat $(…)`, symlinks, and `..` all
  defeat static checks. Isolation belongs in §2 (what the container sees), not in a filter.
- **Do §2 only, skip §1.** Rejected: §2 is a container rebuild (slow); §1 closes the sharper vector
  (non-members) now, in the app, with ADR 0062's pattern already proven in code.

## Consequences

- **Buildable / verifiable now (app-only, §1, gated by the existing shell flag):** the `can_use_shell`
  pure decision, the per-turn sender-keyed shell factory with build-time omission for an unresolved
  sender, the fail-fast on a missing identity map when the shell is on. Unit-tested on a fake
  `AadIdentityLookup`. **With the shell flag OFF (today's production) there is zero behavior change;
  the golden MCP surface is untouched** — the shell is a Teams-door tool, not an MCP one.
- **After §1:** a non-member on a watched channel no longer receives the shell — the 2026-08-12
  vector is closed. The typed-path gates (0042/0054/0062) and the shell finally share **one** trust
  boundary (`identities.yaml`).
- **Residual after §1, until §2:** mutually-listed **members** still cross-read each other's scratch
  via absolute paths — the infra ADR 0010 residual risk, now correctly **scoped to members only**,
  which is what ADR 0057/0062 originally *assumed* the whole shell risk already was. Egress stays
  blocked (ADR 0057). §2 lifts it.
- **ADR 0062's premise is corrected in one line:** its "acceptable because the shell is restricted to
  mutually-trusting channels" becomes "…to mutually-trusting **members**, once §1 gates the shell on
  membership." Until §1, that premise was not met in practice.

## Implementation plan (§1, PIWorkmate app layer — ADR-first, code in a follow-up PR)

Surface, all behind the existing `enable_shell` flag (OFF → byte-for-byte unchanged):

- **`core/domain/authorization.py`** — add `can_use_shell(actor: Actor | None) -> bool` (membership
  gate; `None` → `False`). Pure, unit-tested.
- **`core/application/shell_authz.py`** (new, mirrors `note_read_authz.py`) — `ShellAuthorizer` over
  the `AadIdentityLookup` port: `authorize(requester_aad_id) → Actor | None`, resolving the sender
  and applying `can_use_shell`. Pure orchestration over a port + the pure decision; no Graph, no
  network; unit-tested on a fake lookup.
- **`adapters/inbound/responder.py`** — change `shell_catalog_factory` from
  `Callable[[WorkspaceScope], list[ToolSpec]]` to a per-turn call receiving `message.sender_id`
  (precedent: `user_push_tool_factory` at `responder.py:201, 405`; the note-read factory in ADR 0062
  §Update). At the call site (`~389`) pass both the per-turn `scope` and `sender_id`; the factory
  returns `[]` when the sender does not resolve.
- **`adapters/inbound/teams_graph/app.py`** — in `_build_responder`, build the shell factory with a
  `ShellAuthorizer` constructed from the same identity directory the note/Jira gates use; resolved
  member → `build_shell_catalog(scope, runner, workspace_root=…)`, else `[]`.
- **`config.py`** — when `enable_shell` is true, require + fail-fast-validate the identity map
  (symmetric to the write fail-fast at `config.py:930-938`). Turning the shell on with no identities
  is a startup error.
- **MCP door and operator CLI untouched** (single trusted operator, no inbound AAD `sender_id` — the
  same boundary ADR 0042/0062 draw). Golden MCP surface unchanged.
- **Tests (fakes only):** resolved member → shell tool present; unresolved sender → shell tool
  absent; `enable_shell` false → catalog identical to today; `enable_shell` true + empty identities →
  startup raises.
- **Quality gate before push:** `ruff check` + `ruff format --check`, `mypy`, full `pytest` (not
  pytest alone).

## Follow-ups

- On acceptance: flip to `accepted`; implement §1 in a follow-up PR (ADR-first, per the stage-2
  precedent). Then, on a trusted channel, **re-run the 2026-08-12 probe from a non-member account**
  and confirm the shell tool is absent (no `ls /home/scratchpad`).
- **New infra ADR for §2** (per-conversation executor mount), tracked with the container rebuild in
  infra ADR 0010; it picks the mechanism (ephemeral executor / per-conversation bind-mount /
  namespace confinement).
- The `File(edit)`-vs-`Bash` tool-surface measurement runs only **after §2**, on a channel with real
  members, without cross-read exposure — or, before §2, on a channel whose participants are all
  test accounts.
