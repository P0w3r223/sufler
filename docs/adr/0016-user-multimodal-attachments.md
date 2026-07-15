# 0016 — User multimodal attachments (PDF / Word / images) via neutral content blocks

Date: 2026-07-12
Status: accepted
Author: P0w3r223
Related to: [ADR 0006](0006-write-capability-gate-2.md), [ADR 0008](0008-agent-runtime-and-tool-catalog.md), [ADR 0011](0011-stateful-lossless-conversation-memory.md), [ADR 0015](0015-teams-delegated-graph-polling.md)
Amends: ADR 0011 (user rows may carry neutral attachment blocks), ADR 0015 (adds Files.Read.All + Sites.Read.All scopes, one-time re-consent)

---

## Context

Users need to drop files into a Teams channel thread — PDFs, Word documents, and images
(PNG/JPG/JPEG) — and have the agent read, summarize, and answer from them, including across
follow-up turns in the same thread. Today the whole WorkMate stack is **text-only**:
`UserText` carries a bare `str`, every door discards everything but `.text`, and the Teams
Graph door strips the HTML `body.content` (losing `<attachment>`/`<img>` markers) before
anything downstream sees it.

The plumbing for provider content blocks already exists for *assistant* and *tool* turns:
ADR 0011 stores opaque provider blocks verbatim (`AssistantTurn.blocks`, `RawTurn.blocks`,
`ConversationMessage.blocks`, the `blocks_json` column) and the Anthropic adapter already
emits `content` as a list of blocks for those roles. The Anthropic SDK (`0.116.0`) natively
accepts `image` (base64) and `document` (PDF base64) blocks. Two facts constrain the design:
**Word `.docx` is not a native block type** (must be extracted to text or converted), and
**channel files live in SharePoint**, reachable only with file-scoped Graph permissions —
whereas inline-pasted images arrive as `hostedContents` reachable on the existing channel
scope.

Hard constraints (not reopened): `core ↛ adapters`; the Anthropic block schema lives only in
the outbound adapter; the tool catalog stays read-only on this door (ADR 0006) — attachments
are **content, not a tool**; attachment content is **data, not commands** (CLAUDE.md); secrets
(API key, MSAL cache) stay in outbound adapters only.

## Options Considered

### Content carrier — how the user turn transports attachments

- **Option A (chosen): neutral `Attachment` on `UserText`.** Add an additive
  `attachments: tuple[Attachment, ...] = ()` field to `UserText`, mirroring how `blocks` was
  added to `AssistantTurn`. The core carries a provider-neutral record (kind, media_type,
  name, base64/text); the outbound adapter converts it to Anthropic `image`/`document`/`text`
  blocks at replay. **Pros:** minimal, backward-compatible, keeps the provider schema out of
  the core, one new type. **Cons:** user rows start carrying `blocks` (amends ADR 0011's
  "user rows are text-only" assumption).
- **Option B (rejected): a new `TranscriptEntry` variant.** Touches every `isinstance`
  dispatch and the role-alternation invariants, and buys nothing at the storage layer (the
  row role is still just a string).
- **Option C (rejected): a `read_attachment` tool.** Attachments arrive *with* the message,
  not fetched on demand; modelling them as a tool call is a worse fit and would add a
  mutating-looking surface to a read-only door.

### Memory strategy — how attachments persist across follow-up turns

- **Chosen: replay the full attachment (base64) every turn**, losslessly, consistent with
  ADR 0011. Follow-up questions ("what's the figure in the table on page 3") answer
  faithfully; prompt caching amortizes the re-send cost. Trade-off: larger SQLite rows and
  higher input-token cost, bounded by size/count limits.
- **Rejected: read once, store only extracted text/summary.** Cheaper, but follow-ups are
  limited to whatever the first turn captured.

### Word handling

- **Chosen: extract text server-side with `python-docx`** (paragraphs + table cells),
  emitted as a text block. **Rejected: convert `.docx → PDF`** (needs a heavy converter such
  as LibreOffice on the host).

## Decision

Adopt **Option A + full-replay + text-extracted Word + file scopes**. A neutral `Attachment`
rides on `UserText`; the outbound adapter (`anthropic_llm._to_messages`) turns it into
Anthropic blocks — `document`/`image` **before** the text block (PDF ordering requirement),
with no empty text block when the caption is empty. Attachments persist in the existing
`blocks_json` column in **neutral** form (never Anthropic blocks) and round-trip back into
`UserText` on replay. I/O (fetching bytes from Graph, base64, docx extraction, validation)
lives entirely in the Teams door adapter; `selection.py` stays pure and only parses lightweight
references.

## Consequences

- **New core type** `Attachment` and `UserText.attachments` (additive, default empty →
  backward compatible). Helpers `attachment_to_row`/`attachment_from_row` are the single
  source of the neutral wire shape. `AgentRuntime.run`/`run_turn` gain an `attachments`
  parameter.
- **Amends ADR 0011**: user rows may now carry `blocks` (neutral attachment dicts), whereas
  before only assistant/tool rows did. `_row_of(UserText)` serializes them; `_to_transcript`
  reconstructs `UserText(text, attachments)` and its guard becomes `msg.text or msg.blocks`
  so an attachment-only message (empty caption) is not dropped; `_to_transcript_with_summary`
  preserves the first turn's attachments. **base64 never enters the `text` column or FTS** —
  only the user's caption is indexed.
- **Outbound adapter** owns the Anthropic block schema (`_attachment_block`, `_user_message`);
  the core never sees it. `UserText` without attachments still serializes to a bare string
  (golden tests unchanged).
- **New Graph permissions** `Files.Read.All` + `Sites.Read.All` (SharePoint file download by
  `contentUrl` → driveItem). This is a scope change on top of ADR 0015 and forces a **one-time
  device-code re-consent**. Inline-pasted images (`hostedContents/$value`) need no new scope.
- **Read-only preserved** (ADR 0006): attachments are content blocks, not a tool; no
  `save_note` from this door. Attachment content is treated as data, never executed.
- **Two fetch paths** in the door: `hostedContents/{id}/$value` (inline images, sniffed by
  magic bytes) and `/shares/u!{b64url}/driveItem/content` (files, routed by extension). The
  `.docx` path extracts text; PDFs and images pass through as base64.
- **Limits & degradation**: `max_attachment_mb` and `max_total_attachment_mb` (1–24 MB raw —
  base64 inflates ~1.33×, so 24 MB ≈ the 32 MB API request ceiling) and
  `max_attachments_per_message` (1–20) in `TeamsGraphSettings`. Oversized / unsupported / failed
  attachments degrade to a short in-band text note (the agent informs the user) — never a
  poller crash. Without a PDF page counter we rely on the MB limit for the ~100-page bound.
- **DB size / token cost**: full replay stores base64 in every thread row and re-sends it each
  turn; bounded by the limits and mitigated by prompt caching. Archived (compacted) turns drop
  out of active replay, capping growth.
- **Compaction**: the summarizer flattens history to a single string, so user attachments are
  described **textually** (name + type; full extracted text for `.docx`); base64 never reaches
  the summarizer.
- **New dependency** `python-docx` in the `teams-graph` extra; lazy import in the door.
- **Revisit when** the Files API (`file_id` reference) is preferred over inline base64 to cut
  per-turn payload, or when a PDF page-count guard is needed.

---

## Amendment (2026-07-14) — reply-scoped hosted content + HEIC/HEIF

Live-smoke drzwi Teams ujawnił dwie luki w ścieżce obrazów; obie naprawione bez zmiany decyzji rdzenia:

- **Reply-scoped hosted content.** Wklejka obrazu w ODPOWIEDZI wątku dawała `404`: klient budował
  URL root-scoped (`…/messages/{id}/hostedContents`) z id repliki, a Graph adresuje odpowiedź tylko
  przez `…/messages/{root_id}/replies/{reply_id}/hostedContents`. `get_hosted_content` dostaje teraz
  `root_id`; gdy `message_id != root_id`, ścieżka (by-id i listowanie-fallback) jest reply-scoped —
  post-root bez zmian. Materializer forwarduje `msg.thread_root_id`. Degradacja do notki niezmieniona.
- **HEIC/HEIF (domyślny format zdjęć iPhone).** Nowa zależność `pillow-heif` w extra `teams-graph`
  (leniwa, idempotentna `register_heif_opener()` w adapterze; brak wtyczki → notka, nie crash).
  Formaty fotograficzne nieakceptowane natywnie przez API (HEIF) są re-enkodowane do **JPEG q85**
  (nie PNG — fotografia jako PNG groziłaby przekroczeniem `max_bytes` nawet po downscalingu); BMP/TIFF
  nadal → PNG. `_sniff_image` pozostaje passthrough wyłącznie dla formatów API-native, więc surowy
  HEIC nigdy nie trafia do API (dałby 400).
- **Znane ograniczenie:** HEIC powyżej `_MAX_IMAGE_PIXELS` (40 MP, tryb wysokiej rozdzielczości) nadal
  degraduje do notki (ochrona RAM); domyślne 12 MP z iPhone jest wspierane.
