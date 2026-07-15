# 0015 — Teams channel presence via delegated Microsoft Graph polling (Bot Framework deferred)

Date: 2026-07-11
Status: accepted
Author: P0w3r223
Related to: [ADR 0006](0006-write-capability-gate-2.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md), [ADR 0010](0010-conversation-threading-and-context-limit.md)
Amended by: [ADR 0016](0016-user-multimodal-attachments.md) — adds `Files.Read.All` + `Sites.Read.All` scopes (one-time device-code re-consent) for attachment download

---

## Context

WorkMate must answer inside Microsoft Teams **channels**, reusing the existing
`Responder` seam and the read-only agent runtime (ADR 0006/0008). There are two ways to
reach a Teams channel: a registered **Bot Framework** app (push via webhook, its own bot
identity) or **delegated Microsoft Graph** access (the assistant acts as a signed-in
*user* — the owner's account — via MSAL device-code auth and message polling).

The current environment has **no public HTTPS endpoint and no Azure bot registration**;
the existing `inbound/teams/` (Bot Framework, Microsoft 365 Agents SDK) door therefore
needs a dev tunnel and admin setup to run. Two working spikes already existed for the
delegated path (`teams_auth.py` device-code MSAL with an on-disk token cache; a channel
poller with per-channel watermark, self-message loop protection, dedup, and
429/Retry-After handling), operationally identical to the Telegram long-polling door that
already runs behind the firewall. The owner asked to ship channel answering **now**.

Hard constraints (already decided, not reopened here): read-only tool catalog
(`enable_write=False`, ADR 0006); reuse `Responder`/`InboundMessage`, `build_agent_runtime`
+ `build_compaction_service`, `ConversationService` + `SqliteConversationStore`, and the
`SafeResponder(ConversationalResponder(...))` recipe; `core ↛ adapters`; SDK/`msal`/`httpx`
imports lazy and the message handler SDK-free and fake-testable; the MSAL token cache stays
a disk secret outside the repo and `data/`.

## Options Considered

### Option A: Bot Framework webhook (Microsoft 365 Agents SDK)

- **Description**: Register an Azure bot + app, expose `/api/messages` over public HTTPS,
  receive Teams activities by push. This is the already-spiked `inbound/teams/` door.
- **Pros**: First-class Teams citizen; push (no polling cost, no throttling); its own bot
  identity distinct from any person; scales to many channels/tenants; multi-user.
- **Cons**: Needs Azure bot registration + app registration + admin work; requires a
  public HTTPS endpoint (dev tunnel today, reverse proxy in prod) — not available now;
  higher operational surface.
- **Effort**: L
- **Risk**: Med — blocked on infra/admin that does not exist yet; nothing ships today.

### Option B: Delegated Microsoft Graph polling (device-code MSAL + channel polling)

- **Description**: The assistant signs in **as the owner's user account** (device-code
  flow, refreshed silently), polls watched channels for new messages/replies, and posts
  answers in-thread — all outbound, no inbound endpoint. Reuses the two spikes.
- **Pros**: No public endpoint, no bot registration — runs behind the firewall exactly
  like the Telegram long-polling door; ships today reusing proven code; drops straight
  into the `Responder` seam (echo → `ConversationalResponder` is a one-line swap).
- **Cons**: Identity is a **real user account** — messages appear as that person, and the
  door dies if the account is disabled/offboarded; delegated permissions need admin
  consent; polling has API cost and can be throttled; single-user seat; latency is bounded
  by the poll interval, not push; the token cache is a disk secret.
- **Effort**: M (mostly porting the spikes into the package + the multi-turn fix)
- **Risk**: Low technically (patterns proven), Med on identity/permissions governance.

## Decision

Adopt **Option B (delegated Graph polling) now**, and **defer Option A (Bot Framework) to
later**. The deciding argument: there is no public endpoint or bot registration today, and
delegated polling ships channel answering immediately by reusing the `Responder` seam and
the Telegram long-polling operational model. The bot-identity-vs-user-identity trade is
acceptable for an internal pilot on the owner's account. Bot Framework remains the target
end-state; the `Responder` seam + an injected `GraphChannelClient` port make the future
swap a door-level change, not a core rewrite.

## Consequences

- **New door** `adapters/inbound/teams_graph/` (package, not root scripts): `auth.py`
  (lazy MSAL), `graph.py` (lazy httpx implementing the `GraphChannelClient` port), pure
  `selection.py`, SDK-free `handler.py`, thin `poller.py`, `state.py`, `app.py` wiring.
  New `TeamsGraphSettings` in `config.py`, extra `teams-graph` (`msal`+`httpx` moved out
  of core deps), script `workmate-teams-graph`. The two root spike scripts are removed
  after migration.
- **Identity = the signed-in user account.** Channel replies are attributed to that
  person; the self-message skip (`from.user.id == me_id`) is load-bearing to prevent the
  assistant answering its own replies. Offboarding that account disables the door.
- **Delegated permissions** `ChannelMessage.Read.All`, `ChannelMessage.Send`,
  `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `User.Read` require **admin consent**;
  the door will not function without it.
- **Read-only** agent catalog (`enable_write=False`, ADR 0006) — channel content is
  untrusted data, never commands; no `save_note` from this door.
- **Per-thread memory**: `channel="teams_graph"`, `external_id="{team}/{channel}/{root}"`,
  keeping histories separate per Teams thread and distinct from the Bot Framework door.
- **Multi-turn correctness**: reply fetching MUST NOT gate on `root.lastModifiedDateTime`
  (Graph does not bump it on new replies, which silenced the spike after the first turn);
  the door tracks a per-thread reply watermark plus an active-thread set. This is a
  permanent constraint of the delegated approach.
- **Throttling/cost**: polling is chattier than push; mitigated by a poll-interval floor,
  a cap on active threads (`top_roots`/`top_replies`), eviction of idle threads, and the
  existing 429/Retry-After handling.
- **Secret**: the MSAL token cache stays outside repo/`data/` (default
  `~/.workmate/teams_token_cache.bin`); the watermark state file is not a secret.
- **Revisit when** a public HTTPS endpoint + bot registration become available, or when
  the door must serve more than one user/tenant — then migrate to Option A (Bot Framework),
  swapping the Graph client behind the same `Responder` seam.
