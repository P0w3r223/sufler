"""Porty (protokoły strukturalne) — kontrakty, na które rdzeń i testy mogą liczyć bez SDK.

``LlmClient`` odwzorowuje minimalną powierzchnię potrzebną warstwie LLM: jedna metoda
``complete``. Dzięki wstrzykiwaniu tego portu opis prozą jest testowalny na atrapie w pamięci,
bez sieci i bez klucza API (wzorzec ``Powiadomienia_teams/agent/interpreter.py``).
"""

from __future__ import annotations

from typing import Protocol


class LlmClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...
