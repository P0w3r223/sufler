# 0027. Agent sends images/files to a user on Teams (outbound attachments)

Date: 2026-07-17
Status: accepted (images + file variants delivered 2026-07-27)
Author: P0w3r223
Related to: docs/adr/0026-agent-file-reply-in-thread.md, docs/adr/0016-user-multimodal-attachments.md,
  docs/adr/0022-proactive-dual-target-teams-push.md, docs/adr/0015-teams-delegated-graph-polling.md,
  docs/adr/0006-write-capability-gate-2.md

---

> **Update 2026-07-27:** Admin consent for `Files.ReadWrite.All` has since been granted — the write
> scope is available. The file variant is **no longer scope-blocked** (only `TeamsFileSender` + the
> outbound-attachment build remains); the images-only variant was already deliverable. The rest of
> this decision stands.
>
> **Delivery note (A′3, 2026-07-27):** the **images-only** variant is **built** — tool
> `send_image_to_user` (`core/application/tools.py`), gated behind `enable_user_file_push` (default
> OFF). Two corrections to this ADR surfaced during the build:
>
> 1. **"No new scope" was only half true.** The hostedContents *mechanism* needs no `Files.*` scope,
>    but **1:1 chat delivery** requires chat scopes (`Chat.Create`, `ChatMessage.Send`) that the
>    channel-poller token does **not** carry (it holds only `ChannelMessage.Send`). Those scopes are
>    **already admin-consented** (used by `Powiadomienia_teams`/TeamsPush) and share the MSAL cache,
>    so enabling the gate needs **no new admin consent** — only adding them to
>    `SUFLER_TEAMS_GRAPH_SCOPES` + a device-code re-consent. `config.validate` now fails fast on the
>    gate without those scopes (mirrors `enable_file_reply` → `Files.ReadWrite.All`).
> 2. **Sync port, not an async-notifier method.** This ADR said the async `TeamsNotifier` would gain
>    an attachment method, but agent tools dispatch **synchronously** (thread pool). Consistent with
>    ADR 0026's `TeamsFileSender`, the 1:1 image push is a **sync** port `UserImageSender`
>    (`core/ports/user_push.py`) + sync adapter `HttpxGraphUserImagePush`
>    (`adapters/outbound/graph_user_push.py`) — not a method on `graph_teams_notifier` (async).
>
> **Delivery note (file variant, 2026-07-27):** the **file** variant is now **built** — tool
> `send_document_to_user` (`core/application/tools.py`), gated behind a **separate**
> `enable_user_doc_push` (default OFF). Design points that refined this ADR during the build:
>
> 1. **Not `TeamsFileSender`, a new sync port.** This ADR said the file variant would "reuse
>    `TeamsFileSender`", but that primitive uploads to a **channel's** SharePoint drive
>    (`GET …/filesFolder` → `PUT`) — a 1:1 chat has **no channel drive**, so the file must live in
>    the sender's **OneDrive**. The mechanism is therefore a distinct sync port `UserDocSender`
>    (`core/ports/user_doc_push.py`) + adapter `HttpxGraphUserDocPush`
>    (`adapters/outbound/graph_user_doc_push.py`): `PUT /me/drive/root:/…:/content` →
>    `POST /me/drive/items/{id}/invite` (grant the recipient read — **required**, else the card is
>    unopenable) → `POST /chats` → `POST /chats/{id}/messages` with a `reference` attachment. See
>    `docs/research/graph-1to1-chat-file-attachment.md`.
> 2. **A separate gate, not the image gate.** Images ride hostedContents inline and need **no**
>    `Files.*` scope; the file variant needs `Files.ReadWrite.All` **on top of** the chat scopes.
>    Sharing one gate would force the broad write scope on image-only users, so the file variant has
>    its own `enable_user_doc_push` (chat scopes + `Files.ReadWrite.All`, `config.validate`
>    fail-fast). Both push factories are keyed by `sender_id` and composed into one per-turn factory.
> 3. **Rendering reuses ADR 0026.** The tool renders content → bytes via the existing
>    `DocumentRenderer` (`FILE_REPLY_FORMATS`: md/txt/pdf/docx), so `send_document_to_user` is the
>    1:1-delivery mirror of `reply_with_file` (extra `file-reply` for pdf/docx).
>
> Live-smoke caveats (research-doc): `webUrl` vs `webDavUrl` as `contentUrl`, and whether an
> org-link vs per-user `invite` reliably makes the Teams card openable — to verify on first real run.
> Both `send_image_to_user` and `send_document_to_user` are delivered; nothing outbound remains here.
>
> **Accepted-for-pilot / follow-ups (from code review):**
> - **Least-privilege scope.** The doc upload/`invite` touch only the bot's **own** OneDrive, so
>   delegated `Files.ReadWrite` suffices; validation accepts **either** `Files.ReadWrite` or the
>   broader `Files.ReadWrite.All` (the latter is already consented for ADR 0026's channel upload). The
>   narrow scope's sufficiency for `invite` is a live-smoke confirmation item.
> - **Artifact retention.** Pushed docs go to a dedicated OneDrive subfolder (`Sufler-push/`, not
>   the drive root), created idempotently. **TTL cleanup is deferred to the pilot** — rendered files
>   linger with a standing per-user `read` grant (info-disclosure boundary unchanged: only the bound
>   sender can open them). A hard message-POST failure after the `invite` leaves an orphaned grant;
>   the same future cleanup covers it. Accepted for the OFF-by-default pilot.
> - **Retry-loop duplication.** `_request`/`_retry_after`/`_member`/retry constants are near-identical
>   across `graph_user_doc_push`, `graph_user_push`, and `graph_file_sender`, and have begun to
>   diverge. Extracting a shared `_graph_request` helper is a **tracked follow-up** (deferred here to
>   avoid destabilizing the already-delivered image/channel adapters in this change).

## Context

Inbound multimodal attachments exist (ADR 0016): a user uploads an image or document and the agent reads
it. The mirror image does not exist — the agent cannot **send** an image or file to a user. Product wants
this: the agent produces a chart/screenshot/rendered document and delivers it to the person it is talking
to on Teams.

This shares its core with ADR 0026: both need the **outbound Graph attachment** primitive (upload bytes +
reference them in a message). They differ only in the destination and what wraps the primitive:

| | Destination | Target source | Extra over the primitive |
|---|---|---|---|
| 0026 (file reply) | the current **thread** | `external_id` (trusted, already in the seam) | format rendering |
| 0027 (file push) | the current message's **sender** (1:1) | `sender_id` — present on the message but **dropped** in the seam | anti-exfiltration target binding |

`ChannelMessage.sender_id` carries the AAD id of the sender (`selection.py`), but `handler.py` forwards
only `sender_name` into `InboundMessage`, so the id is lost before it reaches the runtime. Delivering to a
person is a *mutating outward action to an individual*, so target selection is the security core.

Hard constraints: `core ↛ adapters`; frozen MCP surface untouched (rides `extra_catalog` / a per-turn
factory); per-door write gate (ADR 0006); sync tool dispatch → sync Graph client.

## Options considered

- **C1 (chosen).** A scoped `send_file_to_user` tool, **pre-bound to the sender of the current message**.
  Propagate `sender_id` through the seam (additive: `InboundMessage.sender_id`, filled in `handler.py`
  from `ChannelMessage.sender_id`); the per-turn factory binds `target = sender`, so the model never
  supplies an AAD id — it cannot spam or exfiltrate to an arbitrary person (the same discipline as the
  pre-bound issue number in `reply_on_thread`). Split delivery: **images → hostedContents inline** (no new
  scope), **files → OneDrive/SharePoint** (write scope, shared with ADR 0026), reusing `TeamsFileSender`.
- **C2.** A static allowlist of recipients in config; the model picks a recipient from it. More flexible
  (send to someone other than the sender) but a larger abuse/config surface. Deferred to a future
  multi-user iteration; for the pilot, binding to the sender is safer.

## Decision

Adopt **C1**, gated behind `enable_user_file_push` (default OFF). Deliver the **images-only** variant
first — it rides hostedContents inline and needs **no new Graph scope**, so it is a fast, low-risk pilot of
"the agent sends back an image". The **file** variant depends on the `Files.ReadWrite.All` write scope
introduced by ADR 0026 (admin consent + device-code re-consent) and lands with it. The MCP golden surface
is untouched. This ADR amends ADR 0022 (the proactive/outbound Teams surface now includes attachments to a
user).

## Threat model

- **Outward push to a person.** The risk is spam / exfiltration to an individual. Binding the target to
  the *current sender* (model cannot name a recipient) is the primary control — the destination is always
  the person who just messaged the agent, never an arbitrary AAD id.
- **Compensating controls (real, not a boundary):** gate OFF by default; images-only variant carries no
  new scope; the file variant inherits ADR 0026's pre-bound, create-only, length-bounded discipline.
- **Enabling condition:** turn `enable_user_file_push` ON only for a trusted team; ship images-only first,
  add files after the write-scope consent and validation.

## Consequences

- `sender_id` propagation is additive and backward-compatible (`selection.py` already parses it →
  `handler.py` → `InboundMessage`); nothing that ignores the new field breaks.
- ~~`core/ports/notifications.py` gains an attachment method; `graph_teams_notifier.py` gains the 1:1
  file/image send~~ — **superseded by the delivery note above:** the images-only build added a **sync**
  port `core/ports/user_push.py` (`UserImageSender`) + adapter `adapters/outbound/graph_user_push.py`
  (`HttpxGraphUserImagePush`), because agent tools dispatch synchronously (the async notifier would
  not fit). `config.py` gains the `enable_user_file_push` gate. The **file** variant will reuse
  `TeamsFileSender` from ADR 0026 — no second outbound-attachment primitive.
- Medium blast radius; images-only variant is deliverable without the write-scope blocker, so it can ship
  ahead of ADR 0026's file path.
- Reversible: `enable_user_file_push` defaults to today's no-outbound-attachment behavior.
