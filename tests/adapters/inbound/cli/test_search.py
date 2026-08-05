"""CLI ``workmate-search``: formaty wyjścia, ścieżki plików i odporność na polską fleksję.

Wyjście tej komendy trafia do kontekstu modelu przez narzędzie ``Bash`` i jedzie ponownie
w każdej kolejnej turze (ADR 0057/0058), więc jego rozmiar i kształt są kontraktem, nie
kosmetyką — stąd bramka na jeden wiersz fragmentu i na obecność ścieżki gotowej do ``cat``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from workmate.adapters.inbound.cli.search import (
    _parse_args,
    format_json,
    format_paths,
    format_results,
    main,
    note_path,
)

_NOTES_DIR = Path("/mnt/system/notes")


def _summary(note_id: str, *, title: str, snippet: str = "fragment"):
    """Wynik wyszukiwania o kształcie, jaki oddaje ``NotesService`` (bez uruchamiania rankera)."""
    from workmate.core.domain.models import NoteSummary

    return NoteSummary(
        id=note_id,
        title=title,
        project="scada-integration",
        date=date(2025, 6, 12),
        participants=["Anna Kowalska"],
        snippet=snippet,
        score=3.5,
    )


def test_default_format_carries_a_path_ready_for_cat():
    """Pozycja niesie ŚCIEŻKĘ pliku, nie samo logiczne id — kolejnym krokiem jest odczyt."""
    results = [_summary("mpwik/scada-integration/2025-06-12-przeglad-api", title="Przegląd API")]

    rendered = format_results(results, query="api", notes_dir=_NOTES_DIR)

    assert "mpwik/scada-integration/2025-06-12-przeglad-api.md" in rendered.replace("\\", "/")
    assert "Przegląd API" in rendered
    assert "2025-06-12" in rendered


def test_empty_result_says_so_instead_of_printing_nothing():
    """Puste wyszukiwanie ma TREŚĆ: cisza jest dla modelu nieodróżnialna od awarii komendy."""
    assert "Brak notatek" in format_results([], query="czego nie ma", notes_dir=_NOTES_DIR)


def test_snippet_collapses_to_a_single_line_and_is_capped():
    """Fragment bywa akapitem; w wyniku zajmuje jeden wiersz — to budżet kontekstu, nie estetyka."""
    paragraph = "pierwsza linia\n\ndruga linia " + "x" * 400
    results = [_summary("mpwik/p/2025-06-12-a", title="A", snippet=paragraph)]

    rendered = format_results(results, query="a", notes_dir=_NOTES_DIR)

    snippet_line = rendered.splitlines()[-1]
    assert "\n" not in snippet_line
    assert len(snippet_line) < 200
    assert snippet_line.endswith("…")


def test_three_lines_per_result_keeps_output_predictable():
    """Nagłówek + pusta linia + 3 wiersze na notatkę: rozmiar wyjścia daje się przewidzieć."""
    results = [_summary(f"mpwik/p/2025-06-12-{i}", title=f"Notatka {i}") for i in range(3)]

    lines = format_results(results, query="x", notes_dir=_NOTES_DIR).splitlines()

    assert len(lines) == 2 + 3 * 3


def test_paths_format_is_one_path_per_line():
    """``--paths`` jest wejściem dla potoku (xargs/cat) — bez ozdobników."""
    results = [
        _summary("mpwik/p/2025-06-12-a", title="A"),
        _summary("mpwik/p/2025-06-12-b", title="B"),
    ]

    lines = format_paths(results, notes_dir=_NOTES_DIR).splitlines()

    assert len(lines) == 2
    assert all(line.endswith(".md") for line in lines)


def test_json_format_adds_path_and_keeps_polish_characters():
    """JSON niesie te same pola co narzędzie agenta plus ``path``; bez ucieczek ``\\uXXXX``."""
    results = [_summary("mpwik/p/2025-06-12-a", title="Przegląd wdrożenia")]

    payload = json.loads(format_json(results, query="wdrożenie", notes_dir=_NOTES_DIR))

    assert payload["count"] == 1
    assert payload["query"] == "wdrożenie"
    assert payload["results"][0]["path"].endswith("2025-06-12-a.md")
    assert payload["results"][0]["title"] == "Przegląd wdrożenia"


def test_note_path_joins_id_under_the_notes_directory():
    """Id notatki jest ścieżką względną bez rozszerzenia — plik to id + ``.md``."""
    assert note_path(Path("/mnt/system/notes"), "mpwik/p/2025-06-12-a").replace("\\", "/") == (
        "/mnt/system/notes/mpwik/p/2025-06-12-a.md"
    )


def test_limit_defaults_low_because_results_persist_in_context():
    """Domyślny limit jest mały świadomie: każdy wynik zostaje w kontekście do końca rozmowy."""
    assert _parse_args(["fraza"]).limit == 10


def _write_note(notes_dir: Path, relpath: str, *, title: str, body: str) -> None:
    path = notes_dir / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nproject: mpwik\ndate: 2025-06-12\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_search_finds_an_inflected_form(tmp_path: Path, monkeypatch, capsys):
    """POWÓD ISTNIENIA komendy: „migracji" trafia notatkę o „migracja".

    Dopasowanie wzorca (``grep``) tego nie znajdzie, a to jest zwykły kształt polskiego
    zapytania. Test biegnie end-to-end przez ``main`` — łącznie z odczytem ścieżek z env,
    bo w kontenerze-wykonawcy to jedyne źródło konfiguracji (usługa ``exec`` nie ma ``.env``).
    """
    pytest.importorskip("simplemma")
    notes = tmp_path / "notes"
    _write_note(
        notes,
        "mpwik/scada/2025-06-12-migracja.md",
        title="Plan wdrożenia",
        body="Ustalono, że migracja bazy pójdzie etapami.",
    )
    monkeypatch.setenv("WORKMATE_NOTES_DIR", str(notes))
    monkeypatch.setattr("sys.argv", ["workmate-search", "migracji"])

    main()

    out = capsys.readouterr().out
    assert "Znaleziono 1" in out
    assert "2025-06-12-migracja.md" in out.replace("\\", "/")


def test_missing_query_exits_with_usage_code(monkeypatch, capsys):
    """Brak frazy to błąd UŻYCIA (kod 2), rozróżnialny od awarii odczytu (kod 1)."""
    monkeypatch.setattr("sys.argv", ["workmate-search"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
    assert "Podaj frazę" in capsys.readouterr().err


def test_no_results_still_exits_zero(tmp_path: Path, monkeypatch, capsys):
    """Brak trafień kończy się kodem 0: model czyta kod ≠ 0 jako „popraw polecenie" (ADR 0057)."""
    notes = tmp_path / "notes"
    _write_note(notes, "mpwik/scada/2025-06-12-a.md", title="A", body="zupełnie inna treść")
    monkeypatch.setenv("WORKMATE_NOTES_DIR", str(notes))
    monkeypatch.setattr("sys.argv", ["workmate-search", "kwantowa teleportacja"])

    main()

    assert "Brak notatek" in capsys.readouterr().out
