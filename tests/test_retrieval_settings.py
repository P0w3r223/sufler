"""Testy pól dense w ``RetrievalSettings`` i wiringu rankera (ADR 0039) — bez modelu/extra."""

from __future__ import annotations

from workmate.adapters.inbound.retrieval_wiring import build_semantic_ranker
from workmate.config import RetrievalSettings


def test_dense_fields_default_off():
    s = RetrievalSettings()
    assert s.enable_dense is False
    assert s.dense_model == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert s.rrf_k == 60
    assert s.dense_top_n == 0
    assert s.dense_min_similarity == 0.0


def test_dense_env_parsing(monkeypatch):
    monkeypatch.setenv("WORKMATE_RETRIEVAL_ENABLE_DENSE", "true")
    monkeypatch.setenv("WORKMATE_RETRIEVAL_DENSE_MODEL", "some/model")
    monkeypatch.setenv("WORKMATE_RETRIEVAL_RRF_K", "40")
    monkeypatch.setenv("WORKMATE_RETRIEVAL_DENSE_TOP_N", "5")
    monkeypatch.setenv("WORKMATE_RETRIEVAL_DENSE_MIN_SIM", "0.25")
    s = RetrievalSettings.from_env()
    assert s.enable_dense is True
    assert s.dense_model == "some/model"
    assert s.rrf_k == 40
    assert s.dense_top_n == 5
    assert s.dense_min_similarity == 0.25


def test_build_semantic_ranker_disabled_returns_none():
    # Domyślnie OFF → wiring nie próbuje nawet importować fastembed (brak extra bez znaczenia).
    assert build_semantic_ranker(RetrievalSettings(enable_dense=False)) is None
