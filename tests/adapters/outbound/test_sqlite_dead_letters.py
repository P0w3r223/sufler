"""Testy magazynów kwarantanny SQLite: zdarzeń (ADR 0067 §2) i wiadomości (ADR 0069).

Obie tabele mają tę samą własność do obronienia — idempotencja po kluczu i odczyt najnowsze
pierwsze — ale inny klucz: całkowity ``event_id`` z ``events.db`` kontra TEKSTOWY, nieprzezroczysty
identyfikator wiadomości Graph.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workmate.adapters.outbound.sqlite_dead_letters import (
    SqliteDeadLetterReader,
    SqliteDeadLetterStore,
    SqliteInboundDeadLetterStore,
)
from workmate.adapters.outbound.sqlite_readonly import MissingTableError

_CHWILA = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def _store(db: Path | str = ":memory:", *, at: datetime = _CHWILA) -> SqliteDeadLetterStore:
    return SqliteDeadLetterStore(db, clock=lambda: at)


def _inbound(
    db: Path | str = ":memory:", *, at: datetime = _CHWILA
) -> SqliteInboundDeadLetterStore:
    return SqliteInboundDeadLetterStore(db, clock=lambda: at)


def _porzucona(store: SqliteInboundDeadLetterStore, **nadpisz: object) -> None:
    wpis: dict[str, object] = {
        "door": "teams_graph",
        "message_id": "1723800000000",
        "channel": "team-1/chan-1",
        "thread_root_id": "1723799000000",
        "sender": "aad-anna",
        "reason": "MemoryError('materializacja załącznika')",
        "attempts": 2,
    }
    wpis.update(nadpisz)
    store.record(**wpis)  # type: ignore[arg-type]


def test_record_and_read_back():
    store = _store()
    store.record(source="github", event_id=42, reason="RuntimeError('Graph 503')", attempts=5)
    (row,) = store.recent()
    assert row["source"] == "github"
    assert row["event_id"] == 42
    assert row["attempts"] == 5
    assert "Graph 503" in row["reason"]
    assert row["failed_at"] == "2026-08-13T10:00:00+00:00"


def test_record_is_idempotent_on_source_event_id():
    store = _store()
    store.record(source="github", event_id=7, reason="pierwszy", attempts=5)
    store.record(source="github", event_id=7, reason="drugi", attempts=9)
    rows = store.recent()
    assert len(rows) == 1  # INSERT OR IGNORE — bez duplikatu
    assert rows[0]["reason"] == "pierwszy"  # pierwszy powód zachowany


def test_same_event_id_different_source_are_distinct():
    store = _store()
    store.record(source="github", event_id=1, reason="g", attempts=5)
    store.record(source="jira", event_id=1, reason="j", attempts=5)
    assert len(store.recent()) == 2


def test_recent_newest_first_and_limit():
    store = _store()
    for eid in (1, 2, 3):
        store.record(source="github", event_id=eid, reason="x", attempts=5)
    ids = [r["event_id"] for r in store.recent(limit=2)]
    assert ids == [3, 2]


def test_reason_is_capped():
    store = _store()
    store.record(source="github", event_id=1, reason="x" * 5000, attempts=5)
    assert len(store.recent()[0]["reason"]) == 1000


def test_recent_empty_store():
    assert _store().recent() == []


def test_idempotencja_przezywa_restart_procesu(tmp_path: Path):
    """Powód istnienia idempotencji z docstringu: licznik prób żyje W PAMIĘCI notifiera.

    Po restarcie rusza od zera, więc TO SAMO zdarzenie trafia do kwarantanny drugi raz — już
    z innego połączenia i o innej godzinie. Gwarancji „bez duplikatu i bez nadpisania" broni
    ograniczenie UNIQUE w PLIKU, czego sonda na ``:memory:`` sprawdzić nie może: tam druga
    instancja dostaje własną, pustą bazę.
    """
    db = tmp_path / "events.db"
    _store(db).record(source="github", event_id=7, reason="Graph 503", attempts=5)

    po_restarcie = _store(db, at=_CHWILA + timedelta(days=1))
    po_restarcie.record(source="github", event_id=7, reason="inny powód", attempts=1)

    (row,) = po_restarcie.recent()
    assert row["reason"] == "Graph 503"  # pierwszy powód i pierwsza próba przeżywają
    assert row["attempts"] == 5
    assert row["failed_at"] == _CHWILA.isoformat()


def test_recent_z_limitem_zero_nie_oddaje_nic():
    store = _store()
    store.record(source="github", event_id=1, reason="x", attempts=5)
    assert store.recent(limit=0) == []


def test_katalog_bazy_powstaje_gdy_go_nie_ma(tmp_path: Path):
    db = tmp_path / "swiezy" / "events.db"

    _store(db)

    assert db.parent.is_dir()


# --- kwarantanna WIADOMOŚCI (ADR 0069) --------------------------------------


def test_porzucona_wiadomosc_zapisana_i_odczytana():
    store = _inbound()

    _porzucona(store)

    (row,) = store.recent()
    assert row["door"] == "teams_graph"
    assert row["message_id"] == "1723800000000"
    assert row["channel"] == "team-1/chan-1"
    assert row["thread_root_id"] == "1723799000000"
    assert row["sender"] == "aad-anna"
    assert row["attempts"] == 2
    assert "MemoryError" in row["reason"]
    assert row["failed_at"] == "2026-08-13T10:00:00+00:00"


def test_identyfikator_wiadomosci_zostaje_tekstem():
    """Id wiadomości Graph jest NIEPRZEZROCZYSTE — dziś wygląda na liczbę, jutro nie musi.

    To jest powód, dla którego ``dead_letters`` (całkowity ``event_id``) nie dało się użyć wprost.
    """
    store = _inbound()

    _porzucona(store, message_id="1723800000000-r7")

    assert store.recent()[0]["message_id"] == "1723800000000-r7"


def test_wpis_jest_idempotentny_po_drzwiach_i_id_wiadomosci():
    store = _inbound()

    _porzucona(store, reason="pierwszy", attempts=2)
    _porzucona(store, reason="drugi", attempts=9)

    rows = store.recent()
    assert len(rows) == 1
    assert rows[0]["reason"] == "pierwszy"  # pierwszy powód i pierwszy czas przeżywają
    assert rows[0]["attempts"] == 2


def test_to_samo_id_z_innych_drzwi_to_osobna_pozycja():
    store = _inbound()

    _porzucona(store, door="teams_graph")
    _porzucona(store, door="inne_drzwi")

    assert len(store.recent()) == 2


def test_recent_wiadomosci_najnowsze_pierwsze_i_limit():
    store = _inbound()
    for mid in ("m1", "m2", "m3"):
        _porzucona(store, message_id=mid)

    assert [r["message_id"] for r in store.recent(limit=2)] == ["m3", "m2"]


def test_powod_porzucenia_jest_przycinany():
    store = _inbound()

    _porzucona(store, reason="x" * 5000)

    assert len(store.recent()[0]["reason"]) == 1000


def test_pusta_kwarantanna_wiadomosci():
    assert _inbound().recent() == []


def test_obie_kwarantanny_dziela_jeden_plik_bez_kolizji(tmp_path: Path):
    """Tabele SIOSTRZANE w ``events.db``: wpis po jednej stronie mostu nie widzi drugiej."""
    db = tmp_path / "events.db"
    zdarzenia = _store(db)
    wiadomosci = _inbound(db)

    zdarzenia.record(source="github", event_id=7, reason="Graph 503", attempts=5)
    _porzucona(wiadomosci)

    assert len(zdarzenia.recent()) == 1
    assert len(wiadomosci.recent()) == 1
    assert wiadomosci.recent()[0]["message_id"] == "1723800000000"


def test_idempotencja_wiadomosci_przezywa_restart_procesu(tmp_path: Path):
    """Powód ten sam co przy zdarzeniach: po restarcie ta sama pozycja trafia tu drugi raz.

    Tu jest nawet bliżej: licznik prób wiadomości JEST trwały, ale powód porażki żyje w pamięci
    procesu — druga kwarantanna przyszłaby z uboższym opisem i inną godziną.
    """
    db = tmp_path / "events.db"
    _porzucona(_inbound(db), reason="MemoryError('bomba w załączniku')")

    po_restarcie = _inbound(db, at=_CHWILA + timedelta(days=1))
    _porzucona(po_restarcie, reason="brak zapisanego powodu")

    (row,) = po_restarcie.recent()
    assert row["reason"] == "MemoryError('bomba w załączniku')"
    assert row["failed_at"] == _CHWILA.isoformat()


def test_katalog_bazy_powstaje_takze_dla_kwarantanny_wiadomosci(tmp_path: Path):
    db = tmp_path / "swiezy" / "events.db"

    _inbound(db)

    assert db.parent.is_dir()


# --- czytelnik obu kwarantann (ADR 0069 R2) -----------------------------------------------


def _zapelnij(db: Path) -> None:
    """Dwa wpisy w każdej tabeli, różne źródła i różne chwile — materiał na filtry."""
    _store(db, at=_CHWILA).record(source="github", event_id=1, reason="Graph 503", attempts=5)
    _store(db, at=_CHWILA + timedelta(days=2)).record(
        source="teams", event_id=2, reason="HTTP 429", attempts=5
    )
    _porzucona(_inbound(db, at=_CHWILA), door="teams_graph", message_id="m1")
    _porzucona(
        _inbound(db, at=_CHWILA + timedelta(days=2)),
        door="github",
        message_id="m2",
        reason="TimeoutError",
    )


def test_czytelnik_widzi_obie_tabele_najnowsze_pierwsze(tmp_path: Path):
    db = tmp_path / "events.db"
    _zapelnij(db)

    czytelnik = SqliteDeadLetterReader(db)

    assert [r["event_id"] for r in czytelnik.outbound()] == [2, 1]
    assert [r["message_id"] for r in czytelnik.inbound()] == ["m2", "m1"]


def test_czytelnik_filtruje_po_zrodle(tmp_path: Path):
    """Kolumna źródła jest inna w każdej tabeli (``source`` kontra ``door``) — filtr jeden."""
    db = tmp_path / "events.db"
    _zapelnij(db)

    czytelnik = SqliteDeadLetterReader(db)

    assert [r["event_id"] for r in czytelnik.outbound(source="github")] == [1]
    assert [r["message_id"] for r in czytelnik.inbound(source="teams_graph")] == ["m1"]


def test_czytelnik_filtruje_po_okresie(tmp_path: Path):
    db = tmp_path / "events.db"
    _zapelnij(db)

    czytelnik = SqliteDeadLetterReader(db)
    od_wczoraj = czytelnik.outbound(since=_CHWILA + timedelta(days=1))
    do_wczoraj = czytelnik.inbound(until=_CHWILA + timedelta(days=1))

    assert [r["event_id"] for r in od_wczoraj] == [2]
    assert [r["message_id"] for r in do_wczoraj] == ["m1"]


def test_filtr_okresu_dziala_przed_limitem(tmp_path: Path):
    """``LIMIT`` ma przycinać to, o co operator pytał — nie odcinać wierszy przed filtrem."""
    db = tmp_path / "events.db"
    _zapelnij(db)

    stare = SqliteDeadLetterReader(db).outbound(until=_CHWILA + timedelta(days=1), limit=1)

    assert [r["event_id"] for r in stare] == [1]


def test_czytelnik_nie_pisze_do_bazy(tmp_path: Path):
    """Połączenie diagnostyczne jest ``mode=ro``: pomyłka w narzędziu nie tknie produkcji."""
    db = tmp_path / "events.db"
    _zapelnij(db)
    czytelnik = SqliteDeadLetterReader(db)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        czytelnik._conn.execute("DELETE FROM dead_letters")


def test_czytelnik_nie_zaklada_tabel(tmp_path: Path):
    """Baza bez kwarantanny (np. wskazana pomyłkowo) daje nazwany błąd, nie pusty wynik."""
    db = tmp_path / "obca.db"
    sqlite3.connect(db).close()

    with pytest.raises(MissingTableError, match="dead_letters"):
        SqliteDeadLetterReader(db).outbound()
