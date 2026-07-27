# 0039. Gated local dense (semantic) retrieval as a hybrid extension of BM25

Date: 2026-07-27
Status: accepted
Author: Patryk
Related to: [ADR 0023](0023-hybrid-local-retrieval.md) (makes its deferred Phase B concrete),
  [ADR 0008](0008-agent-runtime-and-tool-catalog.md), roadmap §7 (Phase 3 — "embeddings + ranking")

---

## Context

ADR 0023 delivered Phase A (Polish lemmatization + Okapi BM25 in the core) and deliberately
deferred Phase B (dense embeddings) behind a **measurement gate**: morphology is a certain win,
but dense recall on *this* corpus — **15 notes, topically homogeneous, dominated by exact
entities (company / project / participant names)** — is uncertain and can even hurt. Roadmap
Phase 3 ("embeddings + ranking") and the V1 gap analysis (A1) call for closing the dense layer.

The ground is pre-wired for this: `reciprocal_rank_fusion` already lives in `core/domain/ranking.py`
(present, unused in any production path — the declared extension point); `NoteSummary.score` is
already `float`; `eval/retrieval_eval.py` already carries the gate constant `_DENSE_GATE_NDCG5 = 0.03`
and a commented `hybrid` placeholder; the `Lemmatizer` port + lazy-extra + graceful-degradation seam
is a proven template.

Hard constraints (re-verified in code): fully **local/offline, no network in the query path**;
Polish notes; **Windows, CPU-only**; minimal dependencies and a lean base install; **fuse via the
existing `reciprocal_rank_fusion`, do not replace BM25**; `core ↛ adapters`, capability behind a
port with lazy import and a separate extra; **no new MCP tools** (4+1 surface frozen); ranking
quality gated by `eval/`.

## Options considered

### Option A — Lean gated hybrid: multilingual-small (ONNX) + numpy/SQLite brute-force + RRF *(chosen)*
A `SemanticRanker` port in the core; an outbound adapter that embeds notes/queries with a small
multilingual model served through **ONNX Runtime (fastembed), no PyTorch**, persists float vectors
as BLOBs in `~/.workmate/retrieval_index.db` (outside `data/`), and ranks candidates by **numpy
brute-force cosine**. `NotesService` fuses the BM25 id-list and the dense id-list through the
existing `reciprocal_rank_fusion(k=60)`. New extra `retrieval-dense`; per-door `enable_dense` flag
(default OFF); enabled only on long-lived doors, never MCP stdio.
- **Pros**: no torch → install stays ~+100–250 MB, not ~+2 GB; brute-force cosine over 15 (even 10³)
  vectors is exact and sub-millisecond — no ANN, no `sqlite-vec`/`faiss` native-DLL risk on Windows;
  reuses RRF unchanged; additive and fully reversible; model swap is one adapter change.
- **Cons**: multilingual-small trails a PL-specific model on nuanced Polish; may not clear the gate on
  a 15-note entity-heavy corpus (that is the point — measured, not assumed); adds `onnxruntime` + `numpy`.
- **Effort**: M · **Risk**: Low (default OFF; degrades to lexical-PL exactly like a missing `simplemma`).

### Option B — Max Polish quality: PL-specific model (mmlw) via sentence-transformers/torch
Same store/fusion, embed with `sdadas/mmlw-retrieval-roberta-*` (top of the Polish PIRB IR benchmark).
- **Pros**: best-in-class Polish semantic quality; highest chance of clearing the gate.
- **Cons**: `torch` CPU wheel + `transformers` → **~+2 GB installed**, colliding with minimal-deps /
  lean-install / Windows-CPU; ~430 MB weights; slower cold load; heavier air-gapped pre-staging.
- **Effort**: M · **Risk**: Med.

### Option C — No dense: stay lexical-PL (the null option the gate may select)
Ship nothing new; record that the gate was not met (or not worth the cost) on this corpus.
- **Pros**: zero new deps / cost / risk; honest to the ADR-0023 finding.
- **Cons**: roadmap Phase 3 "embeddings" stays formally open; no recall for zero-lexical-overlap paraphrases.
- **Effort**: S · **Risk**: None.

## Decision

**Build Option A and let the gate choose between A and C.** Implement the dense layer as the lean
hybrid (small multilingual model via ONNX, numpy/SQLite brute-force, RRF), add a `hybrid` config to
`eval/retrieval_eval.py`, and **ship dense on the long-lived doors only if
`ndcg@5(hybrid) − ndcg@5(lexical-PL) ≥ 0.03` with no `recall@10` regression** (constants already in
the eval). If it fails, the code stays behind `enable_dense=False` (effectively Option C) with the
eval documenting why. Keep **Option B (mmlw) as a documented one-adapter swap** — attempted only if
multilingual-small fails the gate but a PL model plausibly passes; a ~2 GB install must be *earned*
by measured quality, not assumed.

### Sub-decisions

- **A1 — Embedding model (Critical):** **verified 2026-07-27 against `fastembed>=0.8`:
  `intfloat/multilingual-e5-small` is NOT in fastembed's registry**, so the shipped default is
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (multilingual, 384-dim, ~0.22 GB,
  no query/passage prefix) — the lightest multilingual model fastembed serves. Heavier options:
  `paraphrase-multilingual-mpnet-base-v2` (768-dim/1.0 GB), `multilingual-e5-large` (1024-dim/2.24 GB).
  The adapter still encodes E5 `query:`/`passage:` (and mmlw `zapytanie:`) prefixes by model name, so
  selecting such a model works — omitting the prefixes measurably degrades quality.
- **A2 — Vector storage & search (Important):** numpy brute-force cosine + vectors as SQLite BLOB at
  `~/.workmate/retrieval_index.db` (operational store, like `events.db`). Schema
  `(note_id PK, model, dim, vector BLOB, content_hash, updated_at)`; **incremental re-embed by
  `content_hash`**. No `sqlite-vec` / `faiss` / `chromadb` (ANN pointless below ~10⁴ vectors,
  native-DLL risk on Windows). Reindex is a CLI/adapter concern — **no MCP tool**.
- **A3 — Fusion & threshold (Important):** reuse `reciprocal_rank_fusion(bm25_ids, dense_ids, k=60)`
  unchanged — rank-based, no score normalization (the safe default). Gate on `_DENSE_GATE_NDCG5`
  (ndcg@5 gain over lexical-PL) **and** no recall@10 regression. Optional knobs (default off):
  dense top-N cap, min-similarity floor.
- **A4 — Hexagonal seam (Important):** new `SemanticRanker` Protocol in `core/ports/text.py`
  (`rank(query, candidates) -> list[str]`, note-ids by cosine). `NotesService.__init__(..., *,
  lemmatizer=None, semantic: SemanticRanker | None = None)`; when both present, fuse via RRF; when
  `semantic is None`, today's behavior byte-for-byte. **Fusion stays in the core; embedding + vector
  store + numpy cosine live in the adapter** (numpy never enters the stdlib-only core). Wiring mirrors
  `build_lemmatizer`: a `build_semantic_ranker(settings)` returning the adapter or `None`, injected as
  `None` in `server.py` (stdio stays lexical-PL) and as a real ranker in `agent_wiring.py` when
  `enable_dense` and the extra are present.

## Consequences

- **Additive & reversible**: `enable_dense` default OFF ⇒ identical to today; missing `retrieval-dense`
  extra ⇒ graceful degrade to lexical-PL (same pattern as missing `simplemma`). No change to the MCP
  4+1 surface, `NoteMetadata`, or `NoteSummary` (`score` already `float`). RRF already in core.
- **Doors**: dense only on long-lived doors (Teams/agent/CLI-server — model loaded once); **never MCP
  stdio** (per-session process; a cold model load would tax every session, and lexical-PL suffices there).
- **Offline invariant**: no network in the query path; model weights pre-staged (first-run download is a
  documented deploy step; air-gapped machines pre-stage the ONNX weights).
- **Content stays data, not commands**: the index holds only derived float vectors + `note_id` +
  `content_hash`, nothing executable, outside `data/`.
- **New surfaces**: `SemanticRanker` port; `adapters/outbound/onnx_semantic_ranker.py` (+ SQLite vector
  store); `build_semantic_ranker` wiring; `RetrievalSettings` fields (`enable_dense=False`,
  `dense_model`, `index_path`, `rrf_k`, optional `dense_top_n`); `retrieval-dense` extra + mypy
  `ignore_missing_imports`; `hybrid` config + guard test in `eval/`.
- **Revisit when**: corpus grows past ~10⁴ notes (reconsider ANN) or the gate fails with
  multilingual-small but a PL model is worth the ~2 GB (swap to mmlw, Option B). Phase C (Polish
  cross-encoder reranker) stays out of scope.

## Gate result (2026-07-27)

Ran on the committed 15-note corpus + 20-query golden set with the shipped default
(`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, fastembed 0.8.0):

| metric | baseline | lexical-PL | hybrid |
|---|---|---|---|
| recall@3 | 0.950 | **0.983** | 0.858 |
| recall@5 | 1.000 | 1.000 | 1.000 |
| recall@10 | 1.000 | **1.000** | 1.000 |
| ndcg@5 | 0.972 | **0.994** | 0.899 |
| mrr | 0.963 | **1.000** | 0.867 |

**Gate: hybrid − lexical-PL ndcg@5 = −0.095 (needs ≥ +0.03) → FAILED. Option C selected: dense stays
`enable_dense=False`.** The result confirms the ADR thesis — on this small, entity-heavy corpus
lexical-PL is already at ceiling (recall@10 = 1.000, mrr = 1.000, ndcg@5 = 0.994), leaving no headroom
for dense; it only dilutes the strong lexical signal (hybrid drops recall@3, ndcg@5 and mrr). The built
code remains a reviewed, tested, OFF-by-default capability; **re-run this gate if the corpus grows
substantially or diversifies away from exact-entity queries.** (Minor caveat: fastembed 0.8.0 uses mean
pooling for this model vs CLS in 0.5.1 — irrelevant to the verdict given the >0.12 gap to threshold.)

## Notes

Model-size / latency / Polish-quality figures above are estimates against a Jan-2026 knowledge
cutoff; the micro-eval on the committed corpus empirically settles the quality question and drives
the ship/park decision regardless.
