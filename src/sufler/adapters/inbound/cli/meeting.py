"""Entry point lokalnego harnessu M3 „nowa notatka ze spotkania": ``uv run sufler-meeting``.

Uruchamia CAŁY przepływ M3 (Faza 2 / ADR 0009) end-to-end: transkrypt → streszczenie przez
Claude (``AnthropicMeetingSummarizer``) → złożenie ``NoteMetadata`` w ZAMROŻONYM schemacie →
zapis przez bramkowany, DOPISUJĄCY ``NotesWriteService``. Źródło transkryptu wybiera ``--source``:

- ``memory`` (domyślnie) — wklejony transkrypt (plik/stdin) przez ``InMemoryTranscriptSource``;
  cały przepływ LOKALNIE, bez Azure. Domyka follow-up z ADR 0009.
- ``graph`` — REALNE pobranie z Microsoft Graph (``HttpxGraphTranscriptSource``, B1) po
  ``--meeting <joinWebUrl|id>``. To ścieżka LIVE-SMOKE produkcyjnego M3: wymaga nadanych przez
  admina zakresów (``OnlineMeetingTranscript.Read.All`` + ``OnlineMeetings.Read``) i bramki
  ``SUFLER_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true``. Patrz
  ``docs/how-to/meeting-transcript-live-smoke.md``.

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
    uv run sufler-meeting --project scada-integration --date 2026-07-20 --transcript spotkanie.txt
    echo "...transkrypt..." | uv run sufler-meeting --project scada-integration --date 2026-07-20
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sufler.adapters.inbound import env
from sufler.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from sufler.adapters.outbound.transcript_sources import InMemoryTranscriptSource
from sufler.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from sufler.config import AgentSettings, Settings
from sufler.core.application.meeting_notes import MeetingNoteService
from sufler.core.application.services import NotesWriteService
from sufler.core.errors import LLMError, SuflerError

if TYPE_CHECKING:
    from sufler.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer
    from sufler.core.application.meeting_notes import MeetingNoteOutcome
    from sufler.core.ports.meeting import MeetingNoteVerifier, MeetingSummarizer

_DEFAULT_MEETING_REF = "harness-meeting"


def run_harness(
    transcript: str,
    *,
    project: str,
    meeting_date: date,
    summarizer: MeetingSummarizer,
    write_service: NotesWriteService,
    verifier: MeetingNoteVerifier | None = None,
    meeting_ref: str = _DEFAULT_MEETING_REF,
) -> MeetingNoteOutcome:
    """Złóż przepływ M3 na wklejonym transkrypcie i zwróć wynik (utworzona / już była).

    Sam wiring harnessu: ``InMemoryTranscriptSource`` (jedno mapowanie ``meeting_ref`` →
    ``transcript``) + wstrzyknięty ``summarizer``, opcjonalny ``verifier`` (pass 2, ADR 0047) i
    ``write_service``, spięte przez rdzeniowy ``MeetingNoteService``. Bez I/O konsoli i bez budowy
    adapterów — dzięki temu testujemy go na atrapach i prawdziwym zapisie do katalogu tymczasowego.
    """
    transcripts = InMemoryTranscriptSource({meeting_ref: transcript})
    service = MeetingNoteService(transcripts, summarizer, write_service, verifier=verifier)
    return service.note_from_meeting(meeting_ref, project=project, meeting_date=meeting_date)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sufler-meeting",
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
        "--source",
        choices=("memory", "graph"),
        default="memory",
        help="Źródło transkryptu: 'memory' (plik/stdin, lokalnie) albo 'graph' (Microsoft Graph "
        "po --meeting; wymaga zakresów admina i bramki, patrz how-to live-smoke).",
    )
    parser.add_argument(
        "--meeting",
        default=None,
        help="Dla --source graph: joinWebUrl spotkania (zaczyna się od http) albo id spotkania.",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        default=None,
        help="Ścieżka pliku z transkryptem (UTF-8). Bez tej flagi transkrypt czytany z stdin "
        "(dotyczy --source memory).",
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
    parser.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Druga przelotka-krytyk (ADR 0047): usuwa twierdzenia bez pokrycia w transkrypcie. "
        "Domyślnie WŁĄCZONA w harnessie (weryfikacja jakości), ~2× koszt Claude. "
        "--no-verify wyłącza.",
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
            '(echo "..." | uv run sufler-meeting ...).'
        )
    if not text.strip():
        raise SystemExit("Transkrypt jest pusty — nie ma czego streszczać.")
    return text


def _build_summarizer_or_exit(agent_settings: AgentSettings) -> AnthropicMeetingSummarizer:
    """Zbuduj adapter streszczający nad Claude API; brak extra ``agent`` → czytelny komunikat.

    Import ``anthropic`` dzieje się dopiero w konstruktorze adaptera (leniwy) — bez extra
    kończy się ``ImportError``, który tłumaczymy na instrukcję instalacji zamiast tracebacku.
    """
    from sufler.adapters.outbound.anthropic_summarizer import AnthropicMeetingSummarizer

    try:
        return AnthropicMeetingSummarizer(agent_settings)
    except ImportError as exc:
        raise SystemExit(
            "Brak zależności streszczania (extra 'agent'). Zainstaluj: uv sync --extra agent."
        ) from exc


def _fetch_graph_transcript(meeting_ref: str) -> str:
    """Pobierz transkrypt spotkania z Microsoft Graph (B1) — ścieżka live-smoke produkcyjnego M3.

    Fail-fast i czytelnie (nie traceback): brak tożsamości Entra / wyłączona bramka / brak zakresu
    → ``validate()`` rzuca ``ValueError`` z instrukcją; brak extra ``teams-graph`` → ``ImportError``
    tłumaczony na instrukcję instalacji; błąd Graph (403/404/pusty transkrypt) → komunikat z
    kontekstem. Token to device-code (pierwszy raz logowanie w przeglądarce), jak inne drzwi Graph.
    """
    from sufler.config import TeamsGraphSettings

    settings = TeamsGraphSettings.from_env()
    try:
        settings.validate()
        if not settings.enable_meeting_transcript:
            raise ValueError(
                "Pobranie z Graph wymaga SUFLER_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true "
                "(oraz zakresów admina OnlineMeetingTranscript.Read.All + OnlineMeetings.Read). "
                "Patrz docs/how-to/meeting-transcript-live-smoke.md."
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        from sufler.adapters.inbound.teams_graph.auth import build_token_provider
        from sufler.adapters.outbound.transcript_sources import HttpxGraphTranscriptSource
    except ImportError as exc:
        raise SystemExit(
            "Brak zależności Graph (extra 'teams-graph'). Zainstaluj: uv sync --extra teams-graph."
        ) from exc

    source = HttpxGraphTranscriptSource(build_token_provider(settings))
    try:
        return source.fetch(meeting_ref)
    except (ValueError, KeyError) as exc:
        raise SystemExit(f"Nie udało się pobrać transkryptu z Graph: {exc}") from exc


def _default_out_dir() -> Path:
    """Domyślny katalog wyjściowy harnessu (temp) — poza repo i poza ``data/notes/``."""
    return Path(tempfile.gettempdir()) / "sufler-m3-harness"


def _format_result(outcome: MeetingNoteOutcome, out_dir: Path, *, is_default_out: bool) -> str:
    """Zwięzły raport z przebiegu: id, ścieżka, pola strukturalne złożonej notatki.

    Gdy notatka tego spotkania już istniała (idempotencja, ADR 0043) — raport o pominięciu
    (bez poboru transkryptu i Claude), bo ``outcome.note`` jest wtedy ``None``.
    """
    if outcome.note is None:
        return "\n".join(
            [
                "✓ Notatka M3 tego spotkania była już złożona wcześniej (idempotencja, ADR 0043)",
                f"  id:          {outcome.note_id}",
                f"  plik:        {out_dir / f'{outcome.note_id}.md'}",
            ]
        )
    note = outcome.note
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
    if args.source == "graph":
        if not args.meeting:
            raise SystemExit("--source graph wymaga --meeting <joinWebUrl albo id spotkania>.")
        transcript = _fetch_graph_transcript(args.meeting)
    else:
        transcript = _read_transcript(args.transcript)
    out_dir = args.out or _default_out_dir()
    is_default_out = args.out is None

    projects_repo = YamlProjectsRepository(settings.projects_registry)
    write_service = NotesWriteService(MarkdownNotesWriter(out_dir), projects_repo)
    summarizer = _build_summarizer_or_exit(agent_settings)
    # Ten sam adapter to oba porty (draft + krytyk). --verify (ON domyślnie) włącza pass 2.
    verifier: MeetingNoteVerifier | None = summarizer if args.verify else None

    try:
        outcome = run_harness(
            transcript,
            project=args.project,
            meeting_date=args.date,
            summarizer=summarizer,
            write_service=write_service,
            verifier=verifier,
            meeting_ref=args.meeting_ref,
        )
    except LLMError as exc:
        raise SystemExit(f"Błąd streszczania (Claude API): {exc}") from exc
    except SuflerError as exc:
        # Nieznany projekt w rejestrze, kolizja zapisu itp. — czytelnie, nie traceback.
        raise SystemExit(f"Nie udało się zapisać notatki: {exc}") from exc

    print(_format_result(outcome, out_dir, is_default_out=is_default_out))


if __name__ == "__main__":
    main()
