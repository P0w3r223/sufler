"""Testy kursorowego narzędzia odczytu zdarzeń ``read_events_since`` (A3, ADR 0040).

Semantyka kursora jest w porcie/magazynie, więc testujemy na realnym magazynie in-memory
(``SqliteEventStore(":memory:")`` + ``EventService``), a kopertę błędów — na atrapie
rzucającej ``RepositoryError``.
Kontrakt narzędzia: zawsze rosnąco po ``id``; ``latest_cursor`` = najwyższe zwrócone ``id`` (albo
niezmieniony kursor, gdy nic nowego), tak by kolejne wywołanie z ``after_id=latest_cursor`` dało
WYŁĄCZNIE nowe zdarzenia (paginacja aż do ``count`` = 0).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workmate.core.application.events import EventService
from workmate.core.application.tools import build_events_since_catalog
from workmate.core.domain.events import NewEvent
from workmate.core.errors import RepositoryError

_WHEN = datetime(2026, 7, 27, tzinfo=UTC)


def _tool(catalog):
    return next(spec.fn for spec in catalog if spec.name == "read_events_since")


def _service(*sources: str) -> EventService:
    """``EventService`` nad realnym magazynem in-memory z jednym zdarzeniem per ``source`` z listy.

    ``external_id`` = indeks, więc id rosną 1,2,3… w kolejności podania (kursor deterministyczny).
    """
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore

    service = EventService(SqliteEventStore(":memory:"))
    for i, source in enumerate(sources, start=1):
        service.ingest(
            NewEvent(
                source=source,
                kind="issue_opened",
                external_id=str(i),
                title=f"zdarzenie {i}",
                occurred_at=_WHEN + timedelta(minutes=i),
            )
        )
    return service


def _service_with_projects(*projects: str) -> EventService:
    """``EventService`` in-memory z jednym zdarzeniem per ``project`` z listy (id rosną 1,2,3…)."""
    from workmate.adapters.outbound.sqlite_events import SqliteEventStore

    service = EventService(SqliteEventStore(":memory:"))
    for i, project in enumerate(projects, start=1):
        service.ingest(
            NewEvent(
                source="github",
                kind="issue_opened",
                external_id=str(i),
                title=f"zdarzenie {i}",
                project=project,
                occurred_at=_WHEN + timedelta(minutes=i),
            )
        )
    return service


class _RaisingService:
    """Atrapa, której odczyt zawsze rzuca ``RepositoryError`` (test koperty błędów)."""

    def recent(self, *, source=None, project=None, limit=20):
        raise RepositoryError("magazyn zdarzeń niedostępny")

    def read_since(self, after_id, *, source=None, project=None, limit=50):
        raise RepositoryError("magazyn zdarzeń niedostępny")


def test_catalog_exposes_only_read_events_since():
    catalog = build_events_since_catalog(_service("github"))
    assert [spec.name for spec in catalog] == ["read_events_since"]


def test_bootstrap_returns_newest_window_ascending_with_head_cursor():
    fn = _tool(build_events_since_catalog(_service("github", "jira", "teams")))

    result = fn(limit=2)  # bootstrap: bez after_id → najnowsze okno

    assert result["count"] == 2
    ids = [e["id"] for e in result["events"]]
    assert ids == sorted(ids)  # rosnąco po id
    assert ids == [2, 3]  # najnowsze DWA (2,3), ale rosnąco
    assert result["latest_cursor"] == 3  # głowa strumienia — kolejny poll od 3


def test_bootstrap_on_empty_store_yields_zero_cursor():
    fn = _tool(build_events_since_catalog(_service()))

    result = fn()

    assert result["count"] == 0
    assert result["events"] == []
    assert result["latest_cursor"] == 0  # after_id=0 w kolejnym wywołaniu → wszystko od początku


def test_incremental_returns_only_newer_than_cursor():
    fn = _tool(build_events_since_catalog(_service("github", "jira", "teams")))

    result = fn(after_id=1)  # tylko id > 1

    assert [e["id"] for e in result["events"]] == [2, 3]
    assert result["latest_cursor"] == 3


def test_incremental_at_head_reports_nothing_new_and_keeps_cursor():
    fn = _tool(build_events_since_catalog(_service("github", "jira")))

    result = fn(after_id=2)  # kursor na głowie — nic nowego

    assert result["count"] == 0
    assert result["events"] == []
    assert result["latest_cursor"] == 2  # kursor NIEZMIENIONY (nie cofa się do 0)


def test_pagination_walks_the_stream_with_returned_cursor():
    fn = _tool(build_events_since_catalog(_service("a", "b", "c", "d", "e")))

    first = fn(after_id=0, limit=2)
    assert [e["id"] for e in first["events"]] == [1, 2]
    assert first["latest_cursor"] == 2

    second = fn(after_id=first["latest_cursor"], limit=2)
    assert [e["id"] for e in second["events"]] == [3, 4]
    assert second["latest_cursor"] == 4

    third = fn(after_id=second["latest_cursor"], limit=2)
    assert [e["id"] for e in third["events"]] == [5]
    assert third["latest_cursor"] == 5

    assert fn(after_id=third["latest_cursor"], limit=2)["count"] == 0  # domknięcie: nic nowego


def test_source_filter_narrows_the_stream():
    fn = _tool(build_events_since_catalog(_service("github", "jira", "github")))

    result = fn(after_id=0, source="github")

    assert [e["source"] for e in result["events"]] == ["github", "github"]
    assert [e["id"] for e in result["events"]] == [1, 3]
    assert result["latest_cursor"] == 3


def test_project_filter_narrows_the_stream():
    fn = _tool(build_events_since_catalog(_service_with_projects("workmate", "scada", "workmate")))

    result = fn(after_id=0, project="workmate")  # tryb przyrostowy + filtr projektu

    assert [e["project"] for e in result["events"]] == ["workmate", "workmate"]
    assert [e["id"] for e in result["events"]] == [1, 3]
    assert result["latest_cursor"] == 3


def test_bootstrap_project_filter_narrows_and_stays_ascending():
    fn = _tool(build_events_since_catalog(_service_with_projects("workmate", "scada", "workmate")))

    result = fn(project="workmate")  # bootstrap (recent) + filtr projektu — inna gałąź niż wyżej

    assert [e["project"] for e in result["events"]] == ["workmate", "workmate"]
    assert [e["id"] for e in result["events"]] == [1, 3]  # rosnąco mimo odwrócenia okna
    assert result["latest_cursor"] == 3


def test_read_error_is_enveloped():
    fn = _tool(build_events_since_catalog(_RaisingService()))

    result = fn(after_id=0)  # tryb przyrostowy → read_since rzuca RepositoryError

    # RepositoryError → {"error": ...}
    assert "error" in result and "niedostępny" in result["error"]
    assert "events" not in result


def test_bootstrap_read_error_is_enveloped():
    fn = _tool(build_events_since_catalog(_RaisingService()))

    result = fn()  # bootstrap → recent rzuca RepositoryError (druga gałąź koperty)

    assert "error" in result and "niedostępny" in result["error"]
    assert "events" not in result


def test_bootstrap_source_filter_narrows_and_stays_ascending():
    fn = _tool(build_events_since_catalog(_service("github", "jira", "github")))

    result = fn(source="github")  # bootstrap (recent) + filtr source — symetryczny do project

    assert [e["source"] for e in result["events"]] == ["github", "github"]
    assert [e["id"] for e in result["events"]] == [1, 3]  # rosnąco mimo odwrócenia okna
    assert result["latest_cursor"] == 3


def test_limit_is_clamped_to_ceiling_and_floor():
    from workmate.core.application.tools import _MAX_EVENTS_READ

    # Więcej zdarzeń niż pułap, by udowodnić ścięcie GÓRNE (a nie tylko „mniej niż jest").
    service = _service(*[f"s{i}" for i in range(_MAX_EVENTS_READ + 5)])
    fn = _tool(build_events_since_catalog(service))

    high = fn(after_id=0, limit=10_000)  # ogromny limit nie wciąga całego backlogu
    assert high["count"] == _MAX_EVENTS_READ
    assert high["latest_cursor"] == _MAX_EVENTS_READ

    low = fn(after_id=0, limit=-1)  # -1 = brak limitu w SQLite → ścięte do 1, nie do całości
    assert low["count"] == 1


# --- ten sam ślad po filtrze co na drzwiach Teams (przegląd 2026-08-21) -----


def test_filtered_stream_carries_the_narrowed_view_marker():
    """Te same filtry nad tym samym magazynem dają tę samą fałszywą nieobecność.

    Drzwi Teams dostały ślad po filtrze; bez niego sesja Claude Code odpowiadałaby „nie ma"
    na podstawie widoku, który wykluczył wszystko, co bot sam zapisał do GitHuba z Teamsów.
    """
    fn = _tool(build_events_since_catalog(_service("github", "teams")))

    wynik = fn(source="github")

    assert "source='github'" in wynik["note"]


def test_unfiltered_stream_carries_no_marker():
    # Kontrakt kursorowy JUŻ każe powtarzać odczyt, aż ``count`` = 0 — bez filtru nie ma
    # o czym uprzedzać, a zdanie w każdej turze byłoby szumem.
    fn = _tool(build_events_since_catalog(_service("github", "teams")))

    assert "note" not in fn()


def test_source_description_says_it_is_the_recording_door():
    # Opis obiecywał „issue, PR, CI, recenzje", czyli SYSTEM docelowy — a `source` niesie
    # drzwi, które zdarzenie zapisały. To ta sama nieprawda co w `Activity(action='events')`.
    (spec,) = build_events_since_catalog(_service("github"))

    assert "DRZWI" in spec.description
    assert "recenzje" not in spec.description
    # ADR 0071 decyzja 10: to samo fałszywe zdanie stało na OBU powierzchniach. „Pytaj BEZ
    # filtru" odsyłało po stan GitHuba do widoku, który stanu nie zna — zdjęcie filtru daje
    # pełną historię mostu, nie stan systemu zewnętrznego.
    assert "BEZ tego filtru" not in spec.description
    assert "HISTORIA" in spec.description
