"""Testy adaptera dense (ADR 0039) na atrapie embeddera — pomijane bez numpy (extra dense).

Atrapa zwraca deterministyczne wektory (dobierane po podłańcuchu tekstu), więc sprawdzamy czystą
logikę adaptera bez pobierania modelu: kolejność po cosinusie, próg podobieństwa oraz inkrementalny
cache (re-embedding tylko przy zmianie treści). numpy jest jednak potrzebne do samych operacji.
"""

from __future__ import annotations

import importlib.util
from datetime import date

import pytest

from tests.conftest import make_note
from workmate.adapters.outbound.onnx_semantic_ranker import OnnxSemanticRanker

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None,
    reason="wymaga numpy (extra retrieval-dense)",
)

# Wektory 2D: zapytanie i A wyrównane (cos=1), B prostopadłe (cos=0), C przeciwne (cos=-1).
_VECTORS = {"query": [1.0, 0.0], "aaa": [1.0, 0.0], "bbb": [0.0, 1.0], "ccc": [-1.0, 0.0]}


class _FakeEmbedder:
    """Atrapa: wektor per tekst (dobierany po podłańcuchu), zlicza osadzone teksty."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.embedded: list[str] = []

    def embed(self, texts):
        for text in texts:
            self.embedded.append(text)
            key = next((k for k in self._vectors if k in text), None)
            yield list(self._vectors.get(key, [0.0, 0.0, 1.0]))


def _ranker(tmp_path, **kw):
    return OnnxSemanticRanker(
        model="fake", index_path=tmp_path / "idx.db", embedder=_FakeEmbedder(_VECTORS), **kw
    )


def _notes():
    return [
        make_note("x/y/2025-01-01-aaa", project="p", title="aaa", on=date(2025, 1, 1)),
        make_note("x/y/2025-01-02-bbb", project="p", title="bbb", on=date(2025, 1, 2)),
        make_note("x/y/2025-01-03-ccc", project="p", title="ccc", on=date(2025, 1, 3)),
    ]


def test_rank_orders_by_cosine_and_drops_anticorrelated(tmp_path):
    ids = _ranker(tmp_path).rank("query", _notes())
    # A (cos=1) > B (cos=0); C (cos=-1) odcięte domyślnym progiem 0.0.
    assert ids == ["x/y/2025-01-01-aaa", "x/y/2025-01-02-bbb"]


def test_min_similarity_floor_trims_tail(tmp_path):
    ids = _ranker(tmp_path, min_similarity=0.5).rank("query", _notes())
    assert ids == ["x/y/2025-01-01-aaa"]  # tylko cos=1 przechodzi próg 0.5


def test_cache_reuses_then_only_reembeds_query(tmp_path):
    ranker = _ranker(tmp_path)
    notes = _notes()
    ranker.rank("query", notes)
    first = list(ranker._embedder.embedded)
    assert sum(1 for t in first if "aaa" in t) == 1  # pasaż A osadzony raz
    # Druga runda: pasaże z cache (content_hash bez zmian) → osadzamy tylko zapytanie.
    ranker.rank("query", notes)
    assert ranker._embedder.embedded[len(first) :] == ["query"]  # model 'fake' → brak prefiksu
