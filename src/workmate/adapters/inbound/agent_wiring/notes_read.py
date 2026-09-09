"""Fabryki odczytu bazy wiedzy dla agenta: serwis notatek, katalog trzech narzędzi, zdarzenia."""

from __future__ import annotations

import functools
import logging
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound.retrieval_wiring import build_lemmatizer, build_semantic_ranker
from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.config import RetrievalSettings
from workmate.core.application.events import EventService
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import (
    build_agent_notes_read_catalog,
    build_project_catalog,
    build_tool_catalog,
)
from workmate.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.config import (
        Settings,
    )
    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.repositories import NotesRepository

logger = logging.getLogger(__name__)


def build_notes_service(
    settings: Settings, *, notes_repo: NotesRepository | None = None
) -> NotesService:
    """Zbuduj serwis wyszukiwania notatek z pełnym rankerem (BM25 nad lematami + opcjonalny dense).

    JEDNO źródło budowy rankera dla wszystkich konsumentów: narzędzi agenta, komendy ``/szukaj``
    i CLI ``workmate-search``. Gdyby CLI składało własny wariant, eval retrievalu mierzyłby coś
    innego, niż wykonuje produkcja, a rozjazd byłby niewidoczny do pierwszego złego wyniku.

    ``notes_repo`` podaje wołający, gdy ma już repozytorium do WSPÓŁDZIELENIA (``_read_services``
    daje to samo ``ProjectsService``) — repozytorium cache'uje wczytane notatki, więc druga
    instancja czytałaby ten sam katalog po raz drugi.

    Lematyzator PL (ADR 0023) degraduje łagodnie do rankingu podłańcuchowego przy braku extra
    ``retrieval``. Dense (ADR 0039) powstaje TYLKO obok lematyzatora — fuzja RRF żyje w gałęzi
    BM25, więc sam byłby cichym no-opem.
    """
    retrieval = RetrievalSettings.from_env()
    retrieval.validate()
    lemmatizer = build_lemmatizer(retrieval)
    semantic = build_semantic_ranker(retrieval) if lemmatizer is not None else None
    return NotesService(
        notes_repo if notes_repo is not None else MarkdownNotesRepository(settings.notes_dir),
        lemmatizer=lemmatizer,
        semantic=semantic,
        rrf_k=retrieval.rrf_k,
        dense_top_n=retrieval.dense_top_n,
    )


def _read_services(settings: Settings) -> tuple[NotesService, ProjectsService]:
    """Komplet serwisów ODCZYTU dla tej konfiguracji — JEDEN na proces, współdzielony.

    Składanie jednych drzwi wołało to trzy razy (runtime, per-turowa fabryka odczytu, katalog
    komend), a każde wywołanie budowało własny ranker semantyczny, czyli własny model ONNX
    w pamięci i własny warmup. Trzy modele i trzy warmupy zamiast jednego — koszt startu
    i pamięci, nie poprawności (repozytoria mają własny cache, a ranker własne zamki).

    Klucz cache'u to CAŁA konfiguracja, od której zależy budowa, a nie samo ``settings``:
    ranker czyta ``RetrievalSettings`` z ENV, a ``ProjectsService`` — obecność pliku
    ``events.db``. Bez tych dwóch składników drzwi zbudowane po zmianie zmiennej środowiskowej
    (albo po pojawieniu się mostu) dostawałyby serwis z poprzedniego świata, i to po cichu.
    """
    return _read_services_cached(settings, RetrievalSettings.from_env(), _events_db_if_present())


@functools.cache
def _read_services_cached(
    settings: Settings,
    _retrieval: RetrievalSettings,
    _events_db: Path | None,
) -> tuple[NotesService, ProjectsService]:
    """Właściwa budowa, memoizowana po pełnym kluczu konfiguracji (patrz ``_read_services``).

    Argumenty z podkreśleniem wchodzą WYŁĄCZNIE do klucza — same wartości czytają niżej
    ``build_notes_service`` (z ENV) i ``_events_if_present`` (z dysku), tak jak przed memoizacją.
    """
    notes_repo = MarkdownNotesRepository(settings.notes_dir)
    projects_repo = YamlProjectsRepository(settings.projects_registry)
    return (
        build_notes_service(settings, notes_repo=notes_repo),
        ProjectsService(projects_repo, notes_repo, events=_events_if_present()),
    )


def _events_db_if_present() -> Path | None:
    """Ścieżka ``events.db``, jeśli plik istnieje — składnik klucza cache'u serwisów odczytu."""
    from workmate.config import EventsSettings

    path = Path(str(EventsSettings.from_env().db_path)).expanduser()
    return path if path.exists() else None


def _events_if_present() -> EventService | None:
    """``EventService`` nad wspólnym ``events.db`` — TYLKO gdy plik istnieje (most w użyciu).

    Wzbogaca ``get_project_status`` o aktywność GitHub (ADR 0029). Bez pliku ``None`` — drzwi
    agenta bez mostu NIE tworzą pustego ``events.db`` tylko pod odczyt statusu.
    """
    from pathlib import Path

    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.config import EventsSettings

    path = EventsSettings.from_env().db_path
    if not Path(str(path)).expanduser().exists():
        return None
    return EventService(SqliteEventStore(path))


def _build_notes_read_factory(
    settings: Settings,
    authorizer: NoteReadAuthorizer,
    *,
    enable_write: bool = False,
    shell_available: bool = False,
) -> Callable[[str], list[ToolSpec]]:
    """Per-turowa fabryka CAŁEJ powierzchni bazy wiedzy, bramkowana NADAWCĄ (ADR 0062).

    Wzorzec jak ``user_push_tool_factory``/``my_jira_tasks_factory``: serwisy budujemy RAZ, fabryka
    na turę domyka je autoryzacją TEGO nadawcy. Rozpoznany członek → realne
    ``Project``/``SearchNotes``/``GetNote``/``ListProjects``; nierozpoznany → te SAME narzędzia
    (nazwa i schemat zachowane przez ``functools.wraps``), ale ich ``fn`` zwraca czytelną odmowę,
    którą model relacjonuje — jak płyną błędy narzędzi. W katalogu bazowym są wtedy STŁUMIONE
    (``suppress_notes_read``), żeby nie było drogi obejścia bramki.

    ``Project`` jest tu razem z trójką odczytu, bo ``Project(action='status')`` serwuje treść
    bazy wiedzy (syntezę z notatek projektu) — zostawiony w katalogu bazowym był jedyną drogą
    odczytu, która przeżyła wpięcie bramki. ADR 0062 §3 zapowiadał złożenie odczytu do
    ``build_project_catalog``; robimy to od strony DRZWI, bo ``core/application/tools/`` jest
    wspólny z powierzchnią MCP (zamrożoną golden-testem), której bramka nie dotyczy.

    ``enable_write`` przenosi profil zapisu drzwi (ADR 0006) na tę fabrykę — inaczej złożenie
    ``Project`` tutaj cicho zabrałoby drzwiom zaufanym akcję ``save``.

    ``shell_available`` steruje SAMĄ TRÓJKĄ odczytu, nie bramką. Z powłoką trójka jest zbędna
    (``workmate-search`` plus ``cat`` na montażu ``ro``), więc fabryka niesie sam ``Project`` —
    ale niesie go NADAL, bo to on serwuje treść i to on musi zostać za bramką. Wcześniej cała
    fabryka wygasała przy włączonej powłoce i ``Project`` wracał do katalogu bazowego, czyli
    obok bramki. Patrz ``agent_wiring.__init__`` przy ``bramka_odczytu_dziala``.
    """
    notes, projects = _read_services(settings)
    write_service = (
        NotesWriteService(
            MarkdownNotesWriter(settings.notes_dir),
            YamlProjectsRepository(settings.projects_registry),
        )
        if enable_write
        else None
    )

    def _refusing(
        original: Callable[..., dict[str, Any]], refusal: dict[str, Any]
    ) -> Callable[..., dict[str, Any]]:
        @functools.wraps(original)  # zachowuje sygnaturę → schemat narzędzia bez zmian
        def refuse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return refusal

        return refuse

    def factory(sender_id: str) -> list[ToolSpec]:
        # Z powłoką trójka odczytu jest zbędna — ale ``Project`` zostaje, bo bramka dotyczy
        # POWIERZCHNI SERWUJĄCEJ TREŚĆ, a nie tego, czy istnieje druga droga do tej samej treści.
        # (Że powłoka czyta montaż ``ro`` obok bramki, mówi ADR 0062 §Ryzyko resztkowe. To
        # osobne ryzyko, przyjęte świadomie, i nie jest powodem, żeby otwierać drogę TRZECIĄ.)
        catalog = [
            *build_project_catalog(projects, write_service=write_service),
            *([] if shell_available else build_agent_notes_read_catalog(notes, projects)),
        ]
        try:
            authorizer.authorize(sender_id)
        except NoteAuthorizationError as exc:
            refusal = {"error": f"Brak uprawnień do odczytu bazy wiedzy: {exc}"}
            return [replace(spec, fn=_refusing(spec.fn, refusal)) for spec in catalog]
        return catalog

    return factory


def build_read_catalog(settings: Settings) -> list[ToolSpec]:
    """Katalog narzędzi TYLKO DO ODCZYTU (bez ``save_note``) — dla komend read-only.

    Zawsze read-only, niezależnie od profilu drzwi: strukturalna gwarancja, że komendy
    (``/szukaj`` itd.) nie omijają bramki zapisu (ADR 0006), nawet na CLI z ``enable_write``.
    """
    notes, projects = _read_services(settings)
    return build_tool_catalog(notes, projects, write_service=None)
