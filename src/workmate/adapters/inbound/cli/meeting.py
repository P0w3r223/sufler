"""Entry point lokalnego harnessu M3 „nowa notatka ze spotkania": ``uv run workmate-meeting``.

Uruchamia CAŁY przepływ M3 (Faza 2 / ADR 0009) end-to-end LOKALNIE, bez Azure/Graph:
wklejony transkrypt → streszczenie przez Claude (``AnthropicMeetingSummarizer``) →
złożenie ``NoteMetadata`` w ZAMROŻONYM schemacie → zapis przez bramkowany, DOPISUJĄCY
``NotesWriteService``. Źródłem transkryptu jest ``InMemoryTranscriptSource`` — realny
``GraphTranscriptSource`` jest odłożony do dostępu Azure/M365 (świadomy stub). To domyka
follow-up z ADR 0009: przebieg M3 wobec Claude na wklejonym transkrypcie, bez infrastruktury MS.

Bezpieczeństwo/konwencja:
- O miejscu zapisu (``project``, ``date``) decyduje WYWOŁUJĄCY (flagi), nie treść transkryptu
  (dane niezaufane) — zgodnie z ADR 0009 §3. Transkrypt nie może przekierować notatki gdzie indziej.
- Domyślnie NIE pisze do prawdziwej bazy ``data/notes/``: wynik ląduje w katalogu wyjściowym
  harnessu (temp), żeby powtarzane uruchomienia nie zaśmiecały wspólnej bazy wiedzy. Firmę
  projektu rozwiązuje jednak REALNY rejestr (semantyka produkcyjna). Zapis do bazy = świadome
  ``--out <ścieżka>``.
- Zapis pozostaje CREATE-ONLY (kolizja → sufiks ``-2``/``-3``…), jak każdy ``save_note`` (ADR 0006).
- Import ``anthropic`` jest leniwy (extra ``agent``); brak extra/klucza → czytelny komunikat.

Użycie:
    uv run workmate-meeting --project scada-integration --date 2026-07-20 --transcript spotkanie.txt
    echo "...transkrypt..." | uv run workmate-meeting --project scada-integration --date 2026-07-20
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from workmate.adapters.inbound import env
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.transcript_sources import InMemoryTranscriptSource
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.config import AgentSettings, Settings
from workmate.core.application.meeting_notes import MeetingNoteService
from workmate.core.application.services import NotesWriteService
from workmate.core.errors import LLMError, WorkMateError

if TYPE_CHECKING:
    from workmate.core.domain.models import Note
    from workmate.core.ports.meeting import MeetingSummarizer

_DEFAULT_MEETING_REF = "harness-meeting"


def run_harness(
    transcript: str,
    *,
    project: str,
    meeting_date: date,
    summarizer: MeetingSummarizer,
    write_service: NotesWriteService,
    meeting_ref: str = _DEFAULT_MEETING_REF,
) -> Note:
    """Złóż przepływ M3 na wklejonym transkrypcie i zwróć zapisaną notatkę.

    Sam wiring harnessu: ``InMemoryTranscriptSource`` (jedno mapowanie
    ``meeting_ref`` → ``transcript``) + wstrzyknięty ``summarizer`` i ``write_service``,
    spięte przez rdzeniowy ``MeetingNoteService``. Bez I/O konsoli i bez budowy adapterów —
    dzięki temu testujemy go na atrapie summarizera i prawdziwym zapisie do katalogu tymczasowego.
    """
    transcripts = InMemoryTranscriptSource({meeting_ref: transcript})
    service = MeetingNoteService(transcripts, summarizer, write_service)
    return service.note_from_meeting(meeting_ref, project=project, meeting_date=meeting_date)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workmate-meeting",
        description="Lokalny harness M3 (ADR 0009): transkrypt → streszczenie Claude → notatka.",
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Klucz projektu z rejestru (np. scada-integration) — wyznacza miejsce zapisu.",
    )
    parser.add_argument(
        "--date",
        required=True,
        type=_parse_date,
        help="Data spotkania w formacie YYYY-MM-DD (o dacie decyduje wywołujący, nie transkrypt).",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        default=None,
        help="Ścieżka pliku z transkryptem (UTF-8). Bez tej flagi transkrypt czytany z stdin.",
    )
    parser.add_argument(
        "--meeting-ref",
        default=_DEFAULT_MEETING_REF,
        help=f"Wewnętrzna etykieta spotkania (domyślnie {_DEFAULT_MEETING_REF!r}) — klucz "
        "InMemoryTranscriptSource; NIE wpływa na treść ani ścieżkę zapisanej notatki.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Katalog docelowy notatki. Domyślnie katalog tymczasowy harnessu "
        "(NIE zaśmieca data/notes/). Podaj data/notes/, by zapisać do bazy świadomie.",
    )
    return parser.parse_args(argv)


def _parse_date(value: str) -> date:
    """Parsuj datę ``YYYY-MM-DD``; zły format → czytelny błąd argparse, nie traceback."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"data musi być w formacie YYYY-MM-DD, jest: {value!r}"
        ) from exc


def _read_transcript(transcript_path: Path | None) -> str:
    """Wczytaj transkrypt z pliku (``--transcript``) albo z potoku stdin.

    Interaktywny terminal bez pliku = błąd z podpowiedzią (nie cichy zawis na wejściu):
    wklejenie wielolinijkowego transkryptu jest wygodniejsze plikiem lub potokiem.
    """
    if transcript_path is not None:
        try:
            text = transcript_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(
                f"Nie udało się odczytać transkryptu {transcript_path}: {exc}"
            ) from exc
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        raise SystemExit(
            "Podaj transkrypt: --transcript <plik> albo przekaż go potokiem "
            '(echo "..." | uv run workmate-meeting ...).'
        )
    if not text.strip():
        raise SystemExit("Transkrypt jest pusty — nie ma czego streszczać.")
    return text


def _build_summarizer_or_exit(agent_settings: AgentSettings) -> MeetingSummarizer:
    """Zbuduj adapter streszczający nad Claude API; brak extra ``agent`` → czytelny komunikat.

    Import ``anthropic`` dzieje się dopiero w konstruktorze adaptera (leniwy) — bez extra
    kończy się ``ImportError``, który tłumaczymy na instrukcję instalacji zamiast tracebacku.
    """
    from workmate.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer

    try:
        return AnthropicMeetingSummarizer(agent_settings)
    except ImportError as exc:
        raise SystemExit(
            "Brak zależności streszczania (extra 'agent'). Zainstaluj: uv sync --extra agent."
        ) from exc


def _default_out_dir() -> Path:
    """Domyślny katalog wyjściowy harnessu (temp) — poza repo i poza ``data/notes/``."""
    return Path(tempfile.gettempdir()) / "workmate-m3-harness"


def _format_result(note: Note, out_dir: Path, *, is_default_out: bool) -> str:
    """Zwięzły raport z przebiegu: id, ścieżka, pola strukturalne złożonej notatki."""
    metadata = note.metadata
    location = "katalog tymczasowy harnessu" if is_default_out else "wskazany katalog"
    return "\n".join(
        [
            "✓ Notatka M3 złożona i zapisana (harness lokalny, ADR 0009)",
            f"  id:          {note.id}",
            f"  plik:        {out_dir / f'{note.id}.md'}  ({location})",
            f"  tytuł:       {metadata.title}",
            f"  uczestnicy:  {', '.join(metadata.participants) or '—'}",
            f"  decyzje:     {len(metadata.decisions)} · "
            f"action items: {len(metadata.action_items)} · "
            f"pytania: {len(metadata.open_questions)} · "
            f"tagi: {', '.join(metadata.tags) or '—'}",
        ]
    )


def main() -> None:
    """Odpal harness M3: wczytaj transkrypt, streść przez Claude, zapisz notatkę, wypisz raport."""
    env.force_utf8_io()
    env.load_dotenv()

    args = _parse_args(sys.argv[1:])

    agent_settings = AgentSettings.from_env()
    try:
        # Fail-fast bez klucza Claude API — harness jest „na żywo". Komunikat z validate()
        # jest już czytelny (mówi, którą zmienną ustawić), więc podajemy go bez tracebacku.
        agent_settings.validate()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    settings = Settings.from_env()
    transcript = _read_transcript(args.transcript)
    out_dir = args.out or _default_out_dir()
    is_default_out = args.out is None

    projects_repo = YamlProjectsRepository(settings.projects_registry)
    write_service = NotesWriteService(MarkdownNotesWriter(out_dir), projects_repo)
    summarizer = _build_summarizer_or_exit(agent_settings)

    try:
        note = run_harness(
            transcript,
            project=args.project,
            meeting_date=args.date,
            summarizer=summarizer,
            write_service=write_service,
            meeting_ref=args.meeting_ref,
        )
    except LLMError as exc:
        raise SystemExit(f"Błąd streszczania (Claude API): {exc}") from exc
    except WorkMateError as exc:
        # Nieznany projekt w rejestrze, kolizja zapisu itp. — czytelnie, nie traceback.
        raise SystemExit(f"Nie udało się zapisać notatki: {exc}") from exc

    print(_format_result(note, out_dir, is_default_out=is_default_out))


if __name__ == "__main__":
    main()
