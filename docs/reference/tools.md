# Reference: katalog narzędzi MCP

Cztery narzędzia **odczytu** oraz jedno **zapisu** (`save_note`, bramkowane per
drzwi — patrz [ADR 0006](../adr/0006-write-capability-gate-2.md)). Definicje:
`adapters/inbound/mcp/tools.py`. Logika: `core/application/services.py`.

## `search_notes`

Szuka notatek po słowach kluczowych (metadane + treść), z opcjonalnymi filtrami.

| Parametr | Typ | Domyślnie | Opis |
|----------|-----|-----------|------|
| `query` | `str` | — | Fraza do wyszukania (bez rozróżniania wielkości liter). Pusta = wszystkie. |
| `project` | `str \| None` | `None` | Filtr po kluczu projektu (np. `mpwik`). |
| `participant` | `str \| None` | `None` | Filtr po fragmencie nazwiska uczestnika. |
| `limit` | `int` | `10` | Maksymalna liczba wyników. |

Zwraca: `{ "query", "count", "results": [ {id, title, project, date, participants, snippet, score} ] }`.
Wyniki sortowane malejąco po trafności (tytuł waży więcej), przy remisie po dacie.

## `get_note`

| Parametr | Typ | Opis |
|----------|-----|------|
| `note_id` | `str` | Identyfikator `<firma>/<projekt>/<plik>` (np. `mpwik/scada-integration/2025-06-12-przeglad-api-scada`). |

Zwraca pełną notatkę: `{ id, metadata: {...}, body }` lub `{ "error": ... }`.

## `list_projects`

Bez parametrów. Zwraca `{ "count", "projects": [ {key, company, name, description} ] }`.

## `get_project_status`

| Parametr | Typ | Opis |
|----------|-----|------|
| `project` | `str` | Klucz projektu (np. `workmate`). |

Zwraca status: część **zadeklarowaną** z rejestru (`company`, `status`, `health`,
`phase`, `summary`, `last_updated`) oraz **syntetyzowaną** z notatek (`notes_count`,
`latest_note_date`, `open_action_items`). Nieznany projekt → `{ "error": ... }`.

## `save_note` (ZAPIS — bramkowane per drzwi)

Dodaje nową notatkę ze spotkania. Wystawiane tylko, gdy drzwi mają włączony zapis
(`WORKMATE_ENABLE_WRITE`, domyślnie `true` lokalnie). Patrz [ADR 0006](../adr/0006-write-capability-gate-2.md).

| Parametr | Typ | Domyślnie | Opis |
|----------|-----|-----------|------|
| `title` | `str` | — | Tytuł (zamieniany na slug w nazwie pliku). |
| `project` | `str` | — | Klucz projektu (musi istnieć w rejestrze). |
| `date` | `date` (YYYY-MM-DD) | — | Data spotkania. |
| `body` | `str` | — | Treść notatki (Markdown). |
| `participants` / `decisions` / `action_items` / `open_questions` / `tags` | `list[str]` | `[]` | Pola opcjonalne. |

Wylicza miejsce zapisu: **firma z rejestru projektu**, potem
`<firma>/<projekt>/<data>-<slug>.md`. **Nigdy nie nadpisuje** istniejącej notatki
(przy kolizji dokłada sufiks `-2`, `-3`, …). Zwraca `{ "saved": true, "id", "path" }`
lub `{ "error": ... }` (np. nieznany projekt, pusty slug). Tytuł jest zawężany do
`[a-z0-9-]` — ochrona przed path traversal.

## Obsługa błędów

Błąd danych (np. wadliwa notatka, brak rejestru) jest zwracany jako
`{ "error": "opis" }`, żeby nie wywrócić serwera. Błędy nieoczekiwane (defekty
kodu) świadomie nie są łapane i wypływają jako błąd MCP.
