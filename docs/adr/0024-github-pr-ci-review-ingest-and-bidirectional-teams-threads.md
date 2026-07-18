# 0024. GitHub PR/CI/review ingest, deterministic CI auto-comment, and bidirectional Teams channel threads

Date: 2026-07-16
Status: accepted
Author: P0w3r223
Related to: docs/adr/0019-shared-event-store.md, docs/adr/0020-github-delegated-polling-door.md,
  docs/adr/0021-github-write-capability-gate-4.md, docs/adr/0022-proactive-dual-target-teams-push.md,
  docs/adr/0006-write-capability-gate-2.md, docs/adr/0008-agent-runtime-and-tool-catalog.md

---

## Context

The three-way bridge (GitHub ↔ shared EventStore ↔ Teams) currently ingests only
`issue_opened` / `issue_comment` (pull requests are actively dropped), pushes each event as a
**new** channel root, and writes back to GitHub only reactively from a 1:1 Teams chat. We want:

1. Richer GitHub signals: PR opened, PR vs issue comments split, PR reviews (approved / changes
   requested), and CI results (workflow run success / failure).
2. One **autonomous-but-safe** action: on a CI failure attached to a PR, post a *deterministic,
   non-LLM* comment on that PR.
3. Substantive (LLM-authored) replies that publish to GitHub only after an explicit human "yes".
4. True two-way threading on a Teams channel: an event opens a thread, replies in that thread
   route to the right issue/PR, and GitHub comments append to the *same* thread.

Hard constraints that shape every option: `core ↛ adapters`; mutating writes stay gated per door
(ADR 0006 / 0021); secrets (GitHub PAT, MSAL cache, Anthropic key) never enter an event payload,
the EventStore, Teams, or a GitHub comment; GitHub/Teams content is data, not instructions; the
frozen MCP tool surface (`tests/adapters/test_mcp_tool_surface.py`) stays untouched; reliability in
every direction rests on append-only + dedup `(source, external_id, kind)`, watermark-after-ingest
(at-least-once), a cursor advanced only after a successful send, and two-sided loop guards
(PAT self-skip; `source="teams"` echo).

## Options considered

### A — Ingest & mapping
- **A1 (chosen).** Reuse `/issues` (PRs arrive there with a `pull_request` marker) and
  `/issues/comments` (split by `html_url` containing `/pull/`); add `/pulls/{n}/reviews`
  (per active PR) and `/actions/runs` (whitelisted fields only). Near-flat API cost for
  `pr_opened` / `pr_comment` (no extra requests, no new watermark).
- **A2.** A dedicated `/pulls?sort=updated` listing — but `/pulls` has no `since`, forcing
  client-side windowing *and* a separate `/issues` call for issues. More requests, more state,
  redundant. Rejected.

### B — CI auto-comment (deferred to phase 2)
- **B1 (chosen).** A dedicated core `CiAutoCommentService` consuming the EventStore, idempotent
  via a claim marker `(teams, run_id#attempt, ci_autocomment)`, reusing `create_comment`, gated
  OFF by its own flag.
- **B2.** Inline in the poller — couples "observe" with "act" and injects a write port into a
  read-only ingest loop. Rejected.

### C — Bidirectional threads (deferred to phase 3)
- **C-store-1 (chosen).** A new `ThreadLinkStore` port + SQLite adapter over the *same* events.db
  (new `thread_links` table); the EventStore and the immutable event model stay frozen; a pure
  `event_target(kind, external_id, url)` resolver derives the thread key from the url.
  **C-route-1 (chosen):** thread replies are agent-mediated and gated by confirmation
  (ADR 0006), reusing `comment_github_issue`; the mapping supplies the issue/PR number.
- **C-store-2** (JSON state, two writers → corruption) and **C-route-2** (deterministic
  auto-publish of every thread reply — breaks the confirmation invariant and leaks chat to
  GitHub). Rejected.

### D — CI non-disclosure
A **whitelist mapper**: `map_ci_run` reads only workflow `name`, `conclusion`, run `html_url`,
`pull_requests[].number`, `repository.html_url`, `head_branch` — never `logs_url`, `jobs`,
`head_commit`, or env; the logs endpoint is never called. Defense in depth via
`reject_dangerous_content` on ingest and escaped `to_teams_html(html=False)` on render.

## Decision

Adopt **A1 + B1 + C-store-1 / C-route-1 + D**, rolled out in three independently reversible
phases (a phase's new behavior is off by default until validated):

- **New event kinds** enter only through pure mappers and notifier labels: `pr_opened`,
  `pr_comment`, `pr_review`, `ci_success`, `ci_failure`. `_ALLOWED_GITHUB_WATCH_KINDS` grows to
  `issues, comments, pulls, reviews, ci`; the **default** stays `issues, comments` (backward
  compatible — new kinds are opt-in via `WORKMATE_GITHUB_WATCH_KINDS`).
- **CI de-duplication** keys on `run_id#run_attempt` (a re-run shares `run_id`; keying on the id
  alone would swallow the second failure). CI events carry no human `actor` (no loop vector) and,
  when a PR is attached, canonicalize `url` to the PR page with the run link kept in `summary`.
- **CI auto-comment** (phase 2) and **channel threading** (phase 3) are independent config flags,
  both default OFF; neither adds a new GitHub write path — confirmed writes reuse Gate 4.
- The **MCP golden surface is untouched**: no new agent tool; everything rides `extra_catalog`
  and EventStore consumers, never `build_tool_catalog`.

## Preconditions for enabling the CI auto-comment (phase 2, flag OFF by default)

Because this is the one autonomous (no human confirmation) write, enabling
`enable_ci_auto_comment` on a repo requires verifying two properties first — both documented so the
operator checks them, since neither can be enforced structurally from our side:

- **No comment-triggered CI.** If the repo runs a workflow on `issue_comment` /
  `pull_request_review_comment` that produces a PR-associated failing run, the auto-comment could
  trigger a run whose failure triggers another comment — an unbounded loop (a re-triggered run has a
  new `run_id`, so the `run_id#attempt` marker does not dedup it). In practice a comment-triggered
  workflow runs on the default branch with an empty `pull_requests`, so its `ci_failure` event has
  no `/pull/` url and is skipped — but confirm the repo has no such PR-associated comment workflow
  before enabling.
- **Trusted workflow names.** The comment renders Markdown and embeds the workflow `name`. A repo
  using a dynamic `run-name:` interpolating external input (e.g. a fork PR title) could place
  Markdown (a link) into an autonomously published public comment. Low risk on an internal repo with
  trusted contributors; review before enabling if dynamic `run-name` with untrusted input is used.
- **Single writer.** Idempotency (one comment per run×attempt) rests on a single `workmate-github`
  process being the only writer of `ci_autocomment` markers (the `exists`→`append` pre-check has a
  race window). Run one instance; a multi-writer setup needs an insert-reporting store method first.

## Threat model for the thread→GitHub write path (phase 3b)

The soft-confirmation model (chosen: the agent posts only when the user explicitly asks, enforced by
the tool description, not a hard gate) leaves one risk that must be named, because it is **not
structurally enforced**:

- **Prompt-injection → exfiltration (the "lethal trifecta").** On the `teams_graph` door with the
  GitHub write gate ON, the agent simultaneously holds untrusted input (GitHub-authored event
  `title`/`summary` via `read_recent_events`), a sensitive read (`search_notes` over the private
  notes base), and an outward action (`reply_on_thread` / `comment_github_issue` → a public GitHub
  comment). A crafted event summary could, in principle, induce the agent to post notes content to
  the linked issue. `reject_dangerous_content` strips control chars only; the "content is data, not
  instructions" rule lives in the system prompt (inference-time, bypassable), **not** in an
  architectural boundary.
- **Compensating controls (real, but not a boundary):** `reply_on_thread` pre-binds the target
  number (exfil destination is limited to the *same* linked issue/PR, never an arbitrary one),
  writes are create-only and length-bounded, and — decisively for the pilot — the write gate
  (`WORKMATE_GITHUB_ENABLE_WRITE`) is **OFF by default**, so these tools do not materialize at all.
- **Enabling condition (operator decision):** turning the write gate ON on this door accepts the
  above residual risk. Do it only for a trusted repo/team; to remove the risk structurally later,
  anchor the confirmation to a verified user turn (a hard draft→"yes" gate) or keep outward writes
  behind the human-gated 1:1 path — both out of scope for the soft model chosen here.
- **Identity invariant (loop safety, unenforced):** the proactive push identity must equal this
  door's reactive `me_id`, otherwise the notifier's own channel posts look like human messages to
  the reactive poller. Satisfied in the pilot because both run as the same delegated account sharing
  one token cache; not asserted in code.

## Consequences

- events.db gains a sibling `thread_links` table (new port/adapter); the event model is unchanged.
- The CI auto-comment cursor is persisted by the adapter on the event-loop thread (after the
  offloaded `process_once` returns), never from the worker thread — otherwise it would race the
  poller/notifier writing the same JSON state file. Phase 3 moves consumer cursors toward SQLite.
- The `workmate-github` process may run up to three concurrent consumers over one EventStore
  (poller, notifier, CI auto-commenter).
- **New invariant:** the proactive push identity must equal the reactive poller identity (a single
  Teams `me_id`) — otherwise channel threading loops (the notifier's own post/reply would look like
  a human message to the reactive poller).
- Reviews are polled per active PR (no `since`), so API cost grows with the open-PR count; bounded
  by a per-round cap and a `reviews_since` watermark. Revisit if open-PR / CI volume threatens the
  5000 req/h budget, or if a multi-process GitHub door is introduced (the auto-comment claim then
  needs an insert-reporting store method for a race-free guarantee).
- Nothing reversible is given up: `watch_kinds`, `enable_ci_auto_comment`,
  `enable_channel_threading` all default to today's behavior.
- **One-time upgrade effect (accepted):** comments on PRs previously ingested as `issue_comment`
  are now classified as `pr_comment` even under the default `watch_kinds`. Because the dedup key
  includes `kind`, a PR comment newer than the stored `comments_since` watermark at upgrade time
  is ingested once more and pushed to Teams a second time. The window is self-limiting (only PR
  comments newer than the watermark) and the new label is the desired behavior — so the "existing
  deployments don't change behavior" guarantee holds for *which resources are polled*, not for the
  PR-comment `kind`.
- **CI / review reliability rests on the watermark, not dedup.** Their endpoints have no `since`,
  so `_after_watermark` is a client-side *excluding* filter: anything it drops never reaches the
  store and cannot be recovered by dedup (unlike the inclusive server-side `since` of
  issues/comments). Combined with single-page `/actions/runs` and a per-round review cap, coverage
  is bounded by volume — revisit (server-side `created=>` window, pagination, per-PR review
  cursors) if CI / open-PR volume grows.
