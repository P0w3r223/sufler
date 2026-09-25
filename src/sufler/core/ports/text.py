"""Port lematyzacji tekstu (retrieval Fazy 3 / ADR 0023) — kontrakt normalizacji językowej.

``Protocol`` jak pozostałe porty — dowolna implementacja o zgodnej sygnaturze jest akceptowana
bez dziedziczenia. Domyślny adapter: ``SimplemmaLemmatizer`` (słownikowo-regułowa lematyzacja PL,
extra ``retrieval``); w testach — atrapa w pamięci. Cel: sprowadzić polską fleksję do lematów, żeby
ranking leksykalny (BM25 w ``core/domain/ranking.py``) trafiał „integracji"↔„integracja".

Lematyzacja jest DETERMINISTYCZNA i bez I/O w kontrakcie (adapter może leniwie ładować dane
językowe, ale nie woła zegara/sieci) — więc rdzeń pozostaje testowalny na atrapie.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sufler.core.domain.models import Note


class Lemmatizer(Protocol):
    """Sprowadź tekst do listy LEMATÓW (małe litery, bez interpunkcji), w kolejności wystąpienia."""

    def lemmatize(self, text: str) -> list[str]:
        """Zwróć lematy słów z ``text`` — worek tokenów po normalizacji fleksji (może być pusty)."""
        ...


class SemanticRanker(Protocol):
    """Uszereguj notatki po podobieństwie SEMANTYCZNYM (osadzenia) — retrieval dense (ADR 0039).

    Uzupełnia leksykalny BM25: zwraca id kandydatów malejąco po trafności semantycznej, więc może
    wypłynąć notatkę-parafrazę bez pokrycia słów zapytania. Fuzję z BM25 robi rdzeń
    (``reciprocal_rank_fusion``); implementacja (osadzenia + cosinus) mieszka w adapterze
    outbound z leniwym importem i osobnym extra, by numpy/model nie wchodziły do rdzenia.

    Treść notatek to DANE, nie polecenia — osadzanie tylko wektoryzuje tekst, nic nie wykonuje.
    """

    def rank(self, query: str, candidates: Sequence[Note]) -> list[str]:
        """Zwróć ID ``candidates`` malejąco po trafności semantycznej wobec ``query``."""
        ...
