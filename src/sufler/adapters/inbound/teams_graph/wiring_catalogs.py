"""Wiring katalogów narzędzi zależnych od NADAWCY: grafik pionu i zadania Jira nadawcy."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from sufler.config import (
    JiraSettings,
    ScheduleSettings,
    TeamsGraphSettings,
)

if TYPE_CHECKING:
    from sufler.core.application.tools import ToolSpec

logger = logging.getLogger(__name__)


def _build_team_schedule_catalog(schedule_settings: ScheduleSettings) -> list[ToolSpec]:
    """Zbuduj katalog grafiku Shifts (ADR 0059) — pusty, gdy grafik wyłączony/nieskonfigurowany.

    ``is_enabled()`` (tryb auto) sam sprawdza obecność cudzego cache MSAL, więc na hoście bez montu
    powiadomienia-teams po prostu nie dokładamy narzędzia (ciche wyłączenie). Klient żyje przez cały
    proces (daemon), jak inne sync klienty tutaj.
    """
    if not schedule_settings.is_enabled():
        logger.info(
            "Grafik Shifts WYŁĄCZONY (ADR 0059) — brak cache tokenu %s albo "
            "SUFLER_SCHEDULE_ENABLED=false. Narzędzie `Schedule` nie zostanie wystawione.",
            schedule_settings.token_cache_path,
        )
        return []
    import atexit

    import httpx

    from sufler.adapters.outbound.graph_schedule_api import HttpxGraphScheduleClient
    from sufler.adapters.outbound.msal_silent_token import build_silent_token_provider
    from sufler.core.application.team_schedule import TeamScheduleService
    from sufler.core.application.tools import build_schedule_catalog

    token_provider = build_silent_token_provider(schedule_settings)
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    client = HttpxGraphScheduleClient(transport, token_provider)
    service = TeamScheduleService(
        client, team_id=schedule_settings.team_id, tz=schedule_settings.timezone
    )
    logger.info(
        "Grafik Shifts WŁĄCZONY (ADR 0059) — agent Teams pokazuje zmiany i nieobecności zespołu "
        "%s (strefa %s), token cichy z cache %s (RO, nigdy nie zapisywany).",
        schedule_settings.team_id,
        schedule_settings.timezone,
        schedule_settings.token_cache_path,
    )
    return build_schedule_catalog(service)


def _build_my_jira_tasks_factory(
    settings: TeamsGraphSettings, jira_settings: JiraSettings
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka "moje zadania" Jira (ADR 0054) PER NADAWCA — ``None`` gdy nieskonfigurowana.

    Wymaga skonfigurowanego odczytu Jiry (URL+token) ORAZ mapy tożsamości — TEGO SAMEGO pliku co
    autoryzacja M3 (ADR 0042, pole ``jira_user``), niezależnie od bramki zapisu notatek. Sender bez
    rozwiązanej tożsamości albo bez ``jira_user`` dostaje pustą listę narzędzi (fail-closed, zero
    domysłów) — router komend i responder degradują to do czytelnej odmowy, nie do błędu.

    Te dwa przypadki różnią się tym, czego są objawem, i od ADR 0070 tylko pierwszy jest brakiem.
    Nadawca ZMAPOWANY, ale bez ``jira_user``, to osoba bez konta Jira — stan TRWAŁY i legalny, nie
    mapa do uzupełnienia. Jest pełnym członkiem pionu (czyta bazę wiedzy, pisze notatki, dostaje
    powłokę), a narzędzia ``Jira`` nie dostaje w ogóle — traci przez to także pytania o zadania
    INNYCH ludzi, co z jej własnym brakiem konta nie ma nic wspólnego. Ta asymetria jest w ADR
    0070 §3 nazwana i przyjęta; pusta lista NIE jest tu więc miejscem do „naprawy".

    Zawężenie do WŁASNEGO konta dzieje się TU, przy budowie ``MyJiraTasksService``, i to jest
    jedyne miejsce, gdzie ono żyje (ADR 0054). Od kroku 5.3 (ADR 0009 paczki wdrożeniowej) sześć
    dawnych narzędzi jest jednym ``Jira(action=…)``, więc pole ``member`` STOI w tym samym
    schemacie co akcje ``my_*`` — ale gałęzie ``my_*`` go nie czytają, bo biorą serwis domknięty
    tutaj. Sonda pilnująca tego jest w ``tests/core/test_jira_catalog.py``; dawniej niemożliwość
    przekierowania wynikała z pustej sygnatury, teraz wynika z dispatchera i musi być sprawdzana.
    """
    if not (jira_settings.base_url and jira_settings.token):
        return None
    if not settings.meeting_note_identities.is_file():
        return None
    import atexit

    import httpx

    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from sufler.adapters.outbound.jira_api import build_jira_client
    from sufler.core.application.jira_read import JiraReadService
    from sufler.core.application.my_jira_tasks import MyJiraTasksService
    from sufler.core.application.tools import build_jira_catalog

    identities = YamlIdentityDirectory(settings.meeting_note_identities)
    # Klient żyje przez cały proces (daemon), jak inne sync klienty Jiry/GitHuba tutaj.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    client = build_jira_client(transport, jira_settings)
    # Serwis rozszerzonego odczytu (szczegóły/wyszukiwanie/członek) współdzieli klienta i bazę URL —
    # NIE jest związany z nadawcą (bierze parametry), więc budujemy go raz.
    read_service = JiraReadService(client, base_url=jira_settings.base_url)

    def resolve_member(name: str) -> str | None:
        person = identities.resolve_by_display_name(name)
        return person.jira_user if person and person.jira_user else None

    logger.info(
        "'Moje zadania' Jira WŁĄCZONE (ADR 0054) — agent Teams i komenda /moje-zadania pokazują "
        "otwarte zadania nadawcy, zawężone do JEGO konta Jira przez mapę tożsamości %s. "
        "Dodatkowo rozszerzony ODCZYT (szczegóły zgłoszenia, wyszukiwanie, zadania członka).",
        settings.meeting_note_identities,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        person = identities.resolve_by_aad_user_id(sender_id)
        if person is None or not person.jira_user:
            return []
        service = MyJiraTasksService(
            client, assignee=person.jira_user, base_url=jira_settings.base_url
        )
        return build_jira_catalog(service, read_service, resolve_member)

    return factory
