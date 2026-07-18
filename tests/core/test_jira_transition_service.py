"""Testy JiraWriteService.transition_issue (ADR 0032) — bramkowany best-effort walk po workflow.

Sedno: rdzeń chodzi po tranzycjach hop po hopie na atrapie portu (bez I/O). Pokrywamy niezmienniki
z ADR 0032: direct-hop, pierwszeństwo akcji nad statusem, ambiguity, branch/dead_end, single-hop
safety (cap=1), wielo-hop forced-advance z echem per hop, detekcja cyklu, limit hopów, BRAK
rollbacku przy błędzie w trakcie, pre-flight (klucz/treść) rzucający WriteError oraz idempotencja.
"""

from __future__ import annotations

import pytest

from workmate.core.application.jira import JiraWriteService
from workmate.core.errors import WriteError

_REPORT_KEYS = {
    "transitioned",
    "reached",
    "status",
    "path",
    "hops",
    "stop_reason",
    "available_next",
}


class _WalkWriter:
    """Atrapa ``JiraWritePort`` skryptowana pod walk: kolejka snapshotów + zapis wywołań POST.

    ``read_transitions`` zwraca kolejne snapshoty (``IndexError`` po wyczerpaniu — błędny
    scenariusz ma paść głośno, nie po cichu reużyć ostatniego). ``transition_issue`` notuje POST
    i oddaje ``updated`` (unikat per hop dla ``external_id`` echa) — chyba że id jest w ``fail_ids``
    (wtedy WriteError).
    """

    def __init__(
        self, snapshots, *, updates=None, fail_ids=(), transition_status="", read_fail_at=None
    ):
        self._snapshots = list(snapshots)
        self._updates = list(updates) if updates is not None else None
        self._fail_ids = set(fail_ids)
        self._transition_status = transition_status
        self._read_fail_at = read_fail_at  # indeks odczytu (0-based), który ma paść WriteError
        self.reads = 0
        self.transition_calls: list[tuple[str, str]] = []

    def read_transitions(self, issue_key):
        if self._read_fail_at is not None and self.reads == self._read_fail_at:
            self.reads += 1
            raise WriteError("nie udało się odczytać tranzycji (HTTP 500).")
        snap = self._snapshots[self.reads]
        self.reads += 1
        return snap

    def transition_issue(self, issue_key, transition_id):
        if transition_id in self._fail_ids:
            raise WriteError("nie udało się wykonać tranzycji (HTTP 500).")
        n = len(self.transition_calls)
        self.transition_calls.append((issue_key, transition_id))
        if self._updates is not None:
            updated = self._updates[n]
        else:
            updated = f"2026-07-15T10:0{n}:00.000+0200"
        return {
            "url": f"https://j/browse/{issue_key}",
            "status": self._transition_status,
            "updated": updated,
        }


class _FakeEvents:
    """Atrapa ``EventService`` — notuje echa hopów (``source="teams"``)."""

    def __init__(self) -> None:
        self.ingested: list = []

    def ingest(self, event):
        self.ingested.append(event)
        return event


def _t(id_: str, name: str, to_status: str) -> dict[str, str]:
    return {"id": id_, "name": name, "to_status": to_status}


def _snap(current: str, transitions: list[dict[str, str]]) -> dict:
    return {"current_status": current, "transitions": transitions}


def _svc(writer, *, events=None, project="WM", hops=1) -> JiraWriteService:
    return JiraWriteService(writer, project=project, events=events, max_transition_hops=hops)


def _steps(path: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(h["from"], h["to"]) for h in path]


# --- (1) direct-hop: cel jest widocznym sąsiadem ----------------------------


def test_direct_hop_when_target_is_visible_neighbor():
    writer = _WalkWriter([_snap("To Do", [_t("11", "Start Progress", "In Progress")])])
    events = _FakeEvents()

    result = _svc(writer, events=events).transition_issue("WM-5", "In Progress")

    assert result["reached"] is True
    assert result["transitioned"] is True
    assert result["stop_reason"] is None
    assert result["status"] == "In Progress"
    assert result["hops"] == 1
    assert result["path"] == [
        {"from": "To Do", "to": "In Progress", "at": "2026-07-15T10:00:00.000+0200"}
    ]
    assert writer.transition_calls == [("WM-5", "11")]  # dokładnie jeden POST
    assert len(events.ingested) == 1  # jedno echo
    assert result["available_next"] == []  # sukces → puste (oszczędzamy GET)


# --- (2) pierwszeństwo nazwy AKCJI nad nazwą statusu, case-insensitive -------


def test_action_name_match_takes_precedence_over_target_status_name():
    writer = _WalkWriter(
        [
            _snap(
                "Open",
                [
                    _t("11", "Done", "Closed"),  # nazwa AKCJI == "Done"
                    _t("21", "Finish", "Done"),  # to_status == "Done"
                ],
            )
        ]
    )

    result = _svc(writer).transition_issue("WM-5", "done")  # lowercase → trafia w akcję "Done"

    assert writer.transition_calls == [("WM-5", "11")]  # akcja wygrywa nad statusem
    assert result["status"] == "Closed"
    assert result["reached"] is True


# --- (3) dwie tranzycje pasujące do tego samego celu → ambiguous, brak mutacji


def test_two_transitions_to_same_status_is_ambiguous_no_mutation():
    writer = _WalkWriter([_snap("Open", [_t("11", "Resolve", "Done"), _t("21", "Finish", "Done")])])

    result = _svc(writer).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "ambiguous_target"
    assert result["reached"] is False
    assert result["transitioned"] is False
    assert result["path"] == []
    assert writer.transition_calls == []  # nic nie zmutowaliśmy
    assert {a["action"] for a in result["available_next"]} == {"Resolve", "Finish"}


def test_two_transitions_with_same_action_name_is_ambiguous():
    writer = _WalkWriter([_snap("Open", [_t("11", "Go", "A"), _t("21", "Go", "B")])])

    result = _svc(writer).transition_issue("WM-5", "go")

    assert result["stop_reason"] == "ambiguous_target"
    assert writer.transition_calls == []


# --- (4) cel niewidoczny: branch_point (>1 opcji) / dead_end (0 opcji) -------


def test_non_neighbor_target_with_multiple_options_is_branch_point():
    writer = _WalkWriter([_snap("Open", [_t("11", "A", "X"), _t("21", "B", "Y")])])

    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "branch_point"
    assert result["reached"] is False
    assert result["path"] == []
    assert writer.transition_calls == []  # nie zgadujemy na rozgałęzieniu


def test_non_neighbor_target_with_no_options_is_dead_end():
    writer = _WalkWriter([_snap("Closed", [])])

    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "dead_end"
    assert result["path"] == []
    assert writer.transition_calls == []


# --- (5) cap=1 (domyślny) to single-hop: nie rusza, gdy cel nie jest sąsiadem


def test_cap_one_does_not_move_when_target_not_direct_neighbor():
    writer = _WalkWriter([_snap("To Do", [_t("11", "Start", "In Progress")])])

    result = _svc(writer, hops=1).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "hop_cap"
    assert result["reached"] is False
    assert result["transitioned"] is False
    assert result["path"] == []
    assert writer.transition_calls == []  # gwarancja bezpieczeństwa pilotażu: zero mutacji
    assert {a["to_status"] for a in result["available_next"]} == {"In Progress"}


# --- (6) cap>=2: forced-advance dochodzi do celu wiele hopów dalej, echo per hop


def test_multi_hop_forced_advance_reaches_target_with_per_hop_echo():
    snaps = [
        _snap("To Do", [_t("11", "f1", "In Progress")]),  # wymuszony (jedna opcja)
        _snap("In Progress", [_t("21", "f2", "In Review")]),  # wymuszony
        _snap("In Review", [_t("31", "Finish", "Done")]),  # cel jest sąsiadem
    ]
    writer = _WalkWriter(snaps)
    events = _FakeEvents()

    result = _svc(writer, events=events, hops=3).transition_issue("WM-5", "Done")

    assert result["reached"] is True
    assert result["hops"] == 3
    assert _steps(result["path"]) == [
        ("To Do", "In Progress"),
        ("In Progress", "In Review"),
        ("In Review", "Done"),
    ]
    assert writer.transition_calls == [("WM-5", "11"), ("WM-5", "21"), ("WM-5", "31")]
    assert len(events.ingested) == 3  # jedno echo NA KAŻDY wykonany hop
    assert [e.external_id for e in events.ingested] == [
        f"WM-5:{h['at']}"
        for h in result["path"]  # external_id = f"{key}:{updated}"
    ]
    assert all(e.kind == "jira_transition" and e.source == "teams" for e in events.ingested)


# --- (7) detekcja cyklu: powrót do odwiedzonego statusu zatrzymuje walk ------


def test_cycle_detection_stops_walk():
    snaps = [
        _snap("A", [_t("11", "toB", "B")]),
        _snap("B", [_t("21", "toA", "A")]),
        _snap("A", [_t("11", "toB", "B")]),  # powrót do A (odwiedzony)
    ]
    writer = _WalkWriter(snaps)

    result = _svc(writer, hops=5).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "cycle"
    assert result["status"] == "A"
    assert result["hops"] == 2
    assert writer.transition_calls == [("WM-5", "11"), ("WM-5", "21")]


# --- (8) wyczerpany limit hopów → hop_cap z częściową ścieżką ----------------


def test_hop_cap_exhausted_returns_partial_path():
    snaps = [
        _snap("A", [_t("11", "toB", "B")]),
        _snap("B", [_t("21", "toC", "C")]),
        _snap("C", [_t("31", "toD", "D")]),
    ]
    writer = _WalkWriter(snaps)

    result = _svc(writer, hops=2).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "hop_cap"
    assert result["reached"] is False
    assert result["hops"] == 2
    assert _steps(result["path"]) == [("A", "B"), ("B", "C")]
    assert writer.transition_calls == [("WM-5", "11"), ("WM-5", "21")]


# --- (9) WriteError w trakcie: łapany, ZACHOWANE już wykonane hopy (brak rollbacku)


def test_write_error_on_target_hop_preserves_committed_hops():
    snaps = [
        _snap("A", [_t("11", "toB", "B")]),  # wymuszony — wykona się
        _snap("B", [_t("21", "Finish", "Done")]),  # cel sąsiadem — POST padnie
    ]
    writer = _WalkWriter(snaps, fail_ids={"21"})

    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "error"
    assert result["reached"] is False
    assert _steps(result["path"]) == [("A", "B")]  # wykonany hop zostaje (brak rollbacku)
    assert writer.transition_calls == [("WM-5", "11")]  # nieudany POST nie trafia do zapisu
    assert "detail" in result


def test_write_error_on_forced_hop_returns_error_with_prior_path():
    snaps = [
        _snap("A", [_t("11", "toB", "B")]),  # wymuszony — OK
        _snap("B", [_t("21", "toC", "C")]),  # wymuszony — POST padnie
    ]
    writer = _WalkWriter(snaps, fail_ids={"21"})

    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "error"
    assert _steps(result["path"]) == [("A", "B")]
    assert writer.transition_calls == [("WM-5", "11")]
    # POST forced-hopa padł, issue został w B → available_next = aktualni sąsiedzi B.
    assert {a["to_status"] for a in result["available_next"]} == {"C"}


def test_write_error_on_reread_after_hop_returns_empty_available_next():
    # Forced-hop POST OK, ale re-odczyt po hopie pada: issue jest już przesunięte, a nowych sąsiadów
    # nie znamy — available_next musi być PUSTE (uczciwsze niż stare, nieaktualne tranzycje).
    snaps = [_snap("A", [_t("11", "toB", "B")])]  # tylko pierwszy read; drugi (indeks 1) padnie
    writer = _WalkWriter(snaps, read_fail_at=1)

    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")

    assert result["stop_reason"] == "error"
    assert _steps(result["path"]) == [("A", "B")]  # hop się zdarzył (brak rollbacku)
    assert result["available_next"] == []
    assert "detail" in result


# --- (10) pre-flight: zły/obcy klucz i niebezpieczna treść RZUCAJĄ WriteError


def test_bad_key_raises_write_error_before_any_read():
    writer = _WalkWriter([])  # brak snapshotów — read nie może zostać wywołany
    with pytest.raises(WriteError, match="nie jest poprawnym kluczem"):
        _svc(writer).transition_issue("WM-1/../OPS-1", "Done")
    assert writer.reads == 0
    assert writer.transition_calls == []


def test_foreign_project_key_raises_before_any_read():
    writer = _WalkWriter([])
    with pytest.raises(WriteError, match="spoza skonfigurowanego"):
        _svc(writer, project="WM").transition_issue("OPS-1", "Done")
    assert writer.reads == 0


def test_dangerous_target_content_raises_before_any_read():
    writer = _WalkWriter([])
    with pytest.raises(WriteError):
        _svc(writer).transition_issue("WM-5", "zła\x00treść")
    assert writer.reads == 0


def test_pre_flight_read_error_propagates_as_write_error():
    class _ReadFails(_WalkWriter):
        def read_transitions(self, issue_key):
            raise WriteError("nie udało się odczytać tranzycji (HTTP 404).")

    with pytest.raises(WriteError, match="odczytać"):
        _svc(_ReadFails([])).transition_issue("WM-5", "Done")


# --- (11) już w celu → idempotentny no-op ------------------------------------


def test_already_at_target_is_idempotent_no_op():
    writer = _WalkWriter([_snap("Done", [_t("11", "Reopen", "To Do")])])
    events = _FakeEvents()

    result = _svc(writer, events=events).transition_issue("WM-5", "done")  # case-insensitive

    assert result["reached"] is True
    assert result["transitioned"] is False
    assert result["path"] == []
    assert result["hops"] == 0
    assert result["stop_reason"] is None
    assert writer.transition_calls == []
    assert events.ingested == []


# --- (12) raport zawsze niesie pełny kształt ---------------------------------


def test_report_always_carries_full_shape_on_success():
    writer = _WalkWriter([_snap("To Do", [_t("11", "Start", "In Progress")])])
    result = _svc(writer).transition_issue("WM-5", "In Progress")
    assert set(result) == _REPORT_KEYS


def test_report_carries_full_shape_on_stop():
    writer = _WalkWriter([_snap("Open", [_t("11", "A", "X"), _t("21", "B", "Y")])])
    result = _svc(writer, hops=3).transition_issue("WM-5", "Done")
    assert set(result) >= _REPORT_KEYS  # stop dokłada 'detail' tylko przy błędzie


# --- echo edge cases ---------------------------------------------------------


def test_no_events_service_still_walks_and_reaches():
    writer = _WalkWriter([_snap("To Do", [_t("11", "Start", "In Progress")])])
    result = _svc(writer, events=None).transition_issue("WM-5", "In Progress")
    assert result["reached"] is True  # brak magazynu → brak echa, ale walk działa


def test_echo_skipped_when_hop_has_no_updated_timestamp():
    # Nieudany follow-up GET adaptera → pusty ``updated`` → pomijamy echo, ale hop się LICZY.
    writer = _WalkWriter([_snap("To Do", [_t("11", "Start", "In Progress")])], updates=[""])
    events = _FakeEvents()

    result = _svc(writer, events=events).transition_issue("WM-5", "In Progress")

    assert result["reached"] is True
    assert result["hops"] == 1
    assert result["path"][0]["at"] == ""
    assert events.ingested == []  # brak daty → echo pominięte, tranzycja policzona
