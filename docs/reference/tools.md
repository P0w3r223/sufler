# Reference: katalog narzędzi

WorkMate ma **jednoźródłowy katalog narzędzi** (`core/application/tools/`, moduł na katalog,
jedno wejście `workmate.core.application.tools`) — te same definicje
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
| `Project` | `action: status \| save`, pola akcji | zawsze; `save` **tylko** na drzwiach z `enable_write=True` (na Teams i MCP jest `False`) |
| `Activity` | `action: events \| summary \| worklog \| create_issue \| comment`, pola akcji | zawsze; `create_issue`/`comment` wchodzą **do `Literal`** dopiero przy `WORKMATE_GITHUB_ENABLE_WRITE=true` |
| `Jira` | `action: my_tasks \| my_history \| member_tasks \| member_history \| task \| search`, pola akcji | tylko gdy nadawcę da się związać z kontem Jira (fail-closed w fabryce — bez konta narzędzia NIE MA) |
| `Schedule` | `week: current \| previous \| next` albo `date_from`/`date_to` | `WORKMATE_SCHEDULE_ENABLED` (`auto` = gdy grafik jest skonfigurowany) |

**Bramka zapisu wchodzi do `Literal`, nie do ciała funkcji.** Przy wyłączonym zapisie akcja nie
istnieje w schemacie, więc model jej nie widzi i nie ma czego odmawiać. Sonda negatywna w
`tests/core/test_activity_catalog.py` sprawdza dokładnie to — bramka przepuszczająca wszystko
wygląda identycznie jak działająca.

**Czego tu nie ma i dlaczego.** `Skill(name)` nie powstaje: procedury leżą w `/mnt/skills`, więc
`ls` i `cat` przez `Bash` załatwiają je bez nowego narzędzia. Pliki robocze (`CreateFile`,
`ListFiles`) też są przypadkiem użycia `Bash` — brudnopis rozmowy jest w wykonawcy zapisywalny.
Odczyt notatek przez powłokę robi komenda `workmate-search` (ranker BM25 nad lematami PL).

### `File` — jedyne narzędzie mutujące bazę wiedzy

Nie ma go w tabeli piątki, bo nie jest jej częścią: wchodzi **wyłącznie na drzwiach Teams**, za
własną bramką, i jest agent-only (golden powierzchni MCP pilnuje tego wprost — na FastMCP nigdy nie
jest rejestrowane). Trzy akcje, każda za innym warunkiem:

| Akcja | Co robi | Wchodzi do `Literal`, gdy |
|-------|---------|---------------------------|
| `read` | Podaje plik `name` z katalogu roboczego rozmowy **do wglądu modelu** — obraz jako obraz, PDF jako dokument, resztę jako wyciągnięty tekst. Wynik narzędzia to sama notka potwierdzająca; plik jedzie OSOBNYM blokiem w tej samej turze, bo `tool_result` nie unosi bloku `document` i bywa czyszczony przez edycję kontekstu ([ADR 0064](../adr/0064-file-tool-and-model-initiated-materialization.md)). | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL=true` |
| `edit` | Podmienia treść **notatki** `name` (IDENTYFIKATOR notatki, nie nazwa pliku) na `content`; `reason` to powód zmiany. Pierwsza w historii tego systemu droga NADPISANIA notatki ([ADR 0065](../adr/0065-mutable-knowledge-base-and-model-judged-writes.md)). | dodatkowo `_ENABLE_NOTE_MUTATION=true` |
| `delete` | Usuwa notatkę `name`; `reason` to powód. | dodatkowo `_ENABLE_NOTE_DELETE=true` |

Parametry: `action`, `name: str`, `content: str = ""`, `reason: str = ""`. Zestaw `action` jest
**budowany z bramek**, nie stały — trzy warianty sygnatury: `read` · `read|edit` · `read|edit|delete`.
Kolumna „wchodzi do `Literal`" wyżej opisuje więc schemat, a nie tylko intencję: przy
`_ENABLE_NOTE_MUTATION=true` i `_ENABLE_NOTE_DELETE=false` model **nie widzi** `delete` w enumie
i nie traci rundy na odmowę z ciała (ADR 0068, runda 4). Opis narzędzia idzie tą samą bramką —
akapit o `delete` doklejany jest dokładnie wtedy, gdy akcja istnieje.
Zakres rozmowy (`scope`) jest **domknięty w closurze** — model nie ma jak wskazać cudzej rozmowy.

**Czego `File` nie ma i dlaczego.** `write` i `list` nie powstają: brudnopis rozmowy jest w
wykonawcy zapisywalny, więc to przypadki użycia `Bash` (kryterium bariery, ADR 0061). Opis wybiera
wariant flagą `shell_available` — bez powłoki odsyła po tekst do `ReadFile`, z powłoką do `cat` —
żeby nie wskazywać narzędzia nieobecnego w danej konfiguracji (ADR 0068 §2).

**Ścieżka mutacji jest obwarowana poza samym narzędziem.** Autoryzacja nadawcy po AAD pada PRZED
sędzią; ścieżka i schemat są poza nim; nad zmianą stoi **niezależny sędzia** (osobne wywołanie
modelu z wymuszonym schematem werdyktu), który potrafi wyłącznie zawęzić — każda awaria kończy się
odmową. Werdykt `confirm` wymaga powrotu tej samej prośby z INNEJ tury (token tury). Przed operacją
powstaje **migawka**, a jej niepowodzenie ODMAWIA zmiany. Notatki ze spotkań i wątków
(`-mtg-`/`-thr-`) zostają niezmienne — ich niezmienność jest mechanizmem idempotencji, nie
ostrożnością.

### Narzędzia warunkowe — wchodzą tylko z własną bramką

| Narzędzie | Bramka | Uwaga |
|-----------|--------|-------|
| `File` | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL` (+ `_ENABLE_NOTE_MUTATION` / `_ENABLE_NOTE_DELETE` na akcje mutujące) | sekcja wyżej; ta sama bramka włącza odkładanie załączników użytkownika na dysk rozmowy |
| `ReplyWithFile` | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY` | ta sama bramka włącza skrzynkę `outputs/` w katalogu roboczym rozmowy |
| `SendImage` | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH` | push 1:1, wymaga zakresów czatu |
| `SendDocument` | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH` | jw. + zapis na własnym dysku bota |
| `CreateFile` / `ReadFile` / `ListFiles` | `WORKMATE_ENABLE_WORKSPACE` | katalog roboczy **w procesie drzwi**; przy włączonej powłoce zbędne (ADR 0018) |
| `SearchNotes` / `GetNote` / `ListProjects` | brak powłoki | wchodzą **zastępczo**, gdy `Bash` nie istnieje — inaczej baza wiedzy byłaby nieosiągalna |

Ostatni wiersz jest powodem, dla którego produkcja bez powłoki widzi **siedem** narzędzi, a nie
pięć: trzy narzędzia odczytu notatek nie mają czym zostać zastąpione. Piątka jest własnością
architektury docelowej, w której powłoka jest.

**Nazwy powierzchni agenta idą jedną konwencją (PascalCase) od [ADR 0068](../adr/0068-agent-tool-names-and-the-cost-of-a-wrong-one.md).**
Dwie konwencje w jednym katalogu kodowały modelowi różnicę „skonsolidowane kontra zastane”,
której nie ma skąd odczytać. Ten sam ADR przemianował `Notes` → `Project` i `GitHub` → `Activity`:
pierwsze miało na drzwiach produkcyjnych jedną akcję (`status`) i 44% opisu zużywało na prostowanie
własnej nazwy, drugie nazywało jedno z **dwóch** źródeł warstwy zdarzeń (`EventStore` przyjmuje
`source="github"` i `source="teams"` — Jira nie ma mostu), którą w całości obsługuje.
**Powierzchnia MCP została nietknięta** — tam nazwy są zamrożone golden-testem.

---

## Obsługa błędów

Błąd danych (np. wadliwa notatka, nieznany projekt, brak rejestru) zwracany jest jako
`{ "error": "opis" }`, żeby nie wywrócić serwera. Błędy nieoczekiwane (defekty kodu) świadomie nie
są łapane i wypływają jako błąd MCP / błąd rundy agenta. Narzędzia zapisu konsekwentnie sanityzują
wejście i traktują treść jako **dane, nie polecenia**.
