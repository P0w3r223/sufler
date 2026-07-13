"""Testy dispatchu trybu w drzwiach CLI (``workmate-agent``) — bez sieci, bez klucza.

Sprawdzamy WYBÓR trybu (argv jednorazowo / potok jednorazowo / TTY → czat), nie samo
wywołanie API: ``build_agent_runtime_or_exit`` i ``_run_chat`` podmieniamy atrapami. Klucz
ustawiamy w env, żeby ``AgentSettings.validate`` przeszło bez sekretu w repo.
"""
from __future__ import annotations

import io
import sys
import types

import pytest

from workmate.adapters.inbound.cli import app
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.domain.pricing import TokenUsage


def _fake_build(reply: str):
    runtime = types.SimpleNamespace(run=lambda query: f"{reply}:{query}")
    return lambda *args, **kwargs: runtime


def _no_runtime(*args, **kwargs):
    raise AssertionError("tryb historii nie może budować runtime ani wymagać klucza API")


def test_argv_query_runs_once_and_prints(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["workmate-agent", "co", "z", "mpwik?"])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", _fake_build("ODP"))

    app.main()

    assert "ODP:co z mpwik?" in capsys.readouterr().out


def test_empty_piped_input_raises_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["workmate-agent"])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", lambda *a, **k: object())
    monkeypatch.setattr("sys.stdin", io.StringIO("   "))  # potok (nie-TTY), puste

    with pytest.raises(SystemExit):
        app.main()


def test_interactive_tty_enters_chat(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["workmate-agent"])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    # Runtime buduje dopiero ``_run_chat`` (przez wspólny builder), nie ``main``; więc
    # atrapujemy sam ``_run_chat`` i sprawdzamy, że TTY-bez-argumentu wybiera tryb czatu.
    entered: list[bool] = []
    monkeypatch.setattr(app, "_run_chat", lambda *a, **k: entered.append(True))

    app.main()

    assert entered == [True]  # bez argumentu w TTY → tryb czatu


# --- Tryb --history: dispatch na podgląd (czysty odczyt, bez klucza API) --------


@pytest.mark.parametrize("flag", ["--history", "history"])
def test_history_flag_dispatches_to_preview_without_api_key(monkeypatch, flag):
    # Klucz CELOWO nieustawiony: gdyby dispatch szedł zwykłą ścieżką, walidacja
    # sekretu/budowa runtime by wybuchła. Podgląd musi zadziałać bez klucza.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("WORKMATE_AGENT_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["workmate-agent", flag])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", _no_runtime)
    captured: list[str | None] = []
    monkeypatch.setattr(app, "_print_history", lambda *, channel=None: captured.append(channel))

    app.main()

    assert captured == [None]  # dispatch na podgląd, filtr kanału pusty


def test_history_flag_passes_channel_filter(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["workmate-agent", "--history", "telegram"])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", _no_runtime)
    captured: list[str | None] = []
    monkeypatch.setattr(app, "_print_history", lambda *, channel=None: captured.append(channel))

    app.main()

    assert captured == ["telegram"]  # kanał z argv trafia do podglądu


def test_history_empty_db_reports_no_conversations(monkeypatch, capsys, tmp_path):
    db = tmp_path / "conv.db"
    monkeypatch.setenv("WORKMATE_CONVERSATIONS_DB", str(db))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["workmate-agent", "--history"])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", _no_runtime)

    app.main()

    assert "Brak zapisanych rozmów" in capsys.readouterr().out


def test_history_renders_conversations_from_shared_db(monkeypatch, capsys, tmp_path):
    # Podgląd czyta tę samą bazę SQLite, do której piszą drzwi — zapełniamy ją
    # osobnym store'em, potem uruchamiamy CLI na tej samej ścieżce (wspólne archiwum).
    db = tmp_path / "conv.db"
    store = SqliteConversationStore(db)
    conv = store.open_conversation("telegram", "chat-42")
    store.append_message(conv.id, "user", "kiedy raport dla mpwik")
    store.append_message(
        conv.id, "assistant", "w piatek", usage=TokenUsage(input_tokens=5, output_tokens=3)
    )

    monkeypatch.setenv("WORKMATE_CONVERSATIONS_DB", str(db))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["workmate-agent", "--history"])
    monkeypatch.setattr(app, "build_agent_runtime_or_exit", _no_runtime)

    app.main()

    out = capsys.readouterr().out
    assert "[telegram] chat-42" in out  # nagłówek rozmowy (kanał + external_id)
    assert "kiedy raport dla mpwik" in out  # tura użytkownika
    assert "w piatek" in out  # tura asystenta
    assert "8 tok" in out  # REALNE tokeny 5 + 3 (Design 2)
    assert "$0.0000" in out  # KOSZT (drobny — 4 miejsca po przecinku)


# --- Formatowanie podglądu (czyste funkcje) ------------------------------------


def _msg(role, text="", blocks=None):
    return types.SimpleNamespace(role=role, text=text, blocks=blocks)


def test_format_body_collapses_whitespace_in_text():
    assert app._format_body(_msg("user", "kiedy\n   raport")) == "kiedy raport"


def test_format_body_assistant_without_text_lists_tool_calls():
    blocks = [
        {"type": "tool_use", "name": "search_notes"},
        {"type": "text", "text": ""},
    ]
    assert (
        app._format_body(_msg("assistant", "", blocks))
        == "(wywołuje narzędzia: search_notes)"
    )


def test_format_body_empty_assistant_falls_back_to_placeholder():
    assert app._format_body(_msg("assistant", "", None)) == "(brak treści)"


def test_format_body_routes_tool_role_to_tool_formatter():
    blocks = [{"content": "wynik", "is_error": False}]
    assert app._format_body(_msg("tool", "", blocks)) == "→ wynik"


def test_format_tool_renders_results_and_error_flag():
    blocks = [
        {"content": "ok", "is_error": False},
        {"content": "boom", "is_error": True},
    ]
    assert app._format_tool(_msg("tool", "", blocks)) == "→ ok | boom [błąd]"


def test_format_tool_without_blocks_uses_placeholder():
    assert app._format_tool(_msg("tool", "", None)) == "(wynik narzędzia)"


def test_tool_calls_extracts_only_tool_use_names():
    blocks = [
        {"type": "tool_use", "name": "get_note"},
        {"type": "text", "text": "x"},
        {"type": "tool_use", "name": "list_projects"},
    ]
    assert app._tool_calls(blocks) == ["get_note", "list_projects"]


def test_tool_calls_empty_when_no_blocks():
    assert app._tool_calls(None) == []


def test_shorten_truncates_beyond_width():
    assert app._shorten("abcdef", width=4) == "abcd […]"


def test_shorten_keeps_short_text_intact():
    assert app._shorten("abc", width=4) == "abc"
