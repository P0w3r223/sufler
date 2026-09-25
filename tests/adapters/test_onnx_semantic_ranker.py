"""Testy adaptera dense (ADR 0039) na atrapie embeddera — pomijane bez numpy (extra dense).

Atrapa zwraca deterministyczne wektory (dobierane po podłańcuchu tekstu), więc sprawdzamy czystą
logikę adaptera bez pobierania modelu: kolejność po cosinusie, próg podobieństwa oraz inkrementalny
cache (re-embedding tylko przy zmianie treści). numpy jest jednak potrzebne do samych operacji.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from contextlib import closing
from datetime import date

import pytest

from sufler.adapters.outbound.onnx_semantic_ranker import OnnxSemanticRanker
from tests.conftest import make_note

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
    return _ranker_with_embedder(tmp_path, _FakeEmbedder(_VECTORS), **kw)[0]


def _ranker_with_embedder(tmp_path, embedder, **kw):
    ranker = OnnxSemanticRanker(
        model="fake", index_path=tmp_path / "idx.db", embedder=embedder, **kw
    )
    return ranker, embedder


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
    """Druga runda osadza WYŁĄCZNIE zapytanie — pasaże wracają z indeksu po ``content_hash``.

    Sonda trzyma atrapę wprost, zamiast sięgać po ``ranker._embedder``: pole prywatne mogłoby
    zniknąć przy refaktorze, który niczego nie psuje, i test padłby bez powodu.
    """
    ranker, embedder = _ranker_with_embedder(tmp_path, _FakeEmbedder(_VECTORS))
    notes = _notes()

    ranker.rank("query", notes)
    first = list(embedder.embedded)
    assert sum(1 for t in first if "aaa" in t) == 1  # pasaż A osadzony raz

    ranker.rank("query", notes)
    assert embedder.embedded[len(first) :] == ["query"]  # model 'fake' → brak prefiksu


def test_changed_note_body_is_reembedded_but_untouched_ones_are_not(tmp_path):
    """Cache jest INKREMENTALNY: zmieniona notatka wraca do modelu, reszta nie.

    Sonda odwrotna do powyższej — bez niej „cache zwraca wszystko z indeksu, nigdy nie odświeża"
    przechodzi obie asercje tamtej, a objawem byłby ranking liczony po nieaktualnej treści.
    """
    ranker, embedder = _ranker_with_embedder(tmp_path, _FakeEmbedder(_VECTORS))
    notes = _notes()
    ranker.rank("query", notes)
    embedder.embedded.clear()

    zmienione = [
        make_note(
            "x/y/2025-01-01-aaa",
            project="p",
            title="aaa",
            on=date(2025, 1, 1),
            body="aaa — treść po zmianie",
        ),
        *notes[1:],
    ]
    ranker.rank("query", zmienione)

    osadzone = [t for t in embedder.embedded if t != "query"]
    assert osadzone and all("po zmianie" in t for t in osadzone)  # tylko zmieniona notatka


def test_index_file_uses_wal_like_the_other_sqlite_stores(tmp_path):
    """Indeks osadzeń to magazyn operacyjny jak ``events.db`` — WAL jest częścią jego wzorca.

    Bez WAL czytelnik i zapisujący blokują się nawzajem na PLIKU dzielonym przez drzwi (osobne
    procesy), a ``journal_mode`` jest zapisany w nagłówku, więc widać go z każdego połączenia.
    """
    _ranker(tmp_path).rank("query", _notes())

    with closing(sqlite3.connect(tmp_path / "idx.db")) as inne_polaczenie:
        assert inne_polaczenie.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_failed_embedding_leaves_no_write_transaction_hanging_on_the_index(tmp_path):
    """Transakcja stała otwarta przez CAŁĄ budowę macierzy i nikt jej nie cofał.

    Pierwszy ``INSERT`` otwierał ją niejawnie, ``commit`` przychodził dopiero po policzeniu
    wszystkich osadzeń, a wyjątek w połowie (model padł, plik modelu zniknął) zostawiał ją otwartą
    razem z blokadą zapisu pliku indeksu — kolejne drzwi dostawały „database is locked" do końca
    życia procesu.
    """

    class EmbedderPadajacyNaDrugiej:
        def __init__(self) -> None:
            self.wywolania = 0

        def embed(self, texts):
            for _text in texts:
                self.wywolania += 1
                if self.wywolania == 2:
                    raise RuntimeError("model padł w połowie budowy macierzy")
                yield [1.0, 0.0]

    ranker, _ = _ranker_with_embedder(tmp_path, EmbedderPadajacyNaDrugiej())

    with pytest.raises(RuntimeError):
        ranker.rank("query", _notes())

    with closing(sqlite3.connect(tmp_path / "idx.db", timeout=0.2)) as inne_polaczenie:
        inne_polaczenie.execute(
            "INSERT INTO note_vectors "
            "(note_id, model, dim, vector, content_hash, updated_at) VALUES (?,?,?,?,?,?)",
            ("inny/wpis", "fake", 2, b"\x00" * 8, "hash", "2026-08-17T00:00:00+00:00"),
        )
        inne_polaczenie.commit()
