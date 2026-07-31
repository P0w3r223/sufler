# 0046 — WorkMate ↔ Powiadomienia_teams coexistence on a shared server

Date: 2026-07-29
Status: proposed
Author: P0w3r223
Related to: ADR 0015 (Teams delegated Graph polling), ADR 0019 (shared EventStore), ADR 0022 (proactive dual-target Teams push), ADR 0044 (Linux container deployment), ADR 0045 (state durability and graceful shutdown)

---

## Context

`Powiadomienia_teams/` is a **standalone uv sub-project** (own `pyproject.toml`, `uv.lock`,
`src/powiadomienia_teams/`, tests, Dockerfile, systemd unit). It is a weekly Microsoft Shifts
assistant: every Sunday it detects who has no shifts for the coming week, sends a private 1:1 Teams
DM with last week's schedule as a template, interprets the natural-language reply with Claude, and
writes the shifts back to Shifts on the employee's behalf. It is **already deployed on the server**
(image `powiadomienia-teams:0.2.1`, docker-compose + hardened systemd) and is **excluded from the
WorkMate fleet image** (`.dockerignore:36-38`). The copy checked into this repo may lag the deployed
version — it is a **contract/pattern reference, not the source of truth** about the production version.

WorkMate (this repo) and Powiadomienia_teams are designed to **cooperate** in the future: both run on
the same company server, both speak to Microsoft Teams/Graph, and both send private 1:1 DMs to the
same division roster. This ADR does **not** integrate them. It records the **touch points and risks**
so that a later integration is made with eyes open, and it fixes the operational rules that keep the
current side-by-side deployment safe. **No code changes accompany this ADR.**

The concrete, already-live touch point: WorkMate reuses the **same Entra app registration** that
Powiadomienia_teams primed. `config.py:1350-1351` states this outright — the Teams doors are "the same
app registration and the same MSAL cache as `Powiadomienia_teams`, where the scope has been consented
since 2026-07-14." Sub-project identity values (`Powiadomienia_teams/deploy/env.example:14-26`):
`client_id=c0ffee00-0000-4000-8000-000000000015`, `tenant_id=c0ffee00-0000-4000-8000-000000000017`,
`team_id=c0ffee00-0000-4000-8000-000000000007` (BIAP Pion IT — the only team with a working Shifts
schedule). WorkMate's push door reuses the client/tenant via `WORKMATE_TEAMS_PUSH_CLIENT_ID` /
`_TENANT_ID` / `_TEAM_ID` (`config.py:1393-1400`).

## Decision

Keep the two systems **separate processes with separate state**, sharing only the Entra app
registration (and its admin consent). Concretely:

### 1. Never share a single MSAL token-cache file between the two processes
Both codebases default the MSAL cache to the **identical path** `~/.workmate/teams_token_cache.bin`
(WorkMate `config.py:505`; Powiadomienia `config.py:29`). Neither codebase takes **any cross-process
file lock** around the cache. The two writers behave differently, which makes a shared file unsafe:

- WorkMate now writes the cache **atomically** — `tmp.write_text(...)` + `os.replace(tmp, cache_path)`
  (`adapters/inbound/teams_graph/auth.py`, fixed 2026-07-31, follow-up (a) below closed) — matching
  Powiadomienia's pattern. Still no ambiguous-account guard (WorkMate silently picks `accounts[0]`).
- Powiadomienia writes it **atomically** — `tmp.write_text(...)` + `os.replace(tmp, cache_path)`
  (`graph/auth.py:98-104`) — and **hard-fails** with `AmbiguousAccountError` if the cache accumulates
  more than one account (`graph/auth.py:58-64`), whereas WorkMate silently picks `accounts[0]`.
- With two writers on one file, refresh-token rotation is **last-writer-wins**: the process that saves
  last overwrites the other's freshly rotated refresh token, eventually forcing an interactive
  re-login for one of them.

**Rule:** each process gets its own cache file on its own volume. The fleet already does this
(`deploy/docker/env.example:24-26`: `teams_graph_token_cache.bin` vs `teams_push_token_cache.bin` on
`/var/lib/workmate`); Powiadomienia keeps its cache on a separate volume
(`/var/lib/powiadomienia-teams/…`, `deploy/docker-compose.yml`). The shared code **default** is a
foot-gun only if both are left unconfigured on the same host — configuration, not code, prevents it.

### 2. Keep operational state isolated; do not couple through the EventStore yet
WorkMate shares one `events.db` across its own doors via the `workmate-state` volume (ADR 0019/0044).
Powiadomienia **does not use the EventStore** at all — it has its own `powiadomienia_state.json` plus a
single-instance advisory lock scoped to its own state path (`single_instance.py:40-46`; the lock does
**not** cover the MSAL cache). The two systems therefore have **no shared mutable state today**, which
is the safe default. Any future coupling through a shared EventStore would be a **new write door** and
requires its own ADR + gate (ADR 0019 invariant: doors write events, nobody calls anybody directly).

### 3. Treat overlapping 1:1 DM threads as a known hazard, not a solved problem
Both systems send private 1:1 DMs **from the same bot identity** (the "voice of the bot" —
`teams_token_cache_virtual_workmate.bin` / the scheduling-manager account) **to the same people**.
WorkMate's bridge push (Jira/GitHub events → DM, ADR 0022) and Powiadomienia's Shifts nudge can
**interleave in the same 1:1 chat**, mixing unrelated contexts. Until an integration design addresses
this (distinct thread semantics, or distinct sender identities per concern), operators should scope
push targets narrowly and expect the overlap.

### 4. Preserve the cross-cutting invariants
- **Sources of truth stay sources of truth** (Roadmap §1): **Shifts owns the schedule** — WorkMate
  synthesizes/notifies, it does not write schedules. Only Powiadomienia writes Shifts, with its own
  explicit-consent guardrails.
- **Per-door permission profile** (Roadmap §3): Powiadomienia holds broad write scopes
  (`Schedule.ReadWrite.All`, `Chat.Create`, `ChatMessage.Send`) because writing shifts is its purpose;
  WorkMate's read doors must not inherit those scopes just because the app registration carries them.
  Each door requests the minimum it needs and validates fail-fast.

## Consequences

- **Positive:** one admin consent covers both systems (no second Entra app, no duplicate consent
  round); the current side-by-side deployment is safe as long as cache files and state volumes are kept
  separate; a future integration has a written map of exactly where the two systems touch and where the
  sharp edges are.
- **Negative / residual risk:** the identical code default cache path (`~/.workmate/teams_token_cache.bin`
  in both) remains a latent foot-gun on any host where both run unconfigured (e.g. a dev box) — mitigated
  only operationally. A shared app registration means a consent/registration change for one system can
  affect the other. The 1:1 DM thread overlap (Decision 3) is deferred, not resolved.
- **Follow-ups (not in this ADR):** (a) **done 2026-07-31** — WorkMate's MSAL cache write is now
  atomic (`tmp`+`os.replace`), closing the torn-write window; an ambiguous-account guard (matching
  Powiadomienia's `AmbiguousAccountError`) remains open; (b) if integration proceeds, an ADR for
  the DM-thread model and for any EventStore coupling; (c) optionally a distinct dev-default cache path
  per project to remove the latent collision.

## Alternatives considered

1. **One shared MSAL cache file for both processes** — rejected. No cross-process locking exists in
   either codebase; WorkMate's non-atomic write plus Powiadomienia's ambiguous-account hard-fail make
   concurrent access unsafe (Decision 1).
2. **Merge the two into one image / one fleet** — rejected. They have separate release cycles, separate
   hardening, and separate operational owners; ADR 0044 deliberately keeps the sub-project out of the
   fleet image (`.dockerignore:36-38`). Merging would couple two independently deployable units.
3. **A separate Entra app registration for WorkMate** — viable but costs a second admin consent round
   for scopes already consented once. Deferred: shared registration + separate cache files is sufficient
   for now. Revisit if the two systems' scope needs diverge enough that one consent surface becomes a
   liability.
