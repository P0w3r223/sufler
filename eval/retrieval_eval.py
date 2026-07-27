"""Mikro-eval retrievalu notatek (ADR 0023 — BRAMKA Fazy B).

Porównuje konfiguracje wyszukiwania na złotym zbiorze (``golden_queries.yaml``) metrykami
recall@k / nDCG@k / MRR. Dziś porównuje:
  * ``baseline``   — dawny ranking podłańcuchowy (bez lematyzatora),
  * ``lexical-PL`` — BM25 + lematyzacja PL (Faza A).
Punkt rozszerzenia: gdy powstanie warstwa DENSE (Faza B), dołóż konfigurację ``hybrid`` i porównaj
z ``lexical-PL`` — dense włączamy TYLKO gdy poprawia nDCG@5 o istotny margines bez regresji recall.

Uruchomienie: ``uv run python eval/retrieval_eval.py`` (wymaga extra ``retrieval`` do lematyzacji).
Funkcje są czyste/importowalne — z tego korzysta test-strażnik ``tests/test_retrieval_eval.py``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Sequence

    from workmate.core.application.services import NotesService

_GOLDEN = Path(__file__).resolve().parent / "golden_queries.yaml"
# Próg bramki Fazy B: dense wchodzi tylko, gdy podnosi nDCG@5 o co najmniej tyle nad lexical-PL.
_DENSE_GATE_NDCG5 = 0.03
_METRICS = ("recall@3", "recall@5", "recall@10", "ndcg@5", "ndcg@10", "mrr")


def load_golden(path: Path = _GOLDEN) -> list[dict[str, Any]]:
    """Wczytaj złoty zbiór: lista {query, relevant: [note_id, …]}."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [{"query": d["query"], "relevant": set(d["relevant"])} for d in data]


def _dcg(gains: Sequence[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """nDCG@k dla relewancji binarnej (trafna=1) — jak wysoko trafne notatki w top-k."""
    gains = [1.0 if rid in relevant else 0.0 for rid in ranked[:k]]
    idcg = _dcg([1.0] * min(len(relevant), k))
    return _dcg(gains) / idcg if idcg else 0.0


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    """recall@k — ułamek trafnych notatek obecnych w top-k."""
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def mrr(ranked: Sequence[str], relevant: set[str]) -> float:
    """Reciprocal Rank — 1/pozycja pierwszej trafnej notatki (0, gdy żadnej)."""
    for i, rid in enumerate(ranked):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def evaluate(service: NotesService, golden: list[dict[str, Any]]) -> dict[str, float]:
    """Uśrednione metryki konfiguracji na złotym zbiorze (search_notes, limit=10)."""
    sums = dict.fromkeys(_METRICS, 0.0)
    for item in golden:
        relevant: set[str] = item["relevant"]
        ranked = [r.id for r in service.search_notes(item["query"], limit=10)]
        sums["recall@3"] += recall_at_k(ranked, relevant, 3)
        sums["recall@5"] += recall_at_k(ranked, relevant, 5)
        sums["recall@10"] += recall_at_k(ranked, relevant, 10)
        sums["ndcg@5"] += ndcg_at_k(ranked, relevant, 5)
        sums["ndcg@10"] += ndcg_at_k(ranked, relevant, 10)
        sums["mrr"] += mrr(ranked, relevant)
    n = len(golden) or 1
    return {m: sums[m] / n for m in _METRICS}


def _build_services() -> dict[str, NotesService]:
    """Zbuduj konfiguracje nad REALNYM korpusem data/. ``lexical-PL`` wymaga extra ``retrieval``."""
    from workmate.adapters.inbound import env
    from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
    from workmate.config import Settings
    from workmate.core.application.services import NotesService

    env.load_dotenv()
    repo = MarkdownNotesRepository(Settings.from_env().notes_dir)
    services: dict[str, NotesService] = {"baseline": NotesService(repo)}
    try:
        from workmate.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer

        services["lexical-PL"] = NotesService(repo, lemmatizer=SimplemmaLemmatizer())
    except ImportError:
        print("[uwaga] brak extra 'retrieval' (simplemma) — pomijam konfigurację lexical-PL.\n")
    # Faza B (ADR 0039): hybrid = lexical-PL + dense (RRF). Wymaga extra 'retrieval' i
    # 'retrieval-dense'; brak któregokolwiek → pomijamy 'hybrid' (eval liczy pozostałe).
    if "lexical-PL" in services:
        try:
            from workmate.adapters.outbound.onnx_semantic_ranker import OnnxSemanticRanker
            from workmate.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer
            from workmate.config import RetrievalSettings

            rset = RetrievalSettings.from_env()
            ranker = OnnxSemanticRanker(
                model=rset.dense_model,
                index_path=rset.index_path,
                min_similarity=rset.dense_min_similarity,
            )
            services["hybrid"] = NotesService(
                repo,
                lemmatizer=SimplemmaLemmatizer(),
                semantic=ranker,
                rrf_k=rset.rrf_k,
                dense_top_n=rset.dense_top_n,
            )
        except Exception as exc:  # noqa: BLE001 — brak dense/modelu = pomiń hybrid, licz resztę
            print(f"[uwaga] warstwa dense niedostępna — pomijam konfigurację 'hybrid' ({exc}).\n")
    return services


def main() -> None:
    # Windows: domyślna konsola (cp1250) nie zniesie Δ/≥/— w wydruku — wymuś UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    golden = load_golden()
    services = _build_services()
    results = {name: evaluate(svc, golden) for name, svc in services.items()}

    print(f"Mikro-eval retrievalu — {len(golden)} zapytań, korpus data/\n" + "=" * 70)
    header = "metryka".ljust(12) + "".join(name.rjust(14) for name in results)
    print(header + ("      Δ(lex-base)" if {"baseline", "lexical-PL"} <= results.keys() else ""))
    for metric in _METRICS:
        row = metric.ljust(12) + "".join(f"{results[n][metric]:14.3f}" for n in results)
        if {"baseline", "lexical-PL"} <= results.keys():
            row += f"{results['lexical-PL'][metric] - results['baseline'][metric]:+15.3f}"
        print(row)

    if {"baseline", "lexical-PL"} <= results.keys():
        d = results["lexical-PL"]["ndcg@5"] - results["baseline"]["ndcg@5"]
        print("\n" + "=" * 70)
        print(
            f"Faza A (lexical-PL vs baseline): ΔnDCG@5 = {d:+.3f} — "
            f"{'POPRAWA' if d > 0 else 'brak poprawy'}."
        )
    if {"lexical-PL", "hybrid"} <= results.keys():
        d5 = results["hybrid"]["ndcg@5"] - results["lexical-PL"]["ndcg@5"]
        dr = results["hybrid"]["recall@10"] - results["lexical-PL"]["recall@10"]
        passed = d5 >= _DENSE_GATE_NDCG5 and dr >= 0.0
        print("\n" + "=" * 70)
        print(
            f"Bramka Fazy B (hybrid vs lexical-PL): ΔnDCG@5 = {d5:+.3f} "
            f"(próg ≥ {_DENSE_GATE_NDCG5:.2f}), Δrecall@10 = {dr:+.3f} → "
            + (
                "PRZECHODZI — włącz dense (WORKMATE_RETRIEVAL_ENABLE_DENSE=true)."
                if passed
                else "NIE przechodzi — zostań przy lexical-PL (dense za flagą OFF)."
            )
        )
    else:
        print(
            "\nBramka Fazy B: uruchom z extra 'retrieval-dense' (konfiguracja 'hybrid'), by "
            f"zmierzyć dense; wchodzi TYLKO gdy ΔnDCG@5 ≥ {_DENSE_GATE_NDCG5:.2f} bez regresji "
            "recall@10."
        )


if __name__ == "__main__":
    main()
