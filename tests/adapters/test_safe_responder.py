"""Testy ``SafeResponder`` (odporność drzwi async) — łagodna degradacja przy błędach.

Owija dowolny ``Responder``: przy sukcesie przepuszcza odpowiedź, przy błędzie
domenowym (``SuflerError``, np. ``LLMError``) lub nieoczekiwanym wyjątku zwraca
przyjazny komunikat zamiast wywracać turę. Błąd trafia do logu (nie jest połykany).
"""

from __future__ import annotations

import asyncio
import logging

from sufler.adapters.inbound.responder import InboundMessage, SafeResponder
from sufler.core.errors import LLMError


class _OkResponder:
    async def respond(self, message: InboundMessage) -> str:
        return "prawdziwa odpowiedz"


class _FailingResponder:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def respond(self, message: InboundMessage) -> str:
        raise self._exc


def test_passes_through_successful_reply():
    responder = SafeResponder(_OkResponder())

    reply = asyncio.run(responder.respond(InboundMessage(text="czesc")))

    assert reply == "prawdziwa odpowiedz"


def test_domain_error_degrades_to_fallback_and_logs(caplog):
    # LLMError = przejsciowy blad Claude API (oczekiwany) -> komunikat, log WARNING.
    responder = SafeResponder(_FailingResponder(LLMError("Blad Claude API: 529")))

    with caplog.at_level(logging.WARNING):
        reply = asyncio.run(
            responder.respond(InboundMessage(text="czesc", conversation_id="chat1"))
        )

    assert reply == SafeResponder._FALLBACK
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_unexpected_error_degrades_to_fallback_and_logs_traceback(caplog):
    # Defekt kodu -> nie wywraca bota; log z tracebackiem (ERROR/exception).
    responder = SafeResponder(_FailingResponder(ValueError("nieoczekiwany defekt")))

    with caplog.at_level(logging.ERROR):
        reply = asyncio.run(responder.respond(InboundMessage(text="czesc")))

    assert reply == SafeResponder._FALLBACK
    assert any(record.levelno == logging.ERROR for record in caplog.records)


def test_custom_fallback_message_is_used():
    responder = SafeResponder(_FailingResponder(LLMError("x")), fallback="Awaria.")

    reply = asyncio.run(responder.respond(InboundMessage(text="czesc")))

    assert reply == "Awaria."
