# 0033. Jira Cloud support — dual-provider adapter (REST v3 + ADF, Basic auth)

Date: 2026-07-20
Status: accepted (amended by docs/adr/0054-reduce-jira-to-read-only-my-tasks.md, 2026-07-30 — the
  write/transition halves of this dual-provider client are removed; the read half (search/auth,
  both Server/DC and Cloud) survives and is reused by "my tasks")
Author: P0w3r223
Related to: docs/adr/0030-jira-server-read-door.md,
  docs/adr/0031-jira-write-capability-gate-5.md,
  docs/adr/0032-jira-status-transition-capability.md,
  docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md

---

## Context

The Jira bridge (ADR 0030–0032, released as v1.1.0) targets **Jira Server / Data Center only**:
PAT Bearer auth, REST **v2**, offset pagination (`startAt`/`total`), plain-text bodies, and account
identity by `name`/`key`. During preparation for the live smoke test it turned out the deployment
must integrate with **Jira Cloud** instead. Cloud is incompatible on five axes:

1. **Auth** — Cloud has no Bearer PATs; it uses **Basic** (`email:api_token`) or OAuth.
2. **Search** — the old `GET/POST /rest/api/{2,3}/search` (offset) is **removed on Cloud** (410 Gone,
   enforced 2025). Replacement: `POST /rest/api/3/search/jql` with **cursor** pagination
   (`nextPageToken`/`isLast`, **no `total`**), and inline changelog/comments **capped at 20 each**.
3. **Body format** — REST v3 returns/accepts `description` and `comment.body` as **ADF** (a JSON
   document), not a string.
4. **Identity** — Cloud exposes only `accountId` (`name`/`key` removed for GDPR).
5. **Base URL** — a Cloud site is `https://<site>.atlassian.net`.

The read layer (`jira/selection`) and the write/transition application layer
(`core/application/jira.py`) were already written defensively (they read `accountId` and `fieldId`,
and mock the port — not HTTP), so the incompatibilities are confined to the outbound HTTP adapter
and configuration. The team wants to **keep Server/DC working** (some projects may stay on-prem).

## Options considered

- **A (chosen) — dual-provider: two adapter implementations behind the same ports, selected by
  config.** Add `HttpxJiraCloudClient` next to `HttpxJiraClient`; a factory `build_jira_client`
  picks one by `JiraSettings.deployment` (`server` default, `cloud`). ADF is translated **at the
  adapter boundary** (encode on write, flatten to text on read) so `selection`, poller and services
  stay untouched. Mirrors the repo's "separate concrete doors" pattern (ADR 0030 option A2) rather
  than abstracting a generic client with runtime `if cloud:` branches.
- **B — replace Server/DC with Cloud.** Rejected: the user requires both; DC support is a working,
  released capability and losing it is a regression.
- **C — Cloud on REST v2 + plain text (minimal change).** Rejected: v2 is legacy on Cloud and ADF is
  Atlassian's forward path; the `/search/jql` rewrite is mandatory regardless of body format, so the
  incremental cost of ADF is small and buys a future-proof adapter.

## Decision

Adopt **A**, dual-provider:

- **Config** — `JiraSettings` gains `deployment` (`SUFLER_JIRA_DEPLOYMENT`, default `server`) and
  `email` (`SUFLER_JIRA_EMAIL`). `validate()` requires `email` when `deployment=cloud`; the Teams
  write-gate wiring (`_build_jira_catalog`) adds the same fail-fast so a Cloud write profile can't
  start without it. `token` stays the secret (PAT on DC, API token on Cloud); `self_account` is the
  PAT login on DC and the **`accountId`** on Cloud.
- **Adapter** — `HttpxJiraCloudClient` (REST **v3**): Basic `Authorization` from `email:api_token`;
  `authenticated_account()` via `/rest/api/3/myself` → `accountId`; **`POST /rest/api/3/search/jql`**
  with explicit `fields` (incl. `comment`) + `expand=changelog`, cursor pagination with a page cap
  and a **repeated-token guard** (a known Cloud pagination bug can chain forever); write/transition
  on `/rest/api/3/...`. `description`/`comment.body` are **ADF** — encoded via `text_to_adf` on
  write, flattened via `adf_to_text` on read **inside `search_issues`**, so everything above the
  adapter sees plain strings exactly as for Server/DC.
- **ADF** — a pure `core/domain/adf.py` (`text_to_adf`, `adf_to_text`): minimal doc/paragraph/
  hardBreak (+ mention/emoji) coverage, robust to `None`/non-dict/unknown nodes.
- **Factory** — `build_jira_client(sync_http, settings)` is the single provider-selection point;
  both wiring sites (`inbound/jira/app.py`, `teams_graph/app.py`) call it. Server/DC path, its client
  and tests are untouched (default `deployment=server` → zero-config backward compatibility).

## Consequences

- **Loop-guard invariant unchanged, semantics shifted to `accountId`.** Self-skip still compares the
  event actor to `self_account`; on Cloud both resolve to `accountId` (the `selection._author`
  fallback already handles it). The cross-process invariant holds: poller and Teams write door must
  share the same token/account on the same `events.db`.
- **Known limitation — inline 20/20 cap.** Bulk `/search/jql` returns at most 20 comments and 20
  changelog items per issue. For an incremental poller (short window from the watermark) this is
  effectively always enough; if a single issue gets >20 status changes or comments *between two
  polls*, some may be missed. A conditional per-issue fallback (`GET /issue/{key}/changelog` and
  `/comment`, paginated) is a documented follow-up, not built here.
- **Timezone caveat (Cloud).** JQL datetimes without a zone are interpreted in the **authenticated
  user's** timezone, not the instance's. The coarse `>=` JQL boundary plus the client-side exact
  freshness filter and dedup mitigate it, but the service account's Jira timezone should match the
  poller host (or be normalized via `myself.timeZone`) — noted for live-test.
- **No new tool, no gate change.** Cloud reuses the existing gates (`enable_jira_write`,
  `enable_jira_transition`) and tool surface; the MCP golden test is untouched (tools still enter via
  `extra_catalog`).
- **Out of scope.** OAuth 2.0 (3LO); migrating Server/DC to `/search/jql`; richer ADF rendering
  (tables, panels); the per-issue truncation fallback.
