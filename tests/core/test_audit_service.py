"""Testy ``AuditService`` (ADR 0067): pseudonimizacja raz na turę, redakcja, best-effort.

Serwis zależy tylko od portu ``AuditStore`` — sprawdzamy na atrapie w pamięci, bez bazy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from workmate.core.application.audit import AuditService
from workmate.core.domain.metrics import pseudonymize


class _FakeStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self._fail = fail

    def record_tool_call(self, **kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("baza zablokowana")
        self.rows.append(kwargs)

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        return list(reversed(self.rows))[:limit]


def _clock() -> datetime:
    return datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def test_turn_recorder_pseudonymizes_sender_and_conversation_once():
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="aad-user-42", conversation_id="team/chan/root"
    )
    recorder("File", {"action": "read", "path": "raport.pdf"}, "ok")
    (row,) = store.rows
    assert row["actor_key"] == pseudonymize("aad-user-42")
    assert row["conversation_key"] == pseudonymize("team/chan/root")
    assert row["door"] == "teams"
    assert row["trust_class"] == "unknown"
    assert row["status"] == "ok"
    assert row["occurred_at"] == _clock()


def test_arguments_are_redacted_before_storage():
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )
    recorder("Bash", {"command": "cat /etc/passwd"}, "ok")
    (row,) = store.rows
    assert "cat /etc/passwd" not in row["arg_summary"]
    assert '"command": "<str:15>"' in row["arg_summary"]


def test_recorder_is_best_effort_on_store_failure():
    """Awaria magazynu NIE może wypłynąć — audyt jest poboczny (ADR 0067 §1.1).

    Sonda sprawdza też, że rejestrator NIE zostaje po awarii zepsuty: „przełknąłem wyjątek"
    i „przestałem cokolwiek zapisywać po pierwszym błędzie" wyglądają z zewnątrz identycznie,
    a drugie znaczy dziurę w dzienniku od pierwszego zacięcia bazy do restartu procesu.
    """
    store = _FakeStore(fail=True)
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )

    recorder("Notes", {"action": "save"}, "ok")  # nie rzuca

    assert store.rows == []  # zapis faktycznie nie doszedł — nie połknęliśmy sukcesu
    store._fail = False
    recorder("Notes", {"action": "save"}, "ok")
    assert len(store.rows) == 1  # kolejne wołanie znów zapisuje


def test_trust_class_passes_through():
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c", trust_class="T3"
    )
    recorder("File", {"action": "read"}, "error")
    (row,) = store.rows
    assert row["trust_class"] == "T3"
    assert row["status"] == "error"
    assert row["judge_verdict"] is None


# --- Gniazdo werdyktu sędziego (ADR 0065 §8, znalezisko 9.11) -------------------------


def test_verdict_lands_in_the_same_row_as_the_tool_call():
    """Sonda przeciw kodowi SPRZED poprawki: ``judge_verdict`` był tam na stałe ``None``,
    więc kryterium odbioru Fazy 6 („werdykt w audit.db") było nieosiągalne kodem."""
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )

    recorder.record_verdict("deny", "notatka opisuje inny projekt")
    recorder("File", {"action": "edit", "name": "biap/mpwik/2026-08-01-x"}, "error")

    (row,) = store.rows
    assert row["judge_verdict"] == "deny: notatka opisuje inny projekt"


def test_a_call_without_a_verdict_leaves_the_column_empty():
    """``File(read)`` i każde inne narzędzie nie mają werdyktu — kolumna ma wtedy MILCZEĆ,
    a nie powtarzać ostatni znany werdykt."""
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )

    recorder("File", {"action": "read", "name": "umowa.pdf"}, "ok")

    (row,) = store.rows
    assert row["judge_verdict"] is None


def test_the_verdict_is_consumed_by_the_row_it_belongs_to():
    """Werdykt jednego wywołania nie ma jak dokleić się do NASTĘPNEGO — to cały inwariant
    gniazda: jeden werdykt, jeden wiersz."""
    store = _FakeStore()
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )

    recorder.record_verdict("allow")
    recorder("File", {"action": "edit", "name": "x"}, "ok")
    recorder("Bash", {"command": "ls"}, "ok")

    pierwszy, drugi = store.rows
    assert pierwszy["judge_verdict"] == "allow"
    assert drugi["judge_verdict"] is None


def test_the_slot_is_cleared_even_when_the_store_fails():
    """Awaria zapisu nie może zostawić werdyktu w gnieździe: doczepiłby się do wiersza
    następnego narzędzia i przypisał orzeczenie wywołaniu, którego nie dotyczyło."""
    store = _FakeStore(fail=True)
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )
    recorder.record_verdict("deny", "powód")
    recorder("File", {"action": "edit", "name": "x"}, "error")

    store._fail = False
    recorder("Bash", {"command": "ls"}, "ok")

    (row,) = store.rows
    assert row["tool_name"] == "Bash"
    assert row["judge_verdict"] is None
