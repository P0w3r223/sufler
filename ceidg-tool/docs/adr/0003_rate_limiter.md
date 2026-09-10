# ADR-0003: Rate limiter with two sliding windows, minimum spacing and persistent history

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: ADR-0004 (request_log table), ADR-0006 (injected clock), docs/api_notes.md

---

## Context

Hard requirement: count 50/3 min and 1000/60 min simultaneously and, after a 429,
wait the full 180 s **sending nothing** (documentation: the pause is counted from the
last request; sending during it risks "continuous lack of access"). Acceptance
criterion: a single run never triggers 429. Resuming is a flagship feature, yet a
process resumed after a crash does not remember how many requests its predecessor sent.

## Options

**A. Fixed 3.6 s spacing only.**
Three lines; `180/50 = 3600/1000 = 3.6`, so it satisfies both windows arithmetically
and is exactly what the documentation recommends. But 10 wizard requests take 36 s,
it does not protect against a second process on the same token, and it does not
formally "count both windows". Effort S, risk medium.

**B. Two sliding windows, no forced spacing (burst to 50, then throttle).**
Small jobs finish instantly. A burst of 50 lands on the edge of the server window; if
the server uses fixed windows or counts with delay we get a 429 and a 180 s stall.
Asymmetric risk: save seconds, pay minutes. Effort M, risk medium/high.

**C. All of it: windows with headroom + `min_spacing_s` + post-429 cooldown +
request history persisted in SQLite.**
With `min_spacing = 3.75` (corrected 2026-09-08 from 3.6) the spacing binds and the windows are a safety net; the
windows become load-bearing exactly when needed (resume, second process, lowered
spacing). One class, one entry point for every request including retries. More
state; persistence needs a wall clock next to the monotonic one. Effort M, risk low.

## Decision

**C.** Deciding argument: without persistent history, "crash at request 600, user
resumes immediately" gives the new process an empty history and permission to burst
while the server still counts 600 in its 60-minute window. A memoryless limiter is a
design defect when resuming is a core feature.

```python
class Clock(Protocol):
    def monotonic(self) -> float: ...
    def wall(self) -> float: ...          # epoch: persistence and "resuming at HH:MM"
    def sleep(self, seconds: float) -> None: ...

class RequestHistory(Protocol):
    def recent(self, since_epoch: float) -> Sequence[float]: ...
    def record(self, ts_epoch: float, endpoint: str, status: int | None) -> None: ...

class RateLimiter:
    def __init__(self, windows, min_spacing_s, cooldown_s, clock, history) -> None: ...
    def acquire(self, endpoint: str, extra_delay_s: float = 0.0) -> None: ...
    def note_response(self, status: int, retry_after: float | None) -> None: ...
```

Correctness details:

1. `acquire()` is the only gate. Retry after 5xx has no `sleep` of its own; backoff
   is expressed as a requested delay passed into `acquire()`:
   `earliest = max(spacing, windows, blocked_until, backoff)`. A retry after 1 s
   during a cooldown restarts the 180 s.
2. The cooldown is counted from the last attempt, not from "now":
   `blocked_until = ts_last_request + cooldown_s`. With `Retry-After` use
   `max(Retry-After, cooldown_s)`, never less than the documented 180 s.
3. A loop, not a one-shot computation: after `sleep()` the window content changed,
   so `acquire()` recomputes until it can proceed.
4. Monotonic clock in-process, wall clock in the database. On start the window is
   rebuilt: `mono_ts = mono_now - (wall_now - wall_ts)`, negatives clipped to 0,
   timestamps "from the future" (NTP/DST jump) dropped. Covered by a test.
5. History is scoped by `(environment, token_fp)`: the limit belongs to the token,
   not the machine. `token_fp = sha256(token)[:16]`, never the token itself.
6. History is read on every `acquire()` (indexed query, at most 1000 rows, once per
   3.6 s), which makes the limiter correct for two processes sharing one database.
7. The limiter covers everything, `download_report` included. A report ZIP is a request too.

## Consequences

- `RateLimiter` depends on two protocols and nothing from `sqlite3` or `httpx`;
  its tests use `FakeClock` + `InMemoryHistory`, no network, no database.
- The "API limit, resuming at HH:MM" message is built from `clock.wall()` and is
  deterministic in tests.
- Small jobs are deliberately slow (10 requests take about 38 s). **Do not lower
  `min_spacing_s` below 3.75 s.** Correction 2026-09-08: this consequence previously read
  "lowering it is allowed", which is the change measured dangerous on 2026-09-06 — at 3.6 s
  the busiest 180 s window held **49** requests against our own limit of 48 and the API's 50.
  Both windows require 180/48 = 3600/960 = 3.75 s, so a lower value only lets the limiter
  burst and then repay with a longer stop. `apiprofile.py` still accepts `ge=0.0`; the
  guard that actually holds is
  `test_ratelimit.py::test_shipped_profiles_cannot_burst_past_their_window`.
- Revisit: if the probe shows `X-RateLimit-*` headers, synchronise with the server
  instead of estimating.
