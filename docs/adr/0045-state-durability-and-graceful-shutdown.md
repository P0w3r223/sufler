# 0045 — Poller state durability and graceful shutdown

Date: 2026-07-28
Status: accepted
Author: P0w3r223
Related to: ADR 0019 (shared EventStore), ADR 0020 (GitHub door), ADR 0030 (Jira door), ADR 0035 (weekly worklogs), ADR 0044 (Linux container deployment)

---

## Context

On the containerized fleet (ADR 0044) each long-polling door persists a small JSON watermark file
(`github_state.json`, `jira_state.json`, the Teams-graph state) to the shared `state` volume. Two failure
modes surfaced during deployment review (R1):

1. **Torn write.** `docker stop` (SIGTERM, then SIGKILL after the grace period) or a host reboot can
   interrupt a state write. The doors wrote with `Path.write_text(...)`, which truncates-then-writes in
   place: an interrupted write leaves a **half-written file**. On the next start `load()` did a bare
   `json.loads(...)` with no error handling, so a truncated file raised `JSONDecodeError` and killed the
   process at startup. Under `restart: unless-stopped` that is a **crash-loop** — the door never recovers
   on its own.

2. **No shutdown contract.** No door installed a SIGTERM handler. Every process relied on the default
   signal behavior tearing down `asyncio.run` (or the library loop). A stop always landed as an abrupt
   interruption, maximizing the window in which (1) could occur, and never logging a clean exit.

The worklogi door was already immune to (1): its state module writes atomically (temp + `os.replace`) and
loads tolerantly — the pattern this ADR generalizes.

## Decision

Three coordinated guarantees, copied from the existing worklogi pattern — no new libraries, no new
abstraction:

### 1. Atomic state write

`github/state.py` and `jira/state.py` `save()` now write to a sibling `*.tmp` and `os.replace()` it onto
the real path. `os.replace` is atomic on POSIX and Windows, so the real state file is **never** observed
half-written: a crash mid-write leaves the previous complete file intact (plus a stray `.tmp`). This alone
closes the crash-loop, independent of how the process dies (SIGTERM *or* SIGKILL).

### 2. Corrupt-file policy: warn and start empty

`load()` catches `JSONDecodeError`/`UnicodeDecodeError`/`OSError` (and rejects non-dict shapes), logs a
`WARNING`, and returns an empty state instead of raising. This is safe **only because the doors re-poll
idempotently**: events dedup in `events.db` on a stable key (`UNIQUE(source, external_id, kind)`), which
lives on the same durable volume, so a watermark rebuilt from zero re-ingests nothing new — it just costs
one wider poll. Applied to **github and jira only**.

**Teams-graph is deliberately excluded from (2).** Its state carries a `replied` dedup set whose safety is
*not* backed by `events.db`; starting empty there risks duplicate replies to users. Teams-graph keeps the
atomic-write protection it gains at the door level and the shutdown contract below, but its `load()` stays
strict — a corrupt Teams-graph state should fail loudly, not silently re-answer.

### 3. Graceful shutdown contract

On SIGTERM/SIGINT a door **finishes the current cycle, persists, and exits 0** — it does not abort
mid-cycle.

- **Async doors (github, jira, teams-graph).** The poller takes an optional `asyncio.Event stop`. Its loop
  checks the flag after each round and waits the poll interval *interruptibly* (`wait_for(stop.wait())`),
  so a stop during the idle wait wakes immediately, while a stop during a poll lets that poll finish and
  persist first. `app.py` installs the handler via `loop.add_signal_handler` (guarded with
  `contextlib.suppress(NotImplementedError)` for Windows dev, where it falls back to `KeyboardInterrupt`).
  Where the poller shares the loop with auxiliary pumps (notifier push, CI-autocomment cursor), the poller
  — the owner of the state file — stops cooperatively and the auxiliary pumps are cancelled; their writes
  are atomic and their pushes are at-least-once, so cancellation cannot corrupt state or lose events.
- **Worklogi (sync).** A `signal.signal` handler sets a `threading.Event`; the outer loop and the capped
  nap loop (`_MAX_SLEEP_S`, already designed to wake on signals) check it and exit after the current run.
  State is persisted incrementally per person, so no work is lost.
- **Telegram.** No change: it owns no state file, and `run_polling()` (python-telegram-bot) already
  installs its own SIGINT/SIGTERM/SIGABRT handlers and shuts down cleanly. A custom handler would only
  conflict with the library's.

### 4. Deployment grace period

`docker-compose.yml` sets `stop_grace_period: 45s` on the shared service anchor. The default 10s does not
fit a poll round in flight (httpx timeout is 30s), so Docker would SIGKILL before the cooperative handler
finished — defeating (3). 45s is a starting value; deployments watching many Teams-graph channels may need
more (operator-tunable).

## Consequences

- The R1 crash-loop is closed by (1) alone; (2)+(3)+(4) add defense-in-depth and clean, observable
  shutdowns (each door logs `… zatrzymanie na sygnał, stan zapisany.` and exits 0).
- SMOKE T15 is tightened accordingly: it now requires `JSON_OK` **and** the clean-shutdown log line across
  5/5 repeated stops under load. Immediate SIGTERM kill is no longer an accepted outcome.
- The github/jira/teams-graph pollers gain a `stop` constructor parameter (default `None` = run forever,
  backward-compatible for tests and any caller that does not wire signals).
- Asymmetry is intentional and documented: github/jira get tolerant load, teams-graph does not, for the
  dedup-safety reason above.

## Alternatives considered

- **Cancel all tasks on SIGTERM (no cooperative flag).** Simpler, and safe now that writes are atomic, but
  it aborts mid-cycle rather than finishing it, and gives no clean per-door "finished and persisted" point.
  Rejected in favor of an explicit, observable contract.
- **Make teams-graph load tolerant too.** Rejected: empty Teams-graph state can cause duplicate replies,
  which its `replied` set exists to prevent; failing loudly is safer than silently re-answering.
- **Rely on `restart: unless-stopped` to recover from a torn file.** That *is* the crash-loop — restarting
  into the same unparseable file. Rejected.
