# Współtworzenie WorkMate

Krótki przewodnik dla kogoś, kto dołącza do projektu i nie budował go od zera.

## 1. Środowisko

Wymagania: **Python 3.10+** i [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync            # instaluje zależności + narzędzia dev do .venv
uv run pytest      # powinno przejść na zielono
```

Serwer uruchomisz przez `uv run workmate` (stdio) lub podejrzysz narzędzia
w [MCP Inspectorze](https://github.com/modelcontextprotocol/inspector):
`uv run mcp dev src/workmate/server.py`.

## 2. Architektura w pigułce

„**Jeden rdzeń, wiele drzwi**":

- `core/` — logika niezależna od interfejsu (domena, porty, przypadki użycia).
- `adapters/` — „drzwi" i implementacje portów (MCP, magazyny danych).

**Nośna, samopilnująca się reguła:** `core/` nigdy nie importuje z `adapters/`.
Jeśli kusi Cię, żeby w rdzeniu sięgnąć po coś z adaptera — to znak, że logika
jest nie w tej warstwie.

## 3. Twoja pierwsza zmiana (dotknij całości raz)

Dodaj trywialne narzędzie read-only end-to-end — przejdziesz przez rdzeń,
adapter i test:

1. **Przypadek użycia** w `core/application/services.py` (np. `count_notes`).
2. **Opakowanie MCP** w `adapters/inbound/mcp/tools.py` (`@mcp.tool()` wołające serwis).
3. **Test** w `tests/core/` na atrapie repozytorium (patrz `tests/conftest.py`).
4. `uv run pytest` → zielono.

Szczegóły: [`docs/how-to/add-a-tool.md`](docs/how-to/add-a-tool.md).
Nowe „drzwi" (adapter): [`docs/how-to/add-a-door.md`](docs/how-to/add-a-door.md).

## 4. Konwencje

- **Tylko do odczytu w Fazie 1.** Narzędzie zapisujące → najpierw ADR + zgoda zespołu.
- **Jedno drzwi = jeden pakiet** w `adapters/`. Testy lustrzane wobec `src/`.
- Konfiguracja tylko przez `config.py` (zmienne środowiskowe) — żadnych zaszytych ścieżek.
- Proza po polsku; ADR i `docs/research/` po angielsku.
- Style: `uv run ruff format .` i `uv run ruff check .`; typy: `uv run mypy`.

## 5. Definicja ukończenia

- [ ] `uv run pytest` przechodzi
- [ ] `uv run ruff check .` i `uv run mypy` bez błędów
- [ ] zmiana ma test, jeśli dotyka logiki
- [ ] decyzja architektoniczna? → ADR w `docs/adr/`
- [ ] przegląd kodu przed scaleniem
