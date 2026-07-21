"""Golden-test bramek: KAŻDA zdolność mutująca i wychodząca jest domyślnie ZAMKNIĘTA.

Rozproszone `default=False` w ``config.py`` łatwo przestawić — przy dopisywaniu pola, przy
scalaniu, przy „tymczasowym" włączeniu do testów. Ten test zbiera je w jednym miejscu i traktuje
domyślną wartość jak KONTRAKT, nie szczegół implementacji.

Inwariant (CLAUDE.md, ADR 0006/0021/0031/0032/0034/0035): odczyt jest domyślny, każdy zapis wchodzi
przez własną bramkę wyłączoną z fabryki, a operator włącza ją świadomie. Jeśli ten test padnie,
NIE „naprawiaj" go zmianą oczekiwanej wartości — to sygnał, że ktoś otworzył bramkę domyślnie.

``monkeypatch.delenv`` czyści środowisko, bo ``.env`` operatora nie może wpływać na wynik:
sprawdzamy domyślne wartości KODU, nie bieżącą konfigurację maszyny.
"""

from __future__ import annotations

import pytest

from workmate.config import (
    GithubSettings,
    JiraSettings,
    TeamsPushSettings,
    WorklogiSettings,
    WorkspaceSettings,
)

# (zmienna środowiskowa, opis dla czytelnika raportu)
_MUTATING_GATES = (
    ("WORKMATE_GITHUB_ENABLE_WRITE", "GitHub: tworzenie issue/komentarzy (Gate 4)"),
    ("WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT", "GitHub: autonomiczny auto-komentarz CI"),
    ("WORKMATE_JIRA_ENABLE_WRITE", "Jira: tworzenie zgłoszeń/komentarzy (Gate 5)"),
    ("WORKMATE_JIRA_ENABLE_TRANSITION", "Jira: tranzycja statusu"),
    ("WORKMATE_JIRA_ENABLE_WORKLOG", "Jira: zapis czasu pracy"),
    ("WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF", "Jira: zapis czasu w cudzym imieniu"),
    ("WORKMATE_WORKLOGI_ENABLED", "Karty czasu: cotygodniowa wysyłka do ludzi"),
    ("WORKMATE_TEAMS_PUSH_ENABLE_CHAT", "Teams: proaktywny czat 1:1"),
    ("WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL", "Teams: proaktywny post na kanale"),
    ("WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING", "Teams: wątkowanie kanału"),
    ("WORKMATE_ENABLE_WORKSPACE", "Agent: katalog roboczy (pliki od modelu)"),
)


@pytest.fixture
def clean_env(monkeypatch):
    """Zdejmij wszystkie bramki ze środowiska — testujemy domyślne wartości KODU."""
    for name, _ in _MUTATING_GATES:
        monkeypatch.delenv(name, raising=False)
    for name in ("WORKMATE_WORKLOGI_DRY_RUN", "WORKMATE_JIRA_WORKLOG_DUPLICATE_GUARD"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _all_gates() -> dict[str, bool]:
    """Bieżący stan wszystkich bramek, zebrany z dataclass ustawień."""
    jira = JiraSettings.from_env()
    github = GithubSettings.from_env()
    push = TeamsPushSettings.from_env()
    return {
        "WORKMATE_GITHUB_ENABLE_WRITE": github.enable_github_write,
        "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT": github.enable_ci_auto_comment,
        "WORKMATE_JIRA_ENABLE_WRITE": jira.enable_jira_write,
        "WORKMATE_JIRA_ENABLE_TRANSITION": jira.enable_jira_transition,
        "WORKMATE_JIRA_ENABLE_WORKLOG": jira.enable_jira_worklog,
        "WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF": jira.worklog_allow_on_behalf,
        "WORKMATE_WORKLOGI_ENABLED": WorklogiSettings.from_env().enabled,
        "WORKMATE_TEAMS_PUSH_ENABLE_CHAT": push.enable_chat,
        "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL": push.enable_channel,
        "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING": push.enable_channel_threading,
        "WORKMATE_ENABLE_WORKSPACE": WorkspaceSettings.from_env().enabled,
    }


@pytest.mark.parametrize(("name", "opis"), _MUTATING_GATES, ids=[n for n, _ in _MUTATING_GATES])
def test_gate_is_closed_by_default(name: str, opis: str, clean_env) -> None:
    assert _all_gates()[name] is False, (
        f"BRAMKA OTWARTA DOMYŚLNIE: {name} ({opis}). Zdolności mutujące i wychodzące muszą być "
        "wyłączone z fabryki — operator włącza je świadomie (CLAUDE.md, ADR 0006)."
    )


def test_every_known_gate_is_covered(clean_env) -> None:
    """Nowa bramka musi trafić na listę — inaczej wymknęłaby się temu testowi."""
    assert set(_all_gates()) == {name for name, _ in _MUTATING_GATES}


def test_worklogi_starts_in_dry_run(clean_env) -> None:
    """Sama bramka nie wystarcza: po włączeniu drzwi NADAL nic nie wychodzi do ludzi."""
    assert WorklogiSettings.from_env().dry_run is True


def test_worklog_duplicate_guard_starts_enabled(clean_env) -> None:
    """Strażnik jest domyślnie WŁĄCZONY — tu bezpieczna wartość to ``True``, nie ``False``.

    Bez usuwania worklogów duplikat jest nieusuwalny narzędziem (ADR 0034), więc domyślną
    wartością musi być ostrożność, a nie wygoda.
    """
    assert JiraSettings.from_env().worklog_duplicate_guard is True


def test_notes_write_stays_the_only_default_on_capability(clean_env) -> None:
    """``save_note`` (Gate 2) jest historycznym wyjątkiem — świadomym, udokumentowanym w ADR 0006.

    Test przypina go jawnie, żeby wyjątek pozostał JEDEN: gdyby ktoś dołożył drugą domyślnie
    włączoną zdolność zapisu, powyższe testy to wyłapią.
    """
    from workmate.config import Settings

    assert Settings.from_env().enable_write is True
