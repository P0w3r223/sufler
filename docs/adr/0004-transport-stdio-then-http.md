# 0004. Transport: stdio first, streamable-http at Gate 3

Date: 2026-07-07
Status: superseded by ADR 0007
Author: P0w3r223
Related to: docs/reference/config.md, roadmap_workmate.pdf (Gate 3), docs/adr/0007-gate-3-http-auth-deployment.md

---

## Context

The roadmap runs local `stdio` for weeks 1–3 (each developer's Claude Code) and
switches to a hosted `streamable-http` deployment on the company server in week
4, gated by Gate 3 (deployment, per-person authentication, least privilege).
The server code should support both without a rewrite, and default to the safe
local option.

## Options considered

1. **stdio only.** Simplest; enough for the MVP proof, but not shareable as a
   hosted service — contradicts the week-4 milestone.
2. **http only.** Forces deployment and auth concerns from day one, before Gate
   2/3 are designed; heavier local dev loop.
3. **stdio default, http via config.** One code path, transport chosen by
   `WORKMATE_TRANSPORT`. Matches the roadmap's staged rollout.

## Decision

**Option 3.** `Settings.transport` defaults to `stdio`; setting
`WORKMATE_TRANSPORT=streamable-http` switches transport at run time via
`FastMCP.run(transport=...)`. No code change is needed to deploy over HTTP.

## Consequences

- Local development and MCP Inspector stay on stdio with zero configuration.
- The hosting step (week 4) is a configuration + deployment task, not a code
  change.
- **Authentication, per-person identity, and least-privilege access are NOT
  solved here** — they are Gate 3 decisions owned by the team and must be
  designed before going live over HTTP. This ADR only fixes the transport
  mechanism.
