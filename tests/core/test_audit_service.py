"""Testy ``AuditService`` (ADR 0067): pseudonimizacja raz na turę, redakcja, best-effort.

Serwis zależy tylko od portu ``AuditStore`` — sprawdzamy na atrapie w pamięci, bez bazy.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    return datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


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
    store = _FakeStore(fail=True)
    recorder = AuditService(store, clock=_clock).turn_recorder(
        door="teams", raw_user="u", conversation_id="c"
    )
    # Awaria magazynu NIE może wypłynąć — audyt jest poboczny (ADR 0067 §1.1).
    recorder("Notes", {"action": "save"}, "ok")


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
