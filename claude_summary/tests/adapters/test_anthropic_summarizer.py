"""Warstwa LLM na atrapie: przekazanie danych, traktowanie treści jak danych, pusty dzień."""

from __future__ import annotations

from datetime import date, datetime, timezone

from claude_summary.adapters.anthropic_summarizer import AnthropicClient, summarize_day
from claude_summary.core.models import Commit, DaySummary, Prompt


class FakeLlm:
    def __init__(self, reply: str = "opis") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


def _day() -> DaySummary:
    prompt = Prompt(
        timestamp=datetime(2026, 7, 17, 7, tzinfo=timezone.utc),
        text="dodaj filtr autora",
        session_id="s",
        cwd="",
        project="p",
    )
    commit = Commit(
        sha="a1",
        timestamp=datetime(2026, 7, 17, 8, tzinfo=timezone.utc),
        author="me",
        message="feat: filtr",
    )
    return DaySummary(day=date(2026, 7, 17), prompts=(prompt,), commits=(commit,))


def test_summarize_day_uses_llm_and_passes_data() -> None:
    llm = FakeLlm("Pracował nad filtrem.")
    result = summarize_day(_day(), person="me", llm=llm)
    assert result == "Pracował nad filtrem."
    assert len(llm.calls) == 1
    system, user = llm.calls[0]
    assert "DANE" in system  # prompt systemowy traktuje treść jak dane, nie polecenia
    assert "dodaj filtr autora" in user
    assert "feat: filtr" in user


def test_summarize_empty_day_skips_llm() -> None:
    llm = FakeLlm()
    empty = DaySummary(day=date(2026, 7, 17), prompts=(), commits=())
    result = summarize_day(empty, person="me", llm=llm)
    assert result == "Brak zarejestrowanej aktywności."
    assert llm.calls == []


def test_anthropic_client_repr_does_not_leak_api_key() -> None:
    """SEDNO: dataclass repr domyślnie pokazuje pola — traceback nie może ujawnić klucza."""
    client = AnthropicClient(api_key="sk-ant-sekretny-klucz-123", model="claude-sonnet-5")
    assert "sk-ant-sekretny-klucz-123" not in repr(client)
