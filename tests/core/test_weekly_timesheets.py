"""Testy przebiegu tygodniowego kart czasu (ADR 0035) — izolacja, kolejność, idempotencja.

Wszystko na atrapach strukturalnych i wstrzykniętym zegarze: żadnego Graph, openpyxl ani sieci.
Najwięcej uwagi poświęcamy nie „szczęśliwej ścieżce", lecz temu, co się dzieje, gdy jedna osoba
zawiedzie — bo to ona decyduje, czy w piątek dostanie wiadomość pozostałych czternaście.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from workmate.core.application.weekly_timesheets import (
    FAIL_BAD_DATA,
    FAIL_SEND,
    FAIL_SHEET,
    FAIL_STATE_WRITE,
    FAIL_UNKNOWN_PERSON,
    SKIP_NO_WORK,
    WeeklyTimesheetService,
)
from workmate.core.domain.timesheet import Person, WorkEntry

_TZ = ZoneInfo("Europe/Warsaw")
# Piątek 24.07.2026, 16:00 lokalnie. Okno raportowania to tydzień ZAMKNIĘTY, czyli 13–19.07
# (2026-W29) — nie bieżący. Dzięki temu weekend 18–19.07 wchodzi do raportu zamiast wypaść
# z obu przebiegów (D9); wpisy testowe stoją w dniach 13–19, więc mieszczą się w oknie.
_NOW = datetime(2026, 7, 24, 14, 0, tzinfo=timezone.utc)

_MIKOLAJ = Person(
    source_id="EMP-042", aad_user_id="aad-mikolaj", jira_user="mikolaj@example.org", display_name="Mikołaj"
)
_PIOTR = Person(
    source_id="EMP-017", aad_user_id="aad-piotr", jira_user="piotr@example.org", display_name="Piotr"
)


def _entry(source_id: str, day: int, minutes: int, issue: str = "WT-12") -> WorkEntry:
    return WorkEntry(source_id=source_id, day=date(2026, 7, day), issue_key=issue, minutes=minutes)


class _FakeHours:
    def __init__(self, entries: list[WorkEntry]) -> None:
        self.entries = entries
        self.windows: list[tuple[date, date]] = []

    def read(self, since: date, until: date) -> list[WorkEntry]:
        self.windows.append((since, until))
        return self.entries


class _FakeIdentities:
    def __init__(self, people: list[Person]) -> None:
        self.by_id = {p.source_id: p for p in people}

    def resolve(self, source_id: str) -> Person | None:
        return self.by_id.get(source_id)


class _RecordingWriter:
    def __init__(self, fail_for: str = "") -> None:
        self.written: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_for = fail_for

    def write(self, path: str, headers: tuple, rows: tuple) -> None:
        if self.fail_for and self.fail_for in path:
            raise OSError("dysk niedostępny")
        self.written.append((path, rows))


class _RecordingSender:
    def __init__(self, fail_for: str = "") -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail_for = fail_for

    def __call__(self, aad_user_id: str, html: str) -> None:
        if self.fail_for and self.fail_for == aad_user_id:
            raise RuntimeError("Graph 503")
        self.sent.append((aad_user_id, html))


def _service(
    entries: list[WorkEntry],
    *,
    people: list[Person] | None = None,
    writer: _RecordingWriter | None = None,
    sender: _RecordingSender | None = None,
    dry_run: bool = False,
    done: set[tuple[str, str]] | None = None,
    **kw,
) -> tuple[WeeklyTimesheetService, _RecordingWriter, _RecordingSender]:
    writer = writer or _RecordingWriter()
    sender = sender or _RecordingSender()
    seen = done if done is not None else set()
    service = WeeklyTimesheetService(
        _FakeHours(entries),  # type: ignore[arg-type]
        _FakeIdentities(people if people is not None else [_MIKOLAJ, _PIOTR]),  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        sender,
        output_dir="D:/worklogi",
        tz=_TZ,
        dry_run=dry_run,
        already_done=lambda week, sid: (week, sid) in seen,
        mark_done=lambda week, sid, outcome: seen.add((week, sid)),
        now=lambda: _NOW,
        **kw,
    )
    return service, writer, sender


# --- ścieżka podstawowa ----------------------------------------------------------


def test_sends_one_message_per_person_who_worked() -> None:
    entries = [_entry("EMP-042", 15, 180), _entry("EMP-017", 16, 120)]
    service, writer, sender = _service(entries)
    report = service.run()
    assert len(report.sent) == 2
    assert {aad for aad, _ in sender.sent} == {"aad-mikolaj", "aad-piotr"}
    assert len(writer.written) == 2


def test_reports_the_week_that_is_already_closed() -> None:
    service, _, _ = _service([_entry("EMP-042", 15, 60)])
    assert service.run().week_label == "2026-W29"


def test_reads_the_correct_half_open_window() -> None:
    hours = _FakeHours([_entry("EMP-042", 15, 60)])
    service = WeeklyTimesheetService(
        hours,  # type: ignore[arg-type]
        _FakeIdentities([_MIKOLAJ]),  # type: ignore[arg-type]
        _RecordingWriter(),  # type: ignore[arg-type]
        _RecordingSender(),
        output_dir="D:/w",
        tz=_TZ,
        dry_run=False,
        now=lambda: _NOW,
    )
    service.run()
    assert hours.windows == [(date(2026, 7, 13), date(2026, 7, 20))]


def test_message_carries_the_file_path() -> None:
    service, _, sender = _service([_entry("EMP-042", 15, 60)])
    service.run()
    ((_, html),) = sender.sent
    assert "worklog_mikolaj_emp-042_2026-w29.xlsx" in html


def test_sheet_path_is_deterministic_and_person_scoped() -> None:
    service, writer, _ = _service([_entry("EMP-042", 15, 60)])
    service.run()
    ((path, _),) = writer.written
    assert path == "D:/worklogi/worklog_mikolaj_emp-042_2026-w29.xlsx"


# --- bramka „czy pracował" -------------------------------------------------------


def test_person_without_hours_gets_no_message() -> None:
    entries = [_entry("EMP-042", 15, 180), _entry("EMP-017", 15, 0)]
    service, writer, sender = _service(entries)
    report = service.run()
    assert [o.source_id for o in report.skipped] == ["EMP-017"]
    assert [o.reason for o in report.skipped] == [SKIP_NO_WORK]
    assert {aad for aad, _ in sender.sent} == {"aad-mikolaj"}


def test_nobody_worked_means_no_messages_at_all() -> None:
    service, writer, sender = _service([_entry("EMP-042", 15, 0)])
    report = service.run()
    assert sender.sent == [] and writer.written == []
    assert report.sent == []


# --- izolacja błędów -------------------------------------------------------------


def test_unknown_person_is_failed_closed_and_others_continue() -> None:
    """Bez PEWNEJ tożsamości nie powstaje ani plik, ani wiadomość — a reszta idzie dalej."""
    entries = [_entry("EMP-999", 15, 60), _entry("EMP-042", 15, 60)]
    service, writer, sender = _service(entries)
    report = service.run()
    assert [o.reason for o in report.failed] == [FAIL_UNKNOWN_PERSON]
    assert [o.source_id for o in report.sent] == ["EMP-042"]
    assert all("999" not in path for path, _ in writer.written)


def test_bad_data_fails_only_that_person() -> None:
    entries = [_entry("EMP-042", 15, 20 * 60), _entry("EMP-017", 15, 60)]
    service, _, sender = _service(entries, max_minutes_per_day=16 * 60)
    report = service.run()
    assert [o.reason for o in report.failed] == [FAIL_BAD_DATA]
    assert [aad for aad, _ in sender.sent] == ["aad-piotr"]


def test_sheet_failure_does_not_send_a_message_pointing_at_a_missing_file() -> None:
    """Kolejność kontraktowa: plik PRZED wiadomością, więc awaria zapisu wstrzymuje wysyłkę."""
    entries = [_entry("EMP-042", 15, 60), _entry("EMP-017", 15, 60)]
    service, _, sender = _service(entries, writer=_RecordingWriter(fail_for="mikolaj"))
    report = service.run()
    assert [o.reason for o in report.failed] == [FAIL_SHEET]
    assert [aad for aad, _ in sender.sent] == ["aad-piotr"]


def test_send_failure_isolates_that_person() -> None:
    entries = [_entry("EMP-042", 15, 60), _entry("EMP-017", 15, 60)]
    service, writer, sender = _service(entries, sender=_RecordingSender(fail_for="aad-mikolaj"))
    report = service.run()
    assert [o.reason for o in report.failed] == [FAIL_SEND]
    assert [o.source_id for o in report.sent] == ["EMP-017"]
    assert len(writer.written) == 2  # plik Mikołaja POWSTAŁ, tylko wiadomość nie wyszła


def test_failed_send_is_retried_next_run() -> None:
    """Osoba nieoznaczona jako obsłużona — następny przebieg ma ją dosłać."""
    done: set[tuple[str, str]] = set()
    entries = [_entry("EMP-042", 15, 60)]
    service, _, _ = _service(entries, sender=_RecordingSender(fail_for="aad-mikolaj"), done=done)
    service.run()
    assert done == set()


# --- idempotencja ----------------------------------------------------------------


def test_second_run_in_the_same_week_sends_nothing() -> None:
    done: set[tuple[str, str]] = set()
    entries = [_entry("EMP-042", 15, 180)]
    service, _, sender = _service(entries, done=done)
    service.run()
    service2, _, sender2 = _service(entries, done=done)
    report = service2.run()
    assert sender2.sent == []
    assert report.sent == []


def test_success_is_recorded_per_week_and_person() -> None:
    done: set[tuple[str, str]] = set()
    service, _, _ = _service([_entry("EMP-042", 15, 60)], done=done)
    service.run()
    assert done == {("2026-W29", "EMP-042")}


# --- tryb próbny -----------------------------------------------------------------


def test_dry_run_writes_sheets_but_sends_nothing() -> None:
    """Arkusz jest artefaktem DO PRZEJRZENIA przed uruchomieniem bojowym — musi powstać."""
    service, writer, sender = _service([_entry("EMP-042", 15, 60)], dry_run=True)
    report = service.run()
    assert len(writer.written) == 1
    assert sender.sent == []
    assert report.dry_run is True


def test_dry_run_does_not_persist_state() -> None:
    done: set[tuple[str, str]] = set()
    service, _, _ = _service([_entry("EMP-042", 15, 60)], dry_run=True, done=done)
    service.run()
    assert done == set()  # inaczej bojowy przebieg pominąłby wszystkich


# --- filtr pilotażowy ------------------------------------------------------------


def test_only_source_ids_restricts_the_run() -> None:
    entries = [_entry("EMP-042", 15, 60), _entry("EMP-017", 15, 60)]
    service, _, sender = _service(entries, only_source_ids=("EMP-042",))
    service.run()
    assert [aad for aad, _ in sender.sent] == ["aad-mikolaj"]


# --- raport ----------------------------------------------------------------------


def test_summary_counts_all_three_buckets() -> None:
    entries = [_entry("EMP-042", 15, 60), _entry("EMP-017", 15, 0), _entry("EMP-999", 15, 60)]
    service, _, _ = _service(entries)
    summary = service.run().summary()
    assert "wysłano 1" in summary and "pominięto 1" in summary and "błędy 1" in summary


def test_summary_marks_dry_run() -> None:
    service, _, _ = _service([_entry("EMP-042", 15, 60)], dry_run=True)
    assert "PRÓBNY" in service.run().summary()


# --- izolacja osób w treści ------------------------------------------------------


def test_each_message_contains_only_that_persons_hours() -> None:
    """Najgorszy możliwy błąd tej funkcji — cudze godziny w czyjejś wiadomości."""
    entries = [_entry("EMP-042", 15, 180, "WT-12"), _entry("EMP-017", 15, 120, "WT-99")]
    service, _, sender = _service(entries)
    service.run()
    by_person = dict(sender.sent)
    assert "WT-12" in by_person["aad-mikolaj"] and "WT-99" not in by_person["aad-mikolaj"]
    assert "WT-99" in by_person["aad-piotr"] and "WT-12" not in by_person["aad-piotr"]


def test_state_write_failure_does_not_stop_the_rest_of_the_queue() -> None:
    """Wyjątek zapisu stanu przy osobie 1. nie może pozbawić wiadomości osoby 2.

    Regresja: ``mark_done`` stało poza ``try``, wbrew deklaracji modułu, że wyjątek jednej
    osoby nie zatrzymuje pozostałych. Pełny dysk w środku kolejki wywracał CAŁY przebieg,
    a osoba, przy której to nastąpiło, miała już wysłaną wiadomość i nie była zapisana jako
    obsłużona — przy ponowieniu dostałaby ją drugi raz.
    """
    sender = _RecordingSender()

    def mark_done(week: str, source_id: str, outcome) -> None:
        if source_id == "EMP-042":
            raise OSError("brak miejsca na dysku")

    service = WeeklyTimesheetService(
        _FakeHours([_entry("EMP-042", 15, 180), _entry("EMP-017", 16, 120)]),  # type: ignore[arg-type]
        _FakeIdentities([_MIKOLAJ, _PIOTR]),  # type: ignore[arg-type]
        _RecordingWriter(),  # type: ignore[arg-type]
        sender,
        output_dir="D:/worklogi",
        tz=_TZ,
        dry_run=False,
        already_done=lambda week, sid: False,
        mark_done=mark_done,
        now=lambda: _NOW,
    )

    report = service.run()

    # Obie wiadomości wyszły — pętla przeżyła awarię zapisu stanu.
    assert {aad for aad, _ in sender.sent} == {"aad-mikolaj", "aad-piotr"}
    # Awaria jest WIDOCZNA w raporcie, z własnym powodem (człowiek musi wiedzieć o ryzyku
    # powtórnej wiadomości przy ponowieniu).
    assert [(f.source_id, f.reason) for f in report.failed] == [("EMP-042", FAIL_STATE_WRITE)]
