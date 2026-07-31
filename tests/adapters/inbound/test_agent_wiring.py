"""Testy uwspólnionego wiringu drzwi (``agent_wiring``).

Bez klucza Claude API i bez extra ``agent``: runtime jest podmieniany atrapą przez
monkeypatch, więc żaden import SDK/klienta LLM się nie odpala. Sprawdzamy: katalog
read-only bez ``save_note``, złożenie respondera (``SafeResponder`` vs goły) oraz że
komenda ``/pomoc`` idzie przez router BEZ wołania runtime. Osobno: brak extra ``agent``
→ czytelny ``SystemExit``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from workmate.adapters.inbound import agent_wiring
from workmate.adapters.inbound.agent_wiring import (
    build_agent_runtime_or_exit,
    build_conversational_responder,
    build_read_catalog,
)
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    InboundMessage,
    SafeResponder,
)
from workmate.config import AgentSettings, ConversationSettings, Settings

_REGISTRY = """projects:
  - key: workmate
    company: biap
    name: WorkMate
    description: Asystent wiedzy
    status: active
    health: green
    phase: Faza 2
    summary: Prace w toku
    last_updated: 2025-06-24
"""


def _settings(tmp_path: Path) -> Settings:
    """Realne ``Settings`` z tmp katalogami notatek/rejestru (fns katalogu są leniwe)."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    registry = tmp_path / "registry.yaml"
    registry.write_text(_REGISTRY, encoding="utf-8")
    return Settings(
        data_dir=tmp_path,
        notes_dir=notes_dir,
        projects_registry=registry,
        transport="stdio",
        log_level="INFO",
        enable_write=True,
        tokens_file=tmp_path / "tokens.json",
        bind_host="127.0.0.1",
        bind_port=8000,
        allowed_hosts=("127.0.0.1:*",),
        allowed_origins=(),
        tls_certfile=None,
        tls_keyfile=None,
        metrics_db=None,
    )


class _DummyRuntime:
    """Atrapa runtime: podmienia realny ``AgentRuntime``, więc SDK/klucz nie są potrzebne.

    ``run_turn`` NIE powinien być wołany dla komend — sygnalizuje błąd, gdyby jednak był.
    """

    def run_turn(self, *args: object, **kwargs: object) -> object:  # pragma: no cover
        raise AssertionError("runtime nie powinien być wołany dla komendy")


# --- build_read_catalog: zawsze read-only, bez save_note ------------------------


def test_build_read_catalog_returns_four_readonly_tools_without_save_note(tmp_path: Path):
    catalog = build_read_catalog(_settings(tmp_path))

    names = [spec.name for spec in catalog]
    assert names == ["search_notes", "get_note", "list_projects", "get_project_status"]
    assert "save_note" not in names  # bramka ADR 0006: komendy nigdy nie zapisują


def test_build_read_catalog_tools_are_wired_to_real_read_services(tmp_path: Path):
    """Fns katalogu realnie czytają serwisy (nie są puste) — dowód wiringu read-only."""
    catalog = {spec.name: spec.fn for spec in build_read_catalog(_settings(tmp_path))}

    result = catalog["list_projects"]()
    assert result["count"] == 1
    assert result["projects"][0]["key"] == "workmate"


# --- build_conversational_responder: SafeResponder vs goły ----------------------


def _conv_settings(tmp_path: Path) -> ConversationSettings:
    # Kompaktowanie wyłączone → build_compaction_service nie importuje klienta LLM.
    return ConversationSettings(
        db_path=tmp_path / "conv.db", max_context_tokens=1000, compaction_enabled=False
    )


def _build_responder(tmp_path, monkeypatch, *, safe: bool):
    monkeypatch.setattr(
        agent_wiring, "build_agent_runtime_or_exit", lambda *a, **k: _DummyRuntime()
    )
    return build_conversational_responder(
        _settings(tmp_path),
        AgentSettings(),
        _conv_settings(tmp_path),
        channel="telegram",
        enable_write=False,
        safe=safe,
    )


def test_build_conversational_responder_wraps_in_saferesponder_when_safe(
    tmp_path: Path, monkeypatch
):
    responder = _build_responder(tmp_path, monkeypatch, safe=True)
    assert isinstance(responder, SafeResponder)


def test_build_conversational_responder_returns_bare_when_not_safe(tmp_path: Path, monkeypatch):
    responder = _build_responder(tmp_path, monkeypatch, safe=False)
    assert isinstance(responder, ConversationalResponder)


def test_built_responder_dispatches_command_without_calling_runtime(tmp_path: Path, monkeypatch):
    """Złożony responder wykonuje ``/pomoc`` przez router — komenda ≠ tura, runtime niewołany.

    ``_DummyRuntime.run_turn`` rzuciłby, gdyby komenda trafiła do pętli agenta.
    """
    responder = _build_responder(tmp_path, monkeypatch, safe=True)

    reply = asyncio.run(responder.respond(InboundMessage(text="/pomoc", conversation_id="chat1")))
    # ``_help`` (commands.py) poprzedza listę komend krótkim wprowadzeniem "Jestem WorkMate…".
    assert "Dostępne komendy:" in reply


# --- build_agent_runtime_or_exit: brak extra agent → SystemExit -----------------


def test_missing_agent_extra_raises_systemexit_with_hint(tmp_path: Path, monkeypatch):
    """Brak extra ``agent`` (ImportError z build_agent_runtime) → czytelny ``SystemExit``."""

    def _raise(*args: object, **kwargs: object) -> object:
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(agent_wiring, "build_agent_runtime", _raise)

    with pytest.raises(SystemExit) as exc:
        build_agent_runtime_or_exit(_settings(tmp_path), AgentSettings(), enable_write=False)
    assert "extra" in str(exc.value)
