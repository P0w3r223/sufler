"""Testy czystej konwersji tekst ↔ ADF (ADR 0033) — most Jira Cloud."""

from __future__ import annotations

import pytest

from workmate.core.domain.adf import adf_to_text, text_to_adf


def test_text_to_adf_single_line():
    doc = text_to_adf("Cześć świecie")
    assert doc == {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Cześć świecie"}]}],
    }


def test_text_to_adf_empty_gives_empty_paragraph():
    doc = text_to_adf("")
    assert doc["type"] == "doc"
    assert doc["content"] == [{"type": "paragraph", "content": []}]


def test_text_to_adf_multiline_makes_paragraph_per_line():
    doc = text_to_adf("a\nb")
    assert [p["content"] for p in doc["content"]] == [
        [{"type": "text", "text": "a"}],
        [{"type": "text", "text": "b"}],
    ]


@pytest.mark.parametrize(
    "text",
    ["", "jedna linia", "a\nb\nc", "a\n\nb", "śródlinijna\nprzerwa", "polskie ąćęł znaki"],
)
def test_round_trip_text_adf_text(text):
    # Round-trip zachowuje tekst bez skrajnych ``\n`` (te są celowo przycinane dla czystego wyniku).
    assert adf_to_text(text_to_adf(text)) == text


def test_adf_to_text_trims_edge_newlines():
    # Kontrakt czystego wyniku: wiodące/kończące złamania linii są usuwane (śródlinijne zostają).
    assert adf_to_text(text_to_adf("\nwiodąca")) == "wiodąca"
    assert adf_to_text(text_to_adf("kończąca\n")) == "kończąca"


def test_adf_to_text_passes_through_plain_string():
    assert adf_to_text("już tekst") == "już tekst"


@pytest.mark.parametrize("bad", [None, 123, [], {"type": "doc"}])
def test_adf_to_text_robust_on_non_document(bad):
    assert adf_to_text(bad) == ""


def test_adf_to_text_flattens_hard_break():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "b"},
                ],
            }
        ],
    }
    assert adf_to_text(doc) == "a\nb"


def test_adf_to_text_renders_mention_and_emoji_from_attrs():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "mention", "attrs": {"text": "@Ania"}},
                    {"type": "text", "text": " "},
                    {"type": "emoji", "attrs": {"shortName": ":tada:"}},
                ],
            }
        ],
    }
    assert adf_to_text(doc) == "@Ania :tada:"


def test_adf_to_text_flattens_bullet_list():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "raz"}]}
                        ],
                    },
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "dwa"}]}
                        ],
                    },
                ],
            }
        ],
    }
    assert adf_to_text(doc) == "raz\ndwa"


def test_adf_to_text_nested_block_with_multiple_paragraphs_single_newline():
    # Kontener BLOKOWY z DWOMA akapitami (blockquote) rozdziela je JEDNYM ``\n`` — bez podwójnych
    # z zagnieżdżenia (test bulletList ma po jednym akapicie na pozycję, więc nie złapałby regresji
    # „akapit dokłada własny ``\n``"). Broni inwariantu z docstringu modułu.
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "blockquote",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "pierwszy"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "drugi"}]},
                ],
            }
        ],
    }
    assert adf_to_text(doc) == "pierwszy\ndrugi"


def test_adf_to_text_inline_container_codeblock_joins_children_without_separator():
    # ``codeBlock`` jest kontenerem INLINE: dzieci (tekst + hardBreak) sklejane bez separatora, a
    # nowe linie pochodzą wyłącznie z ``hardBreak`` — nie z blokowego łączenia.
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "codeBlock",
                "content": [
                    {"type": "text", "text": "line1"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "line2"},
                ],
            }
        ],
    }
    assert adf_to_text(doc) == "line1\nline2"


def test_adf_to_text_paragraph_missing_content_key_yields_empty():
    # Węzeł kontenera bez klucza ``content`` (np. pusty akapit z serwera) → "" bez wyjątku.
    doc = {"type": "doc", "content": [{"type": "paragraph"}, {"type": "paragraph"}]}
    assert adf_to_text(doc) == ""


def test_adf_to_text_ignores_unknown_node_types():
    doc = {
        "type": "doc",
        "content": [
            {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "x"}}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "widoczne"}]},
        ],
    }
    assert adf_to_text(doc) == "widoczne"
