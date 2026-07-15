"""Entry point drzwi GitHub (delegowany polling PAT, ADR 0020) — ingest zdarzeń + push do Teams.

Uruchomienie: ``uv run workmate-github`` (wymaga ``uv sync --extra github`` oraz
``WORKMATE_GITHUB_TOKEN``/``_OWNER``/``_REPO``). Odpytuje repo o nowe issue i komentarze i
zapisuje je do wspólnego magazynu zdarzeń (``events.db``). Gdy skonfigurowano cele Teams
(``WORKMATE_TEAMS_PUSH_*``), RÓWNOLEGLE (``asyncio.gather``) uruchamia notifiera wypychającego
te zdarzenia do Teams (czat 1:1 i/lub kanał, ADR 0022) — domyka obieg GitHub → baza → Teams.

Importy ``httpx``/MSAL są leniwe; brak extra kończy się czytelnym komunikatem, nie ``ImportError``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound import env
from workmate.config import EventsSettings, GithubSettings, TeamsPushSettings

if TYPE_CHECKING:
    from workmate.core.application.events import EventService

logger = logging.getLogger(__name__)

_MISSING_GITHUB = (
    "Drzwi GitHub wymagają extra 'github'. Zainstaluj: uv sync --extra github"
)
_MISSING_PUSH = (
    "Proaktywny push do Teams wymaga extra 'teams-graph' (MSAL). "
    "Zainstaluj: uv sync --extra teams-graph"
)


def main() -> None:
    """Uruchom proces drzwi GitHub (polling PAT) + opcjonalny push zdarzeń do Teams."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()

    settings = GithubSettings.from_env()
    settings.validate()
    events_settings = EventsSettings.from_env()
    push_settings = TeamsPushSettings.from_env()
    push_settings.validate()
    asyncio.run(_run(settings, events_settings, push_settings))


async def _run(
    settings: GithubSettings,
    events_settings: EventsSettings,
    push_settings: TeamsPushSettings,
) -> None:
    try:
        import httpx
    except ImportError as exc:
        raise SystemExit(_MISSING_GITHUB) from exc
    from workmate.adapters.inbound.github import state as state_store
    from workmate.adapters.inbound.github.poller import GithubPoller
    from workmate.adapters.outbound.github_api import HttpxGithubClient
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService

    events = EventService(SqliteEventStore(events_settings.db_path))
    state = state_store.load(settings.state_path)

    def persist(current: dict[str, Any]) -> None:
        state_store.save(settings.state_path, current)

    # Poller używa sync klienta (wołanego w puli wątków), notifier — async; osobne menedżery.
    async with httpx.AsyncClient(timeout=30) as async_http:
        with httpx.Client(timeout=30) as sync_http:
            client = HttpxGithubClient(sync_http, settings.token, api_base=settings.api_base)
            poller = GithubPoller(
                client,
                events,
                owner=settings.owner,
                repo=settings.repo,
                watch_kinds=settings.watch_kinds,
                state=state,
                persist=persist,
                poll_interval=settings.poll_interval_s,
                per_page=settings.per_page,
                self_login=settings.self_login,
            )
            tasks = [poller.run()]
            if push_settings.enabled:
                tasks.append(
                    _build_notifier(
                        async_http, events, state, persist, settings, push_settings
                    ).pump()
                )
            else:
                logger.info(
                    "Push do Teams wyłączony (żaden cel) — drzwi GitHub działają ingest-only."
                )
            await asyncio.gather(*tasks)


def _build_notifier(
    async_http: Any,
    events: EventService,
    state: dict[str, Any],
    persist: Callable[[dict[str, Any]], None],
    settings: GithubSettings,
    push_settings: TeamsPushSettings,
) -> Any:
    """Złóż notifiera EventStore → Teams (dual-target). Wymaga MSAL (extra teams-graph)."""
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
        poll_interval=settings.poll_interval_s,
    )


if __name__ == "__main__":
    main()
