# 0049 — Pseudonymized usage metrics (per-door call counter)

Date: 2026-07-29
Status: accepted
Author: P0w3r223
Related to: [ADR 0019](0019-shared-event-store.md) (separate SQLite store pattern),
[ADR 0035](0035-weekly-timesheets.md) (Europe/Warsaw week semantics),
[ADR 0006](0006-write-capability-gate-2.md) (write posture / data boundary)

---

## Context

The plan to make the bot attractive needs the cheapest possible proof that it is used: calls per
door, unique users per week, and returning users. Without a signal we cannot tell whether a feature
landed or died. The data must be collected without turning the bot into a surveillance log — the
inbound identity is an AAD object id (PII), and a plaintext "who asked what, when" table is both a
confidentiality risk and out of proportion for a dozen internal users.

## Decision

1. **A separate, lightweight SQLite counter** (`SqliteMetricsStore`) — its own file/table, never
   mixed with the knowledge base (`data/`) or `events.db`. Same multi-process concurrency pattern as
   the other stores (single connection + `Lock`, `WAL` + `busy_timeout`), because the agent doors
   (teams/telegram/cli) are separate processes sharing one file. Grain = `(door, user_key, week)`
   with an incrementing UPSERT, which yields calls = `SUM`, unique = `COUNT(DISTINCT user_key)`, and
   returning = users present in ≥2 distinct ISO weeks — all as SQL over one table.

2. **Pseudonymized identity, never raw** (privacy by construction). The raw `sender_id` is hashed in
   the core (`pseudonymize` = truncated sha256; empty → `anon`) *before* it reaches the store. The
   database never sees the AAD id, so the counter cannot be read back as "who asked what". Content of
   messages is never stored — only the fact and pseudonymized origin of a call.

3. **Hexagonal, recorded at a single chokepoint.** Pure bucketing + hashing live in
   `core/domain/metrics.py`; the port `MetricsStore` and the thin `MetricsService` orchestrate; the
   SQLite impl is an outbound adapter. Recording happens once, at the top of
   `ConversationalResponder._respond_sync` (covers every agent door and command), so no per-door
   churn. `core ↛ adapters` holds.

4. **Best-effort, never fatal.** The record call is wrapped in try/except at the door: a counter
   failure (e.g. a locked SQLite file) logs a warning and the turn proceeds. Metrics must never break
   a user's answer.

5. **OFF by default.** Enabled only by the presence of `WORKMATE_METRICS_DB`; unset → the service is
   `None` and doors record nothing. No fail-fast — it is a non-critical side channel. Read-out is a
   separate console script (`workmate-metrics`) that folds the counter into a per-door table.

## Alternatives considered

- **Piggyback on structured logs + `record_run`.** No new persistence, but metrics would be computed
  ad-hoc by scraping logs, with no queryable store and log-retention coupling. Rejected in favor of a
  small durable counter (product decision) — the counter is cheaper to query and bounded in size.
- **Store raw `sender_id` (plaintext).** Rejected: PII/confidentiality risk with no upside; the
  pseudonym is sufficient for unique/returning counts.
- **Per-tool / per-MCP-call metrics.** Deferred: the MCP door does not pass through the Responder
  seam; instrumenting it is a separate chokepoint. This ADR covers the conversational doors where
  end-user attractiveness is measured.

## Consequences

- **Buildable/verifiable now (OFF by default):** the pure domain, the port + service, the SQLite
  adapter, the single-point wiring, the report CLI. Unit-tested (bucketing incl. ISO-week boundary,
  pseudonym determinism/anonymity, UPSERT increment, unique/returning aggregation, best-effort
  failure). Golden MCP surface / `NoteMetadata` unchanged.
- **Privacy stance of record:** the counter is pseudonymized and content-free; enabling it is one env
  var, and the data cannot be re-identified from the store alone.
- **Not covered (follow-up):** MCP-door metrics (different seam); any dashboarding beyond the CLI fold.
