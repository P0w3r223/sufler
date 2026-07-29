"""Testy czystej funkcji ``_extract_json`` z adaptera streszczania spotkań.

Modele często owijają JSON w blok markdown (```` ```json … ``` ````), co wywracałoby
``model_validate_json``. ``_extract_json`` zdejmuje ten otok i zwraca surowy JSON.
Funkcja jest czysta (string → string) — testujemy ją wprost, bez extra ``agent`` i bez
klienta Claude. Samo ``summarize()`` (wymaga realnego klienta) należy do smoke-testów.
"""

from __future__ import annotations

import json

import pytest

from workmate.adapters.outbound.anthropic_summarizer import (
    _SUMMARY_MAX_TOKENS,
    _allowlist,
    _draft_system,
    _extract_json,
    _loads_lenient,
    _summary_max_tokens,
    _verify_system,
)
from workmate.core.domain.transcript import SpeakerRoster


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


def test_summary_max_tokens_caps_large_agent_budget():
    # Domyślny budżet konwersacji agenta (128000) wywracał summarize() na wymogu
    # strumieniowania SDK; sufit sprowadza go poniżej progu.
    assert _summary_max_tokens(128_000) == _SUMMARY_MAX_TOKENS


def test_summary_max_tokens_respects_smaller_agent_budget():
    # Gdy operator świadomie ustawił mniejszy budżet, nie podnosimy go do sufitu.
    assert _summary_max_tokens(2_000) == 2_000


def test_summary_max_tokens_stays_below_streaming_threshold():
    # Sufit musi zostać na tyle niski, by wywołanie nie-strumieniowe było dozwolone.
    assert _SUMMARY_MAX_TOKENS <= 8000


def test_loads_lenient_accepts_raw_control_char_in_string():
    # Model bywa nieszczelny: surowy newline w wartości. strict=False go toleruje.
    raw = '{"body": "linia1\nlinia2"}'
    assert _loads_lenient(raw)["body"] == "linia1\nlinia2"


def test_loads_lenient_where_strict_json_would_reject():
    # Ten sam wejściowy JSON wywraca strict parser — dowód, że sufit robi różnicę.
    raw = '{"a": "x\ny"}'
    with pytest.raises(ValueError):
        json.loads(raw)
    assert _loads_lenient(raw)["a"] == "x\ny"


def test_loads_lenient_still_rejects_truly_broken_json():
    # Lenient ≠ wszystkożerny: brak zamknięcia nadal jest błędem (łapany jako LLMError wyżej).
    with pytest.raises(ValueError):
        _loads_lenient('{"a": ')


def test_allowlist_lists_known_speakers_and_forbids_others():
    roster = SpeakerRoster(speakers=("Anna Kowalska", "Jan Nowak"), diarized=True)
    line = _allowlist(roster)
    assert "Anna Kowalska" in line
    assert "Jan Nowak" in line
    assert "i nikt inny" in line


def test_allowlist_says_none_when_no_speakers():
    roster = SpeakerRoster(speakers=(), diarized=False)
    line = _allowlist(roster)
    assert "BRAK" in line
    assert "NIE używaj żadnego nazwiska" in line


def test_draft_system_embeds_allowlist_and_empty_participants_rule():
    roster = SpeakerRoster(speakers=("Anna Kowalska",), diarized=False)
    prompt = _draft_system(roster)
    # Nazwiska z rostera są w promptcie, a participants ma zostać puste (ustala je rdzeń).
    assert "Anna Kowalska" in prompt
    assert "participants ZOSTAW PUSTĄ LISTĄ" in prompt
    # Zakaz sklejania faktów w bio (obrona przed plausible-synthesis).
    assert "NIE łącz osobnych wzmianek" in prompt


def test_verify_system_is_a_removing_critic_not_enricher():
    roster = SpeakerRoster(speakers=("Anna Kowalska",), diarized=True)
    prompt = _verify_system(roster)
    assert "NIE dodawaj nowych faktów" in prompt
    assert "USUŃ" in prompt
    assert "Anna Kowalska" in prompt  # allowlist też w passie 2
