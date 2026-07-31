"""Testy mapowania słownik domenowy ⇆ wiadomości/bloki Anthropic (ADR 0011).

Testujemy CZYSTE funkcje adaptera (``_to_messages`` / ``_from_message``) — plumbing
round-tripu bloków, bez SDK i bez sieci. Realną akceptację ``signature`` przez żywe
Claude API weryfikuje osobny smoke na kluczu — atrapa tego nie sprawdzi (ADR 0011).
Sprawdzamy tu tylko, że bloki przechodzą VERBATIM i w oryginalnej kolejności.
"""

from __future__ import annotations

import types
from typing import Any

from workmate.adapters.outbound.anthropic_llm import (
    _attachment_block,
    _from_message,
    _mark_cache,
    _system_blocks,
    _thinking_config,
    _to_messages,
    _user_message,
)
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AssistantTurn,
    Attachment,
    RawTurn,
    ToolCall,
    ToolOutput,
    ToolResults,
    UserText,
)


class _FakeBlock:
    """Atrapa bloku treści Anthropic: ``model_dump`` oddaje przygotowany słownik."""

    def __init__(self, dump: dict[str, Any], **attrs: Any) -> None:
        self._dump = dump
        for key, value in attrs.items():
            setattr(self, key, value)

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return dict(self._dump)


class _FakeMessage:
    def __init__(self, content: list[_FakeBlock], stop_reason: str | None) -> None:
        self.content = content
        self.stop_reason = stop_reason


# --- _to_messages: wychodzące wiadomości ---------------------------------------


def test_to_messages_sends_raw_turn_blocks_verbatim_and_in_order():
    blocks = (
        {"type": "thinking", "thinking": "", "signature": "SIG=="},
        {"type": "text", "text": "cześć"},
        {"type": "tool_use", "id": "t1", "name": "search_notes", "input": {"q": "x"}},
    )
    messages = _to_messages([RawTurn("assistant", blocks)])

    assert messages == [{"role": "assistant", "content": list(blocks)}]
    # Kolejność bloków nietknięta (thinking MUSI poprzedzać tool_use).
    assert [b["type"] for b in messages[0]["content"]] == ["thinking", "text", "tool_use"]


def test_to_messages_strips_output_only_none_fields_from_replayed_blocks():
    """REGRESJA (wielotura): bloki z pamięci niosą pola WYJŚCIOWE z ``model_dump`` — np.

    ``parsed_output=None`` na bloku ``text`` — których wejściowy schemat API nie przyjmuje
    (400 „Extra inputs are not permitted"), co psuło każdą turę 2+. Odtwarzając, usuwamy
    pola ``None``; pola wymagane (``text``; ``thinking`` + ``signature``) zostają nietknięte.
    """
    blocks = (
        {"type": "thinking", "thinking": "", "signature": "SIG==", "cache_control": None},
        {"type": "text", "text": "cześć", "parsed_output": None, "citations": None},
    )

    messages = _to_messages([RawTurn("assistant", blocks)])

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "SIG=="},
                {"type": "text", "text": "cześć"},
            ],
        }
    ]


def test_to_messages_uses_blocks_when_assistant_turn_carries_them():
    blocks = (
        {"type": "thinking", "thinking": "", "signature": "ABC"},
        {"type": "text", "text": "odpowiedź"},
    )
    turn = AssistantTurn("odpowiedź", (), blocks)

    messages = _to_messages([turn])

    # Gdy AssistantTurn ma bloki — odsyłane VERBATIM (nie odtwarzane z text/tool_calls).
    assert messages == [{"role": "assistant", "content": list(blocks)}]


def test_to_messages_reconstructs_legacy_assistant_turn_without_blocks():
    turn = AssistantTurn("myślę", (ToolCall("t1", "search_notes", {"query": "mpwik"}),))

    messages = _to_messages([turn])

    assert messages == [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "myślę"},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "search_notes",
                    "input": {"query": "mpwik"},
                },
            ],
        }
    ]


def test_to_messages_maps_user_text_and_tool_results():
    entries = [
        UserText("pytanie"),
        ToolResults(
            (
                ToolOutput("t1", '{"count": 1}'),
                ToolOutput("t2", "błąd", is_error=True),
            )
        ),
    ]

    messages = _to_messages(entries)

    assert messages[0] == {"role": "user", "content": "pytanie"}
    tool_msg = messages[1]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": '{"count": 1}',
    }
    # is_error dokładane tylko dla błędu (nie zaśmiecamy sukcesu).
    assert "is_error" not in tool_msg["content"][0]
    assert tool_msg["content"][1]["is_error"] is True


# --- _user_message / _attachment_block: załączniki multimodalne (ADR 0016) -----


def test_user_message_without_attachments_is_bare_string_regression():
    """GOLDEN (regresja): bez załączników ``content`` to nadal goły string, nie lista."""
    msg = _user_message(UserText("pytanie"))

    assert msg == {"role": "user", "content": "pytanie"}
    # ta sama forma przez pełne _to_messages (żaden inny kształt się nie przemknął)
    assert _to_messages([UserText("pytanie")]) == [{"role": "user", "content": "pytanie"}]


def test_user_message_with_attachments_puts_media_blocks_before_caption():
    """Z załącznikami ``content`` to LISTA: bloki mediów PRZED tekstem (wymóg API dla PDF)."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    pdf = Attachment("document", "application/pdf", "umowa.pdf", data_base64="UERG")
    msg = _user_message(UserText("zobacz to", (img, pdf)))

    content = msg["content"]
    assert isinstance(content, list)
    assert [b["type"] for b in content] == ["image", "document", "text"]
    # Caption (blok tekstowy) idzie na KOŃCU, po blokach mediów.
    assert content[-1] == {"type": "text", "text": "zobacz to"}


def test_user_message_empty_caption_omits_empty_text_block():
    """Pusty caption → BRAK bloku ``text`` (API odrzuca pusty text)."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    msg = _user_message(UserText("", (img,)))

    content = msg["content"]
    assert [b["type"] for b in content] == ["image"]  # żadnego pustego text
    assert all(b.get("text") != "" for b in content if b["type"] == "text")


def test_attachment_block_image_carries_base64_source_and_media_type():
    block = _attachment_block(Attachment("image", "image/jpeg", "foto.jpg", data_base64="/9j/PQ=="))

    assert block == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9j/PQ=="},
    }


def test_attachment_block_document_is_pdf_base64():
    block = _attachment_block(
        Attachment("document", "application/pdf", "umowa.pdf", data_base64="UERG")
    )

    assert block == {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": "UERG"},
    }


def test_attachment_block_docx_text_becomes_text_block_with_file_label():
    """.docx (kind=text) → blok tekstowy z ETYKIETĄ pliku + wyekstrahowaną treścią."""
    block = _attachment_block(
        Attachment("text", "text/plain", "notatka.docx", text="Ustalenia\nZadanie | Termin")
    )

    assert block["type"] == "text"
    assert block["text"] == "[Plik: notatka.docx]\nUstalenia\nZadanie | Termin"
    assert "source" not in block  # tekst nie ma base64 source


def test_to_messages_user_text_with_attachments_produces_block_list():
    """Pełna ścieżka ``_to_messages``: UserText z załącznikiem → wiadomość user z listą bloków."""
    img = Attachment("image", "image/png", "z.png", data_base64="QUJD")
    messages = _to_messages([UserText("caption", (img,))])

    assert messages[0]["role"] == "user"
    assert [b["type"] for b in messages[0]["content"]] == ["image", "text"]


# --- _from_message: przychodząca odpowiedź -------------------------------------


def test_from_message_captures_all_blocks_verbatim_with_order_and_semantics():
    thinking = {"type": "thinking", "thinking": "", "signature": "SIG=="}
    text = {"type": "text", "text": "cześć"}
    tool_use = {
        "type": "tool_use",
        "id": "t1",
        "name": "search_notes",
        "input": {"query": "mpwik"},
    }
    message = _FakeMessage(
        content=[
            _FakeBlock(thinking, type="thinking", thinking=""),
            _FakeBlock(text, type="text", text="cześć"),
            _FakeBlock(
                tool_use,
                type="tool_use",
                id="t1",
                name="search_notes",
                input={"query": "mpwik"},
            ),
        ],
        stop_reason="tool_use",
    )

    response = _from_message(message)

    # Bloki VERBATIM i w kolejności (łącznie z thinking + signature).
    assert response.blocks == (thinking, text, tool_use)
    # text = konkatenacja bloków text (thinking WYKLUCZONY z projekcji tekstu).
    assert response.text == "cześć"
    assert response.thinking_text == ""  # display=omitted → pusty tekst thinking
    assert response.tool_calls == (ToolCall("t1", "search_notes", {"query": "mpwik"}),)
    assert response.stop_reason == "tool_use"


def test_from_message_projects_summarized_thinking_text_separately():
    """``display=summarized``: blok thinking niesie streszczenie → trafia do ``thinking_text``,
    ale NIE do ``text`` (projekcja tekstu wyklucza thinking — ADR 0011); bloki verbatim."""
    thinking = {"type": "thinking", "thinking": "Sprawdzam notatki mpwik.", "signature": "S"}
    text = {"type": "text", "text": "Gotowe."}
    message = _FakeMessage(
        content=[
            _FakeBlock(thinking, type="thinking", thinking="Sprawdzam notatki mpwik."),
            _FakeBlock(text, type="text", text="Gotowe."),
        ],
        stop_reason="end_turn",
    )

    response = _from_message(message)

    assert response.thinking_text == "Sprawdzam notatki mpwik."
    assert response.text == "Gotowe."  # thinking NIE wchodzi do projekcji tekstu
    assert response.blocks == (thinking, text)  # oba bloki verbatim (round-trip)


def test_thinking_config_adds_summarized_display_only_for_adaptive():
    assert _thinking_config("adaptive") == {"type": "adaptive", "display": "summarized"}
    # disabled: myślenia nie ma → bez ``display`` (zachowana konfigurowalność trybu).
    assert _thinking_config("disabled") == {"type": "disabled"}


# --- _from_message: przechwycenie realnego usage (Design 2) ---------------------


def test_from_message_captures_real_usage():
    message = _FakeMessage(
        content=[_FakeBlock({"type": "text", "text": "ok"}, type="text", text="ok")],
        stop_reason="end_turn",
    )
    message.usage = types.SimpleNamespace(  # type: ignore[attr-defined]
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=5,
        cache_creation_input_tokens=0,
    )

    response = _from_message(message)

    assert response.usage == TokenUsage(
        input_tokens=100, output_tokens=20, cache_read_input_tokens=5
    )


def test_from_message_usage_defaults_to_zero_when_absent():
    """Atrapa/legacy bez pola ``usage`` → puste ``TokenUsage`` (koszt 0), bez wywrotki."""
    message = _FakeMessage(
        content=[_FakeBlock({"type": "text", "text": "ok"}, type="text", text="ok")],
        stop_reason="end_turn",
    )

    response = _from_message(message)

    assert response.usage == TokenUsage()


def test_from_message_defaults_missing_stop_reason_to_empty_string():
    message = _FakeMessage(
        content=[_FakeBlock({"type": "text", "text": "ok"}, type="text", text="ok")],
        stop_reason=None,
    )

    response = _from_message(message)

    assert response.stop_reason == ""
    assert response.text == "ok"
    assert response.wants_tools is False


# --- prompt caching (#10): breakpointy na system i na ostatnim bloku historii ------------


def test_system_blocks_carries_ephemeral_cache_control():
    blocks = _system_blocks("jesteś WorkMate")
    assert blocks == [
        {"type": "text", "text": "jesteś WorkMate", "cache_control": {"type": "ephemeral"}}
    ]


def test_mark_cache_on_bare_string_content_wraps_and_marks():
    messages = [{"role": "user", "content": "pytanie bez załączników"}]

    marked = _mark_cache(messages)

    assert marked == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "pytanie bez załączników",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        }
    ]
    # Oryginał NIETKNIĘTY — cache_control nie może wyciekać do ConversationStore.
    assert messages == [{"role": "user", "content": "pytanie bez załączników"}]


def test_mark_cache_marks_last_block_of_list_content_leaves_others_untouched():
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "pierwsza tura"}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "wynik"},
                {"type": "text", "text": "ostatni blok"},
            ],
        },
    ]

    marked = _mark_cache(messages)

    assert marked[0] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "pierwsza tura"}],
    }
    assert marked[1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": "wynik",
    }
    assert marked[1]["content"][1] == {
        "type": "text",
        "text": "ostatni blok",
        "cache_control": {"type": "ephemeral"},
    }


def test_mark_cache_on_empty_messages_returns_empty():
    assert _mark_cache([]) == []


def test_mark_cache_on_message_with_empty_content_list_is_noop():
    messages = [{"role": "user", "content": []}]
    assert _mark_cache(messages) == [{"role": "user", "content": []}]
