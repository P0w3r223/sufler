# 0020. GitHub delegated polling door (PAT)

Date: 2026-07-15
Status: accepted
Author: P0w3r223
Related to: docs/adr/0015-teams-delegated-graph-polling.md, docs/adr/0006-write-capability-gate-2.md,
  docs/adr/0019-shared-event-store.md, docs/roadmap.md (Phase 3)

---

## Context

Phase 3 adds GitHub as another door on the same core. GitHub must feed events (new issues,
comments) into the shared EventStore (ADR 0019) so they can reach Teams and be read by any
door's agent. The question is how GitHub talks to WorkMate. The user chose **polling with a
Personal Access Token (PAT)**, mirroring the existing `teams_graph` door that polls Microsoft
Graph — no public endpoint, no tunnel, no inbound server.

## Options considered

1. **Delegated polling with a PAT** (chosen). A new `inbound/github/` door that periodically
   calls the GitHub REST API, maps raw JSON to domain `NewEvent`s via a pure `selection`
   module, and ingests them. Same shape as `teams_graph`: lazy `httpx`, pure decision logic,
   thin poller, JSON state file. No infrastructure beyond a PAT.
2. **GitHub webhooks.** Real-time, but needs a public HTTPS endpoint (IIS/tunnel), HMAC
   signature verification and inbound-traffic handling — larger surface and operational cost,
   rejected for this stage.

## Decision

**Option 1.** A `github` door that polls with a PAT:

- **Read-only ingest, per-door permission profile (ADR 0006).** The notes catalog stays
  read-only (`enable_write=False`); the door only *reads* GitHub and *writes events* to the
  local store. Writing back to GitHub is a separate, gated capability (ADR 0021).
- **Sync client, async door.** `HttpxGithubClient` (port `GithubReadPort`) is synchronous;
  the async poller calls it in a thread pool — exactly how `teams_graph` runs sync MSAL — so
  the same client also serves agent tools without an event-loop clash.
- **Dedup by the store, watermark for cost.** Correctness (no duplicates) comes from the
  EventStore's `UNIQUE` key; the `since` watermark only limits fetched volume. Watermark
  advances only after ingest (at-least-once).
- **Self-ping guard.** `selection` skips events authored by the PAT account's login, so an
  issue/comment created by the bot (ADR 0021) never comes back as a notification.
- **Rate-limit aware.** Backoff on 403/429 using `X-RateLimit-Reset`/`Retry-After`, `Link`
  pagination, a `per_page` cap and a poll-interval floor (GitHub's 5000 req/h budget).
- **Untrusted content = data.** GitHub titles/bodies are data, never commands; sanitized
  before storage. The PAT is a secret (`repr=False`, env), never in repo or `data/`.

## Consequences

- A new process `workmate-github` (extra `github`) runs alongside the MCP server, like the
  other doors. First-run needs only a PAT — no Azure/tunnel.
- The empty `adapters/github/` stub is retired; the real door lives under `adapters/inbound/`
  with the other doors.
- Event *kinds* start with `issue_opened` and `issue_comment`; PRs/CI are easy follow-ups as
  additional `watch_kinds`. In this stage the door is ingest-only (it does not answer issues).
