"""Test handlera Teams przez atrapę TurnContext/Activity — bez SDK, bez Azure.

Handler (``make_on_message``) jest kaczo-typowany, więc testujemy go strukturalnymi
atrapami (jak atrapy repo w ``conftest.py``), bez zainstalowanego extra ``teams``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from workmate.adapters.inbound.responder import EchoResponder, InboundMessage
from workmate.adapters.inbound.teams.bot import make_on_message


@dataclass
class _FakeActivity:
    text: str = ""
    from_property: object = None
    conversation: object = None
    recipient: object = None
    entities: object = None


@dataclass
class _FakeAccount:
    """Atrapa ``ChannelAccount``: ``id`` jest identyfikatorem KANAŁU (``29:…``), nie AAD."""

    name: str = ""
    id: str = ""
    aad_object_id: str | None = None


def _capture_message() -> tuple[dict[str, object], type]:
    """Responder zapamiętujący całą ``InboundMessage`` — jedno miejsce zamiast czterech atrap."""
    seen: dict[str, object] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            seen["message"] = message
            return "ok"

    return seen, _Capture


class _FakeContext:
    """Strukturalna atrapa TurnContext: zapamiętuje wysłane odpowiedzi."""

    def __init__(self, activity: _FakeActivity) -> None:
        self.activity = activity
        self.sent: list[str] = []

    async def send_activity(self, text: str) -> None:
        self.sent.append(text)


def test_handler_echoes_received_note():
    ctx = _FakeContext(_FakeActivity(text="ustalenia z mpwik"))

    asyncio.run(make_on_message(EchoResponder())(ctx))

    assert ctx.sent == ["Odebrałem notatkę: ustalenia z mpwik"]


def test_handler_uses_injected_responder():
    """Szew działa: handler zwraca dokładnie to, co wstrzyknięty responder."""

    class _Fixed:
        async def respond(self, message: InboundMessage) -> str:
            return f"[{message.text}]"

    ctx = _FakeContext(_FakeActivity(text="hej"))

    asyncio.run(make_on_message(_Fixed())(ctx))

    assert ctx.sent == ["[hej]"]


def test_handler_treats_none_text_as_empty():
    """Aktywności nie-tekstowe niosą text=None — nie odsyłamy 'None'."""
    ctx = _FakeContext(_FakeActivity(text=None))

    asyncio.run(make_on_message(EchoResponder())(ctx))

    assert ctx.sent == ["Odebrałem notatkę: "]


def test_handler_extracts_attribution_from_activity():
    @dataclass
    class _Sender:
        name: str = ""
        id: str = ""

    @dataclass
    class _Conv:
        id: str = ""

    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender"] = message.sender
            captured["conversation_id"] = message.conversation_id
            return "ok"

    ctx = _FakeContext(
        _FakeActivity(text="x", from_property=_Sender(name="Anna"), conversation=_Conv(id="c1"))
    )

    asyncio.run(make_on_message(_Capture())(ctx))

    assert captured == {"sender": "Anna", "conversation_id": "c1"}


@pytest.mark.parametrize(
    ("name", "ident", "expected"),
    [("Anna", "id-1", "Anna"), ("", "id-1", "id-1"), ("", "", "")],
)
def test_sender_attribution_falls_back_name_then_id(name, ident, expected):
    """_sender_of: name → id → "" (kolejność ważna dla przyszłego save_note)."""

    @dataclass
    class _Sender:
        name: str = ""
        id: str = ""

    captured: dict[str, str] = {}

    class _Capture:
        async def respond(self, message: InboundMessage) -> str:
            captured["sender"] = message.sender
            return "ok"

    ctx = _FakeContext(_FakeActivity(text="x", from_property=_Sender(name=name, id=ident)))

    asyncio.run(make_on_message(_Capture())(ctx))

    assert captured["sender"] == expected


# --- tożsamość nadawcy (ADR 0042/0062/0063) ---------------------------------


def test_sender_id_carries_aad_object_id():
    """Bramki sender-keyed dostają AAD id — ten sam identyfikator, co w mapie tożsamości."""
    seen, capture = _capture_message()
    ctx = _FakeContext(
        _FakeActivity(
            text="co z mpwik?",
            from_property=_FakeAccount(name="Anna", id="29:kanałowe", aad_object_id="aad-anna"),
        )
    )

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.sender_id == "aad-anna"
    assert message.sender == "Anna"  # atrybucja zostaje przy nazwie


@pytest.mark.parametrize(
    "sender",
    [
        _FakeAccount(name="Gość", id="29:kanałowe", aad_object_id=None),
        _FakeAccount(name="Gość", id="29:kanałowe", aad_object_id=""),
        _FakeAccount(name="Gość", id="29:kanałowe", aad_object_id="   "),
        None,  # aktywność systemowa — w ogóle bez nadawcy
    ],
)
def test_sender_id_is_empty_without_aad_object_id(sender):
    """Fail-closed: brak AAD id NIE degraduje do id kanałowego (``29:…``) ani do nazwy.

    Podstawienie identyfikatora Bot Framework dałoby tożsamość FAŁSZYWĄ — nie rozwiąże się
    w mapie, więc bramka i tak by odmówiła, ale audyt i etykieta zaufania kłamałyby o nadawcy.
    """
    seen, capture = _capture_message()
    ctx = _FakeContext(_FakeActivity(text="x", from_property=sender))

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.sender_id == ""


# --- @wzmianka bota (ADR 0048/0051/0052) ------------------------------------


@dataclass
class _FakeMention:
    type: str = "mention"
    mentioned: object = None


def test_mentions_bot_is_true_when_entity_points_at_recipient():
    seen, capture = _capture_message()
    ctx = _FakeContext(
        _FakeActivity(
            text="<at>WorkMate</at> ogarnij mnie na mpwik",
            recipient=_FakeAccount(id="28:bot-app-id"),
            entities=[_FakeMention(mentioned=_FakeAccount(id="28:bot-app-id"))],
        )
    )

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.mentions_bot is True


@pytest.mark.parametrize(
    ("recipient", "entities"),
    [
        # wzmianka celuje w INNEGO uczestnika
        (_FakeAccount(id="28:bot"), [_FakeMention(mentioned=_FakeAccount(id="29:ktoś-inny"))]),
        # encja innego rodzaju (clientInfo) — nie wzmianka
        (_FakeAccount(id="28:bot"), [_FakeMention(type="clientInfo")]),
        (_FakeAccount(id="28:bot"), []),  # zwykła wiadomość bez wzmianek
        (_FakeAccount(id="28:bot"), None),  # kanał, który nie dokłada encji
        # brak ``recipient`` — nie ma z czym porównać, więc wyzwalacz milczy
        (None, [_FakeMention(mentioned=_FakeAccount(id="28:bot"))]),
    ],
)
def test_mentions_bot_is_false_without_a_mention_of_this_bot(recipient, entities):
    seen, capture = _capture_message()
    ctx = _FakeContext(_FakeActivity(text="x", recipient=recipient, entities=entities))

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.mentions_bot is False


def test_mentions_bot_reads_entities_deserialized_as_dicts():
    """SDK bywa oszczędne: nierozpoznana encja wraca słownikiem, nie modelem — oba czytamy."""
    seen, capture = _capture_message()
    ctx = _FakeContext(
        _FakeActivity(
            text="<at>WorkMate</at> co słychać",
            recipient=_FakeAccount(id="28:bot-app-id"),
            entities=[{"type": "mention", "mentioned": {"id": "28:bot-app-id"}}],
        )
    )

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.mentions_bot is True


def test_mentions_bot_reads_entities_from_additional_properties():
    """Generyczne ``Entity`` trzyma nierozpoznane pola w ``additional_properties``."""

    class _RawEntity:
        type = "mention"
        additional_properties = {"mentioned": {"id": "28:bot-app-id"}}

    seen, capture = _capture_message()
    ctx = _FakeContext(
        _FakeActivity(
            text="<at>WorkMate</at> co słychać",
            recipient=_FakeAccount(id="28:bot-app-id"),
            entities=[_RawEntity()],
        )
    )

    asyncio.run(make_on_message(capture())(ctx))

    message = seen["message"]
    assert isinstance(message, InboundMessage)
    assert message.mentions_bot is True
