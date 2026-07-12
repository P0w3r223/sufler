"""Testy portu LLM — załączniki multimodalne w słowniku domenowym (ADR 0016).

Czyste dataklasy i helpery serializacji (``Attachment``, ``attachment_to_row``/
``attachment_from_row``, ``UserText.attachments``). Bez SDK, bez sieci — sprawdzamy
neutralną formę (NIE bloki Anthropic) i round-trip do wiersza ``blocks_json``.
"""
from __future__ import annotations

from workmate.core.ports.llm import (
    Attachment,
    UserText,
    attachment_from_row,
    attachment_to_row,
)


def test_user_text_defaults_to_no_attachments_for_backward_compat():
    """``attachments`` jest addytywne (domyślnie puste) — stare wywołania bez zmian."""
    assert UserText("pytanie").attachments == ()


def test_user_text_carries_attachments_tuple():
    att = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    entry = UserText("opis", (att,))

    assert entry.text == "opis"
    assert entry.attachments == (att,)


def test_attachment_to_row_emits_neutral_dict_with_all_fields():
    """Wiersz to forma NEUTRALNA (nie blok Anthropic) — jedno źródło kształtu, 5 pól."""
    att = Attachment("document", "application/pdf", "umowa.pdf", data_base64="QkFTRTY0")

    row = attachment_to_row(att)

    assert row == {
        "kind": "document",
        "media_type": "application/pdf",
        "name": "umowa.pdf",
        "data_base64": "QkFTRTY0",
        "text": "",
    }


def test_attachment_round_trips_through_row_for_image():
    att = Attachment("image", "image/jpeg", "foto.jpg", data_base64="/9j/PQ==")

    assert attachment_from_row(attachment_to_row(att)) == att


def test_attachment_round_trips_through_row_for_extracted_docx_text():
    """.docx po ekstrakcji nosi tekst (nie base64) — round-trip zachowuje treść."""
    att = Attachment("text", "text/plain", "notatka.docx", text="Akapit\nWiersz | tabeli")

    assert attachment_from_row(attachment_to_row(att)) == att


def test_attachment_from_row_defaults_missing_keys_to_empty_strings():
    """Wiersz legacy/niepełny → puste pola (odczyt degraduje, nie rzuca)."""
    att = attachment_from_row({"kind": "image", "media_type": "image/png"})

    assert att == Attachment("image", "image/png", "", data_base64="", text="")


def test_attachment_from_row_ignores_unknown_keys():
    row = {"kind": "text", "name": "x.docx", "text": "abc", "spurious": "ignore-me"}

    att = attachment_from_row(row)

    assert att == Attachment("text", "", "x.docx", text="abc")
    assert not hasattr(att, "spurious")
