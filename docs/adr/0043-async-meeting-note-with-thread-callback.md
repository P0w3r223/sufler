# 0043 — Async meeting-note execution with a Teams-thread callback

Date: 2026-07-28
Status: proposed
Author: P0w3r223
Related to: [ADR 0041](0041-production-m3-meeting-note-write-from-teams-door.md) (production `/notatka`),
[ADR 0042](0042-meeting-note-sender-authorization.md) (sender authorization),
[ADR 0009](0009-meeting-note-flow-and-write-surface.md) (M3),
[ADR 0006](0006-write-capability-gate-2.md) (write gate, create-only),
`roadmap-v1-gap-analysis.md` (B3)

---

## Context

The production `/notatka` (ADR 0041) runs its chain **inline** in the poller's message loop:
`transcripts.fetch()` (Graph) → `summarizer.summarize()` (Claude) → `save_note()`. Two structural
problems, both flagged by the M4 design pass:

1. **Liveness stall (HIGH).** The poller iterates messages sequentially and `await`s each `_handle`.
   While a `/notatka` fetch+summary runs (seconds to over a minute), the next channel, `refresh_auth`,
   and state `persist` all wait. One slow note freezes the whole door.

2. **Duplicate/lost note on retry (CRITICAL).** The watermark advances only after a message is handled,
   and `save_note` is create-only (collision → `-2`). A crash mid-chain → after restart the message is
   seen again → `/notatka` reruns → a **second note with a `-2` suffix** plus a repeated Claude cost.
   The "at-least-once with dedup" of reply text does not protect the note write.

The callback primitives already exist: the poller holds `post_reply(team, channel, root, text)` and
`HttpxTeamsNotifier.reply_channel(...)` (async, with `ThreadRootGone` handling) can deliver a result
back to the originating thread.

Hard constraints: hexagonal (async enters through a seam, not the core); the write stays gated +
create-only; the read-only dispatcher (ADR 0017) untouched; no new heavy process/service (single-host
internal tool); authorization stays synchronous/fail-fast (ADR 0042) — only the slow work moves.

## Decision

1. **In-process fire-and-forget.** On `/notatka`, the router does the fast, synchronous part inline —
   parse args + **authorize the resolved `Actor` (ADR 0042)** — then, instead of running the slow chain,
   **schedules** it and returns an immediate ack ("Przyjąłem, składam notatkę…"). The transcript fetch +
   Claude summary + `save_note` run in the background (thread-pool executor); on completion the result
   (success detail or a readable error) is **published back to the same thread** via the existing
   `post_reply` / `reply_channel`. A bounded in-flight limit (semaphore / max concurrent) caps parallel
   Claude calls.

2. **Deterministic `note_id` = the idempotency key (non-negotiable).** The note id is derived from
   `(project, meeting_date, meeting_ref)`. A manual re-`/notatka` after a lost background task collides
   on the same id → "already filed", **not** a `-2` duplicate. This closes the CRITICAL duplicate risk
   regardless of persistence, and is the one part required even before async lands.

3. **Authorization before enqueue.** The `Actor` is resolved and checked synchronously at the door; the
   scheduled task carries the already-resolved actor. A refusal ("brak uprawnień") is immediate, with no
   background work. Async never authorizes in a detached task where the transport identity is gone.

4. **The async seam is additive and adapter-side.** Scheduling + callback live in the Teams-door layer
   (poller/responder wiring), captured via a loop reference (`run_coroutine_threadsafe` /
   `call_soon_threadsafe`) since `_respond_sync` runs in a thread-pool worker. The core `MeetingNoteService`
   stays synchronous and I/O-shaped by its ports. Background-task errors are **never swallowed**: every
   task publishes a clear success/failure line to the thread and logs with context.

5. **Gated, OFF by default.** Async execution ships behind its own flag (default OFF); flipping it on is
   an operator decision with this ADR of record. With the flag OFF, `/notatka` keeps the ADR 0041 inline
   behavior (already gated). This ADR stays `proposed` until accepted.

## Alternatives considered

- **Persistent job on the existing `EventStore`** (append a "meeting-note-requested" record; a consumer
  task claims/executes/completes; callback on done). Survives crash/restart and is idempotent by
  construction, reusing `events.db` (ADR 0019) with no new infra. Deferred: it needs a claim/lease state
  machine over an append-only log and widens the EventStore's role from bridge to job queue —
  over-provisioned for a dozen people occasionally running `/notatka`, where a lost in-flight task is
  recoverable by simply re-running (idempotent via the deterministic id). Documented as the upgrade path
  if in-flight loss proves painful.
- **A separate worker process with a queue.** Rejected by the "no new heavy runtime" constraint and by
  ADR 0041 itself ("a dedicated separate door — premature").
- **Keep it inline (do nothing).** Rejected: the liveness stall and the retry-duplicate are real and
  named; the deterministic id + immediate ack are the minimum fix.

## Consequences

- **Buildable/verifiable now (gated OFF):** the deterministic-id idempotency key, the ack + background
  scheduling seam, the thread callback, the bounded concurrency, error-to-thread publishing. Unit-tested
  on fakes (fake scheduler, fake notifier). Golden MCP surface unchanged.
- **Not verified live (parked 2026-07-28):** transcript scopes are admin-consented, but real
  ack→background→callback ordering and `ThreadRootGone` handling still confirm on live-smoke after the
  gate is flipped and a real meeting is available.
- **CRITICAL/HIGH closed:** the deterministic id removes retry duplicates; ack + background removes the
  door stall.
- **Ephemeral, by choice:** a crash loses an in-flight task; the user re-runs `/notatka` and the
  idempotent id makes the retry safe. Persistence (EventStore job) remains a later, additive upgrade.

## Follow-ups

- On acceptance: flip to `accepted`; live-smoke ack + callback + a forced-retry idempotency check.
- Later, additive: persistent EventStore-backed job if in-flight durability becomes necessary.
- **Deferred (pre-existing, widened by async concurrency; safe while gated OFF):**
  - The background `ThreadPoolExecutor` queue is unbounded — a burst of `/notatka` enqueues
    unbounded pending Claude calls (idempotency only dedups once a write lands). Member-gated +
    low-volume, but consider an explicit in-flight cap ("try later") if volume grows.
  - `teams_graph/auth.py` does a non-atomic read-modify-write of the MSAL token cache; async worker
    threads widen the concurrency. Consider an atomic (temp+replace) cache write or a lock before
    enabling async on a busy tenant.
- Applied from B3 review: unique per-write temp file in `MarkdownNotesWriter` (no shared-inode
  corruption under parallel writers), `NoteExistsError` for idempotent race resolution, Graph
  `HTTPStatusError` mapped to actionable text, per-call auth header in the thread-reply poster,
  and a cheap `require_project` guard before the transcript fetch.
