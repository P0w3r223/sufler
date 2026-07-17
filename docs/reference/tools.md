# Reference: katalog narzędzi

WorkMate ma **jednoźródłowy katalog narzędzi** (`core/application/tools.py`) — te same definicje
(funkcja + docstring + schemat) napędzają drzwi MCP oraz runtime agenta ([ADR 0008](../adr/0008-agent-runtime-and-tool-catalog.md)).

- **Powierzchnia MCP** to zamrożone **4 + 1** narzędzi (`build_tool_catalog`), pilnowane
  golden-testem `tests/adapters/test_mcp_tool_surface.py`.
- **Runtime agenta** (drzwi Teams/Telegram/CLI) widzi dodatkowo narzędzia warstwy roboczej i
  mostu, wstrzykiwane **per drzwi** przez `extra_catalog` — nie ruszają powierzchni MCP.

Logika stoi w `core/application/` (`services.py`, `github.py`, `events.py`, `workspace.py`).

---

## Powierzchnia MCP (odczyt)

### `search_notes`

Szuka notatek po słowach kluczowych (metadane + treść), z opcjonalnymi filtrami.

| Parametr | Typ | Domyślnie | Opis |
|----------|-----|-----------|------|
| `query` | `str` | — | Fraza (bez rozróżniania wielkości liter). Pusta = wszystkie. |
| `project` | `str \| None` | `None` | Filtr po kluczu projektu (np. `scada-integration`). |
| `participant` | `str \| None` | `None` | Filtr po fragmencie nazwiska uczestnika. |
| `limit` | `int` | `10` | Maksymalna liczba wyników. |

Zwraca `{ query, count, results: [ {id, title, project, date, participants, snippet, score} ] }`.
Ranking: BM25 nad lematami (extra `retrieval`) lub fallback podłańcuchowy; remis → po dacie.

### `get_note`

| Parametr | Typ | Opis |
|----------|-----|------|
| `note_id` | `str` | Identyfikator `<firma>/<projekt>/<plik>` (np. `mpwik/scada-integration/2025-06-12-przeglad-api-scada`). |

Zwraca `{ id, metadata: {...}, body }` lub `{ error }`.

### `list_projects`

Bez parametrów. Zwraca `{ count, projects: [ {key, company, name, description} ] }`.

### `get_project_status`

| Parametr | Typ | Opis |
|----------|-----|------|
| `project` | `str` | Klucz projektu (np. `workmate`). |

Zwraca część **zadeklarowaną** z rejestru (`company`, `status`, `health`, `phase`, `summary`,
`last_updated`) + **syntetyzowaną** z notatek (`notes_count`, `latest_note_date`,
`open_action_items`). Nieznany projekt → `{ error }`.

## Powierzchnia MCP (zapis — Bramka 2)

### `save_note`

Dodaje nową notatkę ze spotkania. Wystawiane tylko przy włączonym zapisie (`WORKMATE_ENABLE_WRITE`,
profil per drzwi — [ADR 0006](../adr/0006-write-capability-gate-2.md)).

| Parametr | Typ | Domyślnie | Opis |
|----------|-----|-----------|------|
| `title` | `str` | — | Tytuł (zamieniany na slug w nazwie pliku). |
| `project` | `str` | — | Klucz projektu (musi istnieć w rejestrze). |
| `date` | `date` (YYYY-MM-DD) | — | Data spotkania. |
| `body` | `str` | — | Treść (Markdown). |
| `participants` / `decisions` / `action_items` / `open_questions` / `tags` | `list[str]` | `[]` | Pola opcjonalne. |

Miejsce zapisu: **firma z rejestru projektu**, potem `<firma>/<projekt>/<data>-<slug>.md`. **Nigdy
nie nadpisuje** (kolizja → sufiks `-2`, `-3`, …). Tytuł zawężany do `[a-z0-9-]` (ochrona przed path
traversal). Zwraca `{ saved: true, id, path }` lub `{ error }`.

---

## Narzędzia runtime agenta (przez `extra_catalog`)

Wstrzykiwane per drzwi zależnie od włączonych zdolności; **nie** wchodzą na powierzchnię MCP.

### Katalog roboczy agenta ([ADR 0018](../adr/0018-agent-working-directory.md), bramka `WORKMATE_ENABLE_WORKSPACE`)

| Narzędzie | Parametry | Zwraca |
|-----------|-----------|--------|
| `create_file` | `name: str`, `content: str` | `{ created, name, path }` — tylko tekst (`md/txt/csv/json`). |
| `read_file` | `name: str` | `{ name, content }` lub `{ error }`. |
| `list_files` | — | `{ count, files: [{name, size}] }`. |

### Most / EventStore ([ADR 0019](../adr/0019-shared-event-store.md))

| Narzędzie | Parametry | Zwraca |
|-----------|-----------|--------|
| `read_recent_events` | `source: str \| None = None`, `project: str \| None = None`, `limit: int = 20` | `{ count, events: [...] }` — okno read-only na zdarzenia (filtr źródła/projektu, ADR 0028). |
| `get_project_activity` | `project: str`, `limit: int = 50` | `{ project, event_count, by_kind, latest_activity_at, recent }` — fold aktywności projektu: liczniki wg typu + ostatnia aktywność ([ADR 0029](../adr/0029-branch-pr-state-transitions-and-project-activity.md)). |

### Zapis GitHub (Bramka 4, [ADR 0021](../adr/0021-github-write-capability-gate-4.md), bramka `WORKMATE_GITHUB_ENABLE_WRITE`)

Create-only; `owner`/`repo` pochodzą z **konfiguracji**, nie z treści prośby.

| Narzędzie | Parametry | Zwraca |
|-----------|-----------|--------|
| `create_github_issue` | `title: str`, `body: str`, `labels: list[str] \| None = None` | `{ created, number, url }`. |
| `comment_github_issue` | `issue_number: int`, `body: str` | `{ created, url }`. |

### Odpowiedź w wątku ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md))

| Narzędzie | Parametry | Zwraca |
|-----------|-----------|--------|
| `reply_on_thread` | `body: str` | `{ created, url }` — komentuje issue/PR **pre-związany** z wątkiem Teams (numer z zaufanej mapy `ThreadLinkStore`, nie od modelu). |

---

## Obsługa błędów

Błąd danych (np. wadliwa notatka, nieznany projekt, brak rejestru) zwracany jest jako
`{ "error": "opis" }`, żeby nie wywrócić serwera. Błędy nieoczekiwane (defekty kodu) świadomie nie
są łapane i wypływają jako błąd MCP / błąd rundy agenta. Narzędzia zapisu konsekwentnie sanityzują
wejście i traktują treść jako **dane, nie polecenia**.
