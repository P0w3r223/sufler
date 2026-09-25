"""Wspólny wiring lematyzatora retrievalu (ADR 0023) — jedno źródło budowy dla obu drzwi.

Buduje ``Lemmatizer`` (adapter ``simplemma``) albo ``None`` z łagodną degradacją: wyłączony w
konfiguracji lub brak extra ``retrieval`` → ``None`` → ``NotesService`` używa dawnego rankingu
podłańcuchowego. Import adaptera jest LENIWY, żeby serwer MCP/testy bez extra się nie wywróciły.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sufler.config import RetrievalSettings
    from sufler.core.ports.text import Lemmatizer, SemanticRanker

logger = logging.getLogger(__name__)


def build_lemmatizer(settings: RetrievalSettings) -> Lemmatizer | None:
    """Zbuduj lematyzator PL albo ``None`` (wyłączony / brak extra ``retrieval``)."""
    if not settings.lemmatize:
        return None
    try:
        from sufler.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer
    except ImportError:
        logger.info(
            "Extra 'retrieval' (simplemma) niedostępny — wyszukiwanie bez lematyzacji PL. "
            "Zainstaluj: uv sync --extra retrieval"
        )
        return None
    lemmatizer = SimplemmaLemmatizer(lang=settings.lang)
    # Próba na starcie: zła strefa językowa (np. błędny SUFLER_RETRIEVAL_LANG) ma DEGRADOWAĆ do
    # wyszukiwania bez lematyzacji, a nie wywracać każde search_notes (fail-fast w wiringu).
    try:
        lemmatizer.lemmatize("test")
    except Exception:  # noqa: BLE001 — dowolny błąd simplemma = zła konfiguracja, degradujemy
        logger.warning(
            "Lematyzator (lang=%r) nie działa — wyszukiwanie bez lematyzacji PL.", settings.lang
        )
        return None
    return lemmatizer


def build_semantic_ranker(settings: RetrievalSettings) -> SemanticRanker | None:
    """Zbuduj ranker semantyczny (dense) albo ``None`` (wyłączony / brak extra ``retrieval-dense``).

    Analogicznie do ``build_lemmatizer``: łagodna degradacja do samego BM25. Import adaptera i
    fastembed jest LENIWY; ``warmup`` na starcie wymusza załadowanie modelu, żeby zła konfiguracja
    (brak extra, brak modelu, brak sieci przy pierwszym pobraniu) degradowała TU — fail-fast w
    wiringu — a nie przy każdym ``search_notes``.
    """
    if not settings.enable_dense:
        return None
    from sufler.adapters.outbound.onnx_semantic_ranker import OnnxSemanticRanker

    try:
        ranker = OnnxSemanticRanker(
            model=settings.dense_model,
            index_path=settings.index_path,
            min_similarity=settings.dense_min_similarity,
        )
        ranker.warmup()
    except ImportError:
        logger.info(
            "Extra 'retrieval-dense' (fastembed/onnxruntime) niedostępny — wyszukiwanie bez "
            "warstwy semantycznej. Zainstaluj: uv sync --extra retrieval-dense"
        )
        return None
    except Exception:  # noqa: BLE001 — dowolny błąd modelu/IO = degradacja do samego BM25
        logger.warning(
            "Warstwa semantyczna (model=%r) nie wystartowała — wyszukiwanie bez dense.",
            settings.dense_model,
            exc_info=True,
        )
        return None
    return ranker
