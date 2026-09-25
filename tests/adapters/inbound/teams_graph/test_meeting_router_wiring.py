"""Strażnik wiringu ``/notatka`` (B2 / ADR 0042): niezaufane drzwi zawsze z autoryzatorem.

``MeetingNoteRouter`` z ``authorizer=None`` po cichu wyłącza bramkę członkostwa (tryb zaufanego
CLI). Te testy pilnują, że drzwi Teams (jedyne NIEZAUFANE drzwi zapisu) nigdy tak nie zbudują
routera: przy włączonej bramce zapisu wiring MUSI wpiąć autoryzator, a przy wyłączonej — zwrócić
``None``. Bez tego regresja w wiringu (usunięcie autoryzatora) przeszłaby cicho.
"""

from __future__ import annotations

import pytest

from sufler.adapters.inbound.teams_graph.app import _build_meeting_note_router
from sufler.config import AgentSettings, Settings, TeamsGraphSettings


def test_wiring_returns_none_when_gate_off():
    settings = TeamsGraphSettings(client_id="a", tenant_id="t", enable_meeting_note_write=False)

    router = _build_meeting_note_router(
        settings, lambda: "tok", Settings.from_env(), AgentSettings()
    )

    assert router is None


def test_wiring_builds_authorizer_when_gate_on(tmp_path):
    pytest.importorskip("anthropic")  # summarizer buduje klienta Claude (extra 'agent')
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    settings = TeamsGraphSettings(
        client_id="a",
        tenant_id="t",
        enable_meeting_note_write=True,
        meeting_note_identities=identities,
    )

    router = _build_meeting_note_router(
        settings, lambda: "tok", Settings.from_env(), AgentSettings(api_key="test-dummy")
    )

    # Bramka ON → router istnieje I niesie autoryzator (członkostwa nie może zabraknąć w wiringu).
    assert router is not None
    assert router._authorizer is not None
    # Async OFF (domyślnie) → router liczy inline, bez schedulera/callbacku.
    assert router._scheduler is None
    assert router._callback is None


def test_wiring_builds_async_scheduler_when_async_gate_on(tmp_path):
    pytest.importorskip("anthropic")
    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    settings = TeamsGraphSettings(
        client_id="a",
        tenant_id="t",
        enable_meeting_note_write=True,
        enable_meeting_note_async=True,
        meeting_note_identities=identities,
    )

    router = _build_meeting_note_router(
        settings, lambda: "tok", Settings.from_env(), AgentSettings(api_key="test-dummy")
    )

    # Async ON → router dostaje scheduler (pula wątków) I callback (poster do wątku).
    assert router is not None
    assert router._scheduler is not None
    assert router._callback is not None


def test_oba_routery_dostaja_te_sama_pule_i_poster(tmp_path):
    """Regresja: docstring ``_build_thread_note_router`` deklaruje „async współdzieli pulę/poster",
    a obaj wołający liczyli ``_build_async_note_dispatch`` osobno — każdy stawiał nowy
    ``ThreadPoolExecutor`` i nowy klient HTTP.

    Przy obu bramkach ON sufit równoległych łańcuchów transkrypt+Claude był więc faktycznie
    DWUKROTNOŚCIĄ ``meeting_note_async_workers``: ustawienie mówiło jedno, proces robił drugie,
    a różnicy nie widać w niczym poza ``ps``.
    """
    pytest.importorskip("anthropic")
    from sufler.adapters.inbound.teams_graph.wiring_routers import _build_async_note_dispatch

    identities = tmp_path / "identities.yaml"
    identities.write_text("", encoding="utf-8")
    settings = TeamsGraphSettings(
        client_id="a",
        tenant_id="t",
        enable_meeting_note_write=True,
        enable_meeting_note_async=True,
        meeting_note_identities=identities,
    )

    def token_provider() -> str:  # ta sama TOŻSAMOŚĆ w obu wywołaniach
        return "tok"

    pierwszy = _build_async_note_dispatch(settings, token_provider)
    drugi = _build_async_note_dispatch(settings, token_provider)

    assert pierwszy[0] is drugi[0]  # ten sam scheduler → ta sama pula wątków
    assert pierwszy[1] is drugi[1]  # ten sam callback → ten sam klient HTTP
