"""Wiring routerów komend: notatki ze spotkań i wątków, brief projektu, digest, zapis async."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sufler.config import (
    AgentSettings,
    EventsSettings,
    Settings,
    TeamsGraphSettings,
)

if TYPE_CHECKING:
    from sufler.adapters.inbound.brief_command import BriefRouter
    from sufler.adapters.inbound.change_command import ChangeDigestRouter
    from sufler.adapters.inbound.meeting_command import MeetingNoteRouter
    from sufler.adapters.inbound.thread_note_command import ThreadNoteRouter
    from sufler.core.application.note_read_authz import NoteReadAuthorizer

from sufler.adapters.inbound.teams_graph.wiring_bridge import _events_service
from sufler.adapters.inbound.teams_graph.wiring_common import _MISSING_TEAMS_GRAPH

logger = logging.getLogger(__name__)


def _build_meeting_note_router(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    core_settings: Settings,
    agent_settings: AgentSettings,
) -> MeetingNoteRouter | None:
    """Router komendy ZAPISU ``/notatka`` (produkcyjne M3, ADR 0009 §4 / 0041) albo ``None``.

    ``None``, gdy bramka ``enable_meeting_note_write`` wyłączona (domyślnie, ADR 0006). Włączona:
    składa przepływ M3 z realnych adapterów — transkrypt z Graph (``HttpxGraphTranscriptSource`` na
    tym samym delegowanym tokenie co poller), streszczenie przez Claude (adapter summarizera, extra
    ``agent``) i ZAPIS przez bramkowany, DOPISUJĄCY ``NotesWriteService`` (create-only, ADR
    0006) do PRAWDZIWEJ bazy ``data/notes/``. Config waliduje, że transkrypt jest włączony (skąd
    wziąć treść). ``project``/``date``/``ref`` bierze router z argumentów komendy, nie z treści.
    """
    if not settings.enable_meeting_note_write:
        return None
    try:
        from sufler.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer
        from sufler.adapters.outbound.transcript_sources import HttpxGraphTranscriptSource
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.adapters.inbound.meeting_command import MeetingNoteRouter
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from sufler.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from sufler.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from sufler.core.application.meeting_authz import MeetingNoteAuthorizer
    from sufler.core.application.meeting_notes import MeetingNoteService
    from sufler.core.application.services import NotesWriteService

    transcripts = HttpxGraphTranscriptSource(token_provider)
    summarizer = AnthropicMeetingSummarizer(agent_settings)
    # Pass 2 (ADR 0047): ten sam adapter jest też krytykiem. Włączany flagą jakości (OFF domyślnie,
    # ~2× koszt) — NIE bramka zapisu. None → jednoprzelotowo (0041).
    verifier = summarizer if agent_settings.verify_meeting_note else None
    write_service = NotesWriteService(
        MarkdownNotesWriter(core_settings.notes_dir),
        YamlProjectsRepository(core_settings.projects_registry),
    )
    # Autoryzacja nadawcy (B2 / ADR 0042): AAD id → członek pionu przez katalog tożsamości
    # (fail-closed; config wymusił istnienie pliku). Ta sama mapa zasila "moje zadania" Jira
    # (ADR 0054, pole jira_user) — patrz _build_my_jira_tasks_factory.
    authorizer = MeetingNoteAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))
    # Async (B3 / ADR 0043): przy włączonej bramce async router dostaje scheduler (pula wątków) i
    # callback (sync poster do wątku); inaczej ``(None, None)`` → router liczy inline (0041).
    scheduler, callback = _build_async_note_dispatch(settings, token_provider)
    logger.info(
        "Komenda /notatka WŁĄCZONA (ADR 0009/0041) — agent Teams może złożyć notatkę ze spotkania "
        "z transkryptu Graph do data/notes/ (zapis create-only, ADR 0006). Autoryzacja nadawcy "
        "przez mapę tożsamości %s (członkostwo, ADR 0042). Tryb: %s. Wymaga zakresów transkryptu "
        "na tokenie oraz extra 'agent' (Claude).",
        settings.meeting_note_identities,
        "ASYNC (ack + tło + callback, ADR 0043)" if scheduler else "synchroniczny (inline)",
    )
    return MeetingNoteRouter(
        MeetingNoteService(transcripts, summarizer, write_service, verifier=verifier),
        authorizer=authorizer,
        scheduler=scheduler,
        callback=callback,
    )


def _build_thread_note_router(
    settings: TeamsGraphSettings,
    token_provider: Callable[[], str],
    core_settings: Settings,
    agent_settings: AgentSettings,
) -> ThreadNoteRouter | None:
    """Router przechwycenia „zapisz to" z wątku (ADR 0048, F2) albo ``None``.

    ``None``, gdy bramka ``enable_thread_note_capture`` wyłączona (domyślnie, ADR 0006). Włączona:
    składa przepływ z realnych adapterów — treść wątku z Graph (``HttpxGraphThreadSource`` na tym
    samym delegowanym tokenie co poller, SYNC), streszczenie przez Claude (reuse summarizera M3) i
    ZAPIS przez create-only ``NotesWriteService`` do ``data/notes/``. Autoryzacja nadawcy (B2 /
    ADR 0042) reużywa mapy tożsamości (config wymusił plik). Async współdzieli pulę/poster
    ``/notatka`` (``_build_async_note_dispatch``). ``project`` bierze router z argumentu wzmianki,
    nie z treści wątku (ADR 0009 §3).
    """
    if not settings.enable_thread_note_capture:
        return None
    import atexit

    try:
        import httpx

        from sufler.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer
        from sufler.adapters.outbound.graph_thread_source import HttpxGraphThreadSource
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.adapters.inbound.thread_note_command import ThreadNoteRouter
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory
    from sufler.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
    from sufler.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from sufler.core.application.meeting_authz import MeetingNoteAuthorizer
    from sufler.core.application.services import NotesWriteService
    from sufler.core.application.thread_notes import ThreadNoteService

    # Sync klient httpx żyje przez proces (jak poster async_dispatch); domykamy przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    source = HttpxGraphThreadSource(transport, token_provider)
    summarizer = AnthropicMeetingSummarizer(agent_settings)
    verifier = summarizer if agent_settings.verify_meeting_note else None
    # Jedno repozytorium rejestru dla zapisu i dla podpowiedzi routera: dwa niezależne
    # odczyty tego samego pliku rozjechałyby się przy edycji YAML-a między nimi.
    projects = YamlProjectsRepository(core_settings.projects_registry)
    write_service = NotesWriteService(MarkdownNotesWriter(core_settings.notes_dir), projects)
    authorizer = MeetingNoteAuthorizer(YamlIdentityDirectory(settings.meeting_note_identities))
    scheduler, callback = _build_async_note_dispatch(settings, token_provider)
    logger.info(
        "Przechwycenie 'zapisz to' WŁĄCZONE (ADR 0048) — @wzmianka bota z dyrektywą zapisuje wątek "
        "kanału jako notatkę do data/notes/ (create-only, ADR 0006). Autoryzacja nadawcy przez "
        "mapę tożsamości %s (członkostwo, ADR 0042). Tryb: %s.",
        settings.meeting_note_identities,
        "ASYNC (ack + tło + callback, ADR 0043)" if scheduler else "synchroniczny (inline)",
    )
    return ThreadNoteRouter(
        ThreadNoteService(source, summarizer, write_service, verifier=verifier),
        projects=projects,
        authorizer=authorizer,
        scheduler=scheduler,
        callback=callback,
        # Data notatki liczona w strefie drzwi, nie w UTC znacznika Graph — inaczej „zapisz to"
        # po 22:00 czasu lokalnego zakłada notatkę pod poprzednim dniem, a `-thr-` są niezmienne.
        tz=ZoneInfo(settings.tz_name),
    )


def _build_brief_router(
    settings: TeamsGraphSettings,
    core_settings: Settings,
    events_settings: EventsSettings,
    deliver_pdf: Callable[[str, str, str], None] | None,
    read_authorizer: NoteReadAuthorizer | None = None,
) -> BriefRouter | None:
    """Router one-pagera „ogarnij mnie na <projekt>" (ADR 0051, F4) albo ``None``.

    ``None``, gdy bramka ``enable_project_brief`` wyłączona (domyślnie). Włączona: składa READ-ONLY
    ``ProjectBriefService`` nad tymi samymi repo notatek/projektów co runtime — status (pełna
    synteza, aktywność GitHub gdy most zdarzeń istnieje) + ostatnie notatki. Bez zapisu i bez
    nowego narzędzia MCP (golden surface nietknięty), ale Z bramką członkostwa ODCZYTU
    (``read_authorizer``, ADR 0062): brief serwuje treść notatek, więc „read-only" go z niej nie
    zwalnia. ``deliver_pdf`` (współdzielony, ADR 0026) wysyła ``| pdf`` plikiem; ``None`` →
    ``| pdf`` degraduje do tekstu.
    """
    if not settings.enable_project_brief:
        return None
    from sufler.adapters.inbound.brief_command import BriefRouter
    from sufler.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
    from sufler.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
    from sufler.core.application.project_brief import ProjectBriefService
    from sufler.core.application.services import NotesService, ProjectsService

    notes_repo = MarkdownNotesRepository(core_settings.notes_dir)
    projects_repo = YamlProjectsRepository(core_settings.projects_registry)
    # Empty-query search zwraca notatki po dacie (nie rankinguje zapytania), więc NotesService bez
    # extra retrievalu — brief listuje NAJŚWIEŻSZE notatki projektu.
    service = ProjectBriefService(
        NotesService(notes_repo),
        ProjectsService(projects_repo, notes_repo, events=_events_service(events_settings)),
    )
    logger.info(
        "One-pager 'ogarnij mnie na <projekt>' WŁĄCZONY (ADR 0051) — @wzmianka bota zwraca brief "
        "projektu (status + ostatnie notatki), READ-ONLY. Dostawa PDF: %s.",
        "włączona (reuse file-reply)" if deliver_pdf else "wyłączona (| pdf → tekst)",
    )
    return BriefRouter(service, deliver_pdf=deliver_pdf, read_authorizer=read_authorizer)


def _build_change_digest_router(
    settings: TeamsGraphSettings,
    events_settings: EventsSettings,
    deliver_pdf: Callable[[str, str, str], None] | None,
    read_authorizer: NoteReadAuthorizer | None = None,
) -> ChangeDigestRouter | None:
    """Router digestu „co się zmieniło od <data>" (ADR 0052, F5) albo ``None``.

    ``None``, gdy bramka ``enable_change_digest`` wyłączona (domyślnie). Włączona: składa READ-ONLY
    ``ChangeDigestService`` nad wspólnym ``events.db`` (fold zdarzeń od daty, per projekt). Bez
    mostu zdarzeń → digest pusty (dozwolona degradacja). Bez zapisu/autoryzacji/nowego narzędzia.
    ``deliver_pdf`` (współdzielony z briefem) wysyła ``| pdf`` plikiem; inaczej degraduje do tekstu.
    """
    if not settings.enable_change_digest:
        return None
    from sufler.adapters.inbound.change_command import ChangeDigestRouter
    from sufler.core.application.change_digest import ChangeDigestService

    service = ChangeDigestService(_events_service(events_settings))
    logger.info(
        "Digest 'co się zmieniło od <data>' WŁĄCZONY (ADR 0052) — @wzmianka bota zwraca przegląd "
        "zmian od daty (fold zdarzeń per projekt), READ-ONLY. Dostawa PDF: %s.",
        "włączona (reuse file-reply)" if deliver_pdf else "wyłączona (| pdf → tekst)",
    )
    return ChangeDigestRouter(service, deliver_pdf=deliver_pdf, read_authorizer=read_authorizer)


@functools.cache
def _build_async_note_dispatch(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> tuple[Callable[[Callable[[], None]], None] | None, Callable[[str, str], None] | None]:
    """Scheduler (pula wątków) + callback (sync poster do wątku) dla async ``/notatka`` (ADR 0043).

    ``(None, None)``, gdy ``enable_meeting_note_async`` wyłączona — router liczy inline (0041).
    Włączona: OGRANICZONA pula wątków (``meeting_note_async_workers`` = sufit równoległych łańcuchów
    transkrypt+Claude) i ``HttpxGraphThreadReplyPoster`` (sync, ten sam delegowany token co poller).
    Callback wyłuskuje cel ``team/channel/root`` z ``external_id`` wątku (NIE od modelu) i tam
    wrzuca wynik. Pula i klient żyją przez proces; domykamy je przy wyjściu (jak inne sync klienty).

    ``functools.cache`` jest tu CZĘŚCIĄ KONTRAKTU, nie optymalizacją. Docstring
    ``_build_thread_note_router`` deklaruje, że async „współdzieli pulę/poster ``/notatka``", ale
    obaj wołający liczyli tę funkcję osobno, a ona bezwarunkowo stawiała nowy ``ThreadPoolExecutor``
    i nowy klient HTTP. Przy obu bramkach włączonych sufit równoległych łańcuchów transkrypt+Claude
    był więc faktycznie DWUKROTNOŚCIĄ ``meeting_note_async_workers``, a rejestracji ``atexit`` były
    cztery zamiast dwóch — rozjazd niewidoczny w niczym poza ``ps``. Argumenty są haszowalne
    (ustawienia to zamrożony dataclass, dostawca tokenu — funkcja), a wpis żyje tyle co proces,
    czyli dokładnie tyle, co pula i klient. Ten sam chwyt co ``_read_services_cached``.
    """
    if not settings.enable_meeting_note_async:
        return None, None
    import atexit
    from concurrent.futures import ThreadPoolExecutor

    try:
        import httpx

        from sufler.adapters.outbound.graph_thread_reply import HttpxGraphThreadReplyPoster
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc

    executor = ThreadPoolExecutor(
        max_workers=settings.meeting_note_async_workers, thread_name_prefix="meeting-note"
    )
    atexit.register(lambda: executor.shutdown(wait=False))
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    poster = HttpxGraphThreadReplyPoster(transport, token_provider)

    def scheduler(thunk: Callable[[], None]) -> None:
        # Zlecenie do puli jest NIEBLOKUJĄCE; Future świadomie porzucamy (wynik idzie do wątku,
        # nie do wołającego). Przekroczenie puli → zadania czekają w kolejce (bounded równoległość).
        executor.submit(thunk)

    def callback(external_id: str, text: str) -> None:
        parts = external_id.split("/")
        if len(parts) != 3:
            logger.warning("Zły external_id callbacku /notatka: %r — pomijam.", external_id)
            return
        team_id, channel_id, root_id = parts
        poster.post(team_id, channel_id, root_id, text)

    logger.info(
        "Async /notatka WŁĄCZONY (ADR 0043) — ACK natychmiast, łańcuch w tle (%d wątków), wynik do "
        "wątku kanału. Idempotencja (deterministyczny id) chroni retry.",
        settings.meeting_note_async_workers,
    )
    return scheduler, callback
