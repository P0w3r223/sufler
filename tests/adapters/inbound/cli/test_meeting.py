"""Testy lokalnego harnessu M3 (``workmate.adapters.inbound.cli.meeting``, ADR 0009).

Harness spina wklejony transkrypt → streszczenie → bramkowany, dopisujący zapis. Klucz:
całe okablowanie testujemy BEZ Claude (atrapa ``MeetingSummarizer``) i BEZ zaśmiecania
prawdziwej bazy — zapis idzie prawdziwym ``MarkdownNotesWriter`` do ``tmp_path``. Ścieżki
I/O konsoli (czytanie transkryptu, parsowanie daty, format raportu) testowane osobno.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

from tests.conftest import FakeProjectsRepository
from workmate.adapters.inbound.cli import meeting
from workmate.adapters.inbound.cli.meeting import (
    _build_summarizer_or_exit,
    _default_out_dir,
    _format_result,
    _parse_date,
    _read_transcript,
    main,
    run_harness,
)
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.config import AgentSettings
from workmate.core.application.services import NotesWriteService
from workmate.core.domain.models import MeetingSummary, Project
from workmate.core.domain.transcript import SpeakerRoster
from workmate.core.errors import LLMError, WriteError


class _FakeSummarizer:
    """Atrapa ``MeetingSummarizer`` — zwraca stałe streszczenie, notuje wejście (bez Claude)."""

    def __init__(self, summary: MeetingSummary) -> None:
        self._summary = summary
        self.seen: list[str] = []

    def summarize(self, transcript: str, roster: SpeakerRoster) -> MeetingSummary:
        self.seen.append(transcript)
        return self._summary


def _summary() -> MeetingSummary:
    return MeetingSummary(
        title="Przeglad API",
        participants=["Anna Kowalska", "Jan Nowak"],
        decisions=["Zamrozic kontrakt v1"],
        action_items=["Anna: draft"],
        open_questions=["Kto zatwierdza?"],
        tags=["api"],
        body="Krotkie streszczenie przebiegu.",
    )


def _write_service(out_dir: Path) -> NotesWriteService:
    projects = FakeProjectsRepository(
        [Project(key="scada-integration", company="mpwik", name="SCADA", description="d")],
        records={},
    )
    return NotesWriteService(MarkdownNotesWriter(out_dir), projects)


def test_run_harness_writes_note_to_disk_without_claude(tmp_path: Path):
    summarizer = _FakeSummarizer(_summary())

    outcome = run_harness(
        "Transkrypt: ... ustalenia ...",
        project="scada-integration",
        meeting_date=date(2026, 7, 20),
        summarizer=summarizer,
        write_service=_write_service(tmp_path),
    )

    # Transkrypt trafił do summarizera; miejsce zapisu z wywołania (firma z rejestru).
    assert summarizer.seen == ["Transkrypt: ... ustalenia ..."]
    assert outcome.created is True
    assert outcome.note is not None
    note = outcome.note
    # id deterministyczny z meeting_ref (nie ze slug tytułu Claude), ADR 0043.
    assert note.id.startswith("mpwik/scada-integration/2026-07-20-mtg-")
    # Notatka realnie zapisana na dysku w KATALOGU HARNESSU (nie w data/notes/).
    written = tmp_path / f"{note.id}.md"
    assert written.is_file()
    assert "Krotkie streszczenie przebiegu." in written.read_text(encoding="utf-8")


def test_run_harness_location_from_caller_not_transcript(tmp_path: Path):
    # Streszczenie nie niesie project/date — o dacie/projekcie decyduje wywołujący (ADR 0009 §3).
    outcome = run_harness(
        "dowolna tresc",
        project="scada-integration",
        meeting_date=date(2024, 1, 2),
        summarizer=_FakeSummarizer(_summary()),
        write_service=_write_service(tmp_path),
    )

    assert outcome.note is not None
    assert outcome.note.metadata.project == "scada-integration"
    assert outcome.note.metadata.date == date(2024, 1, 2)
    assert outcome.note.id.startswith("mpwik/scada-integration/2024-01-02-mtg-")


def test_run_harness_unknown_project_raises_write_error(tmp_path: Path):
    with pytest.raises(WriteError):
        run_harness(
            "tresc",
            project="nieistniejacy",
            meeting_date=date(2026, 7, 20),
            summarizer=_FakeSummarizer(_summary()),
            write_service=_write_service(tmp_path),
        )


def test_read_transcript_from_file(tmp_path: Path):
    path = tmp_path / "spotkanie.txt"
    path.write_text("Ala ma kota", encoding="utf-8")
    assert _read_transcript(path) == "Ala ma kota"


def test_read_transcript_empty_file_exits(tmp_path: Path):
    path = tmp_path / "pusty.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _read_transcript(path)


def test_read_transcript_missing_file_exits(tmp_path: Path):
    with pytest.raises(SystemExit):
        _read_transcript(tmp_path / "nie-ma.txt")


def test_read_transcript_from_piped_stdin(monkeypatch: pytest.MonkeyPatch):
    # StringIO.isatty() → False, więc gałąź „potok stdin" czyta treść.
    monkeypatch.setattr("sys.stdin", io.StringIO("z potoku"))
    assert _read_transcript(None) == "z potoku"


def test_read_transcript_interactive_tty_exits(monkeypatch: pytest.MonkeyPatch):
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", _Tty(""))
    with pytest.raises(SystemExit):
        _read_transcript(None)


def test_parse_date_valid():
    assert _parse_date("2026-07-20") == date(2026, 7, 20)


def test_parse_date_invalid_raises():
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_date("20-07-2026")


def test_format_result_reports_id_title_and_counts(tmp_path: Path):
    # Uczestnicy w raporcie pochodzą z rostera mówców (ADR 0047), nie z MeetingSummary.participants
    # — transkrypt musi nieść realne etykiety, żeby diaryzacja wyszła kompletna
    # (>=2 mówców, gęste tury).
    transcript = (
        "Anna Kowalska: Zaczynamy przeglad API.\n"
        "Jan Nowak: Ustalilismy zakres.\n"
        "Anna Kowalska: Domykamy checkliste.\n"
        "Jan Nowak: Zgoda, zamrazamy kontrakt."
    )
    outcome = run_harness(
        transcript,
        project="scada-integration",
        meeting_date=date(2026, 7, 20),
        summarizer=_FakeSummarizer(_summary()),
        write_service=_write_service(tmp_path),
    )

    report = _format_result(outcome, tmp_path, is_default_out=False)

    assert outcome.note_id in report
    assert "Przeglad API" in report
    assert "Anna Kowalska" in report
    assert "decyzje:     1" in report


def test_default_out_dir_is_under_system_temp():
    import tempfile

    out = _default_out_dir()
    assert out.name == "workmate-m3-harness"
    # Katalog tymczasowy (poza repo i poza data/notes/) — harness domyślnie nie zaśmieca bazy.
    assert out.parent == Path(tempfile.gettempdir())


# --- ścieżki błędów main(): kontrakt „czytelny komunikat, nie traceback" (inwariant 5) ---


def test_main_fail_fast_without_api_key(monkeypatch: pytest.MonkeyPatch):
    # Brak klucza → validate() rzuca ValueError; main() zamienia go na czytelny SystemExit
    # (NIE traceback) — tak jak obiecuje docstring modułu.
    monkeypatch.setattr("sys.argv", ["workmate-meeting", "--project", "x", "--date", "2026-07-20"])
    monkeypatch.setattr(AgentSettings, "from_env", staticmethod(lambda: AgentSettings(api_key="")))

    with pytest.raises(SystemExit) as exc:
        main()

    assert "klucz" in str(exc.value).lower()


def test_main_maps_llm_error_to_clean_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Błąd streszczania (Claude API) w run_harness → czytelny SystemExit, nie surowy LLMError.
    transcript = tmp_path / "t.txt"
    transcript.write_text("dowolny transkrypt", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "workmate-meeting",
            "--project",
            "scada-integration",
            "--date",
            "2026-07-20",
            "--transcript",
            str(transcript),
        ],
    )
    monkeypatch.setattr(AgentSettings, "from_env", staticmethod(lambda: AgentSettings(api_key="x")))
    # Bez Claude: podmień budowę summarizera i sam przepływ (błąd zgłasza run_harness).
    monkeypatch.setattr(meeting, "_build_summarizer_or_exit", lambda _s: object())

    def _boom(*_args, **_kwargs):
        raise LLMError("boom")

    monkeypatch.setattr(meeting, "run_harness", _boom)

    with pytest.raises(SystemExit) as exc:
        main()

    assert "Claude API" in str(exc.value)


def test_build_summarizer_missing_extra_exits(monkeypatch: pytest.MonkeyPatch):
    # Brak extra 'agent' → ImportError w konstruktorze; tłumaczymy na instrukcję instalacji.
    def _no_extra(_settings):
        raise ImportError("No module named 'anthropic'")

    monkeypatch.setattr(
        "workmate.adapters.outbound.anthropic_summarizer.AnthropicMeetingSummarizer", _no_extra
    )

    with pytest.raises(SystemExit) as exc:
        _build_summarizer_or_exit(AgentSettings(api_key="x"))

    assert "agent" in str(exc.value)
