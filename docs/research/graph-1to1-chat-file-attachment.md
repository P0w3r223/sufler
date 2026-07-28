# Sending a real file attachment into a 1:1 (oneOnOne) Teams chat via Microsoft Graph

Date: 2026-07-27
Status: accepted
Author: P0w3r223
Related to: docs/adr/0027-agent-outbound-file-push-to-user.md (file variant, A′3),
  docs/adr/0026-agent-file-reply-in-thread.md, docs/adr/0038-selfservice-on-demand-worklog-via-teams-dm.md

---

## Question

What is the exact Graph v1.0 REST sequence for a delegated user/bot to post a **downloadable
file card** (e.g. `.xlsx`/`.pdf`, not an inline image) into a `oneOnOne` chat — upload location,
sharing, message body, scopes, and gotchas? Needed to implement the WorkMate `UserDocSender`
adapter (ADR 0027 file variant).

## Sequence (4 calls)

A 1:1 chat has **no SharePoint site / channel drive**, so the file must live in the **sender's
OneDrive** (`/me/drive`) — exactly what the Teams client does (files land in the sender's
"Microsoft Teams Chat Files" folder, and Teams auto-creates the recipient's sharing permission).
Via Graph you replicate **both** the upload and the sharing step yourself.

1. **Upload** to the sender's OneDrive (single PUT, up to **250 MB** on v1.0 — the old 4 MB figure
   is stale): `PUT /me/drive/root:/{path}:/content` with the raw bytes and the file's real MIME
   type. Path-addressed PUT is an idempotent upsert (same item id, **new eTag** each time). Returns
   the driveItem (`id`, `name`, `webUrl`, `eTag`).
2. **Share** so the recipient can open it — **REQUIRED**; skipping it is the single biggest trap
   (the recipient gets a card they can't open, `FileOpenUserUnauthorized`). A file in the sender's
   OneDrive is not accessible to the other chat member by default (unlike a channel file, which
   inherits the library's membership). Two options:
   - Per-person (chosen — tightest, no email needed): `POST /me/drive/items/{item-id}/invite`
     with `{"recipients":[{"objectId":"<aad-user-id>"}], "roles":["read"], "requireSignIn":true,
     "sendInvitation":false}`. `driveRecipient.objectId` accepts the AAD user id we already hold
     (the bound sender), so no email lookup. Idempotent (re-granting is harmless).
   - Org-wide link: `POST /me/drive/items/{item-id}/createLink` `{"type":"view","scope":
     "organization"}`. Simpler but broader; `invite` is more deterministic for the Teams card.
3. **(eTag/webUrl)** come back from the PUT in step 1 — no extra GET needed (re-read only if you
   cached a stale eTag).
4. **Post the chat message**: `POST /chats/{chat-id}/messages`
   ```json
   {
     "body": {"contentType": "html", "content": "<attachment id=\"<GUID>\"></attachment>…"},
     "attachments": [{"id":"<GUID>","contentType":"reference","contentUrl":"<webUrl>","name":"…"}]
   }
   ```
   Linkage: `attachments[].id` MUST equal the `<attachment id="…">` tag in the body (that tag
   renders the card); the documented convention is `id` = the GUID inside the driveItem `eTag`,
   `contentUrl` = the driveItem URL. Creating the chat if absent is `POST /chats` (`oneOnOne`,
   idempotent — returns the existing chat).

## Scopes (delegated, end-to-end)

`Files.ReadWrite.All` (upload + share) + `Chat.Create` (create the 1:1 if absent) +
`ChatMessage.Send` (post). All already admin-consented for this app (Files.* since 2026-07-27;
chat scopes via `Powiadomienia_teams`) — missing only on the channel-poller token, so enabling the
gate needs them added to `WORKMATE_TEAMS_GRAPH_SCOPES` + a device-code re-consent (no new admin
consent).

## Gotchas / decisions taken

- **Recipient access is not automatic** → always `invite` (step 2). This is the key difference from
  the existing channel flow (`HttpxGraphFileSender`).
- **`contentUrl`: `webUrl` vs `webDavUrl` is genuinely ambiguous** in the docs (the file-attachment
  examples target channels, not chats). Community working examples use `webUrl` (what Teams itself
  embeds); we use `webUrl` first, consistent with `HttpxGraphFileSender`. If a card fails to render
  in the recipient's client, `webDavUrl` is the fallback to A/B test — flagged for live-smoke.
- **Attachment id** = eTag GUID (documented, safer); functionally any GUID matching the body tag
  works, but the eTag ties the card to the real driveItem.
- **Idempotency / retry:** upload (PUT by path), `invite`, and `create_or_get_chat` are safe to
  retry on 429/5xx; the **message POST is NOT** (a retry after an ambiguous timeout = a duplicate
  file card) — same discipline as `HttpxGraphFileSender`/`HttpxGraphUserImagePush`.
- **No v1.0 chat-specific file-attachment sample exists** — the reference-attachment structure is
  identical for chats and channels (same `chatMessage`/`chatMessageAttachment` resources) and is
  community-confirmed for chats, but this is extrapolated from channel docs. Flagged for live-smoke.

## Sources

- Send chatMessage (v1.0): https://learn.microsoft.com/en-us/graph/api/chatmessage-post
- Send message in a chat (v1.0): https://learn.microsoft.com/en-us/graph/api/chat-post-messages
- chatMessageAttachment resource: https://learn.microsoft.com/en-us/graph/api/resources/chatmessageattachment
- Upload small files (PUT content): https://learn.microsoft.com/en-us/graph/api/driveitem-put-content
- Share a file (createLink): https://learn.microsoft.com/en-us/graph/api/driveitem-createlink
- Invite people to a driveItem: https://learn.microsoft.com/en-us/graph/api/driveitem-invite
- Community Q&A (app → Teams file attachment): https://learn.microsoft.com/en-us/answers/questions/1328425
