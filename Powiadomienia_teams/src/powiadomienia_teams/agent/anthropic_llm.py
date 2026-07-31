"""Adapter LLM oparty o Claude API (Anthropic). Import ``anthropic`` jest LENIWY.

Wymaga extra ``agent`` (``anthropic``). Klucz API to sekret — czytany z konfiguracji
(``Settings.anthropic_api_key``), nigdy nie logowany.
"""

from __future__ import annotations

from typing import Any

# Haiku 4.5 — szybka, tania ekstrakcja JSON (interpretacja odpowiedzi to proste zadanie
# strukturalne). Konfigurowalne przez POWIADOMIENIA_LLM_MODEL, gdyby potrzeba więcej mocy.
_DEFAULT_MODEL = "claude-haiku-4-5"
_TIMEOUT_S = 30.0  # patrz `_get_client` — chroni pętlę nasłuchu przed zawieszeniem


class AnthropicLlm:
    """Implementacja ``interpreter.LlmClient`` wołająca Claude Messages API.

    Klient Anthropic tworzony jest RAZ (leniwie) i reużywany — unika kosztu budowy puli
    połączeń przy każdej odpowiedzi, co przyspiesza kolejne interpretacje w listenerze.
    """

    def __init__(
        self, api_key: str, *, model: str = _DEFAULT_MODEL, max_tokens: int = 1024
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            # Timeout JAWNIE: domyślne 10 min SDK × 2 ponowienia to do ~30 min zegara ściennego
            # wewnątrz obsługi JEDNEJ odpowiedzi. Przez ten czas nasłuch nie obsługuje nikogo
            # innego, a proces wygląda na zdrowy — najgorszy możliwy stan dla pracy bezobsługowej.
            self._client = anthropic.Anthropic(
                api_key=self._api_key, timeout=_TIMEOUT_S, max_retries=1
            )
        return self._client

    def complete(self, system: str, user: str) -> str:
        message = self._get_client().messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            # Deterministyczna ekstrakcja JSON — bez myślenia, żeby nie zjadało budżetu
            # max_tokens (na Sonnet 5 pominięcie thinking uruchomiłoby myślenie adaptacyjne).
            thinking={"type": "disabled"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
