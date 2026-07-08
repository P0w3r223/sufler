# Teams bot on Azure — SDK choice and setup path (mid-2026)

Date: 2026-07-08
Status: accepted
Author: P0w3r223
Related to: roadmap_workmate.pdf (Phase 2 — Teams + agent runtime), docs/adr (future Teams-door ADR)

---

Research synthesis for standing up a Microsoft Teams bot (Python) hosted via Azure
Bot Service, for WorkMate Phase 2. Two angles: current SDK state, and the concrete
Azure→Teams setup path. Sources are Microsoft Learn + GitHub repo states, verified
mid-2026.

## Decision: use the Microsoft 365 Agents SDK, not the classic Bot Framework SDK

The classic **Bot Framework SDK for Python (`botbuilder-*`) was archived on
2026-01-05** (repo read-only, last release 4.17.1, support ended 2025-12-31). The
**Bot Framework Emulator repo was also archived 2026-01-05** (still downloadable,
still works for local smoke tests). Both still *function* — the Azure Bot Service
wire protocol is unchanged — but they are a dead end with no security fixes.

The official successor is the **Microsoft 365 Agents SDK**. Python is a
first-class language here and is **stable 1.x** (not preview): `microsoft-agents-
hosting-core` and `microsoft-agents-activity` are at 1.1.0 (2026-06-19). Python
3.10+ (3.11+ recommended).

> Do **not** use the "Teams SDK" (formerly Teams AI v2 / TeamsFX): its Python
> support is still developer preview, and our goal is a door over a shared core,
> which is the Agents SDK's domain.

### Package mapping (Bot Framework → Agents SDK)

| Bot Framework (archived) | Microsoft 365 Agents SDK |
|---|---|
| `botbuilder-core` | `microsoft-agents-hosting-core` |
| `botbuilder-schema` | `microsoft-agents-activity` |
| `botbuilder-integration-aiohttp` | `microsoft-agents-hosting-aiohttp` |
| `botbuilder-core.teams` | `microsoft-agents-hosting-teams` |
| (auth) | `microsoft-agents-authentication-msal` |

**Import trap:** underscores, not dots — `from microsoft_agents.hosting.core
import ...` (never `microsoft.agents`). Most common migration error.

### Minimal echo bot pattern

```python
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import AgentApplication, TurnState, TurnContext, MemoryStorage
# init STORAGE / CONNECTION_MANAGER / ADAPTER / AGENT_APP from env

@AGENT_APP.activity("message")
async def on_message(context: TurnContext, _state: TurnState):
    await context.send_activity(f"Echo: {context.activity.text}")
```

- Env config uses a double-underscore hierarchy, e.g.
  `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`.
- **Local test without Azure:**
  `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED=True` + Bot
  Framework Emulator, default port **3978**. Without anonymous mode you get 401.
- A class-based `ActivityHandler` (`on_message_activity`) is also available for
  near-1:1 migration.

## Azure → Teams setup path (and pitfalls)

1. **Identity (Entra App Registration): choose single-tenant.** Multi-tenant bot
   creation was retired after 2025-07-31; single-tenant (or managed identity) is
   the current default. Creating the Azure Bot resource can auto-generate the app
   + secret. **Pitfall:** for single-tenant the code must set `MicrosoftAppType=
   SingleTenant`, `MicrosoftAppId`, `MicrosoftAppPassword` **and**
   `MicrosoftAppTenantId`. Missing `TenantId` → SDK behaves as multi-tenant →
   token audience mismatch → **401 on reply**.
2. **Azure Bot resource** (type "Azure Bot" — legacy "Web App Bot" / "Bot Channels
   Registration" can no longer be created). **F0 (free) tier** is enough. Secret
   lives under Configuration → Manage → Certificates + secrets.
3. **Messaging endpoint:** `https://<host>/api/messages` (the `/api/messages` path
   and `https` are both mandatory and commonly forgotten).
4. **Enable the Microsoft Teams channel** on the Azure Bot resource.
5. **Expose localhost — Microsoft Dev Tunnels (recommended over ngrok):**
   `devtunnel host -p 3978 --allow-anonymous` (you must log in to `devtunnel`
   first; `--allow-anonymous` is required because Bot Connector calls
   unauthenticated). Paste the tunnel URL + `/api/messages` as the messaging
   endpoint. URL can change on restart of a non-persistent tunnel.
6. **Local test:** Bot Framework Emulator (archived but works) against
   `http://localhost:3978/api/messages`. **Nuance:** Emulator + Dev Tunnels do not
   support single-tenant/managed-identity auth — locally test in anonymous mode
   (empty App ID/secret); single-tenant auth is really verified only via tunnel +
   real Teams. This is why "works in Emulator, 401 in Teams" happens.
7. **Teams app manifest:** a ZIP of `manifest.json` + two icons (color 192×192,
   outline 32×32). Schema ~v1.19+ (current line v1.22–v1.25). **`bots[].botId`
   must equal `MicrosoftAppId`** (most common "installs but never replies" cause).
   Build manually or via Teams Developer Portal (dev.teams.microsoft.com). The
   toolkit was renamed Teams Toolkit → **Microsoft 365 Agents Toolkit** (2025).
8. **Sideload (custom app upload):** Teams → Apps → Manage your apps → Upload a
   custom app → ZIP. Requires "Upload custom apps" enabled in **Teams admin
   center → Teams apps → Setup Policies** (admin-controlled; propagation up to
   24h). If blocked: use a free **Microsoft 365 Developer Program** tenant (custom
   upload on by default), or ask an admin for a dev setup policy.

### Common failures & diagnosis
- **401 on reply (endpoint receives request, bot silent):** identity mismatch —
  stale/regenerated secret not updated in config; single-tenant without
  `MicrosoftAppTenantId`; special chars in secret (regenerate).
- **Installs but silent:** `botId` ≠ App ID; endpoint missing `/api/messages`;
  tunnel down / URL changed.
- **Endpoint gets no requests:** Teams channel not enabled; tunnel unreachable / `http`.
- **Can't sideload:** "Upload custom apps" disabled in tenant (needs admin).

## Key sources
- Bot Framework SDK → Agents SDK migration (Python), Microsoft Learn (2026-02).
- microsoft/botbuilder-python & botframework-emulator — GitHub (archived 2026-01-05).
- microsoft-agents-hosting-core / microsoft-agents-activity — PyPI (1.1.0, 2026-06).
- Register a Bot Framework bot with Azure — Microsoft Learn (2025-12).
- Microsoft Dev Tunnels get-started — Microsoft Learn.
- Upload your custom app / prepare O365 tenant — Microsoft Learn (2026-04 / 2025-09).

## Open items to verify at implementation time
- Exact Agents SDK Python init/config API for aiohttp (`CloudAdapter`,
  `AgentApplication`, connection-manager wiring) against the installed version.
- Whether the target Teams tenant allows custom app upload (Phase-2 blocker).
- Latest manifest schema version compatible with a plain bot (any 1.19+ works).
