# 0071 — Issue closures, and what self-skip was actually skipping

Date: 2026-09-04
Status: accepted
Author: P0w3r223 (owner decision 2026-09-04)
Related to: [ADR 0019](0019-shared-event-store.md) (the append-only store),
[ADR 0020](0020-github-delegated-polling-door.md) (the polling door and self-skip),
[ADR 0021](0021-github-write-capability-gate-4.md) (create-only writes on the same PAT),
[ADR 0024](0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md) (PR/CI ingest),
[ADR 0029](0029-branch-pr-state-transitions-and-project-activity.md) (**the precedent this ADR
follows: transitions as append-only facts**),
[ADR 0040](0040-eventstore-to-mcp-session-cursor-read.md) (the MCP cursor read this touches),
infra `docs/pozostale-do-zrobienia.md` §3.10 (the incident measurement)

---

## Context

On 2026-09-04 a person asked the bot on the Teams channel: *"read me the open issues from
GitHub."* The bot answered with a table of ten issues.

**All ten are closed. The only actually open issue — #96 — could not appear.** Zero correct rows
out of ten, and the one true row structurally invisible.

The bot did not hallucinate. It attached a caveat saying this was a view of the event layer rather
than the state of the repository, and that it had no tool reading GitHub directly — behaving
exactly as the 1.13.0 fix taught it to (list §3.6). The caveat is what kept the answer from being a
lie. It was also **narrower than the truth**: it said "unless an issue was closed outside the
recorded window", when in fact closures are never recorded at all, and the bot's own issues are
never recorded either.

### Mechanism 1 — closures are not a thing

Measured on the running fleet: `select source, kind, count(*) from events group by 1, 2` returns
`('github', 'issue_opened', 10)` and nothing else on the GitHub side. `map_issue`
(`adapters/inbound/github/selection.py`) emits only `issue_opened`; the kind `issue_closed` does
not exist anywhere in the tree, and `notifier._KIND_LABELS` has no entry for it.

**The event layer is a journal of openings. It structurally cannot say what is open.**

The data is already arriving. `HttpxGithubClient.list_issues` hardcodes `"state": "all"`
(`adapters/outbound/github_api.py:72`), so every closed issue comes back in every round — it just
falls into `map_issue`, which reads nothing about state: it takes `created_at` and stamps
`issue_opened` whether the issue is open or not.

### Mechanism 2 — self-skip filters by ACCOUNT, on a premise that is false

Authors of the repository's 18 issues: `milolew` has 10 (#4–#12, #76) and exactly those are in the
store. The poller's own PAT account has 8 (#1, #2, #3, #40, #77, #87, #88, #96) and **none of them
is in the store**. `_from_other_actor` in `select_events` drops them (rule 7 — poller and write
door share one token and one `events.db`, so an echo guard is needed).

The premise underneath is: *our account ⇒ our tool wrote it ⇒ the echo is already in the store
under `source='teams'`.*

**Only #40 and #77 have that echo.** The other six were created with the same account but *outside
the tool* — `gh` CLI and the web UI, from Claude Code sessions. They fall out of both paths and
exist nowhere. The guard filters by **who**, when the thing it actually needs to know is **what our
write door recorded**.

### The sentence that turned a limitation into a false promise

`Activity(events)` (`core/application/tools/activity.py`) says: *"`source` — the DOOR that recorded
the event […]; **ask about GitHub state WITHOUT `source`**."* That sentence entered in 1.13.0 as the
fix for §3.6 (the agent denying its own write because it read `source` as "the system concerned").
It corrected one false belief and **installed another**: that asking without `source` yields the
state of GitHub.

The same false sentence stands a second time, on the **MCP surface**: the `read_events_since`
docstring (`core/application/tools/events.py:44`) says *"ask about GitHub state WITHOUT this
filter"*. That surface is frozen by a golden test, so fixing it costs a deliberate baseline
regeneration.

### What is not wrong

The poller is healthy and current (container `healthy`, watermark advancing). This is an absent
capability, not an outage. And `_build_notifier` is only wired when push is enabled, which infra
`docs/decyzje/0002` keeps off as a product-scope decision — so **the echo guard currently protects
nothing observable, while its one measured effect is removing eight issues from the store.** That
is not a reason to delete it without a replacement; it is the reason the cost of getting the
replacement slightly wrong is low today.

## Options considered

### Recording closures

**A1 — a new `issue_closed` kind, mapped from the same `/issues` payload. CHOSEN.** The precedent
is in this repository and is already an ADR: 0029 records state transitions as append-only facts
with distinct dedup keys, and `map_pull_state` implements exactly that for pull requests —
`pr_merged`/`pr_closed`, a suffixed `external_id`, and an **empty `actor` so the event bypasses
self-skip**, with the reason written into its docstring: a transition is a read-only observation,
not a loop vector. That sentence describes, word for word, what is missing on the issue side. No new
endpoint, no new watch kind, no extra API cost.

**A2 — `issue_reopened`. DEFERRED, and named as a hole.** The `/issues` payload carries no
`reopened_at`. The only timestamp available is `updated_at`, which moves on every label or title
edit. Writing it as `occurred_at` would mean "the reopening happened no later than this" — an upper
bound wearing the costume of a fact, inside a store of facts. **A named hole is cheaper than a
fabricated timestamp.** The correct mechanism is the timeline endpoint `/issues/{n}/events`, where
the `reopened` event carries a real `created_at`; that is a new endpoint, a new port method and a
new watch kind, so it gets its own ADR.

**Rejected:** a dedicated `issue_state` watch kind mirroring `pull_state` — another configuration
knob for data that already arrives free in the same response, and a closure recording that is off by
default is this same defect with an extra step. A mutable state projection or a `repo_state` table —
0029 rejected that as a first step and nothing has changed since.

### Narrowing self-skip

**B1 — self-skip stops meaning "the author is our account" and starts meaning "our WRITE DOOR
recorded this". CHOSEN.** Skip an event if and only if the store holds an echo that only
`GithubWriteService` could have placed: an `issue_opened` for number *n* when
`exists("teams", str(n), "github_issue_created")`; a comment for id *c* when
`exists("teams", str(c), "github_comment_created")`. The keys line up — the echo writes
`external_id=str(result.get("number", ""))` and `str(result.get("id", ""))`
(`core/application/github.py:55` and `:69`), and the mappers use the same values. The `.get`
default matters: an API response missing the field yields `external_id=""`, and the echo lookup then
misses. That is a pre-existing hole in the echo record, not one this change opens, and the write
service must keep a test asserting the key shape — it is now a **contract between two modules**, and
a silent change to it would disable the guard without a single red test. `EventStore.exists`
already exists on the port (`core/ports/events.py:23`); nothing new is needed.

Everything else — `pr_opened`, `pr_review`, `issue_closed` — loses self-skip **with no
replacement**, because the write door is create-only on issues and comments (rule 7, ADR 0021):
there is no path by which we could be the author of a pull request or a review. Filtering those by
login is pure loss, the same loss as on issues.

For `issue_closed` the statement needs care, because it is true by **construction rather than by
nature**: in this repository every closer *is* the PAT account. It never carries that account only
because decision 2 leaves `actor` empty. Read decisions 2 and 6 together — separately, each looks
like it could be revisited on its own, and revisiting either alone reintroduces the interaction.

`selection` is I/O-free and stays that way: the predicate arrives as an injected callable, the way
`diff_branches` already takes its clock from the caller. `self_login` leaves `select_events` together
with `_from_other_actor` — a filter left "just in case" would be a second, quiet path to the same
defect.

**Rejected:** keeping the account filter with a per-kind exception list (that writes the false
premise down again, in smaller print); a second PAT for the poller (breaks rule 7 and needs a
secrets change on the fleet); and clearing `WORKMATE_GITHUB_SELF_LOGIN` — which
**does not do what it looks like it does**: an empty value makes `GithubPoller._resolve_self_login`
(`adapters/inbound/github/poller.py:307-310`) fetch the login from `GET /user` instead, so the
filter stays on with an auto-detected account. That is worth recording precisely, because it is the
first thing someone will reach for as an emergency switch, and it would quietly achieve nothing.
(It is also why the incident happened on a fleet where that variable is not set at all.)

### A live GitHub read tool

**Accepted in principle, deferred to its own ADR.** Worth recording here: the live read promised by
ADR 0029 was never built — `list_pulls`/`list_branches` are called only by the poller, and no tool
builder touches them. That is why the event layer is today the only source of an answer about
GitHub.

Wiring one would be cheap; the cost is elsewhere. A new action means a new paragraph in the
`Activity` description, a new `Literal` value and a new signature field, against a surface byte
budget with roughly 327 B of headroom. It would fit, and it would consume the headroom, and it would
tie an incident fix to a capability expansion inside one review.

The criterion for the follow-up is measurable rather than a matter of taste: **after the backfill,
ask the bot the same question.** If it correctly derives the open issues from `issue_opened` /
`issue_closed` pairs, the live read is not needed. If it does not, that is evidence rather than
intuition.

## Decision

1. **`issue_closed` is mapped from the same `/issues` response**, alongside `issue_opened`, in the
   shape `map_pull_state` established. `notifier._KIND_LABELS` gains a label so the kind never
   renders raw.

2. **`actor` is empty — and this is a choice, not an absence of data.** The list payload's `user`
   is who *opened* the issue, so putting it on a closure would name the author as the closer: a new
   falsehood replacing the old one. `closed_by` **is** available in the list response — measured on
   this repository, all 16 issues on the first page carry the key and all 15 closed ones have it
   filled — so we could name the closer, and we choose not to.

   The reason is decision 6. In this repository **every closer is the PAT account**. An `actor`
   carrying `closed_by` would therefore make every `issue_closed` today look like our own write, and
   the account-based guard would eat all of them — reproducing the incident inside its own fix. After
   decision 6 the guard no longer looks at the account, so the coupling stops being load-bearing;
   it is written down because the two decisions must be read together, and because a later change
   that "just adds the closer" would silently reintroduce the interaction. An empty actor is also
   consistent with `pr_closed` and the CI kinds.

   Naming the closer is a follow-up worth having (it is real information, and it is free), but it
   belongs after decision 6 ships and with its own test for the self-skip interaction.

3. **`external_id` is `{n}#closed@{closed_at}`, not `{n}#closed`.** This is the one place where this
   ADR departs from the `pr_closed` precedent, and the departure is the point. `{n}#closed` encodes
   *"has been closed at some point"* — a **state**, and an append-only store cannot hold state (the
   tension 0029 names in its own Context). After a close → reopen → close cycle, dedup swallows the
   second closure and it can never be recovered. A timestamp in the key makes it a **fact** ("closed
   at T"), costs nothing (the value is already in the payload; no store round-trip), and means
   reopen support can land later **without a migration**. `pr_closed` carries the same latent flaw;
   it is named here as known debt and deliberately not touched, because `events.db` is append-only
   and changing the PR key shape would split that history.

4. **No timestamp fallback.** `map_pull_state` uses `closed_at or updated_at`. For issues that
   fallback is rejected: when `state == "closed"` and `closed_at` is absent, emit nothing and log.
   GitHub always supplies `closed_at` for a closed issue, so the fallback would buy robustness
   against a case that does not occur, at the price of **an invented timestamp in an append-only
   store**.

   This is not an independent stance about truthfulness: it is a **necessary condition for decision
   3**. Without a real `closed_at` there is nothing to build the key `{n}#closed@{closed_at}` from.
   Measured on this repository: all 15 closed issues on the first page carry `closed_at`.

5. **`issue_reopened` is deferred, with a return criterion.** Accepted consequence, stated so nobody
   discovers it later: an issue that was closed and reopened looks closed in the event layer — the
   same class of error as the incident, for one case instead of all of them. We revisit at the first
   real reopening in this repository, via the timeline endpoint, in its own ADR. Decision 3 is what
   makes that revisit cheap.

6. **Self-skip is narrowed to B1**, as described above.

7. **Store dedup does NOT close the echo question, and this must not be assumed.** The uniqueness key
   is `UNIQUE(source, external_id, kind)`. The echo is `('teams', '40', 'github_issue_created')`; the
   poller's event is `('github', '40', 'issue_opened')` — different source, different kind, so the
   database considers them two distinct facts and accepts both. The store guarantees each is written
   once; it has no notion that they describe the same thing. The narrowed guard must therefore be an
   **explicit lookup of the echo key**, never a reliance on `ON CONFLICT`.

   (The same reasoning answers a question worth stating: `issue_opened` and `issue_closed` on the
   same number coexist without difficulty, because `kind` distinguishes the key. The suffix in
   decision 3 is for reopening, not for dedup.)

8. **`recent()` orders by `occurred_at DESC, id DESC`.** Today it orders by `id DESC` — insertion
   order — which approximates event time only because the poller ingests continuously. **The backfill
   breaks that approximation permanently**: July's events would receive the highest ids in the
   database, and "newest first" would become a second false sentence in the same tool description, in
   the very change that removes the first one.

   **Four consumers, not one.** `recent()` feeds `Activity(events)` and `Activity(summary)`
   (`tools/activity.py:135`, `:164`), the MCP cursor read (`tools/events.py:58`), `_activity_facts`
   behind `Project(status)` (`core/application/services.py:267`) and the change digest
   (`change_digest.py:42`). Two of them record the id-ordering assumption in prose that this change
   falsifies — and `change_digest`'s own docstring says it outright: *"a large backfill of old events
   would weaken this assumption, but the pipeline does not do one."* **Decision 9 is that backfill.**

   `_activity_facts` deserves naming separately, because it is the one place where the backfill would
   reproduce the incident in a different tool. It already takes `max(occurred_at)` rather than the
   first element, so ordering *within* the window is handled — but the **window itself** is chosen as
   the top 100 by id. Once ids no longer track time, a store larger than the window can push genuinely
   recent events out of it, and `Project(status)` starts reporting a "last activity" from July. That
   is the incident's shape, in another tool. Decision 8 is therefore a **precondition for decision 9**,
   not an independent tidy-up, and the four prose comments must be corrected in the same step.

   **The MCP cursor is where this gets subtle, and my first draft got it wrong.** ADR 0040 fixes the
   contract as *"both modes return events ascending-by-id and a `latest_cursor` (max id seen)"*, and
   that contract is frozen three times over: in the `read_events_since` docstring (*"always ascending
   by id"*), in `tests/adapters/tool_surface_baseline.json`, and in an explicit
   `assert ids == sorted(ids)` in `tests/core/test_events_since_tool.py`. Changing `recent()` and
   regenerating the baseline would **freeze a newly-false sentence in the very change whose thesis is
   removing a false sentence from that surface** — the same figure this decision holds against
   "newest first".

   So the contract is kept, not amended: the bootstrap sorts its window by id before returning
   (`sorted(..., key=lambda e: e.id)`), which restores ascending-by-id and makes `items[-1].id` the
   window maximum again.

   That still leaves one thing the code stage must resolve rather than assume. With a **time**-ordered
   window, the window's maximum id is no longer the store's high-water mark: backfilled events carry
   the highest ids and the oldest timestamps, so they sit outside the window while ranking above its
   cursor — and the next poll would deliver the whole backfilled batch as "new". The bootstrap cursor
   must therefore be the store's current maximum id, which no port method exposes today
   (`core/ports/events.py` offers `exists`, `append`, `read_since`, `recent`). Adding it is small; not
   noticing it would turn one backfill into a replay of July into every MCP session.

9. **The historical gap is backfilled**, by resetting the poller's issue watermark — no new code.
   `_parse_since("")` returns `None` (`poller.py:331-334`), which drops the `since` parameter, and
   `state=all` is hardcoded, so one round returns the whole history. The new mapper emits openings
   and closures for all 18, the narrowed guard admits the eight, store dedup turns the existing ten
   into no-ops, and `next_since` rebuilds the watermark by itself. Nothing bypasses sanitisation or
   dedup, and no timestamp is invented.

   This is not a hole in history that could be named and left. **It is a hole that makes today's
   answers false**: without it the bot answers tomorrow exactly as badly as it did during the
   incident, with corrected code.

   **What the backfill does not recover, stated rather than discovered:** past comments
   (`comments_since` is left alone — comments are conversation, not state, they are an order of
   magnitude more numerous, and their absence produces no false claims about the repository); who
   closed each issue (by the choice in decision 2, not for want of data — `closed_by` is in the
   payload); and `runs_since` / `reviews_since`, which are **client-side exclusive watermarks** rather
   than server-side filters — resetting them would flood the store with all CI history.

   Two operational traps belong in the runbook, not in a commit message. The watermark must be set
   to the **empty string, not deleted**: a deleted key falls into `_seed`'s `setdefault`
   (`poller.py:325`) and is seeded to "now", so the backfill does nothing while the operator sees a
   green round and concludes it worked. And `WORKMATE_GITHUB_WATCH_KINDS` should be checked first —
   if `pulls` is enabled on the fleet, the same round also emits the historical `pr_opened` events.

10. **The false sentence is removed from BOTH surfaces, and the truth moves to the envelope.** The
    sentence cannot be softened in place, because it promises a capability the layer does not have
    and will not have after this change. The description says what the layer *is* (a history of what
    the bridge recorded, not the state of GitHub); the detail rides in the `note` field, per ADR 0068
    §5, where there is no byte ceiling.

    The existing note fires on a filter **or** on hitting the window cap (`tools/activity.py:146-151`).
    During the incident it stayed silent only because ten events did not reach the default limit of 20
    — **after the backfill the same unfiltered question will hit the cap and the window note will fire
    every time**, so the unconditional note must compose with it rather than replace it. `summary` has
    a different problem, not the same one: `_summary` returns no `note` field at all, ever, and always
    has a `project` filter because the field is required. Both need the note; only one of them needs
    the composition. The note carries no date: a date would go stale at the first backfill.

    The MCP docstring is fixed in the same move, which requires a deliberate regeneration of
    `tests/adapters/tool_surface_baseline.json`. Fixing one door of two would leave the false
    sentence exactly where the state of the repository is asked about most often.

11. **The `notify_cursor` landmine is disarmed as part of the backfill.** The notifier has never
    run, so the cursor is `int(state.get("notify_cursor", 0))` (`github/app.py:252`) — meaning the
    day someone enables push, the whole of `events.db` is delivered to Teams. This is pre-existing,
    but the backfill enlarges it. After the backfill round the cursor is set to `MAX(id)`.

    Its twin, `ci_autocomment_cursor` (`github/app.py:285`), has the same shape and is deliberately
    **not** touched: the backfill returns no `ci_failure` events (`runs_since` is left alone and
    `/issues` does not produce them), so it is not enlarged. Said here so the omission reads as a
    decision rather than an oversight.

## Consequences

### What this buys, stated precisely

**After this change the event layer still does not ANSWER "which issues are open". It allows that to
be INFERRED**, from `issue_opened` / `issue_closed` pairs by number, over a window wide enough to
contain both. Those are two different things, and the difference is exactly what produced the
incident. Anyone reading this ADR as "we fixed open-issue queries" has read it wrong — that is what
the deferred live-read ADR would be for, and decision 9's measurement is what decides whether it is
needed.

Inference also requires a wide enough `limit`; the default of 20 is not it. That is what the
unconditional note in decision 10 has to say.

### Query plan

The two indexes on the events table are `(source, id)` and `(project, id)`
(`adapters/outbound/sqlite_events.py:46-51`), which serve `ORDER BY id DESC LIMIT n` directly.
`ORDER BY occurred_at DESC, id DESC` will not use them: every `Activity(events)`, `summary` and
digest becomes a scan plus a sort of the filtered set (the digest scans 500). At the current size —
tens of rows — this is irrelevant, and the store is append-only with one writer, so it will stay
small for a long time. Recorded so that the day it stops being irrelevant, the cause is already
written down rather than rediscovered.

### Byte budget

Removing the false sentence makes the description **shorter**. The measured surface has roughly
327 B of headroom against the 8000 B ceiling, and the honest paragraph is smaller than the
misleading one — so this repair increases the budget rather than spending it. The number must be
recomputed with `tests/core/test_tool_descriptions.py` rather than copied from here, because every
release moves it.

### Ordering, deployment, and why the backfill goes last

The backfill must flow through the **installed** poller. Run against the old image it would ingest
openings only and freeze the gap into an append-only store, where it cannot be corrected. Sequence:
ADR → code → release → deploy → backfill. The release is MINOR: a new event kind, changed ordering
and a changed description are visible behaviour, but no contract breaks.

### Risk accepted

Narrowing self-skip adds no outgoing edge. The only autonomous write path is `CiAutoCommentService`,
whose `_TRIGGER_KIND` is `ci_failure` (`core/application/ci_autocomment.py:40`) — CI events carry an
empty actor and never passed through self-skip anyway. Every other write requires an explicit human
request. The worst reachable outcome is therefore a single redundant Teams notification if an echo
failed to record, and not even that today, because the notifier does not run.

**One implementation constraint that belongs here rather than in a commit.** `selection` is
I/O-free and stays so, but the injected predicate is not: it reaches SQLite. `poll_once` calls
`select_events` **directly on the asyncio loop** (`adapters/inbound/github/poller.py:190`), unlike
the ingest immediately below it, which is offloaded to a thread pool with the comment that the loop
must not block. `SqliteEventStore.exists` takes a lock shared with the notifier and the write door.
A backfill round would put ~18 such queries on the loop thread — more if `pulls` is watched. Either
offload `select_events`, or resolve the echo keys in one batched query inside the pool and let the
predicate be a pure set membership test.

One consequence for prose, and it is larger than one line. The `selection` docstring calls this a
"SELF-PING loop guard"; it never was a loop guard, it is a duplicate-notification damper, and it
should say so. But the account-based description of the guard is written down in at least six more
places, and after decision 6 two of them become **false rather than merely stale**:
`docs/explanation/architecture.md:113` and `docs/how-to/github-bridge.md:35`. The others —
`core/application/ci_autocomment.py:16` (which explains the only autonomous write path in terms of
"author = the PAT account"), `core/ports/github.py:30`, `core/domain/events.py:26` and
`docs/how-to/gate-matrix.md:76-79` — describe a mechanism that will no longer exist. They travel in
the same change as the code; a decision that corrects one false sentence while leaving six behind
would be the same failure it is written to fix.
