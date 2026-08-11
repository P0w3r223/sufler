"""Testy adaptera dwuprzelotowego streszczania spotkań (ADR 0047).

Dwie warstwy:
- CZYSTE funkcje (``_summary_max_tokens``, ``_allowlist``, ``_draft_system``, ``_verify_system``,
  ``_summary_tool``) — string/dict → dane, bez SDK i bez sieci.
- ``_complete`` z podmienionym klientem Anthropic (atrapa ``messages.create``) — dowodzimy, że
  wynik bierzemy ze STRUCTURED OUTPUT (blok ``tool_use.input``), a nie z parsowania surowego
  tekstu modelu. To likwiduje dawną klasę błędu „Model nie zwrócił poprawnego JSON notatki:
  Expecting ',' delimiter" (kruchy ``json.loads`` na tekście modelu wywracał zapis notatki
  z wątku, ADR 0048, oraz ``/notatka``, ADR 0041).

Realną jakość (brak halucynacji) weryfikuje smoke na kluczu — atrapa jej nie sprawdzi.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from workmate.adapters.outbound.anthropic_summarizer import (
    _SUMMARY_MAX_TOKENS,
    _SUMMARY_TOOL_NAME,
    AnthropicMeetingSummarizer,
    _allowlist,
    _draft_system,
    _summary_max_tokens,
    _summary_tool,
    _verify_system,
)
from workmate.config import AgentSettings
from workmate.core.domain.models import MeetingSummary
from workmate.core.domain.transcript import SpeakerRoster
from workmate.core.errors import LLMError

# --- czyste funkcje: sufit tokenów --------------------------------------------


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


# --- czyste funkcje: schemat narzędzia structured-output ----------------------


def test_summary_tool_derives_input_schema_from_meeting_summary():
    """Schemat wejścia narzędzia = JSON Schema wyprost z pydantic ``MeetingSummary``.

    Jedno źródło prawdy: kształt WYMUSZONY na modelu jest tym samym, który potem waliduje
    ``MeetingSummary.model_validate`` — bez ręcznego duplikowania pól.
    """
    tool = _summary_tool()
    assert tool["name"] == _SUMMARY_TOOL_NAME
    schema = tool["input_schema"]
    assert schema == MeetingSummary.model_json_schema()
    # Kluczowe pola notatki obecne w schemacie (kontrakt z rdzeniem).
    assert set(schema["properties"]) >= {
        "title",
        "participants",
        "decisions",
        "action_items",
        "open_questions",
        "tags",
        "body",
    }


# --- czyste funkcje: allowlist i prompty --------------------------------------


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
    # Prompt kieruje na WYWOŁANIE narzędzia (structured output), nie na surowy JSON.
    assert _SUMMARY_TOOL_NAME in prompt


def test_verify_system_is_a_removing_critic_not_enricher():
    roster = SpeakerRoster(speakers=("Anna Kowalska",), diarized=True)
    prompt = _verify_system(roster)
    assert "NIE dodawaj nowych faktów" in prompt
    assert "USUŃ" in prompt
    assert "Anna Kowalska" in prompt  # allowlist też w passie 2
    assert _SUMMARY_TOOL_NAME in prompt


# --- _complete: structured output przez tool-use ------------------------------


class _RecordingMessages:
    """Atrapa ``client.messages``: zapisuje kwargs ``create`` i oddaje przygotowaną wiadomość."""

    def __init__(self, message: Any) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._message


def _summarizer_returning(
    content: list[Any],
) -> tuple[AnthropicMeetingSummarizer, _RecordingMessages]:
    """Zbuduj adapter z podmienionym klientem; klient oddaje ``content`` z ``messages.create``."""
    summarizer = AnthropicMeetingSummarizer(AgentSettings(api_key="x"))
    recorder = _RecordingMessages(types.SimpleNamespace(content=content))
    summarizer._client = types.SimpleNamespace(messages=recorder)
    return summarizer, recorder


def _tool_use_block(payload: dict[str, Any]) -> types.SimpleNamespace:
    """Blok ``tool_use`` tak, jak zwraca go SDK: ``input`` jest już zwalidowanym słownikiem."""
    return types.SimpleNamespace(type="tool_use", name=_SUMMARY_TOOL_NAME, input=payload)


def test_complete_returns_validated_summary_from_tool_use_input():
    """DOWÓD NAPRAWY: wynik pochodzi z bloku ``tool_use.input`` (słownik od SDK), nie z parsowania
    surowego tekstu. Ten sam ładunek — z surowym newline w ``body`` i przecinkami w listach —
    dawniej (kruchy ``json.loads`` na tekście modelu) wywracał zapis: „Expecting ',' delimiter".
    Teraz to strukturalnie niemożliwe: nie ma już żadnego parsowania tekstu.
    """
    payload = {
        "title": "Spotkanie",
        "participants": [],
        "decisions": ["d1", "d2"],
        "body": "linia1\nlinia2",
    }
    summarizer, recorder = _summarizer_returning([_tool_use_block(payload)])

    result = summarizer.summarize("transkrypt", SpeakerRoster(speakers=(), diarized=False))

    assert isinstance(result, MeetingSummary)
    assert result.title == "Spotkanie"
    assert result.decisions == ["d1", "d2"]
    assert result.body == "linia1\nlinia2"  # surowy newline zachowany, nic się nie rozbiło
    # Żądanie WYMUSZA narzędzie structured-output (tool_choice) i niesie jego schemat.
    sent = recorder.calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": _SUMMARY_TOOL_NAME}
    assert [t["name"] for t in sent["tools"]] == [_SUMMARY_TOOL_NAME]
    assert sent["tools"][0]["input_schema"] == MeetingSummary.model_json_schema()


def test_verify_also_takes_summary_from_tool_use_input():
    """Druga ścieżka (pass 2 / krytyk) idzie przez ten sam ``_complete`` — też structured output."""
    draft = MeetingSummary(title="Draft", decisions=["do usunięcia"])
    payload = {"title": "Po weryfikacji", "participants": [], "decisions": []}
    summarizer, recorder = _summarizer_returning([_tool_use_block(payload)])

    result = summarizer.verify(draft, "transkrypt", SpeakerRoster(speakers=(), diarized=False))

    assert result.title == "Po weryfikacji"
    assert result.decisions == []
    assert recorder.calls[0]["tool_choice"] == {"type": "tool", "name": _SUMMARY_TOOL_NAME}


def test_complete_raises_llm_error_when_model_returns_no_tool_use():
    """Ścieżka błędu: brak bloku ``tool_use`` (np. sam tekst) → czytelny ``LLMError``."""
    summarizer, _ = _summarizer_returning(
        [types.SimpleNamespace(type="text", text="Przepraszam, nie mogę.")]
    )

    with pytest.raises(LLMError, match="tool_use"):
        summarizer.summarize("transkrypt", SpeakerRoster(speakers=(), diarized=False))


def test_complete_wraps_validation_error_in_llm_error():
    """Ładunek bez wymaganego pola ``title`` → walidacja pydantic opakowana w ``LLMError``."""
    summarizer, _ = _summarizer_returning([_tool_use_block({"decisions": ["d1"]})])

    with pytest.raises(LLMError, match="poprawnej notatki"):
        summarizer.summarize("transkrypt", SpeakerRoster(speakers=(), diarized=False))


def test_complete_wraps_anthropic_api_error_in_llm_error():
    """``anthropic.APIError`` nadal opakowany w ``LLMError`` (kontrakt bez zmian)."""
    import anthropic

    summarizer = AnthropicMeetingSummarizer(AgentSettings(api_key="x"))

    class _RaisingMessages:
        def create(self, **kwargs: Any) -> Any:
            raise anthropic.APIError("boom", request=None, body=None)  # type: ignore[arg-type]

    summarizer._client = types.SimpleNamespace(messages=_RaisingMessages())

    with pytest.raises(LLMError, match="Błąd Claude API"):
        summarizer.summarize("transkrypt", SpeakerRoster(speakers=(), diarized=False))
