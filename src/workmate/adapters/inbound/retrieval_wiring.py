"""Wspólny wiring lematyzatora retrievalu (ADR 0023) — jedno źródło budowy dla obu drzwi.

Buduje ``Lemmatizer`` (adapter ``simplemma``) albo ``None`` z łagodną degradacją: wyłączony w
konfiguracji lub brak extra ``retrieval`` → ``None`` → ``NotesService`` używa dawnego rankingu
podłańcuchowego. Import adaptera jest LENIWY, żeby serwer MCP/testy bez extra się nie wywróciły.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workmate.config import RetrievalSettings
    from workmate.core.ports.text import Lemmatizer

logger = logging.getLogger(__name__)


def build_lemmatizer(settings: RetrievalSettings) -> Lemmatizer | None:
    """Zbuduj lematyzator PL albo ``None`` (wyłączony / brak extra ``retrieval``)."""
    if not settings.lemmatize:
        return None
    try:
        from workmate.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer
    except ImportError:
        logger.info(
            "Extra 'retrieval' (simplemma) niedostępny — wyszukiwanie bez lematyzacji PL. "
            "Zainstaluj: uv sync --extra retrieval"
        )
        return None
    lemmatizer = SimplemmaLemmatizer(lang=settings.lang)
    # Próba na starcie: zła strefa językowa (np. błędny WORKMATE_RETRIEVAL_LANG) ma DEGRADOWAĆ do
    # wyszukiwania bez lematyzacji, a nie wywracać każde search_notes (fail-fast w wiringu).
    try:
        lemmatizer.lemmatize("test")
    except Exception:  # noqa: BLE001 — dowolny błąd simplemma = zła konfiguracja, degradujemy
        logger.warning(
            "Lematyzator (lang=%r) nie działa — wyszukiwanie bez lematyzacji PL.", settings.lang
        )
        return None
    return lemmatizer
