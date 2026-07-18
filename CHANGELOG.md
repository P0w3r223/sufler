# Changelog

Wszystkie istotne zmiany w projekcie WorkMate. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

## [Unreleased]

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

[1.0.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.0.0
