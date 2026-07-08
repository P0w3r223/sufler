# WorkMate

Wewnętrzny asystent wiedzy pionu Inteligentnych Technologii. Daje Claude Code
każdego developera dostęp do wspólnej bazy wiedzy — **notatek ze spotkań**
i **statusu projektów** — przez wąskie, typowane narzędzia MCP.

> **Status:** 🟢 **Faza 1 (MVP) — serwer MCP: 4 narzędzia odczytu + zapis notatek (`save_note`).**
> Fazy 2 (Teams) i 3 (GitHub) są zaplanowane. Patrz [`docs/roadmap.md`](docs/roadmap.md).

## Architektura w jednym akapicie

Zasada: **„jeden rdzeń, wiele drzwi"**. Cała logika mieszka w rdzeniu (`core/`)
i jest niezależna od interfejsu. Kanały dostępu („drzwi") to tanie adaptery
(`adapters/`): dziś MCP dla Claude Code, jutro Teams, potem GitHub — **nad tymi
samymi narzędziami**. Reguła zależności, która to spina: `core` nigdy nie
importuje z `adapters`. Szczegóły: [`docs/explanation/architecture.md`](docs/explanation/architecture.md).

## Szybki start

Wymagania: **Python 3.10+** oraz [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Instalacja zależności (tworzy .venv, instaluje też narzędzia dev)
uv sync

# 2. Testy
uv run pytest

# 3. Uruchomienie serwera lokalnie (transport stdio)
uv run workmate

# 4. Podgląd narzędzi w izolacji (MCP Inspector)
uv run mcp dev src/workmate/server.py
```

### Podłączenie do Claude Code

Repozytorium zawiera [`.mcp.json`](.mcp.json) w scope `project`, więc po
sklonowaniu i otwarciu Claude Code w tym katalogu serwer `workmate` pojawi się
automatycznie (przy pierwszym użyciu Claude Code poprosi o zatwierdzenie).
Alternatywnie, ręcznie:

```bash
claude mcp add --scope project workmate -- uv run workmate
```

Potem w Claude Code zapytaj np. *„co ustaliliśmy z mpwik w sprawie API?"* —
Claude wywoła `search_notes` i odpowie na podstawie notatek.

## Mapa repozytorium

```
PROJEKT/
├── src/workmate/         # kod serwera
│   ├── core/             # RDZEŃ — domena, porty, przypadki użycia (bez I/O, bez MCP)
│   ├── adapters/         # DRZWI — inbound/mcp (Faza 1), teams/ github/ (stuby)
│   ├── config.py         # typowana konfiguracja ze zmiennych środowiskowych
│   └── server.py         # punkt składania (wiring rdzenia z drzwiami)
├── data/                 # baza wiedzy: notatki notes/<firma>/<projekt>/*.md + rejestr projektów (.yaml)
├── docs/                 # dokumentacja (tutorial / how-to / reference / explanation / adr / research)
├── tests/                # testy (lustrzane wobec src/)
├── pyproject.toml        # zależności, skrypt `workmate`, konfiguracja narzędzi (uv)
├── .mcp.json             # rejestracja serwera dla Claude Code (scope project)
└── CLAUDE.md             # kontekst dla Claude Code
```

## Narzędzia

Cztery narzędzia **odczytu** oraz jedno **zapisu** (`save_note`, bramkowane per drzwi):

| Narzędzie | Do czego |
|-----------|----------|
| `search_notes` | Szuka notatek po słowach kluczowych i metadanych (filtry: projekt, uczestnik). |
| `get_note` | Zwraca pełną treść notatki po identyfikatorze. |
| `list_projects` | Wypisuje projekty pionu z rejestru. |
| `get_project_status` | Status projektu: stan zadeklarowany + synteza z notatek. |
| `save_note` | **Zapis:** dodaje notatkę do właściwego katalogu `firma/projekt` (nigdy nie nadpisuje). |

Pełna specyfikacja: [`docs/reference/tools.md`](docs/reference/tools.md).

## Konfiguracja

W trybie lokalnym (`stdio`) WorkMate **nie wymaga żadnych sekretów ani kluczy API** —
odczyt i zapis notatek działają na lokalnych plikach. Wszystkie zmienne środowiskowe
są opcjonalne (m.in. `WORKMATE_ENABLE_WRITE` bramkująca `save_note`) — patrz
[`.env.example`](.env.example) i [`docs/reference/config.md`](docs/reference/config.md).

Wdrożenie sieciowe (`streamable-http` na serwerze firmowym) dokłada uwierzytelnianie
per osoba tokenami bearer i drzwi tylko do odczytu — patrz
[`docs/how-to/deploy-http.md`](docs/how-to/deploy-http.md) oraz
[ADR 0007](docs/adr/0007-gate-3-http-auth-deployment.md).

## Dalej

- Chcesz coś zmienić? → [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Dokąd to zmierza? → [`docs/roadmap.md`](docs/roadmap.md)
- Decyzje projektowe → [`docs/adr/`](docs/adr/)
