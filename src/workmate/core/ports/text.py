"""Port lematyzacji tekstu (retrieval Fazy 3 / ADR 0023) — kontrakt normalizacji językowej.

``Protocol`` jak pozostałe porty — dowolna implementacja o zgodnej sygnaturze jest akceptowana
bez dziedziczenia. Domyślny adapter: ``SimplemmaLemmatizer`` (słownikowo-regułowa lematyzacja PL,
extra ``retrieval``); w testach — atrapa w pamięci. Cel: sprowadzić polską fleksję do lematów, żeby
ranking leksykalny (BM25 w ``core/domain/ranking.py``) trafiał „integracji"↔„integracja".

Lematyzacja jest DETERMINISTYCZNA i bez I/O w kontrakcie (adapter może leniwie ładować dane
językowe, ale nie woła zegara/sieci) — więc rdzeń pozostaje testowalny na atrapie.
"""
from __future__ import annotations

from typing import Protocol


class Lemmatizer(Protocol):
    """Sprowadź tekst do listy LEMATÓW (małe litery, bez interpunkcji), w kolejności wystąpienia."""

    def lemmatize(self, text: str) -> list[str]:
        """Zwróć lematy słów z ``text`` — worek tokenów po normalizacji fleksji (może być pusty)."""
        ...
