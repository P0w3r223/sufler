"""Wiring mostu GitHub ↔ Teams: katalog narzędzi mostu, klient, karty czasu, zdarzenia."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sufler.config import (
    EventsSettings,
    GithubSettings,
)

if TYPE_CHECKING:
    from sufler.adapters.outbound.github_api import HttpxGithubClient
    from sufler.core.application.events import EventService
    from sufler.core.application.tools import ToolSpec
    from sufler.core.application.worklog import WorklogService
    from sufler.core.ports.github import GithubReadPort
    from sufler.core.ports.thread_links import ThreadLinkStore

logger = logging.getLogger(__name__)


def _build_bridge_catalog(
    events_settings: EventsSettings,
    github_settings: GithubSettings,
) -> tuple[list[ToolSpec], Callable[[str], tuple[str, int] | None] | None]:
    """Narzędzia warstwy SPAJAJĄCEJ dla agenta Teams (ADR 0019/0021/0024): zdarzenia + zapis GitHub.

    Zwraca ``(katalog, odczyt_powiązania_wątku)``. ``Activity(action='events')`` jest ZAWSZE (agent
    widzi, co zdarzyło się w innych warstwach). Zapis do GitHub (issue/komentarz) dokładamy TYLKO
    przy włączonej bramce i skonfigurowanym celu — profil per drzwi (ADR 0006/0021). Zdarzenia
    z zapisu idą jako ``source=teams`` (strażnik pętli — notifier ich nie odeśle). Gdy zapis jest
    włączony, oddajemy też odczyt powiązania wątek↔issue — od kroku 5.5 (ADR 0009 paczki) idzie on
    do NAGŁÓWKA SESJI, a nie jako osobne narzędzie ``reply_on_thread``.

    Jira nie ma tu żadnej zdolności mutującej ani zdarzeń push (ADR 0054 zredukował ją do jednej,
    wyłącznie odczytowej funkcji — patrz ``_build_my_jira_tasks_factory``, per nadawca, poza tym
    katalogiem). Propozycja czasu z commitów (ADR 0034) wchodzi BEZ bramki, gdy tylko GitHub jest
    skonfigurowany — po wycięciu ścieżki zapisu to czysty odczyt, a odczyt jest domyślny.
    """
    from sufler.adapters.outbound.sqlite_events import SqliteEventStore
    from sufler.core.application.events import EventService
    from sufler.core.application.tools import build_activity_catalog

    events = EventService(SqliteEventStore(events_settings.db_path))

    if not (github_settings.token and github_settings.owner and github_settings.repo):
        return build_activity_catalog(events=events), None

    from sufler.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
    from sufler.core.application.github import GithubWriteService

    # JEDEN klient GitHub na proces, współdzielony przez odczyt commitów i zapis — dwa klienty
    # do tego samego hosta trzymałyby dwie pule połączeń bez żadnego zysku.
    client = _github_client(github_settings)
    worklog = _worklog_service(client, github_settings)
    if not github_settings.enable_github_write:
        return build_activity_catalog(events=events, worklog=worklog), None

    write_service = GithubWriteService(
        client, owner=github_settings.owner, repo=github_settings.repo, events=events
    )
    thread_links = SqliteThreadLinkStore(events_settings.db_path)
    logger.info(
        "GitHub write WŁĄCZONY dla %s/%s — agent Teams może tworzyć issue/komentarze. "
        "Uwaga (ADR 0024): powiązanie wątku z issue trafi do nagłówka sesji TYLKO, gdy drzwi "
        "GitHub biegną z ENABLE_CHANNEL_THREADING=true na WSPÓLNYM events.db i tej samej parze "
        "team/channel — to notifier zapełnia mapę wątków. Bez tego mapa jest pusta, a agent "
        "komentuje wyłącznie na numer podany przez człowieka.",
        github_settings.owner,
        github_settings.repo,
    )
    return (
        build_activity_catalog(events=events, worklog=worklog, write_service=write_service),
        _make_thread_link_lookup(thread_links),
    )


def _github_client(github_settings: GithubSettings) -> HttpxGithubClient:
    """Zbuduj sync klienta GitHub żyjącego przez cały proces (daemon).

    Klient trzyma pulę połączeń, więc domykamy go przy wyjściu z procesu — bez tego pula
    zostaje sierotą i interpreter zamyka gniazda dopiero przy zbieraniu śmieci.
    """
    import atexit

    import httpx

    from sufler.adapters.outbound.github_api import HttpxGithubClient

    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    return HttpxGithubClient(transport, github_settings.token, api_base=github_settings.api_base)


def _worklog_service(client: GithubReadPort, github_settings: GithubSettings) -> WorklogService:
    """Serwis propozycji czasu z commitów (ADR 0034) — czysty ODCZYT, bez bramki.

    Bramki nie ma celowo: po wycięciu ścieżki zapisu nic tu nie mutuje, a repo trzyma zasadę
    „odczyt domyślny, bramkujemy zapis" (ADR 0006). Zdolność stoi wyłącznie na GitHubie — klucze
    Jira wyłuskujemy regexem z treści commitów, więc konfiguracja Jiry jest tu zbędna.

    Sufity estymacji egzekwujemy TU, bo ``GithubSettings.validate`` woła tylko poller GitHuba
    (``sufler-github``), a to te drzwi liczą propozycję (ta sama asymetria co przy Jirze).

    Zwraca SERWIS, nie katalog: od kroku 5.2 (ADR 0009) propozycja czasu jest akcją
    ``Activity(action='worklog')``, a nie własnym narzędziem.
    """
    from sufler.core.application.worklog import WorklogService
    from sufler.core.domain.worklog import SessionPolicy

    github_settings.validate_worklog_limits()
    service = WorklogService(
        client,
        owner=github_settings.owner,
        repo=github_settings.repo,
        policy=SessionPolicy(
            idle_gap_minutes=github_settings.worklog_idle_gap_minutes,
            ramp_up_minutes=github_settings.worklog_ramp_up_minutes,
            round_minutes=github_settings.worklog_round_minutes,
            max_session_hours=github_settings.worklog_max_session_hours,
            tz=ZoneInfo(github_settings.worklog_tz),
        ),
        max_range_days=github_settings.worklog_max_range_days,
    )
    logger.info(
        "Propozycja czasu z commitów WŁĄCZONA dla %s/%s (strefa %s) — narzędzie jest ODCZYTOWE, "
        "godziny do Jiry wprowadza człowiek arkuszem WorklogPRO (ADR 0035).",
        github_settings.owner,
        github_settings.repo,
        github_settings.worklog_tz,
    )
    return service


def _make_thread_link_lookup(
    thread_links: ThreadLinkStore,
) -> Callable[[str], tuple[str, int] | None]:
    """Odczyt powiązania wątek Teams ↔ issue/PR (ADR 0024, Faza 3b) — do NAGŁÓWKA SESJI.

    Z ``external_id`` (``team/channel/root`` — konwencja tych drzwi) czyta cel z zaufanego
    ``ThreadLinkStore``. Zwraca ``(rodzaj, numer)`` albo ``None``, gdy wątek nie jest z niczym
    powiązany. Numer pochodzi z mapowania, nie od modelu — to się nie zmienia.

    Do kroku 5.5 (ADR 0009 paczki) ta sama informacja jechała jako OSOBNE narzędzie
    ``reply_on_thread`` z numerem domkniętym w closurze. Narzędzie zniesiono, bo wołało tę samą
    metodę (``GithubWriteService.create_comment``) co ``Activity(action='comment')``, za tą samą
    bramką ``enable_github_write`` i obok niej w tym samym katalogu. Nie zawężało więc niczego:
    model, który chciałby skomentować inne issue, miał to drugie narzędzie pod ręką z numerem
    przyjmowanym wprost. Jedyną wartością było wypełnienie argumentu — czyli zastosowanie
    istniejącej zdolności, a takie rzeczy należą do treści promptu, nie do katalogu
    (kryterium §1 „audit harness primitives first").
    """

    def lookup(external_id: str) -> tuple[str, int] | None:
        parts = external_id.split("/")
        if len(parts) != 3:
            return None
        target = thread_links.get_target(*parts)
        if target is None:
            return None
        kind, number = target
        return kind, int(number)

    return lookup


def _events_service(events_settings: EventsSettings) -> EventService | None:
    """``EventService`` nad wspólnym ``events.db`` TYLKO gdy plik istnieje (most zdarzeń w użyciu).

    Wspólny helper dla briefu (aktywność w statusie, ADR 0029) i digestu (fold zdarzeń, ADR 0052).
    Bez pliku ``None`` — drzwi nie tworzą pustego ``events.db`` tylko pod odczyt.
    """
    from pathlib import Path

    from sufler.adapters.outbound.sqlite_events import SqliteEventStore
    from sufler.core.application.events import EventService

    if not Path(str(events_settings.db_path)).expanduser().exists():
        return None
    return EventService(SqliteEventStore(events_settings.db_path))
