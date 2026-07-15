"""Adapter LLM oparty o Claude API (Anthropic). Import ``anthropic`` jest LENIWY.

Wymaga extra ``agent`` (``anthropic``). Klucz API to sekret — czytany z konfiguracji
(``Settings.anthropic_api_key``), nigdy nie logowany.
"""
from __future__ import annotations

_DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicLlm:
    """Implementacja ``interpreter.LlmClient`` wołająca Claude Messages API."""

    def __init__(
        self, api_key: str, *, model: str = _DEFAULT_MODEL, max_tokens: int = 1024
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            # Deterministyczna ekstrakcja JSON — bez myślenia, żeby nie zjadało budżetu
            # max_tokens (na Sonnet 5 pominięcie thinking uruchomiłoby myślenie adaptacyjne).
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            getattr(block, "text", "") for block in message.content
            if getattr(block, "type", "") == "text"
        )
