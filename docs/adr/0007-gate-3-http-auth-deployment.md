# 0007. Gate 3: streamable-http deployment with self-managed per-person bearer auth

Date: 2026-07-08
Status: accepted
Author: P0w3r223
Related to: supersedes docs/adr/0004-transport-stdio-then-http.md; builds on docs/adr/0006-write-capability-gate-2.md; docs/roadmap.md (Gate 3)

---

## Context

ADR 0004 fixed the transport switch (`SUFLER_TRANSPORT=streamable-http`) but
explicitly deferred authentication, per-person identity, and least privilege to
Gate 3. This ADR is that decision. Week 4 goes live: Sufler is hosted on a
Windows Server and reached over HTTP by each developer's Claude Code. The team
fixed three hard constraints before design: (1) self-managed per-person bearer
tokens, no external IdP (no Entra/OAuth); (2) Windows host, uvicorn as a Windows
Service behind IIS as a TLS-terminating reverse proxy; (3) the hosted HTTP door
exposes only the 4 read tools (`enable_write=false`) — `save_note` stays on the
trusted local stdio dev door only. The remaining open questions this ADR settles
are *where* token verification is wired into the FastMCP stack, *where* token
secrets live so they stay outside the core's and the tools' reach, and *how*
this respects the `core ↛ adapters` dependency rule.

## Options considered

### Option A: FastMCP native auth (`token_verifier` + `AuthSettings`)
- **Description**: Pass a custom `TokenVerifier` to the `FastMCP(...)`
  constructor and enable `auth=AuthSettings(...)`. The framework's
  `BearerAuthBackend` reads `Authorization: Bearer` and calls the verifier, which
  maps token → `AccessToken(subject=person, scopes=[...])`.
- **Pros**: Least glue code; verification, `WWW-Authenticate`, and
  `RequireAuthMiddleware` are wired by the framework; identity lands in the MCP
  auth context for free.
- **Cons**: `AuthSettings` requires `issuer_url` and, when set,
  `streamable_http_app()` publishes OAuth Protected-Resource Metadata and emits
  `WWW-Authenticate` pointing at an issuer (verified in
  `mcp/server/fastmcp/server.py`). This is OAuth 2.1 Resource-Server framing —
  directly at odds with the team's "no IdP, opaque tokens" decision, and an MCP
  client hitting a 401 may launch OAuth discovery/DCR and fail.
- **Risk**: Medium — semantic mismatch; couples us to an OAuth surface we
  deliberately rejected.

### Option B: Thin bearer middleware in the MCP door (chosen)
- **Description**: A small ASGI/Starlette middleware in
  `adapters/inbound/mcp/auth.py` wraps `mcp.streamable_http_app()` via
  `app.add_middleware(...)`. It reads `Authorization: Bearer <token>`, verifies
  against a hashed token store, attaches the resolved person to request state,
  and returns `401 + WWW-Authenticate: Bearer` (no OAuth issuer) on missing or
  invalid tokens. `main()` serves this app with an explicit `uvicorn.Config`.
- **Pros**: Honest opaque-bearer semantics, zero OAuth surface; auth lives
  entirely in the door layer; full control over TLS/host/port; trivial to
  unit-test in isolation; no new runtime dependency (stdlib `hmac`/`hashlib`;
  uvicorn/starlette already present transitively).
- **Cons**: ~40–60 LOC we own and must review; must set FastMCP
  `transport_security` allowed hosts/origins when binding to a real host
  (DNS-rebinding protection auto-enables only for localhost).
- **Risk**: Low — self-contained, reversible, matches the constraints as written.

### Option C: IIS terminates authentication
- **Description**: IIS (ARR + URL Rewrite) validates credentials before proxying
  to the local app.
- **Pros**: Auth never reaches Python; central Windows-side enforcement.
- **Cons**: IIS has no native opaque-bearer→person mapping without Windows Auth
  (= AD/Kerberos, an IdP the team excluded) or a custom native module. It cannot
  feed per-person identity into Sufler for least-privilege decisions or audit.
- **Risk**: High — either smuggles in an IdP or becomes a bespoke IIS module; no
  clean identity handoff.

## Decision

Adopt **Option B**. Token verification is a thin bearer middleware living in the
door layer (`adapters/inbound/mcp/auth.py`); it stays out of `core/` entirely, so
the `core ↛ adapters` rule is untouched — identity is a door-level attribute the
four read use-cases never see. Concretely:

- **Injection point**: the `server.py` composition root, only when
  `transport == "streamable-http"`, builds `app = mcp.streamable_http_app()`,
  applies `app.add_middleware(TokenAuthMiddleware, verifier=...)`, and serves it
  via an explicit `uvicorn.Config`. The stdio path stays `mcp.run("stdio")`,
  unchanged and unauthenticated (local, trusted). Native FastMCP auth (Option A)
  remains the documented migration target if the org later standardizes on
  OAuth/Entra.
- **Token → identity**: opaque random tokens, one per person. At rest we store
  only `sha256`; the verifier does a constant-time (`hmac.compare_digest`) lookup
  returning `{person, scopes}`. Token store schema: a list of
  `{ "person": "...", "token_sha256": "...", "scopes": ["read"] }`.
- **Where secrets live**: the token store is a file **outside `data/`** and
  outside the repo working tree — `C:\ProgramData\Sufler\tokens.json`, path via
  new `SUFLER_TOKENS_FILE`, NTFS-restricted to the service account + admins.
  Because every read tool is bounded to `notes_dir`/`projects_registry` under
  `data/`, a store outside `data/` is physically unreachable by the tools —
  satisfying the roadmap's "secrets out of the core's reach, not an indexed
  folder."
- **Least privilege per door (enforced in code)**: the HTTP branch builds a
  dedicated server with `enable_write=False` **regardless of the environment**
  (`_build_http_server` in `server.py`) → only the 4 read tools are ever
  registered on the network door; a forgotten `SUFLER_ENABLE_WRITE` env cannot
  expose `save_note`. Write exists only on the local stdio dev door. TLS is
  all-or-nothing (both cert and key, or neither) — a partial config is a hard
  startup error, never a silent downgrade to plaintext HTTP. Logging is configured
  at startup so the per-request identity audit line (INFO) is actually emitted.
- **Windows form + TLS**: run uvicorn as a **Windows Service** (process
  lifecycle) bound to loopback, behind **IIS as a TLS-terminating reverse proxy**
  (company cert, port 443) — the idiomatic Windows path. IIS/ARR must have
  response buffering disabled, because streamable-http streams over SSE/chunked.
  A single-box fallback — uvicorn serving TLS directly via
  `ssl_certfile`/`ssl_keyfile` — uses the same code path and stays available if
  IIS is unavailable.

## Consequences

- **Go-live minimum (all required)**: TLS on the wire; bearer middleware in the
  door; tokens hashed at rest in a store outside `data/` with NTFS ACLs;
  `enable_write=false` on the HTTP door; `401` with a generic message on missing
  or invalid token (never echo the token); explicit `transport_security` allowed
  hosts/origins for the bound hostname; per-request identity written to the log
  (who called which tool).
- **Deferrable (later, non-blocking)**: token expiry/rotation and revocation
  tooling; per-person rate limits/quotas; a real secret manager (DPAPI/Vault)
  instead of a flat file; a structured audit sink; finer scopes; migration to
  OAuth/Entra (the Option A path already exists in the framework).
- **What changes in code**: new `adapters/inbound/mcp/auth.py`; `config.py` gains
  `tokens_file`, `bind_host`, `bind_port` (and optional TLS cert paths), all
  defaulted so the stdio path is unaffected; `server.py` HTTP branch wraps the
  ASGI app and runs uvicorn; `core/` is not touched.
- **New constraints**: any per-person authorization logic must stay in the door,
  never in `core/`; the token store must never move under `data/`; onboarding a
  developer = issue a token + hand them the HTTP `.mcp.json`.
- **What we give up**: no standards-based token lifecycle (introspection, JWT
  expiry, central revocation) — accepted as the deliberate fast-but-safe MVP
  posture.
- **Risks**: long-lived static tokens can leak → mitigated by TLS-only, hashing
  at rest, a written rotation policy, and env-var expansion so tokens never land
  in a committed `.mcp.json`; IIS/ARR buffering can break streaming → disable it;
  Claude Code HTTP-transport support for `headers`/`${VAR}` expansion is confirmed
  during implementation of the onboarding artifact.
- **Revisit when**: the org adopts Entra/SSO, a non-Claude-Code client needs
  access, or token count outgrows a hand-edited file — then switch to Option A
  (native OAuth RS) or a real secret manager.
- **Supersedes ADR 0004**, which only fixed the transport mechanism and
  explicitly left auth open.
