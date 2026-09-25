"""Warstwa spajająca przez narzędzie ``GitHub`` (ADR 0019/0021; krok 5.2 ADR 0009 paczki).

Te sondy biegły dawniej na ``build_events_catalog`` i ``build_github_write_catalog`` — dwóch
builderach, które krok 5.2 osierocił. Zostały PRZENIESIONE, nie skasowane, bo sprawdzają rzecz,
której ``test_github_catalog.py`` nie sprawdza: całą ścieżkę na **prawdziwym**
``GithubWriteService``, nie na atrapie zapisu. Atrapa potwierdza, że dispatcher woła to, co
trzeba; te sondy — że serwis pod nim faktycznie składa odpowiedź i tłumaczy ``WriteError``
na kopertę.

Bramka zapisu jest ta sama co przy ``save_note``: bez ``write_service`` akcje ``create_issue``
i ``comment`` nie istnieją w schemacie (sonda negatywna stoi w ``test_github_catalog.py``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sufler.core.application.github import GithubWriteService
from sufler.core.application.tools import build_activity_catalog
from sufler.core.domain.events import Event, NewEvent
from sufler.core.errors import WriteError

_WHEN = datetime(2026, 7, 15, tzinfo=UTC)


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[Event] = []

    def exists(self, s, e, k):
        return False

    def append(self, event: NewEvent) -> Event:
        row = Event(id=len(self.rows) + 1, ingested_at=_WHEN, **event.model_dump())
        self.rows.append(row)
        return row

    def read_since(self, after_id, *, source=None, project=None, limit=50):
        return []

    def recent(self, *, source=None, project=None, limit=20):
        return list(reversed(self.rows))[:limit]

    def recent_by_time(self, *, source=None, project=None, limit=20):
        """Jak ``recent``, ale po CZASIE ZDARZENIA — wiernie wobec magazynu (ADR 0071).

        Nie alias: alias ukryłby różnicę, o którą w tej zmianie chodzi, a atrapa odpowiadałaby
        na pytanie o czas kolejnością przyjęcia.
        """
        okno = self.recent(source=source, project=project, limit=limit)
        return sorted(okno, key=lambda e: e.occurred_at, reverse=True)


class _EventsService:
    """Minimalna atrapa ``EventService`` dla akcji ``events``."""

    def __init__(self, store):
        self._store = store

    def recent(self, *, source=None, project=None, limit=20):
        return self._store.recent(source=source, project=project, limit=limit)

    def recent_by_time(self, *, source=None, project=None, limit=20):
        """Jak ``recent``, ale po CZASIE ZDARZENIA — wiernie wobec magazynu (ADR 0071).

        Nie alias: alias ukryłby różnicę, o którą w tej zmianie chodzi, a atrapa odpowiadałaby
        na pytanie o czas kolejnością przyjęcia.
        """
        okno = self.recent(source=source, project=project, limit=limit)
        return sorted(okno, key=lambda e: e.occurred_at, reverse=True)


class _OkWriter:
    def create_issue(self, owner, repo, title, body, labels):
        return {"number": 7, "html_url": "http://gh/7", "created_at": "2026-07-15T10:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        return {"id": 1, "html_url": "http://gh/c/1", "created_at": "2026-07-15T10:00:00Z"}


class _FailingWriter:
    def create_issue(self, *a, **k):
        raise WriteError("nie udało się utworzyć issue (HTTP 422).")

    def create_comment(self, *a, **k):
        raise WriteError("nie udało się dodać komentarza (HTTP 403).")


def _github(*, write=None):
    store = _FakeStore()
    store.append(NewEvent(source="github", kind="issue_opened", external_id="1", occurred_at=_WHEN))
    events = _EventsService(store)
    service = GithubWriteService(write, owner="o", repo="r") if write is not None else None
    return build_activity_catalog(events=events, write_service=service)[0]  # type: ignore[arg-type]


def test_akcja_events_czyta_zdarzenia_ze_sklepu():
    wynik = _github().fn(action="events")
    assert wynik["count"] == 1
    assert wynik["events"][0]["external_id"] == "1"


def test_create_issue_idzie_przez_prawdziwy_serwis_zapisu():
    wynik = _github(write=_OkWriter()).fn(action="create_issue", title="Awaria", body="opis")
    assert wynik == {"created": True, "number": 7, "url": "http://gh/7"}


def test_comment_idzie_przez_prawdziwy_serwis_zapisu():
    wynik = _github(write=_OkWriter()).fn(action="comment", number=7, body="ok")
    assert wynik["created"] is True


def test_write_error_serwisu_wraca_koperta_a_nie_wyjatkiem():
    """``WriteError`` z HTTP 422 ma dojść do modelu jako czytelny błąd, nie wywrócić tury."""
    wynik = _github(write=_FailingWriter()).fn(action="create_issue", title="x", body="y")
    assert "error" in wynik and "422" in wynik["error"]
