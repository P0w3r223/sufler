"""Testy adaptera SQLite magazynu zdarzeń (SqliteEventStore, ADR 0019).

Realny plik tymczasowy: append/exists/read_since/recent, deduplikacja przez UNIQUE +
ON CONFLICT, filtry (source/project), kursor z sufitem, migracja starszego pliku (ADR 0028)
oraz współbieżność wieloprocesowa (dwa niezależne połączenia do tego samego pliku).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.core.domain.events import NewEvent, composite_external_id

_WHEN = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)


def _event(external_id: str, *, kind: str = "issue_opened", **kw) -> NewEvent:
    base = {
        "source": "github",
        "kind": kind,
        "external_id": external_id,
        "occurred_at": _WHEN,
    }
    base.update(kw)
    return NewEvent(**base)


def test_append_assigns_id_and_ingested_at(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    ev = store.append(_event("7", title="Nowe issue", actor="alice", url="http://x/7"))
    assert ev.id == 1
    assert ev.external_id == "7"
    assert ev.title == "Nowe issue"
    assert ev.actor == "alice"
    assert ev.ingested_at is not None
    assert ev.occurred_at == _WHEN


def test_exists_reflects_key(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    assert store.exists("github", "7", "issue_opened") is False
    store.append(_event("7"))
    assert store.exists("github", "7", "issue_opened") is True
    assert store.exists("github", "7", "issue_comment") is False  # inny kind


def test_append_dedup_on_conflict_keeps_first(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    first = store.append(_event("7", title="oryginał"))
    again = store.append(_event("7", title="zmieniony"))
    assert again.id == first.id  # brak dubla
    assert again.title == "oryginał"  # ON CONFLICT DO NOTHING — pierwotny wpis zostaje
    assert len(store.recent(limit=50)) == 1


def test_read_since_and_recent_ordering(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1"))
    store.append(_event("2"))
    store.append(_event("3"))

    since = store.read_since(1)
    assert [e.external_id for e in since] == ["2", "3"]  # rosnąco po id, kursor notifiera
    recent = store.recent(limit=2)
    assert [e.external_id for e in recent] == ["3", "2"]  # najnowsze pierwsze


def test_source_filter(tmp_path):
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1"))
    store.append(NewEvent(source="teams", kind="note", external_id="n1", occurred_at=_WHEN))
    store.append(_event("2"))
    assert [e.external_id for e in store.recent(source="github")] == ["2", "1"]
    assert [e.external_id for e in store.read_since(0, source="teams")] == ["n1"]


def test_two_connections_share_file(tmp_path):
    """Wieloproces: drzwi GitHub piszą, notifier czyta — dwa połączenia, jeden plik (WAL)."""
    path = tmp_path / "events.db"
    writer = SqliteEventStore(path)
    reader = SqliteEventStore(path)
    writer.append(_event("7", title="z drzwi github"))
    hits = reader.read_since(0)
    assert [e.external_id for e in hits] == ["7"]
    assert reader.exists("github", "7", "issue_opened") is True


# --- znaczniki czasu: OBA aware (inaczej porównanie kursora rzuca TypeError) ------


def test_ingested_at_is_timezone_aware_utc_like_occurred_at(tmp_path):
    """``ingested_at`` daje baza (``CURRENT_TIMESTAMP``) i przychodzi BEZ strefy — adapter musi
    ją dokleić.

    Naiwny znacznik obok aware ``occurred_at`` wywraca każde porównanie (``TypeError: can't
    compare offset-naive and offset-aware``), a porównuje je notifier przy decyzji „czy to
    zdarzenie jest świeże". Dotychczasowa asercja (``is not None``) przechodziła również dla
    naiwnego.
    """
    store = SqliteEventStore(tmp_path / "events.db")

    ev = store.append(_event("7"))

    assert ev.ingested_at.tzinfo is not None
    assert ev.ingested_at.utcoffset() == UTC.utcoffset(None)
    assert ev.ingested_at >= ev.occurred_at  # porównanie w ogóle się wykonuje


# --- atrybucja i filtry (ADR 0028) ------------------------------------------------


def test_repo_and_project_round_trip_through_append(tmp_path):
    """Atrybucja (ADR 0028) musi przeżyć zapis — po niej filtruje podgląd projektu."""
    store = SqliteEventStore(tmp_path / "events.db")

    ev = store.append(_event("7", repo="biap/workmate", project="workmate"))

    assert (ev.repo, ev.project) == ("biap/workmate", "workmate")
    assert store.recent()[0].project == "workmate"


def test_project_filter_narrows_read_since_and_recent(tmp_path):
    """Filtr po projekcie to druga (obok ``source``) oś podglądu — bez testu żył wyłącznie
    w SQL-u."""
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1", project="workmate"))
    store.append(_event("2", project="scada-integration"))
    store.append(_event("3", project="workmate"))

    assert [e.external_id for e in store.read_since(0, project="workmate")] == ["1", "3"]
    assert [e.external_id for e in store.recent(project="workmate")] == ["3", "1"]


def test_source_and_project_filters_combine_with_and(tmp_path):
    """Oba warunki naraz muszą się SKŁADAĆ — sklejenie klauzul przez ``OR`` (albo zgubienie
    jednej) przepuściłoby cudze zdarzenia do podglądu projektu."""
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("1", project="workmate"))
    store.append(
        NewEvent(
            source="teams",
            kind="note",
            external_id="n1",
            project="workmate",
            occurred_at=_WHEN,
        )
    )
    store.append(_event("2", project="inny"))

    hits = store.read_since(0, source="github", project="workmate")

    assert [e.external_id for e in hits] == ["1"]


def test_read_since_respects_limit_and_the_cursor_advances(tmp_path):
    """Kursor notifiera: sufit tnie stronę, a ostatnie ``id`` z niej wznawia odczyt bez luki
    i bez powtórki. Bez sufitu jedna runda wciągałaby całą historię."""
    store = SqliteEventStore(tmp_path / "events.db")
    for n in range(1, 6):
        store.append(_event(str(n)))

    first = store.read_since(0, limit=2)
    second = store.read_since(first[-1].id, limit=2)

    assert [e.external_id for e in first] == ["1", "2"]
    assert [e.external_id for e in second] == ["3", "4"]


# --- dedup: klucz to (source, external_id, kind), NIE samo external_id -------------


def test_same_external_id_from_two_sources_are_two_events(tmp_path):
    """``UNIQUE`` obejmuje ``source`` — „#7" na GitHubie i „#7" na Teams to różne rzeczy."""
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event("7"))
    store.append(NewEvent(source="teams", kind="issue_opened", external_id="7", occurred_at=_WHEN))

    assert len(store.recent()) == 2


def test_same_number_in_two_repos_survives_dedup_via_composite_id(tmp_path):
    """ADR 0028: unikalność MIĘDZY repo daje złożenie repo w ``external_id``, nie zmiana klucza.

    Bez ``composite_external_id`` issue #5 w drugim repo trafiłoby na ``ON CONFLICT DO NOTHING``
    pierwszego i NIGDY nie doszłoby do Teams — cicho, bo dedup jest zamierzony.
    """
    store = SqliteEventStore(tmp_path / "events.db")
    store.append(_event(composite_external_id("biap/workmate", "5"), repo="biap/workmate"))
    store.append(_event(composite_external_id("biap/inny", "5"), repo="biap/inny"))

    assert sorted(e.repo for e in store.recent()) == ["biap/inny", "biap/workmate"]


# --- migracja starszego pliku (ADR 0028) ------------------------------------------


def test_pre_0028_db_gains_repo_and_project_columns_keeping_old_rows(tmp_path):
    """``CREATE TABLE IF NOT EXISTS`` nie rusza istniejącej tabeli, więc plik sprzed ADR 0028
    nie ma kolumn ``repo``/``project`` — a indeks po ``project`` by się na nim wywrócił.

    Ten plik żyje w ``~/.workmate/events.db`` i przeżywa aktualizacje, więc ścieżka migracji
    jest realna, nie hipotetyczna.
    """
    path = tmp_path / "events.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(
        """
        CREATE TABLE events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            external_id TEXT NOT NULL,
            actor       TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            summary     TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            UNIQUE(source, external_id, kind)
        );
        INSERT INTO events(source, kind, external_id, title, occurred_at)
        VALUES ('github', 'issue_opened', '1', 'stare zdarzenie', '2026-07-15T10:00:00+00:00');
        """
    )
    legacy.commit()
    legacy.close()

    store = SqliteEventStore(path)

    stare = store.recent()
    assert [e.external_id for e in stare] == ["1"]
    assert (stare[0].repo, stare[0].project) == ("", "")  # backfill pustym, nie NULL-em
    store.append(_event("2", project="workmate"))
    assert [e.external_id for e in store.recent(project="workmate")] == ["2"]


def test_reopening_a_migrated_db_is_idempotent(tmp_path):
    """Drugie otwarcie nie może próbować dokładać kolumn ponownie (``duplicate column name``)."""
    path = tmp_path / "events.db"
    SqliteEventStore(path).append(_event("7"))

    ponownie = SqliteEventStore(path)

    assert [e.external_id for e in ponownie.recent()] == ["7"]


def test_store_creates_missing_parent_directory(tmp_path):
    """Most startuje z ``~/.workmate/events.db`` na świeżej maszynie — brak katalogu nie może
    być błędem startu."""
    store = SqliteEventStore(tmp_path / "brak" / "takiego" / "events.db")

    assert store.append(_event("7")).id == 1
    assert (tmp_path / "brak" / "takiego" / "events.db").exists()


def test_home_relative_path_opens_the_expanded_file_not_a_literal_tilde(tmp_path, monkeypatch):
    """``~`` w ścieżce bazy było rozwijane WYŁĄCZNIE na potrzeby ``mkdir``.

    ``connect`` dostawał napis z ``~``, więc katalog powstawał pod rozwiniętą ścieżką
    (``~/.workmate``), a baza — pod literalnym ``~`` w katalogu roboczym procesu. Domyślna
    konfiguracja mówi ``~/.workmate/events.db``, więc to jest droga produkcyjna, nie egzotyczna.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    store = SqliteEventStore("~/.workmate/events.db")
    store.append(_event("issue-1"))

    assert (tmp_path / ".workmate" / "events.db").is_file()
    assert not (tmp_path / "~").exists()  # żadnego katalogu o nazwie "~" obok
