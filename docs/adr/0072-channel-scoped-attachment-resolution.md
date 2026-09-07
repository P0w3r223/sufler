# 0072 — Channel-scoped attachment resolution

Date: 2026-09-07
Status: accepted
Author: P0w3r223 (owner decision 2026-09-07)
Amends: [ADR 0016](0016-user-multimodal-attachments.md) (attachment reachability is now bounded
by the channel's own drive, not by what the bot account happens to be allowed to read)
Related to: [ADR 0015](0015-teams-delegated-graph-polling.md) (the delegated token and its scopes),
[ADR 0026](0026-agent-file-reply-in-thread.md) (the outbound direction, whose `filesFolder`
lookup this ADR mirrors inbound)

---

## Context

ADR 0016 gave the Teams door the ability to read files a person drops into a channel thread.
A file attachment arrives as an entry in the message's `attachments` array with
`contentType: "reference"` and a `contentUrl` pointing at SharePoint. The door takes that URL
**verbatim** — the only condition is that it is non-empty — encodes it into
`GET /shares/{id}/driveItem/content`, and fetches it with the bot's delegated token, which
ADR 0016 itself widened to `Files.Read.All` + `Sites.Read.All`.

**Graph resolves that request against the bot's account, not against the sender's.** The message
says which file to open; nothing establishes that the person who asked may open it. Any member of
a watched channel who can post through the Graph API rather than the Teams client can therefore
name any file the bot account can read — including files on sites they have no access to — and
the door will fetch it, extract its text into the model's context, and let it come back out in a
reply on the channel. This is a confused deputy: the door holds authority the sender does not, and
spends it on the sender's instruction.

The gap is not an oversight of principle, but of reach. ADR 0016 reasoned about *channel files*
throughout ("channel files live in SharePoint, reachable only with file-scoped Graph permissions")
and never wrote down the invariant that follows from it — that only channel files are in scope.
Nothing in the code carries that sentence either.

Two measurements framed this decision:

* **The asymmetry is visible in one file.** `selection.py` guards the narrow path and leaves the
  wide one open: `_public_image_url` requires `https` and a host allowlist, with a comment naming
  it an SSRF barrier, because a pasted `<img src>` could point at an internal service. The
  file-attachment path, which reaches a whole tenant's document estate, has no check at all. A
  test even asserts that `"u://plik"` survives parsing, so the absence is deliberate, not missed.
* **The blast radius is wide and the usage is not.** Production has carried **4 attachment blocks
  in its entire history** (`conversations.db`, 2026-07-31 → 2026-09-07). Whatever we refuse here,
  we refuse almost nothing that anyone actually does.

## Options Considered

### Option A — allowlist the tenant's SharePoint hosts

Cheapest, and it matches the barrier already used for public images. Rejected: it does not
address this threat at all. The victim file sits on the same legitimate tenant host as the
attachment, so every URL worth stopping passes. It would buy the *appearance* of the guard the
neighbouring path has, which is worse than no guard, because the next reader would stop looking.

### Option B — channel drive plus the sender's own OneDrive

Closer to how people actually attach files: Teams lets someone attach from their personal
OneDrive, and that file legitimately lives outside the channel's drive. Rejected **for now**, on
two grounds. It still leaves the sender able to make the bot read any file of *their own* that
they never shared with the channel, publishing it into a thread other people read — a smaller
escalation, but the same shape. And it needs a trusted sender → drive mapping, which is a second
Graph lookup and a second thing to get wrong, bought for a case the measurement says is not
occurring. Revisit if refusals show up in the log.

### Option C (chosen) — the channel's own files folder, and nothing else

Resolve the share to driveItem **metadata** first, compare its `parentReference.driveId` with the
`driveId` of the channel's `filesFolder`, and only then fetch the bytes — by explicit
`/drives/{driveId}/items/{itemId}/content` rather than by re-resolving the share.

The mechanism is not new to this codebase. `graph_file_sender.py` already asks
`GET /teams/{team}/channels/{channel}/filesFolder` for `parentReference.driveId` when it uploads a
file reply into a channel. This ADR points the same question at the inbound direction: *the drive
this channel owns* is the answer to "which files belong to this conversation", in both directions.

## Decision

Adopt Option C.

1. **The permissive method is removed, not deprecated.** `download_shared_url(url)` disappears
   from the door's port and from its implementation, replaced by
   `download_channel_file(team_id, channel_id, url)`. A method that resolves an arbitrary share
   with the bot's authority should not remain in the surface for a future caller to find; leaving
   it behind a comment would be exactly the "security by lack" this project has already reversed
   once and does not intend to re-introduce.
2. **Refusal is a distinct outcome, not a fetch failure.** Crossing the channel boundary raises
   `AttachmentOutsideChannel`, which the materializer catches *before* its generic handler and
   turns into a note naming the reason and the way forward ("upload it into the channel"). A
   person who attached the wrong thing gets an instruction; the log gets a warning that says a
   boundary was crossed, not that a download failed. Conflating the two would hide the only
   signal that tells us Option B is worth revisiting.
3. **The channel's drive id is cached per channel** for the process lifetime. It is a property of
   the channel, not of the message, and the poller asks about the same few channels forever.
4. **Scope is exactly the file-attachment path.** Inline images (`hostedContents`) are already
   fetched through the message's own endpoint and are bounded by it; public images keep their host
   allowlist. Neither changes.

## Consequences

* An attachment that does not live in the channel's files folder — a personal OneDrive file, a
  file from another site — is **refused** with a note. Measured cost of that refusal on this
  deployment: nothing that has happened so far. If it starts happening, the warning log is the
  trigger to reconsider Option B, and this ADR is the place to record it.
* A file attachment now costs **two Graph calls** (metadata, then content) instead of one, plus at
  most one `filesFolder` call per channel per process. Inline and public images are unchanged.
* The door no longer follows a redirect chain it cannot inspect: content is fetched from an
  explicit drive and item, so what was validated and what is downloaded are the same object.
* `Files.Read.All` / `Sites.Read.All` stay as ADR 0016 set them. This ADR narrows what the door
  *does* with that authority; narrowing the grant itself is a tenant-side change with its own
  re-consent cost, and is not reopened here.
* The invariant ADR 0016 assumed is now written down and enforced in code, so the next reader does
  not have to infer it from prose about SharePoint.
