"""Testy czystej funkcji ``_extract_json`` z adaptera streszczania spotkań.

Modele często owijają JSON w blok markdown (```` ```json … ``` ````), co wywracałoby
``model_validate_json``. ``_extract_json`` zdejmuje ten otok i zwraca surowy JSON.
Funkcja jest czysta (string → string) — testujemy ją wprost, bez extra ``agent`` i bez
klienta Claude. Samo ``summarize()`` (wymaga realnego klienta) należy do smoke-testów.
"""
from __future__ import annotations

import pytest

from workmate.adapters.outbound.anthropic_summarizer import _extract_json


def test_extract_json_passes_through_plain_json():
    text = '{"title": "Spotkanie", "participants": []}'

    assert _extract_json(text) == text


def test_extract_json_strips_wrapper_with_language_tag():
    wrapped = '```json\n{"title": "Spotkanie"}\n```'

    assert _extract_json(wrapped) == '{"title": "Spotkanie"}'


def test_extract_json_strips_wrapper_without_language_tag():
    wrapped = '```\n{"title": "Spotkanie"}\n```'

    assert _extract_json(wrapped) == '{"title": "Spotkanie"}'


def test_extract_json_trims_surrounding_whitespace_around_wrapper():
    wrapped = '\n\n  ```json\n{"a": 1}\n```  \n\n'

    assert _extract_json(wrapped) == '{"a": 1}'


def test_extract_json_preserves_multiline_json_inside_wrapper():
    inner = '{\n  "title": "Spotkanie",\n  "decisions": [\n    "d1",\n    "d2"\n  ]\n}'
    wrapped = f"```json\n{inner}\n```"

    assert _extract_json(wrapped) == inner


def test_extract_json_returns_plain_multiline_json_unchanged():
    text = '{\n  "title": "Spotkanie",\n  "participants": ["Anna"]\n}'

    assert _extract_json(text) == text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('   {"a": 1}   ', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('```JSON\n{"a": 1}\n```', '{"a": 1}'),
    ],
)
def test_extract_json_normalizes_various_wrappings(raw: str, expected: str):
    assert _extract_json(raw) == expected
