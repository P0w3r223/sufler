"""Entry point lokalnego harnessu runtime'u agenta: ``uv run workmate-agent``.

Zaufane lokalne drzwi (jak stdio dev): buduje katalog READ+WRITE nad tymi samymi
serwisami co MCP, wpina klient Claude API i odpala runtime na zapytaniu z argumentów
lub stdin. Bez Teams, bez Azure. Import Claude API jest leniwy — brak extra ``agent``
kończy się czytelnym komunikatem, nie surowym ``ImportError``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from workmate.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from workmate.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from workmate.adapters.outbound.yaml_projects_repo import YamlProjectsRepository
from workmate.config import AgentSettings, Settings
from workmate.core.agent.runtime import AgentRuntime
from workmate.core.application.services import (
    NotesService,
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools import build_tool_catalog
from workmate.core.errors import LLMError


def _apply_env_file(env_file: Path) -> None:
    """Wczytaj plik ``.env`` do ``os.environ`` (``setdefault`` — realne env wygrywa).

    Odporność na kodowanie: PowerShell domyślnie zapisuje UTF-16 LE z BOM;
    ``utf-8-sig`` obsługuje UTF-8 z/bez BOM, gałąź UTF-16 — pliki z PowerShella.
    """
    data = env_file.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_dotenv() -> None:
    """Znajdź repo-lokalny ``.env`` (korzeń z ``pyproject.toml``) i wczytaj go, jeśli jest.

    Wygoda deva: klucz i ustawienia agenta można trzymać w ``.env`` (w
    ``.gitignore``) zamiast eksportować ręcznie. Realne zmienne środowiskowe
    zawsze mają priorytet (patrz ``_apply_env_file``). Bez zależności zewnętrznej.
    """
    here = Path(__file__).resolve()
    root = next((p for p in (here, *here.parents) if (p / "pyproject.toml").is_file()), None)
    if root is not None and (root / ".env").is_file():
        _apply_env_file(root / ".env")


def _build_runtime(settings: Settings, agent_settings: AgentSettings) -> AgentRuntime:
    """Zbuduj runtime: repozytoria → serwisy → katalog read+write → klient LLM."""
    from workmate.adapters.outbound.anthropic_llm import AnthropicLLMClient

    notes_repo = MarkdownNotesRepository(settings.notes_dir)
    projects_repo = YamlProjectsRepository(settings.projects_registry)
    notes_service = NotesService(notes_repo)
    projects_service = ProjectsService(projects_repo, notes_repo)
    write_service = NotesWriteService(MarkdownNotesWriter(settings.notes_dir), projects_repo)
    catalog = build_tool_catalog(notes_service, projects_service, write_service=write_service)
    llm = AnthropicLLMClient(agent_settings)
    return AgentRuntime(llm, catalog, max_tool_iterations=agent_settings.max_tool_iterations)


def main() -> None:
    """Uruchom runtime na zapytaniu z argv (albo stdin) i wypisz odpowiedź."""
    _load_dotenv()
    settings = Settings.from_env()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()

    query = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
    if not query:
        raise SystemExit('Podaj zapytanie, np.: uv run workmate-agent "co ustalono z mpwik?"')

    try:
        runtime = _build_runtime(settings, agent_settings)
    except ImportError as exc:
        raise SystemExit(
            "Runtime agenta wymaga extra 'agent'. Zainstaluj: uv sync --extra agent"
        ) from exc

    try:
        print(runtime.run(query))
    except LLMError as exc:
        # Błąd sieci/limitu/auth Claude API → czytelny komunikat, nie surowy traceback.
        raise SystemExit(f"Błąd komunikacji z Claude API: {exc}") from exc


if __name__ == "__main__":
    main()
