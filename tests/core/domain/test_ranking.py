"""Testy czystego rankingu (ranking.py, ADR 0023) — Okapi BM25 + RRF, funkcje deterministyczne."""

from __future__ import annotations

from sufler.core.domain.ranking import bm25_rank, reciprocal_rank_fusion


def test_bm25_empty_query_or_docs_returns_empty():
    assert bm25_rank([], {"a": ["x"]}) == {}
    assert bm25_rank(["x"], {}) == {}


def test_bm25_skips_docs_without_query_terms():
    scores = bm25_rank(["scada"], {"hit": ["scada", "raport"], "miss": ["budżet", "plan"]})
    assert "hit" in scores and "miss" not in scores
    assert scores["hit"] > 0


def test_bm25_more_distinct_matches_scores_higher():
    docs = {
        "both": ["scada", "api", "raport"],
        "one": ["api", "notatka", "plan"],
    }
    scores = bm25_rank(["scada", "api"], docs)
    assert scores["both"] > scores["one"]  # dwa różne słowa zapytania > jedno


def test_bm25_rare_term_weighs_more_via_idf():
    # 'scada' rzadkie (1 dok.), 'raport' częste (3 dok.) — trafienie rzadkiego waży więcej.
    docs = {
        "rare": ["scada", "x"],
        "common1": ["raport", "y"],
        "common2": ["raport", "z"],
        "common3": ["raport", "w"],
    }
    scores = bm25_rank(["scada"], docs)
    common = bm25_rank(["raport"], docs)
    assert scores["rare"] > common["common1"]  # IDF: rzadki term > częsty przy tym samym tf


def test_bm25_length_normalization_prefers_shorter_doc():
    # Ten sam tf=1, ale krótszy dokument jest trafniejszy (normalizacja długości).
    docs = {"short": ["api", "x"], "long": ["api"] + ["pad"] * 30}
    scores = bm25_rank(["api"], docs)
    assert scores["short"] > scores["long"]


def test_rrf_fuses_rankings_rewarding_consensus():
    lexical = ["a", "b", "c"]
    dense = ["b", "a", "d"]
    fused = reciprocal_rank_fusion(lexical, dense)
    # 'b' wysoko w obu (ranga 1 i 0), 'a' też wysoko (0 i 1) — oba przed 'c'/'d' (tylko w jednej).
    assert set(fused[:2]) == {"a", "b"}
    assert fused.index("b") <= fused.index("c")
    assert fused.index("a") <= fused.index("d")


def test_rrf_empty_inputs():
    assert reciprocal_rank_fusion() == []
    assert reciprocal_rank_fusion([]) == []
