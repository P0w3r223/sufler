# Changelog

Wszystkie istotne zmiany w projekcie WorkMate. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

## [Unreleased]

## [1.2.0] — 2026-07-21

### Dodane
- **Cotygodniowe karty czasu → arkusz WorklogPRO + prywatna wiadomość na Teams**
  ([ADR 0035](docs/adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md)): nowe drzwi
  `workmate-worklogi` (extra `worklogi`, bramka OFF, domyślnie tryb PRÓBNY). W piątek biorą godziny
  za mijający tydzień, generują KAŻDEJ osobie arkusz importu WorklogPRO i wysyłają jej prywatną
  wiadomość 1:1 z tabelą godzin i ścieżką pliku; wiadomość dostają tylko osoby, które pracowały.
  Zmiana kierunku wobec ADR 0034: skoro import wykonuje sam pracownik, worklog ma **prawdziwego
  autora** — obejście „w imieniu" przestaje być potrzebne.
  Nowy czysty rdzeń: `core/domain/week.py` (termin piątkowy i okno tygodnia przez `ZoneInfo`,
  odporne na DST), `timesheet.py` (agregacja w minutach, predykat `worked()`), `timesheet_sheet.py`
  (projekcja na kolumny WorklogPRO, notacja czasu bez `1d`, pełne ISO z offsetem),
  `timesheet_message.py` (tabela HTML z `html.escape` na każdej wartości),
  `application/weekly_timesheets.py` (izolacja per osoba, arkusz PRZED wiadomością, `RunReport`).
  Nowe porty `HoursSource`/`SheetWriter`/`IdentityDirectory` oraz adaptery: zapis xlsx (openpyxl —
  pierwszy ZAPIS Excela w repo), atrapa źródła na JSON, katalog tożsamości Graph + YAML.
  Strażnik `assert_single_person` woływany DWA razy (po agregacji i na granicy zapisu).
- **`TeamsNotifier.send_chat_html`** — wysyłka gotowego HTML 1:1 z pominięciem renderera Markdown.
  Konieczna, bo `to_teams_html` escapuje surowy HTML i nie włącza tabel. Bezpieczna wyłącznie dzięki
  kontraktowi: treść składa czysta funkcja rdzenia. `send_chat` bez zmian (test regresyjny pilnuje,
  że nadal escapuje).
- **`TeamMember.Read.All`** w `_DEFAULT_TEAMS_PUSH_SCOPES` — lista członków zespołu z Graph.
  Bez nowej zgody admina: ta sama rejestracja aplikacji i ten sam cache MSAL co `Powiadomienia_teams`.
- **`tzdata`** jako zależność rdzenia z markerem `sys_platform == 'win32'` (Windows nie ma
  systemowej bazy stref, Linux ma).
- **Ewidencja czasu w Jirze z historii commitów GitHub — SZKIELET**
  ([ADR 0034](docs/adr/0034-jira-worklog-from-github-commits.md)): rdzeń kompletny i pokryty testami,
  adaptery oraz wpięcie w drzwi obecne, bramka domyślnie OFF. Nowa czysta domena
  `core/domain/worklog.py` (commity → sesje pracy; cięcie po przerwie **lub** dobie kalendarzowej,
  rozbieg, zaokrąglanie w górę, `confidence`, rachunki w MINUTACH — sumy dzienne i per zgłoszenie
  zgadzają się co do minuty). Nowy port `JiraWorklogPort` (trzeci obok read/write) i
  `GithubReadPort.list_commits`. Serwis `WorklogService` daje **DWA KROKI**: `propose_worklog`
  (odczyt, zero mutacji) i `log_jira_worklog` (jeden wpis, godziny i dzień podane wprost) — sklejenie
  ich zamieniłoby wiadomości commitów w polecenia zapisu (ADR 0006). Nowy szew
  `core/application/worklog_author.py`: `SelfAuthorStrategy` (zaimplementowana) plus udokumentowane
  sloty `PerUserTokenStrategy`/`TempoWorklogStrategy`. **Jira nie pozwala ustawić autora worklogu** —
  `on_behalf_of` jest adnotacją w treści, nie atrybucją; adaptery świadomie nie wysyłają pola
  `author`, a wynik niesie jawne `note`. Konfiguracja: `WORKMATE_JIRA_ENABLE_WORKLOG` (OFF),
  osobna, węższa `..._WORKLOG_ALLOW_ON_BEHALF` (OFF) i dziewięć pokręteł estymacji/sufitów
  z walidacją fail-fast. Narzędzia `propose_worklog`/`log_jira_worklog` wchodzą przez
  `extra_catalog` — zamrożona powierzchnia MCP (4+1) bez zmian.

### Zmienione
- Wspólne strażniki zapisu do Jiry (`bounded`, `require_jira_key` — pełny kształt `PROJ-123`,
  blokada path-traversal) wydzielone do `core/domain/guards.py`, a parsowanie znaczników Jiry do
  `core/domain/jira_time.py`. Powód: zdolności mutujące urosły do dwóch serwisów, a duplikowanie
  kontroli bezpieczeństwa to dryf. Zachowanie i komunikaty błędów bez zmian.
- **Wsparcie Jira Cloud (dual-provider)** ([ADR 0033](docs/adr/0033-jira-cloud-support.md)): most Jira
  obsługuje teraz OBA warianty za przełącznikiem `WORKMATE_JIRA_DEPLOYMENT` (`server` domyślnie —
  wstecznie zgodne; `cloud`). Nowy `HttpxJiraCloudClient` (REST v3): Basic auth (`WORKMATE_JIRA_EMAIL`
  + API token), `POST /search/jql` z paginacją kursorową (`nextPageToken`/`isLast`, bez `total`, obrona
  przed zapętleniem), tożsamość po `accountId`, treść jako **ADF** (kodowana przy zapisie, spłaszczana do
  tekstu przy odczycie — NA GRANICY adaptera, więc `selection`/poller/serwisy bez zmian). Nowy czysty
  moduł `core/domain/adf.py` (`text_to_adf`/`adf_to_text`). Fabryka `build_jira_client` wybiera
  implementację wg `deployment` (jedno źródło; oba wpięcia ją wołają). Ścieżka Server/DC, jej klient i
  testy — nietknięte. Znane ograniczenie: bulk `/search/jql` ucina inline changelog/komentarze do 20/20
  (dla pollera inkrementalnego wystarcza; fallback per-issue jako follow-up).

## [1.1.0] — 2026-07-18

### Dodane
- **Fundament projekt↔repo↔Jira** ([ADR 0028](docs/adr/0028-project-repo-jira-mapping-and-event-dimension.md)):
  rejestr z `github_repos`/`jira_project_key` + reverse-lookup; wymiar `project`/`repo` w zdarzeniu
  (migracja `ADD COLUMN`); filtr `project` w `read_recent_events`; dedup multi-repo (`composite_external_id`).
- **Realny stan branchy/PR** ([ADR 0029](docs/adr/0029-branch-pr-state-transitions-and-project-activity.md)):
  endpointy `list_pulls`/`list_branches`; atrybucja zdarzeń do projektu; tranzycje `pr_merged`/`pr_closed`;
  zdarzenia `branch_pushed`/`branch_deleted` (SHA-diff); narzędzie `get_project_activity`; wzbogacony
  `get_project_status` (aktywność GitHub, porażki CI). Nowe watch-kindy `pull_state`, `branches` (opt-in).
- **Drzwi Jira read (Server/Data Center)** ([ADR 0030](docs/adr/0030-jira-server-read-door.md)): delegowany
  polling REST v2 tokenem PAT (`workmate-jira`, extra `jira`) → wspólny `EventStore` → Teams. `JiraSettings`
  + `JiraReadPort`/`HttpxJiraClient` (B1.1); `jira/selection` mapuje issue na zdarzenia `jira_issue_created`
  /`jira_transition`/`jira_comment` (dedup tranzycji po `id` wpisu changelogu, watermark JQL po `updated`,
  self-skip konta PAT), poller + entry point + notifier (B1.2). Atrybucja `project` per issue z rejestru
  (`jira_project_key`). Notifier zgeneralizowany: etykieta źródła z `event.source` (`[Jira]`/`[GitHub]`),
  źródło konsumpcji konfigurowalne. Read-only (bez nowej bramki zapisu); wątkowanie kanału OFF w B1.
- **Zapis do Jira (Gate 5, create-only)** ([ADR 0031](docs/adr/0031-jira-write-capability-gate-5.md)):
  bramkowana zdolność mutująca `create_jira_issue` + `comment_jira_issue` (odpowiednik Gate 4 GitHuba).
  Osobny `JiraWritePort`/`JiraWriteService` (rdzeń), bramka `enable_jira_write` per drzwi (domyślnie OFF,
  wystawiana na drzwiach agenta `teams_graph` przez `extra_catalog` — powierzchnia MCP nietknięta). Projekt
  tworzenia z konfiguracji (`WORKMATE_JIRA_WRITE_PROJECT`), nie z treści; komentarz waliduje PEŁNY kształt
  klucza (`PROJ-123`) i zgodność projektu — blokuje obejście cross-project (także path-traversal `WM-1/../X`).
  Strażnik pętli: echo zapisu jako `source="teams"` (notifier `source="jira"` go nie odsyła) + self-skip PAT
  w pollerze; fail-fast walidacji sprzecznej konfiguracji. Tranzycja statusu odłożona do ADR 0032.
- **Tranzycja statusu Jira** ([ADR 0032](docs/adr/0032-jira-status-transition-capability.md)): bramkowane
  narzędzie `transition_jira_issue` — best-effort „walk" po workflow. Model podaje status/akcję docelową →
  serwis dopasowuje ją do widocznej tranzycji (akcja > status, case-insensitive) → `POST /transitions`; id
  tranzycji nigdy nie pochodzi od modelu. NIEZALEŻNA bramka `enable_jira_transition` (domyślnie OFF; profil
  „tylko-tranzycja" bez zapisu) współdzieli szew Gate 5 (`JiraWritePort`/`JiraWriteService`). Wielo-hop za
  `WORKMATE_JIRA_MAX_TRANSITION_HOPS` (domyślnie 1 = single-hop, bezpieczny pilotaż; sufit 10): forced-advance
  tylko przez stany WYMUSZONE, STOP na rozgałęzieniu (bez zgadywania), detekcja cyklu, limit hopów. **Brak
  rollbacku** — zawsze strukturalny raport (`reached`/`path`/`stop_reason`/`available_next`), echo `source=
  "teams"` per hop (`external_id=f"{key}:{updated}"`). Ten sam strażnik klucza/projektu i pętli co zapis.
- **Wątkowanie kanału dla Jiry (B2)** ([ADR 0024](docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md),
  domknięcie odłożenia z [ADR 0030](docs/adr/0030-jira-server-read-door.md)): resolver wątków
  (`core/domain/threads.py`) rozpoznaje teraz URL-e Jira `/browse/{KEY}` → cel `("jira", KEY)`, więc
  utworzenie/tranzycja/komentarz tego samego zgłoszenia trafiają do JEDNEGO wątku na kanale. Wpięcie
  `SqliteThreadLinkStore` w drzwi `workmate-jira` (`_build_thread_links`, wzorzec GitHuba), za tą samą
  flagą `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING` (domyślnie OFF). Klucz `kind="jira"` nie koliduje
  z `pr`/`issue` na wspólnym `events.db`; samowystarczalne w drzwiach Jiry (notifier wypełnia mapę).

## [1.0.0] — 2026-07-17

Pierwsze wydanie produkcyjne — Fazy 1–4 domknięte, most trójstronny zweryfikowany na żywo.

### Dodane

**Faza 1 — serwer MCP (baza wiedzy)**
- Serwer MCP `workmate` z 4 narzędziami odczytu (`search_notes`, `get_note`, `list_projects`,
  `get_project_status`) nad notatkami i statusem projektów (układ firma → projekt — ADR 0005).
- Zamrożony schemat notatki i kontrakt narzędzi (Bramka 1 — ADR 0003).
- Narzędzie zapisu `save_note` z profilem uprawnień per drzwi (Bramka 2 — ADR 0006).
- Wdrożenie sieciowe `streamable-http` z uwierzytelnianiem per osoba i drzwiami read-only
  (Bramka 3 — ADR 0007).

**Faza 2 — runtime agenta i drzwi**
- Runtime agenta w rdzeniu (`workmate-agent`) nad jednoźródłowym katalogiem narzędzi (ADR 0008).
- Drzwi Teams (Bot Framework — ADR 0009 i delegowany Microsoft Graph — ADR 0015), Telegram, CLI.
- Załączniki multimodalne na Teams: obrazy, PDF, dokumenty Office (ADR 0016).
- Bezstratna, wątkowa pamięć rozmów z kompaktowaniem historii i realnym rozliczaniem kosztów
  (ADR 0010–0014); read-only command dispatcher (ADR 0017); katalog roboczy agenta (ADR 0018).

**Fazy 3–4 — most GitHub ↔ EventStore ↔ Teams**
- Wspólny magazyn zdarzeń (append-only SQLite z deduplikacją — ADR 0019).
- Drzwi GitHub `workmate-github` (delegowany polling PAT — ADR 0020); ingest issue/PR/komentarzy/
  recenzji/CI białą listą pól (ADR 0024).
- Bramkowany zapis do GitHub (create-only, Bramka 4 — ADR 0021); proaktywny push do Teams
  (kanał + czat 1:1 — ADR 0022); dwukierunkowe wątki na kanale i deterministyczny auto-komentarz
  przy porażce CI (ADR 0024).
- Dwustronny strażnik pętli (self-skip konta PAT; echo `source="teams"`).

**Retrieval**
- Lokalny retrieval leksykalny notatek: BM25 nad lematami (polski `simplemma`) z fallbackiem
  podłańcuchowym; jakość pilnowana mikro-evalem (ADR 0023).

### Zaplanowane (proposed)
- Tworzenie notatek z Teams (ADR 0025), odpowiedź w wątku plikiem (ADR 0026), wychodzące
  pliki/zdjęcia do użytkownika (ADR 0027) — bramki domyślnie OFF; ADR 0026/0027 wymagają
  zgody admina na zakres zapisu Microsoft Graph.

[1.2.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.2.0
[1.1.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.1.0
[1.0.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.0.0
