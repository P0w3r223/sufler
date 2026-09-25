# Reference: konfiguracja

Konfiguracja jest scentralizowana w `src/sufler/config/` (zestaw zamrożonych dataklas
`*Settings` z metodami `from_env()` + `validate()`, jeden moduł na domenę; importuj z
`sufler.config`) i czytana ze zmiennych środowiskowych
(lub z `.env` w korzeniu repo — patrz [`.env.example`](../../.env.example)).

**Co wymaga sekretów, a co nie:**

- **Serwer MCP w trybie `stdio`** (Faza 1) — **nie wymaga żadnych sekretów**; odczyt i zapis
  notatek działają na plikach z `data/`.
- **Runtime agenta** (drzwi Teams/CLI) — **wymaga klucza Claude** (`ANTHROPIC_API_KEY`);
  jego brak to twardy błąd startu, nie tryb degradacji.
- **Most GitHub / push do Teams** — wymagają PAT GitHub i/lub cache tokenu Microsoft Graph.
- **Jira** — wyłącznie odczyt „moje zadania" (`get_my_jira_tasks` / `/moje-zadania`); wymaga PAT/API
  tokenu Jira, ale bez pollera ani mostu do Teams ([ADR 0054](../adr/0054-reduce-jira-to-read-only-my-tasks.md)).

Sekrety trzymamy **wyłącznie poza repo** (env / plik poza `data/`). Wszystkie bramki zapisu
(`*_ENABLE_WRITE`, `*_ENABLE_CHANNEL*`, `*_CI_AUTO_COMMENT`, `ENABLE_WORKSPACE`, a od ADR 0064/0065
także `*_ENABLE_FILE_TOOL`, `*_ENABLE_NOTE_MUTATION`, `*_ENABLE_NOTE_DELETE`) są **domyślnie
wyłączone** i włączane świadomie per drzwi. Bramki zdolności czysto obronnych
(`*_ENABLE_NOTE_READ_AUTHZ`, `*_ENABLE_TRUST_LABELS`) też startują wyłączone — domyślną wartością
jest zachowanie sprzed decyzji, nie „bezpieczniejsze".

---

## Serwer MCP (bazowe `Settings`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_DATA_DIR` | `<repo>/data` | Katalog bazowy danych. |
| `SUFLER_NOTES_DIR` | `<data_dir>/notes` | Katalog z notatkami `.md`. |
| `SUFLER_PROJECTS_REGISTRY` | `<data_dir>/projects/registry.yaml` | Plik rejestru projektów. |
| `SUFLER_TRANSPORT` | `stdio` | Transport MCP: `stdio` lub `streamable-http`. |
| `SUFLER_LOG_LEVEL` | `INFO` | Poziom logowania. |
| `SUFLER_ENABLE_WRITE` | `false` | Czy wystawić narzędzie zapisu `save_note` ([ADR 0006](../adr/0006-write-capability-gate-2.md), amendment 2026-07-31 — domyślnie OFF wszędzie, bez wyjątku dla lokalnego stdio). Drzwi HTTP dodatkowo wymuszają `false` niezależnie od env. |
| `SUFLER_NOTE_SNAPSHOTS_DIR` | `<data_dir>/snapshots/notes` | Migawki notatek sprzed mutacji ([ADR 0065](../adr/0065-mutable-knowledge-base-and-model-judged-writes.md)) — jedyna ścieżka cofnięcia `File(edit\|delete)`. Domyślna wartość leży **wewnątrz** `data/`: przy włączonej mutacji przestaw ją na wolumen stanu. Powody są trzy i niezależne — patrz akapit pod tabelą. |
| `SUFLER_METRICS_DB` | *(brak = wyłączone)* | Licznik użycia, pseudonimizowany ([ADR 0049](../adr/0049-usage-metrics-pseudonymized-counter.md)). |
| `SUFLER_AUDIT_DB` | *(brak = wyłączone)* | Dziennik audytu wywołań narzędzi ([ADR 0067](../adr/0067-observability-audit-journal-and-notifier-dead-letter.md)) — rejestruje akcje i ścieżki, nigdy treści. |

Trzy ostatnie ścieżki wchodzą na listę `Settings.persistent_paths()`, którą drzwi sprawdzają
`require_writable` przy starcie: **zła ścieżka to twardy błąd startu**, a nie cichy brak zapisu
(rejestrator audytu łyka błędy per wywołanie, więc bez tej sondy dawał „dziennik" z zerem wierszy).
Migawki wchodzą na sondę **tylko przy `_ENABLE_NOTE_MUTATION=true`**, bo bezwarunkowa sonda kładłaby
drzwi na katalogu, którego proces nigdy nie tknie. Bazy metryk i audytu wchodzą wyłącznie wtedy, gdy
operator jawnie wskazał plik.

Obie te bazy plus obie kwarantanny w `events.db` czyta `sufler-diagnostics` (ADR 0069, R2):
`sufler-diagnostics {audit|dead-letters|inbound} [--db …] [--source …] [--since 24h] [--json]`.
Bez `--db` narzędzie bierze ścieżkę ze zmiennej (`SUFLER_AUDIT_DB` dla audytu,
`SUFLER_EVENTS_DB` dla kwarantann) i **nie zgaduje** domyślnej — otwiera połączenie tylko do
odczytu, więc zła ścieżka wraca jako błąd, a nie jako pusty, świeżo założony plik.

**Dlaczego migawki mają wyjść spod `data/` — trzy niezależne powody, dwa zależne od wdrożenia.**

1. **Agent czyta katalog notatek zachłannie**, więc kopie leżące w środku wracałyby jako wyniki
   wyszukiwania. Ten powód obowiązuje ZAWSZE, niezależnie od montażu.
2. **W tym repozytorium `data/` jest montowane `:ro`** (`deploy/docker/docker-compose.yml` — bind
   z katalogu hosta, bazą wiedzy zarządza `git pull`), więc pierwsza migawka padłaby na sondzie
   zapisywalności.
3. **Na flocie wdrożeniowej `data/` jest montowane RW** (`infra-docker-workmate/docker-compose.yml`
   — nazwany wolumen `workmate-data`, bo `/notatka` z Teams musi tam pisać; drugi, osobny montaż
   `workmate-data:/mnt/system` jest `:ro` dla powłoki). Tam powodem jest co innego:
   `tools/restore-notes.sh` czyści **cały** wolumen docelowy, więc odtworzenie kopii skasowałoby
   razem z bazą wiedzy mechanizm cofania pojedynczej zmiany.

Zalecenie jest w obu wdrożeniach to samo — wolumen stanu — ale uzasadnienie różne; nie przenoś
jednego na drugie.

### Tryb HTTP (`streamable-http`, Bramka 3 / [ADR 0007](../adr/0007-gate-3-http-auth-deployment.md))

Znaczące **tylko** przy `SUFLER_TRANSPORT=streamable-http`. Procedura: [`how-to/deploy-http.md`](../how-to/deploy-http.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_TOKENS_FILE` | `C:\ProgramData\Sufler\tokens.json` | Magazyn tokenów per osoba (`sha256` w spoczynku). **Musi leżeć poza `data/`** — inaczej twardy błąd startu. |
| `SUFLER_BIND_HOST` | `127.0.0.1` | Adres nasłuchu uvicorn (za IIS: loopback). |
| `SUFLER_BIND_PORT` | `8000` | Port nasłuchu uvicorn. |
| `SUFLER_ALLOWED_HOSTS` | loopback | Dozwolone nagłówki `Host` (dołóż publiczny host w obu formach: z portem i bez). |
| `SUFLER_ALLOWED_ORIGINS` | *(puste)* | Dozwolone `Origin`. |
| `SUFLER_TLS_CERTFILE` / `SUFLER_TLS_KEYFILE` | *(brak)* | Certyfikat/klucz TLS, gdy uvicorn terminuje TLS bez IIS. |

---

## Runtime agenta (`AgentSettings`, extra `agent`)

Napędza drzwi Teams/CLI. `validate()` twardo wymaga klucza.

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `ANTHROPIC_API_KEY` *(lub `SUFLER_AGENT_API_KEY`)* | *(brak — wymagane)* | **Sekret.** Klucz Claude API. `SUFLER_AGENT_API_KEY` ma priorytet. |
| `SUFLER_AGENT_MODEL` | `claude-sonnet-5` | Model agenta. |
| `SUFLER_AGENT_MAX_TOKENS` | *(rozsądny limit)* | Sufit tokenów odpowiedzi. |
| `SUFLER_AGENT_MAX_TOOL_ITERATIONS` | *(kilka)* | Maks. iteracji pętli narzędzi na turę. |
| `SUFLER_AGENT_THINKING` | `adaptive` | Tryb rozumowania (`adaptive`/`disabled`). |

## Pamięć rozmów i kompaktowanie (`ConversationSettings`)

| Zmienna | Opis |
|---------|------|
| `SUFLER_CONVERSATIONS_DB` | Ścieżka SQLite pamięci rozmów (poza `data/`). |
| `SUFLER_CONV_MAX_TOKENS` / `SUFLER_CONV_IDLE_MINUTES` | Budżet kontekstu wątku i granica bezczynności. |
| `SUFLER_COMPACTION_ENABLED` | Czy kompaktować historię przy zbliżaniu do limitu ([ADR 0014](../adr/0014-conversation-compaction.md)). |
| `SUFLER_COMPACTION_THRESHOLD_TOKENS`, `SUFLER_COMPACTION_KEEP_TURNS`, `SUFLER_COMPACTION_MODEL` | Parametry progu i strategii kompaktowania. Próg jest BEZWZGLĘDNY (domyślnie 150 000 tokenów wejścia); dawne `SUFLER_CONTEXT_WINDOW_TOKENS` i `SUFLER_COMPACTION_THRESHOLD_FRACTION` nie są już czytane ([ADR 0058](../adr/0058-context-editing-and-absolute-compaction-threshold.md)). |
| `SUFLER_CONTEXT_EDITING_ENABLED`, `SUFLER_CONTEXT_EDITING_TRIGGER_TOKENS`, `SUFLER_CONTEXT_EDITING_KEEP_TOOL_USES`, `SUFLER_CONTEXT_EDITING_CLEAR_AT_LEAST_TOKENS` | Czyszczenie starych wyników narzędzi po stronie Claude API ([ADR 0058](../adr/0058-context-editing-and-absolute-compaction-threshold.md)). `KEEP_TOOL_USES` musi być >= `SUFLER_AGENT_MAX_TOOL_ITERATIONS` — inaczej start jest odrzucany. |

## Wspólny magazyn zdarzeń (`EventsSettings`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_EVENTS_DB` | `~/.sufler/events.db` | Plik `EventStore` mostu ([ADR 0019](../adr/0019-shared-event-store.md)). **Wspólny** dla drzwi GitHub, Jira i Teams; ustaw na trwałą ścieżkę serwera. |

## Retrieval (`RetrievalSettings`, extra `retrieval`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_RETRIEVAL_LEMMATIZE` | `true` | Lematyzacja zapytań/treści (BM25 nad lematami). Bez extra `retrieval` — fallback podłańcuchowy. |
| `SUFLER_RETRIEVAL_LANG` | `pl` | Język lematyzacji. |

## Katalog roboczy agenta (`WorkspaceSettings`, [ADR 0018](../adr/0018-agent-working-directory.md))

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_ENABLE_WORKSPACE` | `false` | Bramka narzędzi `CreateFile`/`ReadFile`/`ListFiles` (niezależna od zapisu notatek; nazwy w PascalCase od [ADR 0068](../adr/0068-agent-tool-names-and-the-cost-of-a-wrong-one.md)). |
| `SUFLER_WORKSPACE_DIR` | pod `data_dir` | Katalog plików roboczych per rozmowa. |
| `SUFLER_WORKSPACE_MAX_FILE_MB` / `_MAX_FILES` / `_MAX_TOTAL_MB` | limity | Sufity rozmiaru/liczby/łącznego budżetu. |
| `SUFLER_WORKSPACE_ALLOWED_EXT` | `md,txt,csv,json` | Dozwolone rozszerzenia (tylko tekst). |
| `SUFLER_WORKSPACE_RETENTION_DAYS` | TTL | Wygasanie bezczynnych katalogów. |

---

## Drzwi Teams — delegowany Graph (`TeamsGraphSettings`, extra `teams-graph`)

Produkcyjny wariant drzwi Teams: polling kanału przez Microsoft Graph jako zalogowany użytkownik
([ADR 0015](../adr/0015-teams-delegated-graph-polling.md)/[0016](../adr/0016-user-multimodal-attachments.md)).
Procedura: [`how-to/teams-graph.md`](../how-to/teams-graph.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_TEAMS_GRAPH_CLIENT_ID` / `_TENANT_ID` | *(wymagane)* | Aplikacja Entra (public client, device-code). |
| `SUFLER_TEAMS_GRAPH_WATCH` | *(puste → tryb odkrywania)* | Pary `team_id:channel_id` (po przecinku). Puste = wypisz zespoły/kanały i zakończ. |
| `SUFLER_TEAMS_GRAPH_SCOPES` | zakresy z ADR 0015/0016 | Zakresy Graph (wymagają zgody admina). |
| `SUFLER_TEAMS_GRAPH_TOKEN_CACHE` | `~/.sufler/teams_token_cache.bin` | **Sekret** (cache MSAL, chmod 600). |
| `SUFLER_TEAMS_GRAPH_STATE` | `~/.sufler/teams_graph_state.json` | Watermarki wątków. |
| `SUFLER_TEAMS_GRAPH_POLL_INTERVAL` | `10` | Odstęp odpytań (s). |
| `SUFLER_TEAMS_GRAPH_TOP_ROOTS` / `_TOP_REPLIES` | `20` / `50` | Limit kosztu API na rundę. |
| `SUFLER_TEAMS_GRAPH_ACTIVE_IDLE_HOURS` | próg | Po ilu godzinach ciszy wątek przestaje być odpytywany o odpowiedzi. |
| `SUFLER_TEAMS_GRAPH_MAX_ATTACHMENT_MB` | `8` | Sufit pojedynczego załącznika (RAW). |
| `SUFLER_TEAMS_GRAPH_MAX_ATTACHMENTS` | `20` | Maks. załączników na wiadomość. |
| `SUFLER_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB` | `20` | Łączny budżet załączników. |
| `SUFLER_TEAMS_GRAPH_MAX_EXTRACT_MB` | `50` | Sufit rozmiaru pliku ekstrahowanego do tekstu (docx/xlsx/pptx). |
| `SUFLER_TEAMS_GRAPH_MAX_IMAGE_EDGE` | `2048` | Sufit dłuższej krawędzi obrazu (px, downscaling). |

### Bramki zdolności na drzwiach Teams (ADR 0062–0066) — wszystkie domyślnie `false`

Pięć przełączników, których **kod nie włącza sam** i których żaden wariant wdrożenia nie ustawia
domyślnie. Kolumna *Wymaga* zbiera warunki, których niespełnienie **zatrzymuje start drzwi**, ale
egzekwują je DWA różne mechanizmy: zależności między bramkami i obecność mapy tożsamości sprawdza
`TeamsGraphSettings.validate()`, a zapisywalność katalogu migawek — `require_writable` po liście
`Settings.persistent_paths()`, wołane w `main()` drzwi (efekt uboczny `mkdir` nie może wejść do
`validate`). Macierz „chcę, żeby…": [`how-to/gate-matrix.md`](../how-to/gate-matrix.md) rozdziela to
per wiersz.

| Zmienna | Co włącza | Wymaga |
|---------|-----------|--------|
| `SUFLER_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ` | Autoryzację odczytu notatek — fail-closed po mapie tożsamości; nierozpoznany nadawca nie dostaje narzędzi bazy wiedzy ([ADR 0062](../adr/0062-note-read-authorization.md)) | istniejący plik `SUFLER_TEAMS_GRAPH_IDENTITIES` |
| `SUFLER_TEAMS_GRAPH_ENABLE_FILE_TOOL` | `File(action='read')` — model podaje sobie plik z katalogu roboczego DO WGLĄDU — **oraz** odkładanie załączników użytkownika na dysk rozmowy ([ADR 0064](../adr/0064-file-tool-and-model-initiated-materialization.md)). Wyłączona gasi obie strony naraz | — |
| `SUFLER_TEAMS_GRAPH_ENABLE_NOTE_MUTATION` | `File(action='edit')` — pierwszą w historii tego systemu drogę **zmiany** istniejącej notatki, przez `NoteMutationService` i niezależnego sędziego ([ADR 0065](../adr/0065-mutable-knowledge-base-and-model-judged-writes.md)) | `validate()`: `_ENABLE_FILE_TOOL=true` (mutacja jest akcją narzędzia `File`) + istniejący plik `_IDENTITIES`. Osobno `require_writable`: zapisywalny `SUFLER_NOTE_SNAPSHOTS_DIR` (sondowany dopiero przy tej bramce włączonej) |
| `SUFLER_TEAMS_GRAPH_ENABLE_NOTE_DELETE` | `File(action='delete')` — kasowanie notatki | `_ENABLE_NOTE_MUTATION=true`; **plus warunek spoza kodu**: działająca nocna kopia wolumenu, na której ADR 0065 opiera całą odwracalność |
| `SUFLER_TEAMS_GRAPH_ENABLE_TRUST_LABELS` | Koperty klas zaufania T0–T3 wokół treści OBCEJ, z granicą znaczoną nonce'em losowanym na turę, i lepką skazę rozmowy ([ADR 0066](../adr/0066-content-trust-classes-and-sticky-conversation-taint.md)) | — (patrz uwaga niżej) |

`_ENABLE_TRUST_LABELS` jest **jedyną z tej piątki bez warunku w `validate()`**: włączona sama daje
koperty, ale nie rozszczepienie nadawcy na T1/T2 — to liczy ten sam autoryzator co
`_ENABLE_NOTE_READ_AUTHZ`, więc bez niej każdy nadawca jest nierozpoznany. Chcąc całe ADR 0066,
włącz obie. Sama koperta **nie jest obroną przed wstrzyknięciem promptu** — granicą zostają bramki
zdolności, montaż `ro`, wykonawca bez sieci i odwracalność.

## Push do Teams (`TeamsPushSettings`, most → Teams)

Notifier wypychający zdarzenia `EventStore` do Teams ([ADR 0022](../adr/0022-proactive-dual-target-teams-push.md)).
Współdzieli cache tokenu z `TeamsGraphSettings`.

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_TEAMS_PUSH_CLIENT_ID` / `_TENANT_ID` | *(wymagane)* | Ta sama aplikacja Entra (device-code). |
| `SUFLER_TEAMS_PUSH_SCOPES` / `_TOKEN_CACHE` | jak Graph | Zakresy push i wspólny cache MSAL. |
| `SUFLER_TEAMS_PUSH_ENABLE_CHAT` | `false` | Push na czat 1:1 (wymaga `_CHAT_USER_ID`). |
| `SUFLER_TEAMS_PUSH_ENABLE_CHANNEL` | `false` | Push na kanał (wymaga `_TEAM_ID` + `_CHANNEL_ID`). |
| `SUFLER_TEAMS_PUSH_ENABLE_CHANNEL_THREADING` | `false` | Dwukierunkowe wątki ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)); wymaga `_ENABLE_CHANNEL`. Warunek odpowiadania z wątku bez podawania numeru issue przez model. |
| `SUFLER_TEAMS_PUSH_CHAT_USER_ID` / `_TEAM_ID` / `_CHANNEL_ID` | — | Cele push (AAD id / team / channel). |

## Drzwi GitHub / most (`GithubSettings`, extra `github`)

Polling repo tokenem PAT ([ADR 0020](../adr/0020-github-delegated-polling-door.md)). Procedura: [`how-to/github-bridge.md`](../how-to/github-bridge.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_GITHUB_TOKEN` | *(wymagane)* | **Sekret.** Klasyczny PAT (scope `repo`). |
| `SUFLER_GITHUB_OWNER` / `_REPO` | *(wymagane)* | Repo docelowe. |
| `SUFLER_GITHUB_API_BASE` | `https://api.github.com` | Baza API (dla GitHub Enterprise). |
| `SUFLER_GITHUB_POLL_INTERVAL` | `60` (podłoga `30`) | Odstęp odpytań (s). |
| `SUFLER_GITHUB_PER_PAGE` | `50` | Rozmiar strony. |
| `SUFLER_GITHUB_WATCH_KINDS` | `issues,comments` | Białą listą: `issues,comments,pulls,reviews,ci` ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)) oraz `pull_state` (tranzycje PR merged/closed) i `branches` (push/delete gałęzi, SHA-diff) ([ADR 0029](../adr/0029-branch-pr-state-transitions-and-project-activity.md)). |
| `SUFLER_GITHUB_ENABLE_WRITE` | `false` | Bramka 4: akcje `Activity(action='create_issue'/'comment')`, create-only ([ADR 0021](../adr/0021-github-write-capability-gate-4.md)). |
| `SUFLER_GITHUB_ENABLE_CI_AUTO_COMMENT` | `false` | Deterministyczny auto-komentarz przy porażce CI; wymaga `_ENABLE_WRITE` ORAZ `ci` w `WATCH_KINDS`. |
| `SUFLER_GITHUB_STATE` | `~/.sufler/github_state.json` | Watermarki + kursory notifiera/CI. |

### Propozycja czasu z commitów ([ADR 0034](../adr/0034-jira-worklog-from-github-commits.md), odczyt)

Narzędzie `propose_worklog` wchodzi **bez bramki**, gdy skonfigurowany jest GitHub — po usunięciu
ścieżki zapisu (2026-07-21) niczego nie mutuje, a repo bramkuje zapis, nie odczyt (ADR 0006).
Poniższe pokrętła stroją wyłącznie estymację; wartość spoza zakresu = twardy błąd startu.
Procedura: [`how-to/worklog-from-commits.md`](../how-to/worklog-from-commits.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_GITHUB_WORKLOG_IDLE_GAP_MINUTES` | `90` | Przerwa między commitami kończąca sesję (5..720). |
| `SUFLER_GITHUB_WORKLOG_RAMP_UP_MINUTES` | `30` | Czas doliczany przed pierwszym commitem sesji (0..240, ≤ `IDLE_GAP`). |
| `SUFLER_GITHUB_WORKLOG_ROUND_MINUTES` | `15` | Kwant zaokrąglenia w górę (`1`/`5`/`10`/`15`/`30`/`60`). |
| `SUFLER_GITHUB_WORKLOG_MAX_SESSION_HOURS` | `8.0` | Sufit JEDNEJ sesji — backstop przed absurdem z rzadkiego commitowania (max `24`). |
| `SUFLER_GITHUB_WORKLOG_MAX_RANGE_DAYS` | `31` | Szerokość okna jednego `propose_worklog` (backstop `92`). |
| `SUFLER_GITHUB_WORKLOG_TZ` | `Europe/Warsaw` | Strefa liczenia doby kalendarzowej (nazwa IANA, `ZoneInfo` — odporna na DST). |

## Drzwi Jira Server/DC lub Cloud — TYLKO odczyt „moje zadania" (`JiraSettings`, extra `jira`)

Jira jest zredukowana do JEDNEJ, wyłącznie odczytowej zdolności ([ADR 0054](../adr/0054-reduce-jira-to-read-only-my-tasks.md),
supersedes [0031](../adr/0031-jira-write-capability-gate-5.md)/[0032](../adr/0032-jira-status-transition-capability.md)):
narzędzie `get_my_jira_tasks` zwraca TYLKO otwarte zadania przypisane pytającemu, zero parametrów.
Nie ma już pollera (`sufler-jira`), pushu do Teams ani mostu Teams↔Jira. **Server/DC** (PAT Bearer,
REST v2) lub **Cloud** ([ADR 0033](../adr/0033-jira-cloud-support.md); Basic email+API-token, REST
v3/ADF, `search/jql`) wg `SUFLER_JIRA_DEPLOYMENT`. Procedura: [`how-to/jira-my-tasks.md`](../how-to/jira-my-tasks.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `SUFLER_JIRA_DEPLOYMENT` | `server` | Wariant: `server` (Server/DC, PAT Bearer, v2) lub `cloud` (Cloud, Basic, v3/ADF) — [ADR 0033](../adr/0033-jira-cloud-support.md). |
| `SUFLER_JIRA_BASE_URL` | *(wymagane)* | URL instancji. Server/DC = własny host; Cloud = `https://<site>.atlassian.net`. |
| `SUFLER_JIRA_TOKEN` | *(wymagane)* | **Sekret.** PAT (Server/DC) lub API token z id.atlassian.com (Cloud). |
| `SUFLER_JIRA_EMAIL` | — | E-mail konta do Basic-auth. **Wymagany na Cloud** (`deployment=cloud`); pusty na Server/DC. |
| `SUFLER_JIRA_MY_ACCOUNT` | — | Principal, którego zadania wystawia narzędzie MCP na drzwiach stdio (Claude Code/CLI). Bez tej zmiennej narzędzie NIE wchodzi — z góry skonfigurowany, jeden na proces (nie nadaje się na współdzielony serwer HTTP z wieloma osobami). Na drzwiach Teams tożsamość idzie zamiast tego z mapy AAD→Jira (`SUFLER_TEAMS_GRAPH_IDENTITIES`, pole `jira_user`). |

Wątkowanie i tranzycja statusu, poller, push do Teams i zapis (create/comment) — **usunięte w
całości**. Watch-listy projektów, self-skip, interwał pollingu, projekt zapisu itd. nie mają już
zastosowania (nie ma czego pollingować ani co zapisywać).

## Drzwi Teams — Bot Framework (`TeamsSettings`, extra `teams`)

Lokalny wariant przez Bot Framework Emulator/Azure ([`how-to/teams-bot.md`](../how-to/teams-bot.md)).

| Zmienna | Opis |
|---------|------|
| `SUFLER_TEAMS_APP_ID` / `_APP_PASSWORD` / `_TENANT_ID` | Rejestracja bota (Azure). |
| `SUFLER_TEAMS_ANONYMOUS` | `true` = tryb bez uwierzytelniania (tylko loopback/Emulator). |
| `SUFLER_TEAMS_BIND_HOST` / `_PORT` | Adres/port nasłuchu drzwi bota. |
| `SUFLER_TEAMS_IDENTITIES` | Mapa tożsamości AAD → członek pionu; ten sam format i zwykle ten sam plik co `SUFLER_TEAMS_GRAPH_IDENTITIES`. |
| `SUFLER_TEAMS_ENABLE_NOTE_READ_AUTHZ` | Bramka członkostwa ODCZYTU bazy wiedzy ([ADR 0062](../adr/0062-note-read-authorization.md)), domyślnie OFF; włączona wymaga `_IDENTITIES` (fail-fast). Tożsamość nadawcy pochodzi z `activity.from.aadObjectId` — gość bez AAD id jest nierozpoznany i odczytu nie dostaje. |

---

## Jak wyznaczana jest ścieżka domyślna

`config/_env.py` szuka korzenia repozytorium, idąc w górę do katalogu z `pyproject.toml`. Dzięki temu
serwer działa niezależnie od bieżącego katalogu roboczego, bez zaszywania ścieżek w kodzie.

## Karty czasu (WorklogPRO) — WYCOFANE

Cały moduł cotygodniowych kart czasu (generowanie arkuszy WorklogPRO, wysyłka DM, self-service na
żądanie) został wycofany z projektu w całości ([ADR 0055](../adr/0055-withdraw-worklogpro-timesheets.md),
supersedes 0035/0036/0037/0038) — to decyzja trwała, nie pauza. `WorklogiSettings`, drzwi
`sufler-worklogi`/`sufler-worklog-selfservice` i wszystkie zmienne `SUFLER_WORKLOGI_*` nie
istnieją już w `config/`. Propozycja czasu z commitów GitHub (`propose_worklog`, czyste odczytowe
narzędzie, [ADR 0034](../adr/0034-jira-worklog-from-github-commits.md)) **zostaje bez zmian** — patrz
sekcja *Propozycja czasu z commitów* wyżej (`GithubSettings`).
