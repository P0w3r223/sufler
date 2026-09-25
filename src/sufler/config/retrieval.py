"""Ustawienia wyszukiwania po bazie wiedzy (BM25 + opcjonalna ścieżka gęsta)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sufler.config._env import (
    _bool_from_env,
    _float_from_env,
    _int_from_env,
    _path_from_env,
)

# Domyślny magazyn wektorów retrievalu dense (ADR 0039, Faza B): POZA repo i data/ — dane
# operacyjne (osadzenia pochodne notatek), nie baza wiedzy. Env: SUFLER_RETRIEVAL_INDEX.
_DEFAULT_RETRIEVAL_INDEX = Path.home() / ".sufler" / "retrieval_index.db"


@dataclass(frozen=True)
class RetrievalSettings:
    """Konfiguracja retrievalu notatek (ADR 0023 Faza A + ADR 0039 Faza B) — ranking wyszukiwania.

    ``lemmatize`` włącza lematyzację PL (BM25 nad lematami, odporność na fleksję) — domyślnie ON,
    o ile dostępny extra ``retrieval`` (``simplemma``); brak extra degraduje do dawnego rankingu
    podłańcuchowego (wiring łapie ``ImportError``). Wyłączalne env do debugowania/porównań.

    Warstwa DENSE (ADR 0039, Faza B) jest za bramką mikro-evalu i domyślnie WYŁĄCZONA
    (``enable_dense=False``). Włączona (tylko na długożyjących drzwiach, nigdy MCP stdio) dokłada
    osadzenia semantyczne fuzowane z BM25 przez RRF; wymaga extra ``retrieval-dense`` (fastembed).
    Ma sens tylko z ``lemmatize`` (fuzja żyje w gałęzi BM25) — wiring drzwi buduje ranker dense
    wyłącznie obok lematyzatora. ``dense_model`` to nazwa modelu osadzeń (fastembed), ``index_path``
    — magazyn wektorów (SQLite, poza repo/data), ``rrf_k`` — dyskonto rang RRF, ``dense_top_n``
    (0 = całość) opcjonalnie przycina ogon rankingu dense; ``dense_min_similarity`` (0.0) odcina
    notatki poniżej progu cosinusa.
    """

    lemmatize: bool = True
    lang: str = "pl"
    enable_dense: bool = False
    dense_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    index_path: Path = _DEFAULT_RETRIEVAL_INDEX
    rrf_k: int = 60
    dense_top_n: int = 0
    dense_min_similarity: float = 0.0

    @classmethod
    def from_env(cls) -> RetrievalSettings:
        return cls(
            lemmatize=_bool_from_env("SUFLER_RETRIEVAL_LEMMATIZE", default=True),
            lang=os.environ.get("SUFLER_RETRIEVAL_LANG", "pl"),
            enable_dense=_bool_from_env("SUFLER_RETRIEVAL_ENABLE_DENSE", default=False),
            dense_model=os.environ.get(
                "SUFLER_RETRIEVAL_DENSE_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            index_path=_path_from_env("SUFLER_RETRIEVAL_INDEX", _DEFAULT_RETRIEVAL_INDEX),
            rrf_k=_int_from_env("SUFLER_RETRIEVAL_RRF_K", 60),
            dense_top_n=_int_from_env("SUFLER_RETRIEVAL_DENSE_TOP_N", 0),
            dense_min_similarity=_float_from_env("SUFLER_RETRIEVAL_DENSE_MIN_SIM", 0.0),
        )

    def validate(self) -> None:
        """Twardy błąd startu przy wartościach, które wywracają ranking dopiero przy użyciu.

        ``rrf_k`` wchodzi do ``1.0 / (k + rank)``, a ``rank`` startuje od zera — ``0`` (wiarygodna
        wartość dla operatora chcącego „wyłączyć dyskonto") albo liczba ujemna dawały
        ``ZeroDivisionError`` w środku ``NotesService.search_notes``, czyli surowy wyjątek zamiast
        błędu narzędzia. Zapalnik jest odłożony: fuzja biegnie dopiero przy ``enable_dense=true``,
        a ta bramka jest domyślnie wyłączona.
        """
        if self.rrf_k < 1:
            raise ValueError(f"SUFLER_RETRIEVAL_RRF_K musi być >= 1, jest: {self.rrf_k}.")
        if self.dense_top_n < 0:
            raise ValueError(
                f"SUFLER_RETRIEVAL_DENSE_TOP_N musi być >= 0 (0 = całość), "
                f"jest: {self.dense_top_n}."
            )
        if not -1.0 <= self.dense_min_similarity <= 1.0:
            raise ValueError(
                "SUFLER_RETRIEVAL_DENSE_MIN_SIM to próg cosinusa, więc musi być w zakresie "
                f"-1.0..1.0, jest: {self.dense_min_similarity}."
            )
        if not self.lang.strip():
            raise ValueError("SUFLER_RETRIEVAL_LANG nie może być puste.")
