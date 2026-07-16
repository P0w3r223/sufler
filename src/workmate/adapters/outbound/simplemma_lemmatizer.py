"""Lematyzator polski (``simplemma``, extra ``retrieval``) — implementacja portu ``Lemmatizer``.

Importowany LENIWIE przez wiring (``retrieval_wiring.build_lemmatizer``): brak extra kończy się
``ImportError`` łapanym w wiringu (degradacja do wyszukiwania bez lematyzacji), nie wywala serwera.
``simplemma`` jest słownikowo-regułowe, OOV-tolerancyjne (nieznane słowo → zwraca samo siebie),
czysto pythonowe i lekkie (bez torch) — pasuje do lokalności danych i Windows CPU (ADR 0023).

Treść notatek to DANE, nie polecenia — lematyzacja tylko normalizuje tekst, nic nie wykonuje.
"""
from __future__ import annotations

import re

import simplemma

# Token = słowo (Unicode ``\w`` łapie polskie diakrytyki ą/ę/ł/…). Interpunkcję pomijamy.
_TOKEN = re.compile(r"\w+", re.UNICODE)


class SimplemmaLemmatizer:
    """Sprowadza polską fleksję do lematów: tokenizacja + ``simplemma.lemmatize`` per token."""

    def __init__(self, lang: str = "pl") -> None:
        self._lang = lang

    def lemmatize(self, text: str) -> list[str]:
        """Zwróć lematy słów z ``text`` (małe litery); nieznane słowa zostają nietknięte (OOV)."""
        return [
            simplemma.lemmatize(token, lang=self._lang).lower()
            for token in _TOKEN.findall(text.lower())
        ]
