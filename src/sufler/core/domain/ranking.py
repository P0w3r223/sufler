"""Czysty ranking leksykalny (retrieval Fazy 3 / ADR 0023) — Okapi BM25 nad workami lematów.

Logika DOMENOWA bez I/O i bez zależności aplikacyjnych: funkcje deterministyczne (bez zegara,
bez losowości), więc testowalne wprost. Zastępuje naiwną heurystykę pokrycia słów z
``services.py::_score`` prawdziwym BM25 — przy tak małym korpusie (kilkanaście notatek) liczymy go
na bieżąco z lematyzowanych pól (waga tytułu realizowana przez POWTÓRZENIE jego lematów w worku).

BM25 dobiera trafność po TF (częstość termu w dokumencie, z nasyceniem ``k1``) i IDF (rzadkość termu
w korpusie), z normalizacją długości dokumentu (``b``) — dzięki temu długa notatka nie wygrywa samą
objętością, a rzadkie słowo zapytania waży więcej niż częste.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

_K1 = 1.5  # nasycenie częstości termu (typowy zakres 1.2–2.0)
_B = 0.75  # siła normalizacji długości dokumentu (0 = brak, 1 = pełna)


def bm25_rank(
    query_terms: Sequence[str],
    docs: Mapping[str, Sequence[str]],
    *,
    k1: float = _K1,
    b: float = _B,
) -> dict[str, float]:
    """Policz wynik BM25 każdego dokumentu wobec zapytania; zwróć ``{id: wynik}`` dla wyników > 0.

    ``query_terms`` to lematy zapytania; ``docs`` mapuje id → worek lematów dokumentu (pola już
    zważone przez powtórzenie). Dokumenty bez żadnego termu zapytania są pomijane (wynik 0), tak jak
    dawny scorer pomijał notatki bez trafień. Kolejność sortowania rozstrzyga wołający (po wyniku, a
    przy remisie np. po dacie) — tu zwracamy same wyniki.
    """
    if not query_terms or not docs:
        return {}

    unique_terms = set(query_terms)
    counts: dict[str, Counter[str]] = {doc_id: Counter(bag) for doc_id, bag in docs.items()}
    lengths = {doc_id: sum(c.values()) for doc_id, c in counts.items()}
    total = len(docs)
    avgdl = (sum(lengths.values()) / total) or 1.0

    # IDF liczymy raz na term (rzadkość w korpusie). Wariant z ``+1`` w log jest nieujemny nawet,
    # gdy term jest w ponad połowie dokumentów — bezpieczne przy maleńkim korpusie.
    idf = {
        term: math.log(1.0 + (total - df + 0.5) / (df + 0.5))
        for term in unique_terms
        for df in (sum(1 for c in counts.values() if c.get(term)),)
    }

    scores: dict[str, float] = {}
    for doc_id, count in counts.items():
        dl = lengths[doc_id] or 1
        norm = k1 * (1.0 - b + b * dl / avgdl)
        score = 0.0
        for term in unique_terms:
            tf = count.get(term, 0)
            if tf:
                score += idf[term] * (tf * (k1 + 1.0)) / (tf + norm)
        if score > 0.0:
            scores[doc_id] = score
    return scores


def reciprocal_rank_fusion(*rankings: Sequence[str], k: int = 60) -> list[str]:
    """Połącz kilka list ID (malejąco po trafności) w jedną przez RRF: ``score = Σ 1/(k+ranga)``.

    Ranga-zależne (ignoruje surowe wyniki), więc łączy niekompatybilne skale (BM25 vs cosinus) bez
    normalizacji — bezpieczny default fuzji (ADR 0023, Faza B). Zachowane tu, bo to czysta funkcja
    domenowa; warstwa dense wpina ją, gdy bramka mikro-evalu ją włączy.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(fused, key=lambda doc_id: fused[doc_id], reverse=True)
