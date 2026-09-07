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
import contextlib
import logging
import signal
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound import env
from workmate.config import (
    EventsSettings,
    GithubSettings,
    Settings,
    TeamsPushSettings,
    require_writable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workmate.core.application.ci_autocomment import CiAutoCommentService
    from workmate.core.application.events import EventService

logger = logging.getLogger(__name__)

_MISSING_GITHUB = "Drzwi GitHub wymagają extra 'github'. Zainstaluj: uv sync --extra github"
_MISSING_PUSH = (
    "Proaktywny push do Teams wymaga extra 'teams-graph' (MSAL). "
    "Zainstaluj: uv sync --extra teams-graph"
)


def main() -> None:
    """Uruchom proces drzwi GitHub (polling PAT) + opcjonalny push zdarzeń do Teams."""
    env.load_dotenv()
    env.configure_logging()

    settings = GithubSettings.from_env()
    settings.validate()
    core_settings = Settings.from_env()
    events_settings = EventsSettings.from_env()
    # ``data_dir`` podajemy TU, choć te drzwi rdzenia poza tym nie potrzebują: to one ZAPISUJĄ
    # zdarzenia, więc to one muszą sprawdzić, że plik nie leży w bazie wiedzy. Bramka wołana bez
    # katalogu danych (jak było) kontrolowała sam kształt ścieżki, a inwariant — treść z drzwi nie
    # trafia do notatek — nie miał tu żadnej mocy, mimo że drugie drzwi go egzekwowały.
    events_settings.validate(data_dir=core_settings.data_dir)
    push_settings = TeamsPushSettings.from_env()
    push_settings.validate()
    # R/L1: watermark drzwi i wspólny events.db MUSZĄ być zapisywalne — inaczej stan leci w próżnię
    # na koncie kontenera z niezapisywalnym ~ (fail-fast na starcie, nie cichy crash-loop w pętli).
    require_writable(settings.state_path, "WORKMATE_GITHUB_STATE")
    require_writable(events_settings.db_path, "WORKMATE_EVENTS_DB")
    asyncio.run(_run(settings, events_settings, push_settings, core_settings))


def _resolve_project(registry_path: Path, owner: str, repo: str) -> str:
    """Klucz projektu z rejestru dla repo tych drzwi (ADR 0028/0029) — atrybucja zdarzeń GitHub.

    Best-effort: brak dopasowania albo nieczytelny rejestr → pusty (zdarzenia bez projektu).
    """
    from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository

    slug = f"{owner}/{repo}".strip().lower()
    try:
        for project in YamlProjectsRepository(registry_path).all():
            if any(r.strip().lower() == slug for r in project.github_repos):
                return project.key
    except Exception:
        logger.warning("Nie udało się rozwiązać projektu dla repo %s/%s.", owner, repo)
    return ""


async def _run(
    settings: GithubSettings,
    events_settings: EventsSettings,
    push_settings: TeamsPushSettings,
    core_settings: Settings,
) -> None:
    try:
        import httpx
    except ImportError as exc:
        raise SystemExit(_MISSING_GITHUB) from exc
    from workmate.adapters.inbound.github import state as state_store
    from workmate.adapters.inbound.github.poller import GithubPoller
    from workmate.adapters.inbound.heartbeat import heartbeat_path, write_heartbeat
    from workmate.adapters.outbound.github_api import HttpxGithubClient
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.core.application.events import EventService

    events = EventService(SqliteEventStore(events_settings.db_path))
    state = state_store.load(settings.state_path)
    project = _resolve_project(core_settings.projects_registry, settings.owner, settings.repo)

    def persist(current: dict[str, Any]) -> None:
        state_store.save(settings.state_path, current)

    # Puls żywotności (R5): siostra pliku stanu na wolumenie, odświeżana po każdej udanej rundzie.
    hb_path = heartbeat_path(settings.state_path)

    def beat() -> None:
        write_heartbeat(hb_path)

    # Graceful shutdown (R1): SIGTERM/SIGINT → poller dokańcza rundę, zapisuje i wraca.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows nie ma add_signal_handler — tam zamknięcie idzie przez KeyboardInterrupt (SIGINT).
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

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
                project=project,
                stop=stop,
                heartbeat=beat,
            )
            tasks = [asyncio.create_task(poller.run())]
            if push_settings.enabled:
                thread_links = _build_thread_links(events_settings, push_settings)
                tasks.append(
                    asyncio.create_task(
                        _build_notifier(
                            async_http,
                            events,
                            state,
                            persist,
                            settings,
                            push_settings,
                            events_settings,
                            thread_links,
                        ).pump()
                    )
                )
            else:
                logger.info(
                    "Push do Teams wyłączony (żaden cel) — drzwi GitHub działają ingest-only."
                )
            ci_auto = _build_ci_autocommenter(client, events, state, settings)
            if ci_auto is not None:
                logger.info(
                    "Auto-komentarz CI WŁĄCZONY (deterministyczny, ADR 0024) — porażka CI na PR "
                    "dostanie komentarz konta PAT."
                )

                def save_ci_cursor(cursor_id: int) -> None:
                    state["ci_autocomment_cursor"] = cursor_id
                    persist(state)

                tasks.append(
                    asyncio.create_task(
                        _pump_ci_autocomment(ci_auto, save_ci_cursor, settings.poll_interval_s)
                    )
                )
            # Poller kończy się kooperacyjnie po sygnale stop (dokończ rundę → zapisz → wróć);
            # pętle poboczne (notifier push, kursor CI) anulujemy — zapisy stanu są atomowe,
            # a push jest at-least-once, więc anulowanie w połowie nie uszkadza stanu.
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()  # propaguj awarię pętli (restart); czysty stop = brak wyjątku


def _build_thread_links(events_settings: EventsSettings, push_settings: TeamsPushSettings) -> Any:
    """Złóż ``ThreadLinkStore`` (SQLite nad events.db), gdy wątkowanie kanału ON; inaczej ``None``.

    Wątkowanie (ADR 0024) dokłada zdarzenia tego samego issue/PR do jednego wątku na kanale. Gdy
    OFF (domyślnie) — zwracamy ``None``, a notifier tworzy nowy root per zdarzenie (jak dotąd).
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
    settings: GithubSettings,
    push_settings: TeamsPushSettings,
    events_settings: EventsSettings,
    thread_links: Any = None,
) -> Any:
    """Złóż notifiera EventStore → Teams (dual-target). Wymaga MSAL (extra teams-graph)."""
    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_PUSH) from exc
    from workmate.adapters.inbound.heartbeat import notifier_heartbeat_path, write_heartbeat
    from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier
    from workmate.adapters.outbound.sqlite_dead_letters import SqliteDeadLetterStore
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

    # Kwarantanna niewysyłalnych zdarzeń (ADR 0067 §2): tabela siostra w ``events.db``, więc
    # poison message trafia do dead_letters po ``max_attempts`` próbach, a kursor idzie dalej.
    dead_letters = SqliteDeadLetterStore(events_settings.db_path)
    # Puls NOTIFIERA — odrębny plik obok pulsu pollera; bity po rundzie produktywnej, więc
    # zablokowany notifier (kursor stoi) zdradza się nieświeżym pulsem mimo żywego pollera.
    notify_hb_path = notifier_heartbeat_path(settings.state_path)

    def notifier_beat() -> None:
        write_heartbeat(notify_hb_path)

    return EventNotifier(
        events,
        sender,
        targets=targets,
        save_cursor=save_cursor,
        cursor=int(state.get("notify_cursor", 0)),
        poll_interval=settings.poll_interval_s,
        thread_links=thread_links,
        dead_letters=dead_letters,
        heartbeat=notifier_beat,
    )


def _build_ci_autocommenter(
    client: Any,
    events: EventService,
    state: dict[str, Any],
    settings: GithubSettings,
) -> CiAutoCommentService | None:
    """Złóż serwis auto-komentarza CI, gdy WŁĄCZONY (i włączona bramka zapisu); inaczej ``None``.

    Wymaga OBU flag: ``enable_ci_auto_comment`` (konkretna zdolność, ADR 0024) i
    ``enable_github_write`` (ogólna bramka zapisu, Gate 4) — zgodność wymuszona już w
    ``GithubSettings.validate``. Zapis idzie przez ten sam bramkowany ``GithubWriteService`` co
    narzędzia agenta (jedna sanityzowana ścieżka). Kursor ``ci_autocomment_cursor`` jest
    NIEZALEŻNY od ``notify_cursor`` (osobny konsument).
    """
    if not (settings.enable_ci_auto_comment and settings.enable_github_write):
        return None
    from workmate.core.application.ci_autocomment import CiAutoCommentService
    from workmate.core.application.github import GithubWriteService

    write_service = GithubWriteService(
        client, owner=settings.owner, repo=settings.repo, events=events
    )
    return CiAutoCommentService(
        events,
        write_service,
        cursor=int(state.get("ci_autocomment_cursor", 0)),
    )


async def _pump_ci_autocomment(
    service: CiAutoCommentService,
    save_cursor: Callable[[int], None],
    poll_interval: int,
) -> None:
    """Pętla auto-komentarza CI: co interwał obsłuż nowe porażki CI. Sync serwis → pula wątków.

    ``process_once`` jest synchroniczny (SQLite + sync klient GitHub), więc offloadujemy go do puli
    wątków — nie blokujemy pętli, na której działają równolegle poller i notifier. Kursor utrwalamy
    DOPIERO PO powrocie z puli, NA WĄTKU PĘTLI (jak poller/notifier) — nigdy z wątku roboczego, żeby
    nie zapisywać współbieżnie tego samego pliku stanu (uszkodzenie/wyścig). Zapis tylko gdy kursor
    drgnął (unikamy zbędnej rywalizacji o plik). Błąd rundy nie kładzie pętli (best-effort).
    """
    loop = asyncio.get_running_loop()
    last_saved = service.cursor
    while True:
        try:
            await loop.run_in_executor(None, service.process_once)
            if service.cursor != last_saved:
                save_cursor(service.cursor)
                last_saved = service.cursor
        except Exception:
            logger.exception("Błąd rundy auto-komentarza CI — ponowię za chwilę")
        await asyncio.sleep(poll_interval)


if __name__ == "__main__":
    main()
