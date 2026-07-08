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

from workmate.adapters.inbound.agent_wiring import build_agent_runtime
from workmate.config import AgentSettings, Settings
from workmate.core.errors import LLMError


def _apply_env_file(env_file: Path) -> None:
    """Wczytaj plik ``.env`` do ``os.environ`` (``setdefault`` — realne env wygrywa).

    Odporność na kodowanie: PowerShell domyślnie zapisuje UTF-16 LE z BOM;
    ``utf-8-sig`` obsługuje UTF-8 z/bez BOM, gałąź UTF-16 — pliki z PowerShella.
    """
    data = env_file.read_bytes()
    try:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"Nie udało się odczytać {env_file.name} — sprawdź kodowanie "
            f"(zapisz jako UTF-8): {exc}"
        ) from exc
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
        runtime = build_agent_runtime(settings, agent_settings, enable_write=True)
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
