"""Entry point drzwi Jira (delegowany polling PAT, ADR 0030) — ingest zdarzeń + push do Teams.

Uruchomienie: ``uv run workmate-jira`` (wymaga ``uv sync --extra jira`` oraz
``WORKMATE_JIRA_BASE_URL``/``_TOKEN``/``_WATCH_PROJECTS``). Odpytuje Jirę o nowe/zmienione issue
i zapisuje utworzenia/tranzycje/komentarze do wspólnego magazynu zdarzeń (``events.db``). Gdy
skonfigurowano cele Teams (``WORKMATE_TEAMS_PUSH_*``), RÓWNOLEGLE (``asyncio.gather``) uruchamia
notifiera wypychającego zdarzenia ``source="jira"`` do Teams (czat 1:1 i/lub kanał, ADR 0022).

Wątkowanie kanału (ADR 0024, B2) dokłada zdarzenia tego samego zgłoszenia (utworzenie/tranzycja/
komentarz) do JEDNEGO wątku na kanale — flaga ``WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING``
(domyślnie OFF); przy OFF notifier tworzy nowy root per zdarzenie. Mapę wątków wypełnia notifier
tego procesu (samowystarczalne w drzwiach Jiry). Importy ``httpx``/MSAL są leniwe; brak extra
kończy się czytelnym komunikatem, nie ``ImportError``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound import env
from workmate.config import EventsSettings, JiraSettings, TeamsPushSettings

if TYPE_CHECKING:
    from pathlib import Path

    from workmate.core.application.events import EventService

logger = logging.getLogger(__name__)

_MISSING_JIRA = "Drzwi Jira wymagają extra 'jira'. Zainstaluj: uv sync --extra jira"
_MISSING_PUSH = (
    "Proaktywny push do Teams wymaga extra 'teams-graph' (MSAL). "
    "Zainstaluj: uv sync --extra teams-graph"
)


def main() -> None:
    """Uruchom proces drzwi Jira (polling PAT) + opcjonalny push zdarzeń do Teams."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()

    settings = JiraSettings.from_env()
    settings.validate()
    events_settings = EventsSettings.from_env()
    push_settings = TeamsPushSettings.from_env()
    push_settings.validate()
    asyncio.run(_run(settings, events_settings, push_settings))


def _build_project_map(registry_path: Path, watch_projects: tuple[str, ...]) -> dict[str, str]:
    """Mapa klucz projektu Jira → klucz projektu WorkMate z rejestru (ADR 0028) — atrybucja zdarzeń.

    Zawężona do nasłuchiwanych projektów. Best-effort: nieczytelny rejestr → pusta mapa (zdarzenia
    bez projektu). Klucze normalizujemy do wielkich liter (klucze Jira są case-insensitive).
    """
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository

    watched = {p.strip().upper() for p in watch_projects}
    mapping: dict[str, str] = {}
    try:
        for project in YamlProjectsRepository(registry_path).all():
            jira_key = (project.jira_project_key or "").strip().upper()
            if jira_key and jira_key in watched:
                mapping[jira_key] = project.key
    except Exception:
        logger.warning("Nie udało się zbudować mapy projektów Jira z rejestru.")
    return mapping


async def _run(
    settings: JiraSettings,
    events_settings: EventsSettings,
    push_settings: TeamsPushSettings,
) -> None:
    try:
        import httpx
    except ImportError as exc:
        raise SystemExit(_MISSING_JIRA) from exc
    from workmate.adapters.inbound.jira import state as state_store
    from workmate.adapters.inbound.jira.poller import JiraPoller
    from workmate.adapters.outbound.jira_api import HttpxJiraClient
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.config import Settings
    from workmate.core.application.events import EventService

    events = EventService(SqliteEventStore(events_settings.db_path))
    state = state_store.load(settings.state_path)
    project_map = _build_project_map(
        Settings.from_env().projects_registry, settings.watch_projects
    )

    def persist(current: dict[str, Any]) -> None:
        state_store.save(settings.state_path, current)

    # Poller używa sync klienta (wołanego w puli wątków), notifier — async; osobne menedżery.
    async with httpx.AsyncClient(timeout=30) as async_http:
        with httpx.Client(timeout=30) as sync_http:
            client = HttpxJiraClient(sync_http, settings.token, base_url=settings.base_url)
            poller = JiraPoller(
                client,
                events,
                base_url=settings.base_url,
                watch_projects=settings.watch_projects,
                state=state,
                persist=persist,
                poll_interval=settings.poll_interval_s,
                per_page=settings.per_page,
                self_account=settings.self_account,
                project_map=project_map,
            )
            tasks = [poller.run()]
            if push_settings.enabled:
                thread_links = _build_thread_links(events_settings, push_settings)
                tasks.append(
                    _build_notifier(
                        async_http, events, state, persist, settings, push_settings, thread_links
                    ).pump()
                )
            else:
                logger.info(
                    "Push do Teams wyłączony (żaden cel) — drzwi Jira działają ingest-only."
                )
            await asyncio.gather(*tasks)


def _build_thread_links(events_settings: EventsSettings, push_settings: TeamsPushSettings) -> Any:
    """Złóż ``ThreadLinkStore`` (SQLite nad events.db), gdy wątkowanie kanału ON; inaczej ``None``.

    Wątkowanie (ADR 0024, B2) dokłada zdarzenia tego samego zgłoszenia Jiry (utworzenie/tranzycja/
    komentarz) do jednego wątku na kanale — resolver kojarzy je po ``/browse/{KEY}`` w url. Gdy OFF
    (domyślnie) — ``None``, a notifier tworzy nowy root per zdarzenie. Ta sama mapa (osobna tabela)
    co drzwi GitHub na wspólnym ``events.db``; ``kind="jira"`` nie koliduje z ``pr``/``issue``.
    """
    if not push_settings.enable_channel_threading:
        return None
    from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore

    return SqliteThreadLinkStore(events_settings.db_path)


def _build_notifier(
    async_http: Any,
    events: EventService,
    state: dict[str, Any],
    persist: Callable[[dict[str, Any]], None],
    settings: JiraSettings,
    push_settings: TeamsPushSettings,
    thread_links: Any = None,
) -> Any:
    """Złóż notifiera EventStore → Teams dla źródła ``jira`` (dual-target). Wymaga MSAL (teams).

    ``thread_links`` (ADR 0024, B2): gdy podany (wątkowanie ON), zdarzenia tego samego zgłoszenia
    lecą do jednego wątku na kanale; gdy ``None`` (OFF) — każde jako nowy root.
    """
    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_PUSH) from exc
    from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier
    from workmate.core.application.notifier import EventNotifier, NotifyTargets

    token_provider = build_token_provider(push_settings)
    sender = HttpxTeamsNotifier(async_http, token_provider)
    targets = NotifyTargets(
        chat_user_id=push_settings.chat_user_id,
        team_id=push_settings.team_id,
        channel_id=push_settings.channel_id,
        enable_chat=push_settings.enable_chat,
        enable_channel=push_settings.enable_channel,
    )

    def save_cursor(cursor_id: int) -> None:
        state["notify_cursor"] = cursor_id
        persist(state)

    return EventNotifier(
        events,
        sender,
        targets=targets,
        save_cursor=save_cursor,
        cursor=int(state.get("notify_cursor", 0)),
        source="jira",
        poll_interval=settings.poll_interval_s,
        thread_links=thread_links,
    )


if __name__ == "__main__":
    main()
