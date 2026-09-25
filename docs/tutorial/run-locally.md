# Tutorial: uruchom Sufler pierwszy raz

Cel: od zera do momentu, w którym Claude Code odpowiada na podstawie notatek.
Czas: ~10 minut.

## Krok 0 — wymagania

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- (opcjonalnie) Claude Code

## Krok 1 — instalacja

```bash
cd PROJEKT
uv sync
```

`uv` utworzy `.venv/` i zainstaluje zależności wraz z narzędziami dev.

## Krok 2 — testy

```bash
uv run pytest
```

Powinno przejść na zielono — to potwierdza, że rdzeń i repozytoria działają.

## Krok 3 — serwer lokalnie

```bash
uv run sufler
```

Serwer startuje na transporcie `stdio` (czeka na klienta MCP). Zatrzymaj `Ctrl+C`.

## Krok 4 — podgląd narzędzi (MCP Inspector)

```bash
uv run mcp dev src/sufler/server.py
```

Inspector pozwala wywołać `search_notes`, `get_note`, `list_projects`,
`get_project_status` ręcznie i zobaczyć wejście/wyjście — bez Claude Code.

Spróbuj: `list_projects` (zobaczysz projekty `scada-integration` i `workmate`
z firmami `mpwik`/`biap`), potem `search_notes` z `query = "API"`. Możesz też
`save_note`, aby dodać nową notatkę do właściwego katalogu firmy/projektu.

## Krok 5 — podłączenie do Claude Code

W tym repo jest już [`.mcp.json`](../../.mcp.json) (scope `project`), więc
otwarcie Claude Code w katalogu `PROJEKT` wykryje serwer automatycznie
(potwierdź dostęp, gdy Claude Code zapyta).

Zapytaj: *„co ustaliliśmy z mpwik w sprawie API?"* — Claude powinien wywołać
`search_notes` i odpowiedzieć na podstawie notatek z `data/notes/mpwik/`.

## Co dalej

- Jak działa całość → [`../explanation/architecture.md`](../explanation/architecture.md)
- Dodaj własne narzędzie → [`../how-to/add-a-tool.md`](../how-to/add-a-tool.md)
