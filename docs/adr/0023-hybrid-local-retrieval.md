# 0023. Hybrid local retrieval for `search_notes` (Polish lexical foundation, gated dense)

Date: 2026-07-16
Status: proposed
Author: P0w3r223
Related to: docs/roadmap.md (Phase 3 — "embeddings and ranking over notes"),
  docs/adr/0002-read-only-first.md, docs/adr/0003-note-schema.md

---

## Context

`search_notes` ranks notes with naive lexical scoring (`core/application/services.py::_score`):
`term in text` (substring) over weighted fields, summed by query-word coverage. Two weaknesses
matter for this knowledge base: **Polish inflection** — substring matching misses declension
(`integracja` ≠ `integracji`, `koszt` ≠ `kosztów`) — and **no semantics** (synonyms/paraphrase).
The roadmap's Phase 3 calls for "embeddings and ranking over notes".

Hard constraints (from the team): data must stay **on-prem** (no external embedding API); high
retrieval accuracy is required; the corpus is **small (~15 notes) and topically homogeneous**; the
core must stay I/O-free, SDK-free, deterministic and offline-testable; target is **Windows,
CPU-only**; minimal dependencies; the MCP 4+1 tool surface is **frozen** (golden test).

Research (three parallel perspectives, cited) converged: (1) the biggest *certain* accuracy gain for
Polish is morphology (lemmatization) + real BM25; (2) hybrid sparse+dense beats dense-alone,
especially for small models, and RRF(k=60) is a safe fusion; (3) **whether dense adds recall over
good lexical on a corpus this small and homogeneous is uncertain — it must be measured, not
assumed** (dense can even hurt on exact entities: company/project/participant names, which dominate
meeting notes). At this scale, brute-force numpy cosine is sub-millisecond; sqlite-vec has documented
Windows DLL-loading issues and is pre-v1.

## Options considered

1. **External embedding API** (Voyage/OpenAI/Cohere). Best semantic quality, simplest code — but
   internal notes leave the org (governance) + a vendor key/cost. **Rejected: violates on-prem.**
2. **Pure lexical, Polish-aware only** (lemmatization + BM25, no embeddings). Fixes the #1 weakness,
   fully local, lightweight — but no semantic recall. Strong, but the roadmap wants embeddings.
3. **Phased hybrid, all local** (chosen). A Polish-lexical BM25 foundation, plus a *gated* local
   dense layer fused by RRF, added only if a micro-eval proves it helps.

## Decision

**Option 3, phased, with a measurement gate:**

- **Phase A (unconditional):** lemmatization behind a `Lemmatizer` port (adapter: `simplemma`,
  pure-Python, OOV-tolerant, no torch) + **pure Okapi BM25 in the core** (`core/domain/ranking.py`,
  deterministic, no I/O). Replaces the substring scorer; field weight (title ×3) via lemma
  repetition. Biggest ROI, lowest risk, no heavy deps. Enabled on all doors (lemmatization is
  cheap — even MCP stdio). Falls back to the old substring scorer when the extra is absent
  (`lemmatizer=None`) — additive and reversible; existing calls/tests unchanged.
- **Gate (micro-eval):** a small golden set (20–40 real vertical queries, mined from
  `conversations.db` tool-call history + team input; humans label relevant note ids — trivial at
  15 notes). Compare baseline vs lexical-PL vs hybrid on recall@k / nDCG@k. Dense ships **only** if
  it beats lexical-PL by a meaningful margin without recall regression.
- **Phase B (gated):** local dense embeddings (`multilingual-e5-small` via ONNX/fastembed first;
  escalate to `sdadas/mmlw-retrieval-roberta-base` if quality demands) + **numpy brute-force**
  cosine (vectors as BLOB in SQLite, `~/.workmate/retrieval_index.db`, outside `data/`) + RRF(k=60).
  Behind a per-door `enable_dense` flag (default OFF), enabled only on **long-lived doors**
  (Teams/agent — model loaded once), never on short-lived MCP stdio. `reciprocal_rank_fusion` already
  lives in `core/domain/ranking.py` awaiting this layer.
- **Phase C (optional):** Polish cross-encoder reranker on top-N, only if the eval shows headroom.

The decisive argument: research is certain about morphology (Phase A) and uncertain about dense on
this corpus — so dense enters behind a measurement, not by assumption. The MCP surface is untouched
at every step (we improve the *internals* of `search_notes`; the tool signature/description stay).

## Consequences

- Ranking (BM25, RRF) lives in the core as pure deterministic functions — testable, no clock/random.
- Linguistic transforms (lemmatization, embeddings) and vector storage live in adapters behind
  ports — `core ↛ adapters` preserved; mmlw↔e5 and numpy↔future are one-adapter swaps.
- `NoteSummary.score` changes `int → float` (BM25/RRF yield non-integers) — a small *output*
  contract change; the golden surface test (asserts only `parameters`+`description`) is unaffected.
- New optional extra `retrieval` (`simplemma`); a future `retrieval-dense` extra for Phase B. Heavy
  model import stays lazy (pattern of `agent`/`teams-graph`). On-prem deployment must pre-stage
  model weights (HuggingFace download on first use) for air-gapped machines — a documented step.
- Content stays data, not commands: retrieval only ranks/reads; the index holds derived float
  vectors + `note_id` + snippet, nothing executable. Index lives outside `data/` (operational, not
  a knowledge-base artifact the agent reads — same invariant as `EventsSettings`/`WorkspaceSettings`).
