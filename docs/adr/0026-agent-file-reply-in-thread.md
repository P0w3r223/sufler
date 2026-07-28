# 0026. Reply in a Teams thread with a rendered file (md/txt/pdf/docx)

Date: 2026-07-17
Status: accepted (implemented 2026-07-27 — A′1 primitive + A′2 consumer)
Author: P0w3r223
Related to: docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md,
  docs/adr/0018-agent-working-directory.md, docs/adr/0016-user-multimodal-attachments.md,
  docs/adr/0022-proactive-dual-target-teams-push.md, docs/adr/0006-write-capability-gate-2.md

---

> **Update 2026-07-27:** Admin consent for `Files.ReadWrite.All` has since been granted — the write
> scope is available. The blocker described below (pending write-scope admin consent / device-code
> re-consent) is **no longer current**; only the build remains (`TeamsFileSender` + render/upload).
> The rest of this decision stands.

## Context

The outbound direction of every door is **text only**. `Responder.respond(...) -> str`; the Teams
channel poller turns that string into an HTML message; the push notifier
(`graph_teams_notifier.py`) sends Markdown→HTML with `allow_links=False`; `reply_on_thread` (ADR 0024)
posts a plain-text GitHub comment. No component can emit a file. Attachments today are strictly
**inbound** (ADR 0016): the agent reads what a user uploads, never the reverse.

Product wants the agent to answer *in the current Teams thread* with a **file** in a user-specified
format (e.g. "give me that summary as a .docx"). This requires a capability that does not exist: an
**outbound Graph attachment** — upload bytes to the channel's SharePoint drive and reference them in a
reply message. ADR 0018 already sketched exactly this (Decision II: `upload_channel_file` +
`post_reply_with_attachment`, gated on `Files.ReadWrite.All`) and deliberately deferred it. This ADR
picks that up and adds the new axis 0018 did not cover: **format rendering**.

Hard constraints: `core ↛ adapters`; the frozen MCP tool surface stays untouched (the new tool rides
`extra_catalog` / a per-turn factory, never `build_tool_catalog`); the write gate is per door (ADR 0006);
tools are synchronous (dispatched in a thread-pool executor), so the file path needs a **sync** Graph
client, mirroring how Gate-4 GitHub write uses a sync client alongside the async poller.

## Options considered

- **B1 (chosen).** A scoped `reply_with_file` tool injected per turn by the same factory that supplies
  `reply_on_thread` (ADR 0024): the thread target is **pre-bound** from `external_id`
  (`team/channel/root`) via `ThreadLinkStore`, so the model supplies only `content` + `format`, never the
  destination. New pieces, all respecting `core ↛ adapters`:
  - a core port `TeamsFileSender` (analogue of `TeamsNotifier`) + a **sync** Graph adapter
    (`upload_channel_file`, `post_reply_with_attachment`);
  - a core port `DocumentRenderer` + adapter — `md`/`txt` are plain bytes (may live in core), `pdf`/`docx`
    are heavy, dependency-bearing renderers isolated in the adapter (an `extra`);
  - `build_file_reply_catalog` in `tools.py` and a factory in `teams_graph/app.py`.
  An upload failure degrades to a text reply (never crashes the poller), like the inbound path.
- **B2.** Widen the shared `Responder` seam so `respond` returns text **plus** attachments, uploaded by
  the poller afterward. This changes the *shared* contract of every door (Telegram/CLI/MCP), gives the
  model a weaker affordance ("how do I signal *attach a file*?"), and blurs the tool-as-side-effect
  boundary the repo keeps consistently. Larger blast radius, worse fit. Rejected.

## Decision

Adopt **B1**, gated behind `enable_file_reply` (default OFF). Rendering: `md`/`txt` pure, `pdf`/`docx`
behind `DocumentRenderer` with a new PDF dependency (e.g. `reportlab`/`fpdf2`) in an `extra`; `docx`
reuses `python-docx` already present for inbound. The MCP golden surface is untouched. This ADR accepts
ADR 0018 Decision II and adds the format-rendering decision on top; it amends ADR 0016 (attachments are
no longer inbound-only).

## Preconditions for enabling (flag OFF by default)

- **New Graph write scope.** Uploading a file to a channel requires `Files.ReadWrite.All` /
  `Sites.ReadWrite.All` — **admin consent + a one-time device-code re-consent** (delete
  `~/.workmate/teams_token_cache.bin` to force it). This is the single biggest blocker and the reason
  0018 deferred it. Inline images do not need it, but a rendered document is a SharePoint file and does.
- **Single writer / idempotency.** A retried turn must not double-post a file; the tool is create-only
  and the reply is one message per successful upload, but the operator runs one door instance.

## Threat model

- **Prompt-injection → exfiltration, now file-shaped.** This is the ADR 0024 thread-write trifecta with
  a *larger* outward payload: the agent holds untrusted input (event/file content), a sensitive read
  (`search_notes` over the private base), and an outward action that can now emit an **entire rendered
  document**, not just a short comment. A crafted prompt could package notes content into a .docx and
  post it to the linked thread.
- **Compensating controls (real, not a boundary):** the thread target is pre-bound (the file lands only
  in the *same* linked thread, never an arbitrary chat), the gate is OFF by default, and content is
  sanitized on the way in. The "content is data" rule is inference-time, not structural.
- **Enabling condition:** turn `enable_file_reply` ON only for a trusted team/channel; prefer to pair it
  with the ADR 0025-style hard confirmation before wide rollout.

## Consequences

- New ports (`TeamsFileSender`, `DocumentRenderer`) + adapters; a new PDF dependency in an `extra`;
  medium-large blast radius (`core/ports/`, `core/application/tools.py`, `adapters/outbound/`,
  `teams_graph/app.py`, `config.py`).
- The delegated token gains write scope — a meaningful escalation of what a compromised cache can do;
  document it in the deploy checklist.
- B and C (ADR 0027) share the `TeamsFileSender` primitive — build it once here, reuse for user push.
- Everything reversible: `enable_file_reply` defaults to today's text-only behavior.

## Implementation (2026-07-27)

- **A′1 — primitive.** Core port `TeamsFileSender` (`core/ports/file_output.py`) + sync Graph adapter
  `HttpxGraphFileSender` (`adapters/outbound/graph_file_sender.py`): `upload_channel_file`
  (GET filesFolder → PUT content) then `post_reply_with_attachment` (reference attachment bound to
  body via `<attachment id="GUID">`). Retry policy mirrors `graph_teams_notifier` (429 always; the
  file-carrying reply never; folder read + idempotent upload yes); 404 → `ThreadRootGone`.
- **A′2 — consumer.** Core port `DocumentRenderer` + `FILE_REPLY_FORMATS` map
  (`core/ports/document.py`) with adapter `DefaultDocumentRenderer`
  (`adapters/outbound/document_renderer.py`): `md`/`txt` are raw UTF-8, `docx` via `python-docx`,
  `pdf` via `fpdf2` with a **bundled Unicode font** (`assets/DejaVuSans.ttf`, DejaVu license) — the
  built-in Helvetica is latin-1 and would crash on Polish glyphs. Core `build_file_reply_catalog`
  (`core/application/tools.py`) exposes the per-turn `reply_with_file(content, file_format, filename)`
  tool: target `team/channel/root` is pre-bound from the thread `external_id` (never from the model),
  format/size/empty guards raise `InvalidRequestError` → `{"error": ...}` (degrade to text), the
  caption HTML is core-built and escaped. Wired in `teams_graph/app.py`
  (`_build_file_reply_factory` + `_compose_thread_factories`, composed with the ADR-0024 GitHub thread
  factory), gated by `enable_file_reply` (OFF) with fail-fast validation that `Files.ReadWrite.All` is
  in scopes and a `max_file_reply_kb` bound. New extra `file-reply` (`fpdf2`). Uploaded filenames are
  content-addressed (`<slug>-<hash8>.<fmt>`) so a retried turn overwrites idempotently while distinct
  content never clobbers another reply in the channel. The DejaVu font is force-included in the wheel
  (`[tool.hatch.build.targets.wheel] artifacts`, verified in `dist/*.whl`). Gate: ruff+mypy clean,
  pytest 1492 (+32); `@code-reviewer` conditional-approve (no CRIT/HIGH), the two MEDIUMs addressed.
  Real upload/tile = operator (device-code re-consent for the write scope).
