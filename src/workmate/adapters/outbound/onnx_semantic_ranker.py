"""Ranker semantyczny (dense) notatek — ONNX Runtime (fastembed), cosinus na numpy, cache w SQLite.

Implementacja portu ``SemanticRanker`` (ADR 0039, Faza B). Importowany LENIWIE przez wiring
(``retrieval_wiring.build_semantic_ranker``): brak extra ``retrieval-dense`` daje ``ImportError``
łapany w wiringu (degradacja do samego BM25), nie wywala serwera. fastembed serwuje mały model
WIELOJĘZYCZNY przez ONNX Runtime (BEZ torch) — pasuje do lokalności danych i Windows CPU.

Osadzenia to DANE pochodne treści (nie polecenia): trzymamy wektor float + hash treści per notatka
w SQLite POZA ``data/`` (magazyn operacyjny, jak ``events.db``, jedno połączenie). Re-embedding
jest INKREMENTALNY po ``(model, content_hash)`` — liczymy tylko dla zmienionych notatek.
Brute-force cosinus (numpy) jest dokładny i sub-milisekundowy przy setkach notatek; ANN
(faiss/sqlite-vec) jest tu zbędne i dokłada ryzyko natywnych bibliotek na Windows (ADR 0039).

``min_similarity`` (domyślnie 0.0) odcina notatki poniżej progu cosinusa — 0.0 usuwa tylko
anty-skorelowane; podniesienie przycina ogon mało trafnych, gdy dense zasila drzwi agenta.
``embedder`` można wstrzyknąć (test na atrapie); domyślnie budujemy fastembed dla ``model``.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from workmate.core.domain.models import Note

# Prefiksy retrievalowe: rodzina E5 wymaga "query: "/"passage: ", mmlw — "zapytanie: " dla zapytań.
# Ich POMINIĘCIE mierzalnie psuje jakość (ADR 0039). Dobierane po nazwie modelu; inne bez prefiksu.
_E5_QUERY, _E5_PASSAGE = "query: ", "passage: "
_MMLW_QUERY = "zapytanie: "

logger = logging.getLogger(__name__)


class OnnxSemanticRanker:
    """Ranking kandydatów po cosinusie osadzeń; wektory notatek cache'owane w SQLite per hash."""

    def __init__(
        self,
        *,
        model: str,
        index_path: Path,
        embedder: Any | None = None,
        min_similarity: float = 0.0,
    ) -> None:
        # Import LENIWY — brak extra ``retrieval-dense`` = ImportError łapany w wiringu.
        import numpy as np

        self._np = np
        self._model_name = model
        if embedder is None:
            from fastembed import TextEmbedding

            embedder = TextEmbedding(model_name=model)
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._index_path = index_path
        index_path.parent.mkdir(parents=True, exist_ok=True)
        # Jedno trwałe połączenie (jak sqlite_events) — drzwi są długożyjące, unikamy connect/turę.
        self._conn = sqlite3.connect(index_path, check_same_thread=False)
        # Ten sam wzorzec współbieżności co ``sqlite_events``: ``Lock`` (jedno połączenie dzielone
        # przez pulę wątków), ``busy_timeout`` (inny PROCES — drugie drzwi na tym samym pliku
        # indeksu — może trzymać zapis) i ``WAL`` (czytelnik nie blokuje zapisującego).
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._init_db()

    def warmup(self) -> None:
        """Wymuś załadowanie modelu (pobranie/rozpakowanie) — do fail-fast w wiringu."""
        self._embed(self._query_prefix() + "rozgrzewka")

    def _query_prefix(self) -> str:
        name = self._model_name.lower()
        if "mmlw" in name:
            return _MMLW_QUERY
        return _E5_QUERY if "e5" in name else ""

    def _passage_prefix(self) -> str:
        return _E5_PASSAGE if "e5" in self._model_name.lower() else ""

    def _embed(self, text: str) -> Any:
        """Zwróć znormalizowany (L2) wektor float32 dla pojedynczego tekstu."""
        vector = next(iter(self._embedder.embed([text])))
        arr = self._np.asarray(vector, dtype=self._np.float32)
        norm = float(self._np.linalg.norm(arr))
        return arr / norm if norm else arr

    @staticmethod
    def _passage_text(note: Note) -> str:
        """Naturalny tekst notatki do osadzenia (tytuł + treść + pola) — bez prefiksu."""
        meta = note.metadata
        fields = " ".join(
            [
                *meta.decisions,
                *meta.open_questions,
                *meta.action_items,
                *meta.tags,
                *meta.participants,
            ]
        )
        return f"{meta.title}\n{note.body}\n{fields}".strip()

    def _init_db(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS note_vectors ("
            "note_id TEXT PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, "
            "vector BLOB NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def _cached_vector(self, note_id: str, content_hash: str) -> Any | None:
        """Wektor z indeksu, gdy zgadza się model i hash treści; ``None`` = trzeba policzyć."""
        with self._lock:
            row = self._conn.execute(
                "SELECT model, content_hash, vector FROM note_vectors WHERE note_id = ?",
                (note_id,),
            ).fetchone()
        if row and row[0] == self._model_name and row[1] == content_hash:
            return self._np.frombuffer(row[2], dtype=self._np.float32)
        return None

    def _store_vectors(self, rows: list[tuple[str, str, int, bytes, str, str]]) -> None:
        """Utrwal świeżo policzone wektory JEDNĄ krótką transakcją; porażkę cofnij ``rollback``.

        Transakcja otwiera się dopiero PO policzeniu osadzeń. Wcześniej pierwszy ``INSERT``
        otwierał ją niejawnie i trzymała się przez całą budowę macierzy (sekundy pracy modelu),
        blokując zapis pliku indeksu innym wątkom i procesom — a wyjątek w połowie zostawiał ją
        otwartą, bo nikt nie robił ``rollback``.

        Cache osadzeń to dane POCHODNE: nieudany zapis cofamy i logujemy, bo ranking jest już
        policzony, a wywrócenie tury kosztowałoby więcej niż ponowne osadzenie przy następnym
        pytaniu.
        """
        if not rows:
            return
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO note_vectors "
                    "(note_id, model, dim, vector, content_hash, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(note_id) DO UPDATE SET "
                    "model=excluded.model, dim=excluded.dim, vector=excluded.vector, "
                    "content_hash=excluded.content_hash, updated_at=excluded.updated_at",
                    rows,
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                self._conn.rollback()
                logger.warning(
                    "Nie udało się utrwalić cache osadzeń (%s): %s", self._index_path, exc
                )

    def rank(self, query: str, candidates: Sequence[Note]) -> list[str]:
        """Zwróć id kandydatów malejąco po cosinusie osadzeń; odetnij poniżej ``min_similarity``."""
        if not candidates:
            return []
        vectors: list[Any] = []
        pending: list[tuple[str, str, int, bytes, str, str]] = []
        updated_at = datetime.now(UTC).isoformat()
        for note in candidates:
            text = self._passage_text(note)
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            cached = self._cached_vector(note.id, content_hash)
            if cached is not None:
                vectors.append(cached)
                continue
            arr = self._embed(self._passage_prefix() + text)
            vectors.append(arr)
            pending.append(
                (
                    note.id,
                    self._model_name,
                    int(arr.shape[0]),
                    arr.tobytes(),
                    content_hash,
                    updated_at,
                )
            )
        self._store_vectors(pending)
        matrix = self._np.vstack(vectors)
        q = self._embed(self._query_prefix() + query)
        sims = matrix @ q  # wektory znormalizowane L2 → iloczyn skalarny = cosinus
        order = self._np.argsort(-sims)  # malejąco po podobieństwie
        return [candidates[int(i)].id for i in order if float(sims[int(i)]) >= self._min_similarity]
