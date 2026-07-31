"""Golden-test bramek: KAŻDA zdolność mutująca i wychodząca jest domyślnie ZAMKNIĘTA.

Rozproszone `default=False` w ``config.py`` łatwo przestawić — przy dopisywaniu pola, przy
scalaniu, przy „tymczasowym" włączeniu do testów. Ten test zbiera je REFLEKSYJNIE (po polach
``bool`` nazwanych ``enable*``/``enabled`` na wszystkich dataklasach ``*Settings``), więc nowa
bramka nie może się wymknąć ręcznie utrzymywanej liście (mutacja: dopisanie
``enable_x: bool = True`` gdziekolwiek na tych klasach musi wywalić ten test).

Inwariant (CLAUDE.md, ADR 0006/0021/0034): odczyt jest domyślny, każdy zapis wchodzi
przez własną bramkę wyłączoną z fabryki, a operator włącza ją świadomie — BEZ WYJĄTKU (od
2026-07-31 obejmuje też ``save_note``/``WORKMATE_ENABLE_WRITE``, amendment ADR 0006). Jeśli ten
test padnie, NIE „naprawiaj" go zmianą oczekiwanej wartości — to sygnał, że ktoś otworzył
bramkę domyślnie.

``monkeypatch.delenv`` czyści CAŁE środowisko ``WORKMATE_*``, bo ``.env`` operatora nie może
wpływać na wynik: sprawdzamy domyślne wartości KODU, nie bieżącą konfigurację maszyny.
"""

from __future__ import annotations

import dataclasses

import pytest

from workmate.config import (
    GithubSettings,
    RetrievalSettings,
    Settings,
    TeamsDigestSettings,
    TeamsGraphSettings,
    TeamsPushSettings,
    WorkspaceSettings,
)

# Wszystkie dataklasy ustawień, które mogą nieść bramkę zdolności mutującej/wychodzącej.
_SETTINGS_CLASSES = (
    Settings,
    RetrievalSettings,
    TeamsGraphSettings,
    WorkspaceSettings,
    GithubSettings,
    TeamsPushSettings,
    TeamsDigestSettings,
)


def _gate_fields(cls: type) -> list[str]:
    """Pola ``bool`` nazwane ``enable*``/``enabled`` na danej dataklasie ustawień."""
    return [
        f.name
        for f in dataclasses.fields(cls)
        if f.type == "bool" and (f.name == "enabled" or f.name.startswith("enable_"))
    ]


@pytest.fixture
def clean_env(monkeypatch):
    """Zdejmij WSZYSTKIE zmienne ``WORKMATE_*`` — testujemy domyślne wartości KODU, nie maszyny."""
    import os

    for name in list(os.environ):
        if name.startswith("WORKMATE_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _all_gates() -> dict[str, bool]:
    """Bieżący stan WSZYSTKICH bramek `enable*`/`enabled`, zebrany refleksyjnie z `from_env()`."""
    gates: dict[str, bool] = {}
    for cls in _SETTINGS_CLASSES:
        instance = cls.from_env()
        for field_name in _gate_fields(cls):
            gates[f"{cls.__name__}.{field_name}"] = getattr(instance, field_name)
    return gates


def test_every_gate_is_closed_by_default(clean_env) -> None:
    gates = _all_gates()
    open_gates = {name: value for name, value in gates.items() if value is not False}
    assert not open_gates, (
        f"BRAMKI OTWARTE DOMYŚLNIE: {sorted(open_gates)}. Każda zdolność mutująca i wychodząca "
        "musi być wyłączona z fabryki — operator włącza ją świadomie (CLAUDE.md, ADR 0006)."
    )


def test_every_known_gate_is_covered(clean_env) -> None:
    """Refleksja musi znaleźć co najmniej te bramki, które znamy z przeglądu 2026-07-31."""
    known = {
        "Settings.enable_write",
        "RetrievalSettings.enable_dense",
        "TeamsGraphSettings.enable_file_reply",
        "TeamsGraphSettings.enable_user_file_push",
        "TeamsGraphSettings.enable_user_doc_push",
        "TeamsGraphSettings.enable_meeting_transcript",
        "TeamsGraphSettings.enable_meeting_note_write",
        "TeamsGraphSettings.enable_meeting_note_async",
        "TeamsGraphSettings.enable_thread_note_capture",
        "TeamsGraphSettings.enable_project_brief",
        "TeamsGraphSettings.enable_change_digest",
        "WorkspaceSettings.enabled",
        "GithubSettings.enable_github_write",
        "GithubSettings.enable_ci_auto_comment",
        "TeamsPushSettings.enable_chat",
        "TeamsPushSettings.enable_channel",
        "TeamsPushSettings.enable_channel_threading",
        "TeamsDigestSettings.enabled",
    }
    assert known <= set(_all_gates())
