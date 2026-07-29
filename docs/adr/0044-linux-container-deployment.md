# 0044 — Linux container deployment for the WorkMate fleet

Date: 2026-07-28
Status: accepted
Author: P0w3r223
Related to: ADR 0007 (Gate 3 HTTP auth), ADR 0019 (shared EventStore), ADR 0035 (weekly worklogs), `Powiadomienia_teams/` deploy pattern

---

## Context

The application code is already portable and CI-verified on `ubuntu-latest` (`.github/workflows/ci.yml`
runs the full quality gate on Linux). What was missing was the **operational packaging** for the *main*
WorkMate fleet on Ubuntu: `deploy/http/` only scaffolded a Windows target (IIS reverse proxy via
`web.config`, a Windows Service via NSSM, PowerShell smoke tests). The `Powiadomienia_teams/` sub-project
already ships a proven Linux deployment (multi-stage Dockerfile, `docker-compose.yml`, hardened systemd
unit), so a house pattern exists to mirror.

WorkMate is not one process. It is a fleet:

- `workmate` — MCP server, network mode (`streamable-http`), **read-only by construction** (ADR 0007).
- `workmate-github`, `workmate-jira`, `workmate-teams-graph` — outbound long-polling doors.
- `workmate-worklogi` — weekly scheduler (internal loop, single-instance lock, ADR 0035).
- `workmate-worklog-selfservice` — **on-demand** invocation (`--submission`/`--source-id`, ADR 0038).
- `workmate-telegram` — long-polling bot door.

Two cross-cutting invariants constrain the topology:

1. **Shared `EventStore`** (ADR 0019): every door writes events to one SQLite `events.db`; the notifier
   pushes to Teams; the agent reads from any door. All processes MUST see the *same* file.
2. **Loop guard** (ADR 0021/0031): a write-door and its poller MUST share the same token/account on the
   same `events.db` (echo `source` + self-skip). This is configuration, but it depends on (1).

## Decision

Ship a container deployment under `deploy/docker/`, mirroring the `Powiadomienia_teams/` pattern:

- **One image, many entrypoints.** A single multi-stage `Dockerfile` (build context = repo root) with all
  fleet extras (`agent github jira teams-graph worklogi telegram file-reply retrieval`). The image
  `ENTRYPOINT` is `tini --`; each Compose service sets its own `command:` (`workmate`, `workmate-github`,
  …). The test stage runs the full pytest gate during build — the runtime image cannot be produced from
  red source (house guarantee, matches the sub-project).

- **Shared state on a named volume.** A `state` volume is mounted at `/var/lib/workmate` in every service;
  all operational paths point there via env (`WORKMATE_EVENTS_DB`, `WORKMATE_CONVERSATIONS_DB`,
  `*_STATE`, MSAL token caches, `WORKMATE_TOKENS_FILE`, `WORKMATE_RETRIEVAL_INDEX`). This satisfies the
  shared-EventStore invariant: separate containers share one inode, so SQLite WAL + `busy_timeout`
  behave exactly as multiple processes on one host.

- **Notes are a bind mount.** The knowledge base `./data` is bind-mounted **read-only** into the MCP
  server (and the pollers, for project resolution). Notes are managed on the host by `git pull`; the
  HTTP door never writes (ADR 0007), and `save_note` stays on trusted local stdio.

- **nginx terminates TLS and streams SSE.** An `nginx` service fronts `mcp:8000` on the internal network:
  `proxy_buffering off` + long `proxy_read_timeout` for `streamable-http` (the Linux equivalent of the
  IIS/ARR `responseBufferLimit=0`). `WORKMATE_ALLOWED_HOSTS` must carry the public host (DNS-rebinding
  protection), and `proxy_set_header Host` must match it, else the door answers `421`.

- **On-demand and priming stay out of autostart.** `worklog-selfservice` and the one-time MSAL
  `--login` / discovery runs go through `docker compose run --rm <svc>` (the self-service door is under a
  Compose `profiles: [tools]`, so `up` never starts it). Device-code token caches are primed once
  interactively and then persist on the `state` volume.

- **PII isolation.** `worklogi` output (imienne godziny) goes to a **separate** `worklogi-out` volume,
  never under `./data` or the repo (ADR 0035 fail-fast is preserved because the path is outside both).

Hardening mirrors the sub-project: non-root uid `10001`, `read_only` rootfs with writable volumes only,
`no-new-privileges`, `tmpfs /tmp`, capped json-file logging, `apt-get upgrade` in the runtime stage.

## Alternatives considered

- **systemd on bare metal** (one hardened unit per process, like `powiadomienia-teams.service`). Rejected
  as the primary path per the deployment-shape decision: Docker Compose gives per-process isolation and a
  reproducible, test-gated image in one artifact, and reuses the sub-project's proven pattern. systemd
  remains a viable future addition (the fleet is plain console scripts + env, so units are trivial to add).
- **Caddy instead of nginx.** Simpler auto-TLS, but nginx is the more common, better-understood choice on
  the target Ubuntu servers; SSE needs only `proxy_buffering off`, which nginx does plainly.
- **Baking notes into the image.** Rejected: notes change far more often than code; a bind mount keeps the
  knowledge base git-managed on the host and avoids rebuild-on-note-edit.

## Consequences

- Standing up the fleet is `git pull` + configure `env` + prime MSAL logins once + `docker compose up -d`.
- Adding a process later = one more Compose service reusing the shared image and `state` volume.
- The Windows `deploy/http/` scaffold stays valid for a Windows target; this ADR adds the Linux path, it
  does not remove the other.
- Operators must remember the interactive MSAL priming step before starting `teams-graph`/`worklogi`; the
  README makes it the explicit first run-step. A lost refresh token still needs a human `--login`
  (unchanged from ADR 0015/0035).
