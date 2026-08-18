"""Testy czytelnika powierzchni diagnostycznych (ADR 0069 R2): trzy magazyny, jedno polecenie.

Bronione własności: wpis widać z identyfikatorami, powodem i czasem; filtry po źródle i po okresie
zawężają; ``--json`` niesie te same pola; brak wskazanej bazy to POMYŁKA operatora (kod 1), a pusty
wynik — odpowiedź (kod 0). Osobno pilnujemy, że wydruk kwarantanny wejściowej nie ma skąd wziąć
treści wiadomości: magazyn jej nie przechowuje (ADR 0069 §5) i narzędzie tego nie zmienia.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workmate.adapters.inbound.cli.diagnostics import (
    TimeSpecError,
    _wpisy,
    main,
    parse_moment,
)
from workmate.adapters.outbound.sqlite_audit import SqliteAuditStore
from workmate.adapters.outbound.sqlite_dead_letters import (
    SqliteDeadLetterStore,
    SqliteInboundDeadLetterStore,
)

_TERAZ = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _bez_zmiennych(monkeypatch: pytest.MonkeyPatch) -> None:
    """Środowisko dewelopera nie może decydować o wyniku — każdy test wskazuje bazę sam."""
    monkeypatch.delenv("WORKMATE_EVENTS_DB", raising=False)
    monkeypatch.delenv("WORKMATE_AUDIT_DB", raising=False)


def _events_db(tmp_path: Path) -> Path:
    db = tmp_path / "events.db"
    SqliteDeadLetterStore(db, clock=lambda: _TERAZ - timedelta(days=3)).record(
        source="github", event_id=41, reason="RuntimeError('Graph 503')", attempts=5
    )
    SqliteDeadLetterStore(db, clock=lambda: _TERAZ).record(
        source="teams", event_id=42, reason="HTTP 429", attempts=5
    )
    SqliteInboundDeadLetterStore(db, clock=lambda: _TERAZ).record(
        door="teams_graph",
        message_id="1723800000000",
        channel="team-1/chan-1",
        thread_root_id="1723799000000",
        sender="aad-anna",
        reason="MemoryError('bomba w załączniku')",
        attempts=2,
    )
    return db


def _audit_db(tmp_path: Path) -> Path:
    db = tmp_path / "audit.db"
    SqliteAuditStore(db).record_tool_call(
        occurred_at=_TERAZ,
        actor_key="ab12cd34ef56ab78",
        conversation_key="99aa88bb77cc66dd",
        door="teams_graph",
        tool_name="Project",
        arg_summary='{"action": "search"}',
        status="ok",
        trust_class="T1",
    )
    return db


def test_kwarantanna_wyjsciowa_wypisuje_identyfikatory_powod_i_czas(tmp_path, capsys):
    assert main(["dead-letters", "--db", str(_events_db(tmp_path))]) == 0

    out = capsys.readouterr().out
    assert "zdarzenie 42" in out
    assert "teams" in out
    assert "HTTP 429" in out
    assert _TERAZ.isoformat() in out


def test_kwarantanna_wejsciowa_pokazuje_czym_odnalezc_wiadomosc(tmp_path, capsys):
    """Kanał, wątek, id i nadawca — komplet do otwarcia wątku w Teams."""
    assert main(["inbound", "--db", str(_events_db(tmp_path))]) == 0

    out = capsys.readouterr().out
    for identyfikator in ("1723800000000", "team-1/chan-1", "1723799000000", "aad-anna"):
        assert identyfikator in out
    assert "bomba w załączniku" in out  # powód, nie treść wiadomości


def test_dziennik_audytu_ma_wreszcie_czytelnika(tmp_path, capsys):
    assert main(["audit", "--db", str(_audit_db(tmp_path))]) == 0

    out = capsys.readouterr().out
    assert "Project" in out
    assert "teams_graph" in out
    assert "ab12cd34ef56ab78" in out  # pseudonim, nie tożsamość
    assert "zaufanie T1" in out


def test_filtr_po_zrodle_zaweza(tmp_path, capsys):
    assert main(["dead-letters", "--db", str(_events_db(tmp_path)), "--source", "github"]) == 0

    out = capsys.readouterr().out
    assert "zdarzenie 41" in out
    assert "zdarzenie 42" not in out


def test_filtr_po_okresie_zaweza(tmp_path, capsys):
    """``--since 1d`` liczy się od TERAZ, więc trzydniowy wpis wypada z okna."""
    assert main(["dead-letters", "--db", str(_events_db(tmp_path)), "--since", "1d"]) == 0

    out = capsys.readouterr().out
    assert "zdarzenie 42" in out
    assert "zdarzenie 41" not in out


def test_json_niesie_te_same_pola(tmp_path, capsys):
    assert main(["inbound", "--db", str(_events_db(tmp_path)), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["surface"] == "inbound"
    assert payload["count"] == 1
    (wpis,) = payload["entries"]
    assert wpis["message_id"] == "1723800000000"
    assert wpis["channel"] == "team-1/chan-1"
    assert set(wpis) == {
        "door",
        "message_id",
        "channel",
        "thread_root_id",
        "sender",
        "reason",
        "attempts",
        "failed_at",
    }
    assert "text" not in wpis and "content" not in wpis  # kwarantanna nie niesie treści


def test_pusty_wynik_to_sukces_z_komunikatem(tmp_path, capsys):
    db = tmp_path / "events.db"
    SqliteDeadLetterStore(db)  # tabele są, wpisów nie ma

    assert main(["dead-letters", "--db", str(db)]) == 0
    assert "brak wpisów" in capsys.readouterr().out.lower()


def test_brak_wskazanej_bazy_konczy_sie_bledem(capsys):
    """Ścieżka nie jest zaszyta: bez ``--db`` i bez zmiennej narzędzie nie zgaduje ~/.workmate."""
    assert main(["inbound"]) == 1
    assert "WORKMATE_EVENTS_DB" in capsys.readouterr().err


def test_audyt_bierze_sciezke_ze_swojej_zmiennej(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("WORKMATE_AUDIT_DB", str(_audit_db(tmp_path)))

    assert main(["audit"]) == 0
    assert "Project" in capsys.readouterr().out


def test_zla_sciezka_nie_zaklada_bazy(tmp_path, capsys):
    """Tryb odczytu: pomyłka w ścieżce wraca jako błąd, a nie jako pusty, świeżo założony plik."""
    brakujaca = tmp_path / "nie_ma.db"

    assert main(["audit", "--db", str(brakujaca)]) == 1
    assert "Nie znaleziono" in capsys.readouterr().err
    assert not brakujaca.exists()


def test_baza_bez_tabeli_mowi_ze_magazyn_nic_nie_zapisal(tmp_path, capsys):
    obca = tmp_path / "obca.db"
    sqlite3.connect(obca).close()

    assert main(["inbound", "--db", str(obca)]) == 1
    assert "inbound_dead_letters" in capsys.readouterr().err


def test_nieczytelna_granica_okresu_konczy_sie_bledem(tmp_path, capsys):
    assert main(["audit", "--db", str(_audit_db(tmp_path)), "--since", "wczoraj"]) == 1
    assert "granicy okresu" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("spec", "oczekiwane"),
    [
        ("30m", _TERAZ - timedelta(minutes=30)),
        ("24h", _TERAZ - timedelta(hours=24)),
        ("7d", _TERAZ - timedelta(days=7)),
        ("2026-08-17", datetime(2026, 8, 17, tzinfo=UTC)),
        ("2026-08-17T09:30", datetime(2026, 8, 17, 9, 30, tzinfo=UTC)),
        ("2026-08-17T09:30+02:00", datetime(2026, 8, 17, 7, 30, tzinfo=UTC)),
    ],
)
def test_parse_moment(spec: str, oczekiwane: datetime):
    """Czas naiwny czytamy jako UTC — magazyny zapisują UTC, strefa maszyny nic tu nie znaczy."""
    assert parse_moment(spec, now=_TERAZ) == oczekiwane


def test_parse_moment_odrzuca_bzdury():
    with pytest.raises(TimeSpecError):
        parse_moment("kiedyś", now=_TERAZ)


@pytest.mark.parametrize(
    ("ile", "oczekiwane"), [(0, "0 wpisów"), (1, "1 wpis"), (3, "3 wpisy"), (12, "12 wpisów")]
)
def test_naglowek_odmienia_liczebnik(ile: int, oczekiwane: str):
    assert _wpisy(ile) == oczekiwane
