# 0050 — Corpus seed imports Office/PDF via shared text extraction

Date: 2026-07-29
Status: accepted
Author: P0w3r223
Related to: [ADR 0016](0016-user-multimodal-attachments.md) (attachment text extraction),
[ADR 0005](0005-company-project-note-layout.md) (deterministic note id = file path),
[ADR 0006](0006-write-capability-gate-2.md) (write posture / `save_note` as sole writer),
W0 (corpus cold-start seed importer)

---

## Context

The W0 seed importer (`workmate-seed-corpus`, ADR 0006-compliant: it reuses `save_note`, adds no
mutating tool) originally imported only markdown/text files. The real cold-start corpus, however,
lives in a SharePoint document library as Word/Excel/PowerPoint/PDF. Two forks were decided with
the operator:

1. **Source** — how the importer reaches SharePoint content. Building an MS Graph source into the
   (synchronous) CLI means async→sync plumbing, `Sites.Read`/`Files.Read` scope, and an interactive
   MSAL login on the operator's machine. The alternative — point the existing importer at a folder
   the operator has synced locally (OneDrive "Sync") or downloaded — needs zero network code.
2. **Formats** — markdown/text only, or also Office/PDF (requiring text extraction).

Separately, the Teams attachment materializer (ADR 0016) *already* extracts text from
docx/xlsx/pptx: the same `bytes → str` functions the seed importer needs.

## Decision

1. **No network in the importer.** SharePoint is reached via an operator-synced/downloaded local
   folder passed to `--source`. The importer stays a pure local-filesystem tool; it never
   authenticates or calls Graph. This keeps the import a deliberate, human-run act (consistent with
   the write posture) and avoids threading an async Graph client through a sync CLI.

2. **Extract Office/PDF to text at the import boundary.** Supported: `.md/.txt/.csv/.log/.json/
   .xml/.yaml/.yml` (decoded as text) and `.docx/.xlsx/.pptx/.pdf` (text extracted). Content is
   copied faithfully as DATA — never interpreted (hard rule 4). Unsupported or unreadable files are
   **skipped with a reason**, never crashing the batch and never masquerading as an empty note.

3. **One extraction module, shared.** The `bytes → str` extractors move out of the teams-graph
   materializer into `adapters/inbound/document_text.py`; both the materializer and the seed
   importer import them (DRY on the *same concept*, not lookalike code). PDF extraction (`pypdf`) is
   added there. A single dispatcher (`extract_text_from_bytes` / `extract_text_from_path`) routes by
   extension and normalizes every library error to one `DocumentExtractionError`.

4. **Dependencies behind a `seed` extra.** `python-docx`, `openpyxl`, `python-pptx`, `pypdf` install
   via `uv sync --extra seed`. Imports stay lazy, so the MCP server and doors remain lightweight; the
   importer is an occasional operator tool, not part of the runtime.

5. **The mikro-eval gate still governs live writes** (hard rule 9). This ADR only widens *what* the
   dry-run importer can read. Writing extracted notes into `data/notes` still requires running
   `eval/retrieval_eval.py` before/after (per the how-to), and the write is the operator's call.

## Consequences

- **Positive.** Real corpus (Word/Excel/PPT/PDF) becomes importable today with no new network,
  auth, or async surface. Extraction logic has a single home, so a fix (e.g. table handling) helps
  both attachments and seed. `NoteMetadata`, the MCP tool surface, and `save_note` are untouched.
- **Negative / limits.** No OCR: a scanned PDF with no text layer imports as an empty document
  (skipped downstream, not an error). Titles for binary docs without an H1 fall back to the filename.
  Extracted text is capped (200k chars / 2000 sheet rows) to bound note size. Operator must sync the
  SharePoint folder manually — no scheduled/automatic pull (out of scope; revisit only if a
  recurring import is needed, which would reopen fork 1 toward a Graph source).
