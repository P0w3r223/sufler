"""Hierarchia wyjątków rdzenia.

Adaptery i warstwa aplikacji podnoszą podklasy ``WorkMateError`` z konkretnym
kontekstem. Granica (adapter MCP) łapie ``RepositoryError`` i zamienia go na
czytelny komunikat dla klienta, zamiast wywracać cały serwer — to realizacja
zasady "fail fast na błędnych danych, degraduj łagodnie na granicy".
"""
from __future__ import annotations


class WorkMateError(Exception):
    """Bazowy wyjątek wszystkich błędów domenowych WorkMate."""


class RepositoryError(WorkMateError):
    """Błąd odczytu/parsowania danych z magazynu (notatki, rejestr projektów)."""


class WriteError(WorkMateError):
    """Nie udało się zapisać notatki: nieznany projekt, pusty slug albo problem I/O.

    To błąd *oczekiwany* (najczęściej złe wejście wołającego). Granica MCP łapie
    go i zwraca ``{"error": ...}`` — analogicznie do ``RepositoryError`` przy
    odczycie. Wyjątki nieznane nadal wypływają jako defekt kodu.
    """


class LLMError(WorkMateError):
    """Błąd komunikacji z modelem (runtime agenta, Faza 2 / ADR 0008).

    Sieć, limit żądań, uwierzytelnianie albo niespodziewany kształt odpowiedzi
    Claude API. Podnoszony wyłącznie w adapterze outbound (``AnthropicLLMClient``),
    żeby wokabularz SDK nie wchodził do rdzenia; drzwi degradują łagodnie.
    """
