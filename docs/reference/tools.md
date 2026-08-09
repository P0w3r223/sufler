# Reference: katalog narzędzi

WorkMate ma **jednoźródłowy katalog narzędzi** (`core/application/tools.py`) — te same definicje
(funkcja + docstring + schemat) napędzają drzwi MCP oraz runtime agenta ([ADR 0008](../adr/0008-agent-runtime-and-tool-catalog.md)).

- **Powierzchnia MCP** jest zamrożona golden-testem `tests/adapters/test_mcp_tool_surface.py`
  w **czterech** konfiguracjach naraz. Trzon to `search_notes`, `get_note`, `list_projects`,
  `get_project_status` i `save_note` (bramka zapisu); przy działającym moście dochodzi
  `read_events_since`, a przy skonfigurowanej Jirze — `get_my_jira_tasks` i `get_my_jira_history`.
  Daje to **5 do 8 nazw** zależnie od konfiguracji. Poprzedni zapis mówił „zamrożone 4 + 1"
  i był o trzy nazwy w tyle.
- **Powierzchnia MCP nie została skonsolidowana i to jest decyzja, nie zaległość.** Sesja Claude
  Code nie ma dostępu do naszego kontenera-wykonawcy, więc `Bash` i `workmate-search` są dla niej
  nieosiągalne: zdjęcie tych narzędzi nie PRZENIOSŁOBY zdolności, tylko ją SKASOWAŁO.
- **Runtime agenta** (drzwi Teams/CLI) widzi zupełnie inną powierzchnię — pięć narzędzi
  skonsolidowanych, wstrzykiwanych **per drzwi** przez `extra_catalog`. Sekcja niżej.

Logika stoi w `core/application/` (`services.py`, `github.py`, `jira.py`, `events.py`, `workspace.py`).

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

## Narzędzia runtime agenta — powierzchnia po konsolidacji (1.6.0)

**Ta sekcja opisywała do 1.6.0 świat sprzed konsolidacji** — osobne `create_file`,
`read_recent_events`, `create_github_issue`, `get_my_jira_tasks`, `reply_on_thread`. Wydanie 1.6.0
sprowadziło runtime agenta do **pięciu** narzędzi. Kryterium jest **bariera, nie temat**:
narzędzie typowane powstaje wyłącznie tam, gdzie powłoka w kontenerze-wykonawcy nie może dosięgnąć.

> **Sama decyzja mieszka w paczce wdrożeniowej** (`docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md`
> w repozytorium `infra-docker-workmate`), bo jej kryterium — którą barierę powłoka w wykonawcy
> przechodzi, a której nie — jest własnością wdrożenia, nie aplikacji. Po tej stronie stoi
> [ADR 0061](../adr/0061-consolidated-tool-surface-upstream-pointer.md): wskazuje upstream i niesie
> to, czego upstream mieć nie może — kryterium cytowane w kodzie, bramkę wchodzącą do `Literal`
> oraz cenę, którą płaci [ADR 0054](../adr/0054-reduce-jira-to-read-only-my-tasks.md)
> (gwarancja strukturalna zamieniona na proceduralną).

| Narzędzie | Parametry | Kiedy wchodzi do katalogu |
|-----------|-----------|---------------------------|
| `Bash` | `command: str`, `timeout_s: int = 0` | `WORKMATE_ENABLE_SHELL=true` **i** działająca usługa `exec` (gniazdo `WORKMATE_EXEC_SOCKET`) |
| `Notes` | `action: project_status \| save`, pola akcji | zawsze; `save` **tylko** na drzwiach z `enable_write=True` (na Teams i MCP jest `False`) |
| `GitHub` | `action: events \| activity \| worklog \| create_issue \| comment`, pola akcji | zawsze; `create_issue`/`comment` wchodzą **do `Literal`** dopiero przy `WORKMATE_GITHUB_ENABLE_WRITE=true` |
| `Jira` | `action: my_tasks \| my_history \| member_tasks \| member_history \| task \| search`, pola akcji | tylko gdy nadawcę da się związać z kontem Jira (fail-closed w fabryce — bez konta narzędzia NIE MA) |
| `Schedule` | `week: current \| previous \| next` albo `date_from`/`date_to` | `WORKMATE_SCHEDULE_ENABLED` (`auto` = gdy grafik jest skonfigurowany) |

**Bramka zapisu wchodzi do `Literal`, nie do ciała funkcji.** Przy wyłączonym zapisie akcja nie
istnieje w schemacie, więc model jej nie widzi i nie ma czego odmawiać. Sonda negatywna w
`tests/core/test_github_catalog.py` sprawdza dokładnie to — bramka przepuszczająca wszystko
wygląda identycznie jak działająca.

**Czego tu nie ma i dlaczego.** `Skill(name)` nie powstaje: procedury leżą w `/mnt/skills`, więc
`ls` i `cat` przez `Bash` załatwiają je bez nowego narzędzia. Pliki robocze (`create_file`,
`list_files`) też są przypadkiem użycia `Bash` — brudnopis rozmowy jest w wykonawcy zapisywalny.
Odczyt notatek przez powłokę robi komenda `workmate-search` (ranker BM25 nad lematami PL).

### Narzędzia warunkowe — wchodzą tylko z własną bramką

| Narzędzie | Bramka | Uwaga |
|-----------|--------|-------|
| `reply_with_file` | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY` | ta sama bramka włącza skrzynkę `outputs/` w katalogu roboczym rozmowy |
| `send_image_to_user` | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH` | push 1:1, wymaga zakresów czatu |
| `send_document_to_user` | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH` | jw. + zapis na własnym dysku bota |
| `create_file` / `read_file` / `list_files` | `WORKMATE_ENABLE_WORKSPACE` | katalog roboczy **w procesie drzwi**; przy włączonej powłoce zbędne (ADR 0018) |
| `search_notes` / `get_note` / `list_projects` / `get_project_status` | brak powłoki | wchodzą **zastępczo**, gdy `Bash` nie istnieje — inaczej baza wiedzy byłaby nieosiągalna |

Ostatni wiersz jest powodem, dla którego produkcja bez powłoki widzi **siedem** narzędzi, a nie
pięć: trzy narzędzia odczytu notatek nie mają czym zostać zastąpione. Piątka jest własnością
architektury docelowej, w której powłoka jest.

---

## Obsługa błędów

Błąd danych (np. wadliwa notatka, nieznany projekt, brak rejestru) zwracany jest jako
`{ "error": "opis" }`, żeby nie wywrócić serwera. Błędy nieoczekiwane (defekty kodu) świadomie nie
są łapane i wypływają jako błąd MCP / błąd rundy agenta. Narzędzia zapisu konsekwentnie sanityzują
wejście i traktują treść jako **dane, nie polecenia**.
