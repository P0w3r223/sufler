"""Test-strażnik bramki retrievalu (ADR 0023): Faza A (lexical-PL) nie regresuje vs baseline.

Metryki liczone na REALNYM korpusie ``data/`` + złotym zbiorze ``eval/golden_queries.yaml`` — oba
zacommitowane, więc deterministyczne. Wymaga extra ``retrieval`` (simplemma) — inaczej pomijany.
Pilnuje, by ulepszony ranking nie okazał się GORSZY od dawnego na złotym zbiorze pionu.
"""
from __future__ import annotations

import pytest
import retrieval_eval as ev  # z katalogu eval/ (pythonpath w pyproject)

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.config import Settings
from workmate.core.application.services import NotesService


def test_metric_helpers():
    assert ev.recall_at_k(["a", "b"], {"a", "c"}, 5) == 0.5
    assert ev.mrr(["x", "a"], {"a"}) == 0.5
    # nDCG nagradza wyższą pozycję trafnej notatki.
    assert ev.ndcg_at_k(["a", "b"], {"a"}, 5) > ev.ndcg_at_k(["b", "a"], {"a"}, 5)
    assert ev.ndcg_at_k(["x"], set(), 5) == 0.0


def test_golden_set_ids_exist_in_corpus():
    repo = MarkdownNotesRepository(Settings.from_env().notes_dir)
    corpus = {n.id for n in repo.all()}
    for item in ev.load_golden():
        missing = item["relevant"] - corpus
        assert not missing, f"złoty zbiór wskazuje nieistniejące notatki: {missing}"


def test_lexical_pl_does_not_regress_vs_baseline():
    pytest.importorskip("simplemma")
    from workmate.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer

    golden = ev.load_golden()
    repo = MarkdownNotesRepository(Settings.from_env().notes_dir)
    base = ev.evaluate(NotesService(repo), golden)
    lex = ev.evaluate(NotesService(repo, lemmatizer=SimplemmaLemmatizer()), golden)

    # Faza A ma być CO NAJMNIEJ tak dobra jak baseline (empirycznie: lepsza) — strażnik regresji.
    assert lex["ndcg@5"] >= base["ndcg@5"]
    assert lex["recall@3"] >= base["recall@3"]
    assert lex["mrr"] >= base["mrr"]
