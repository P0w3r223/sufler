# Can an MCP server push events into a Claude Code session?

Date: 2026-07-27
Status: accepted
Author: P0w3r223
Related to: [ADR 0040](../adr/0040-eventstore-to-mcp-session-cursor-read.md), [ADR 0019](../adr/0019-shared-event-store.md)

---

## Question

WorkMate holds a shared append-only `EventStore` of cross-system events (GitHub/Jira/CI/Teams,
ADR 0019). For Roadmap V1 Phase 3 gap **A3** we need those events to reach a **Claude Code session**
that has WorkMate connected as an MCP server. The load-bearing unknown: **does the Claude Code MCP
client receive server-initiated push (resource subscriptions / notifications), or must we fall back
to a pollable resource/tool over the `EventStore`?**

## Method

Three-perspective web research (pass 1) — Claude Code client capabilities, the MCP spec's
server→client surface, and real-world ecosystem patterns — then a focused pass 2 on the one
decision-flipping, single-sourced finding (Claude Code "Channels").

## Findings

### 1. Standard MCP server→client push does NOT reach a Claude Code session
- **Resource subscriptions** (`resources/subscribe` + `notifications/resources/updated`) are **not
  implemented** in Claude Code — issue #7252, *closed as not planned*. Resources are discovery-only
  (`resources/list` for @-mention autocomplete); `resources/read` is never auto-called, so resource
  content never enters context on its own.
- `notifications/message` (logging) and `notifications/progress` are **received but never surfaced**
  to the model or user.
- `sampling/createMessage` and `elicitation/create` are **not implemented**.
- Per the MCP spec, every server→client mechanism is **optional for the client**; capability
  negotiation at `initialize` gates all of it, and a minimal client may legitimately ignore
  subscriptions, all `*/list_changed`, logging, progress, sampling, and elicitation.
- **Transport matters:** on **stdio** a server *may* write unsolicited notifications freely (it owns
  stdout); on **Streamable HTTP** server push needs the client to hold open a standalone GET SSE
  stream, which is client-optional. Either way there is no MCP mechanism to *wake* a client that
  isn't listening.

### 2. Claude Code "Channels" can push — but is not a responsible foundation today
Claude Code has a proprietary extension (`claude/channel`, `notifications/claude/channel`) that pushes
events into a live session as `<channel>` tags. Verified status (official docs
`code.claude.com/docs/en/channels{,-reference}` + GH issues):
- **Research preview**, `experimental` capability; the docs state the `--channels` flag syntax and
  **protocol contract may change**.
- A **custom/self-built** server cannot register a channel in a normal session without the
  `--dangerously-load-development-channels` startup flag (full-screen "local development" warning),
  or Enterprise-only `allowedChannelPlugins` allowlisting (`channelsEnabled` is off by default for
  Team/Enterprise). No per-server `settings.json` opt-in.
- **Delivery is at-most-once with silent drops**, no ack, no persistence across session restart; if
  the session hasn't loaded the channel or org policy blocks it, events vanish with no error to the
  server.
- **Non-portable** (Claude-Code-only; every other MCP client ignores it), **zero observed
  third-party production adoption**, and two open unfixed non-delivery bugs (#36827, #45563).
- Not available on Amazon Bedrock / Google Vertex / Microsoft Foundry.

### 3. The de-facto ecosystem pattern is a pollable "read since cursor" tool
Across independent sources, the working way to make an assistant aware of async events is a plain tool
the client's normal request/response loop calls — `read_events(since=<cursor>)` — with idempotent
handling keyed on event id. Major clients (Claude Desktop, ChatGPT, LangChain, ADK, OpenAI Agents) do
**not** turn server notifications into agent actions. Blocking/long-poll "wait for event" tools are a
recognized anti-pattern (client timeouts, freezes the agent). The MCP-native async story (Tasks,
SEP-1686, 2025-11-25) is itself **poll-based**, not push. A standardized push future (Triggers &
Events WG, chartered 2026-03-24) exists but is pre-spec — track, don't build on it.

### 4. WorkMate already owns ~80% of the fallback
The `EventStore` (ADR 0019) already provides `read_since(after_id)` over a monotonic DB `id` cursor,
`UNIQUE(source, external_id, kind)` dedup, and ingest-time `reject_dangerous_content`. An out-of-band
human nudge (notifier → Teams, ADR 0022) already exists. The only missing piece was exposing a cursor
read on the MCP door.

## Conclusion

Server-initiated push into a Claude Code session is **not viable** today (standard push unavailable;
Channels not a responsible load-bearing dependency). Adopt the **pollable cursor read tool** over the
`EventStore` as the portable, standard-MCP foundation, and keep Channels as a possible gated,
degrade-to-poll accelerator for later. → implemented as **ADR 0040** (`read_events_since`).

## Key sources
- Claude Code MCP / Channels docs: `code.claude.com/docs/en/mcp`, `.../channels`, `.../channels-reference`
- claude-code issues: #7252 (resource subscriptions, not planned), #3174 (message notif not shown),
  #1785 (sampling), #7108 (elicitation), #36827 / #45563 (channel non-delivery)
- MCP spec 2025-06-18 (lifecycle, resources, transports, elicitation) + 2025-11-25 changelog (Tasks)
- MCP Triggers & Events WG charter; SEP-1686 (Tasks); community: discussions #1192 / #491, Hookdeck
  "MCP event gateway"
