# Reference: konfiguracja

Konfiguracja jest scentralizowana w `src/workmate/config.py` (zestaw zamrożonych dataklas
`*Settings` z metodami `from_env()` + `validate()`) i czytana ze zmiennych środowiskowych
(lub z `.env` w korzeniu repo — patrz [`.env.example`](../../.env.example)).

**Co wymaga sekretów, a co nie:**

- **Serwer MCP w trybie `stdio`** (Faza 1) — **nie wymaga żadnych sekretów**; odczyt i zapis
  notatek działają na plikach z `data/`.
- **Runtime agenta** (drzwi Teams/Telegram/CLI) — **wymaga klucza Claude** (`ANTHROPIC_API_KEY`);
  jego brak to twardy błąd startu, nie tryb degradacji.
- **Most GitHub / Jira / push do Teams** — wymagają PAT GitHub, PAT Jira i/lub cache tokenu Microsoft Graph.

Sekrety trzymamy **wyłącznie poza repo** (env / plik poza `data/`). Wszystkie bramki zapisu
(`*_ENABLE_WRITE`, `*_ENABLE_TRANSITION`, `*_ENABLE_CHANNEL*`, `*_CI_AUTO_COMMENT`, `ENABLE_WORKSPACE`)
są **domyślnie wyłączone** i włączane świadomie per drzwi.

---

## Serwer MCP (bazowe `Settings`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_DATA_DIR` | `<repo>/data` | Katalog bazowy danych. |
| `WORKMATE_NOTES_DIR` | `<data_dir>/notes` | Katalog z notatkami `.md`. |
| `WORKMATE_PROJECTS_REGISTRY` | `<data_dir>/projects/registry.yaml` | Plik rejestru projektów. |
| `WORKMATE_TRANSPORT` | `stdio` | Transport MCP: `stdio` lub `streamable-http`. |
| `WORKMATE_LOG_LEVEL` | `INFO` | Poziom logowania. |
| `WORKMATE_ENABLE_WRITE` | `true` | Czy wystawić narzędzie zapisu `save_note` ([ADR 0006](../adr/0006-write-capability-gate-2.md)). Drzwi HTTP wymuszają `false`. |

### Tryb HTTP (`streamable-http`, Bramka 3 / [ADR 0007](../adr/0007-gate-3-http-auth-deployment.md))

Znaczące **tylko** przy `WORKMATE_TRANSPORT=streamable-http`. Procedura: [`how-to/deploy-http.md`](../how-to/deploy-http.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_TOKENS_FILE` | `C:\ProgramData\WorkMate\tokens.json` | Magazyn tokenów per osoba (`sha256` w spoczynku). **Musi leżeć poza `data/`** — inaczej twardy błąd startu. |
| `WORKMATE_BIND_HOST` | `127.0.0.1` | Adres nasłuchu uvicorn (za IIS: loopback). |
| `WORKMATE_BIND_PORT` | `8000` | Port nasłuchu uvicorn. |
| `WORKMATE_ALLOWED_HOSTS` | loopback | Dozwolone nagłówki `Host` (dołóż publiczny host w obu formach: z portem i bez). |
| `WORKMATE_ALLOWED_ORIGINS` | *(puste)* | Dozwolone `Origin`. |
| `WORKMATE_TLS_CERTFILE` / `WORKMATE_TLS_KEYFILE` | *(brak)* | Certyfikat/klucz TLS, gdy uvicorn terminuje TLS bez IIS. |

---

## Runtime agenta (`AgentSettings`, extra `agent`)

Napędza drzwi Teams/Telegram/CLI. `validate()` twardo wymaga klucza.

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `ANTHROPIC_API_KEY` *(lub `WORKMATE_AGENT_API_KEY`)* | *(brak — wymagane)* | **Sekret.** Klucz Claude API. `WORKMATE_AGENT_API_KEY` ma priorytet. |
| `WORKMATE_AGENT_MODEL` | `claude-sonnet-5` | Model agenta. |
| `WORKMATE_AGENT_MAX_TOKENS` | *(rozsądny limit)* | Sufit tokenów odpowiedzi. |
| `WORKMATE_AGENT_MAX_TOOL_ITERATIONS` | *(kilka)* | Maks. iteracji pętli narzędzi na turę. |
| `WORKMATE_AGENT_THINKING` | `adaptive` | Tryb rozumowania (`adaptive`/`disabled`). |

## Pamięć rozmów i kompaktowanie (`ConversationSettings`)

| Zmienna | Opis |
|---------|------|
| `WORKMATE_CONVERSATIONS_DB` | Ścieżka SQLite pamięci rozmów (poza `data/`). |
| `WORKMATE_CONV_MAX_TOKENS` / `WORKMATE_CONV_IDLE_MINUTES` | Budżet kontekstu wątku i granica bezczynności. |
| `WORKMATE_COMPACTION_ENABLED` | Czy kompaktować historię przy zbliżaniu do limitu ([ADR 0014](../adr/0014-conversation-compaction.md)). |
| `WORKMATE_CONTEXT_WINDOW_TOKENS`, `WORKMATE_COMPACTION_THRESHOLD_FRACTION`, `WORKMATE_COMPACTION_KEEP_TURNS`, `WORKMATE_COMPACTION_MODEL` | Parametry progu i strategii kompaktowania. |

## Wspólny magazyn zdarzeń (`EventsSettings`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_EVENTS_DB` | `~/.workmate/events.db` | Plik `EventStore` mostu ([ADR 0019](../adr/0019-shared-event-store.md)). **Wspólny** dla drzwi GitHub, Jira i Teams; ustaw na trwałą ścieżkę serwera. |

## Retrieval (`RetrievalSettings`, extra `retrieval`)

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_RETRIEVAL_LEMMATIZE` | `true` | Lematyzacja zapytań/treści (BM25 nad lematami). Bez extra `retrieval` — fallback podłańcuchowy. |
| `WORKMATE_RETRIEVAL_LANG` | `pl` | Język lematyzacji. |

## Katalog roboczy agenta (`WorkspaceSettings`, [ADR 0018](../adr/0018-agent-working-directory.md))

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_ENABLE_WORKSPACE` | `false` | Bramka narzędzi `create_file`/`read_file`/`list_files` (niezależna od zapisu notatek). |
| `WORKMATE_WORKSPACE_DIR` | pod `data_dir` | Katalog plików roboczych per rozmowa. |
| `WORKMATE_WORKSPACE_MAX_FILE_MB` / `_MAX_FILES` / `_MAX_TOTAL_MB` | limity | Sufity rozmiaru/liczby/łącznego budżetu. |
| `WORKMATE_WORKSPACE_ALLOWED_EXT` | `md,txt,csv,json` | Dozwolone rozszerzenia (tylko tekst). |
| `WORKMATE_WORKSPACE_RETENTION_DAYS` | TTL | Wygasanie bezczynnych katalogów. |

---

## Drzwi Teams — delegowany Graph (`TeamsGraphSettings`, extra `teams-graph`)

Produkcyjny wariant drzwi Teams: polling kanału przez Microsoft Graph jako zalogowany użytkownik
([ADR 0015](../adr/0015-teams-delegated-graph-polling.md)/[0016](../adr/0016-user-multimodal-attachments.md)).
Procedura: [`how-to/teams-graph.md`](../how-to/teams-graph.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_TEAMS_GRAPH_CLIENT_ID` / `_TENANT_ID` | *(wymagane)* | Aplikacja Entra (public client, device-code). |
| `WORKMATE_TEAMS_GRAPH_WATCH` | *(puste → tryb odkrywania)* | Pary `team_id:channel_id` (po przecinku). Puste = wypisz zespoły/kanały i zakończ. |
| `WORKMATE_TEAMS_GRAPH_SCOPES` | zakresy z ADR 0015/0016 | Zakresy Graph (wymagają zgody admina). |
| `WORKMATE_TEAMS_GRAPH_TOKEN_CACHE` | `~/.workmate/teams_token_cache.bin` | **Sekret** (cache MSAL, chmod 600). |
| `WORKMATE_TEAMS_GRAPH_STATE` | `~/.workmate/teams_graph_state.json` | Watermarki wątków. |
| `WORKMATE_TEAMS_GRAPH_POLL_INTERVAL` | `10` | Odstęp odpytań (s). |
| `WORKMATE_TEAMS_GRAPH_TOP_ROOTS` / `_TOP_REPLIES` | `20` / `50` | Limit kosztu API na rundę. |
| `WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS` | próg | Po ilu godzinach ciszy wątek przestaje być odpytywany o odpowiedzi. |
| `WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB` | `8` | Sufit pojedynczego załącznika (RAW). |
| `WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS` | `20` | Maks. załączników na wiadomość. |
| `WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB` | `20` | Łączny budżet załączników. |
| `WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB` | `50` | Sufit rozmiaru pliku ekstrahowanego do tekstu (docx/xlsx/pptx). |
| `WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE` | `2048` | Sufit dłuższej krawędzi obrazu (px, downscaling). |

## Push do Teams (`TeamsPushSettings`, most → Teams)

Notifier wypychający zdarzenia `EventStore` do Teams ([ADR 0022](../adr/0022-proactive-dual-target-teams-push.md)).
Współdzieli cache tokenu z `TeamsGraphSettings`.

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_TEAMS_PUSH_CLIENT_ID` / `_TENANT_ID` | *(wymagane)* | Ta sama aplikacja Entra (device-code). |
| `WORKMATE_TEAMS_PUSH_SCOPES` / `_TOKEN_CACHE` | jak Graph | Zakresy push i wspólny cache MSAL. |
| `WORKMATE_TEAMS_PUSH_ENABLE_CHAT` | `false` | Push na czat 1:1 (wymaga `_CHAT_USER_ID`). |
| `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL` | `false` | Push na kanał (wymaga `_TEAM_ID` + `_CHANNEL_ID`). |
| `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING` | `false` | Dwukierunkowe wątki ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)); wymaga `_ENABLE_CHANNEL`. Warunek działania `reply_on_thread`. |
| `WORKMATE_TEAMS_PUSH_CHAT_USER_ID` / `_TEAM_ID` / `_CHANNEL_ID` | — | Cele push (AAD id / team / channel). |

## Drzwi GitHub / most (`GithubSettings`, extra `github`)

Polling repo tokenem PAT ([ADR 0020](../adr/0020-github-delegated-polling-door.md)). Procedura: [`how-to/github-bridge.md`](../how-to/github-bridge.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_GITHUB_TOKEN` | *(wymagane)* | **Sekret.** Klasyczny PAT (scope `repo`). |
| `WORKMATE_GITHUB_OWNER` / `_REPO` | *(wymagane)* | Repo docelowe. |
| `WORKMATE_GITHUB_API_BASE` | `https://api.github.com` | Baza API (dla GitHub Enterprise). |
| `WORKMATE_GITHUB_POLL_INTERVAL` | `60` (podłoga `30`) | Odstęp odpytań (s). |
| `WORKMATE_GITHUB_PER_PAGE` | `50` | Rozmiar strony. |
| `WORKMATE_GITHUB_WATCH_KINDS` | `issues,comments` | Białą listą: `issues,comments,pulls,reviews,ci` ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)) oraz `pull_state` (tranzycje PR merged/closed) i `branches` (push/delete gałęzi, SHA-diff) ([ADR 0029](../adr/0029-branch-pr-state-transitions-and-project-activity.md)). |
| `WORKMATE_GITHUB_ENABLE_WRITE` | `false` | Bramka 4: zapis create-only (issue/komentarz) + `reply_on_thread` ([ADR 0021](../adr/0021-github-write-capability-gate-4.md)). |
| `WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT` | `false` | Deterministyczny auto-komentarz przy porażce CI; wymaga `_ENABLE_WRITE` ORAZ `ci` w `WATCH_KINDS`. |
| `WORKMATE_GITHUB_SELF_LOGIN` | login konta PAT | Strażnik pętli self-skip (pomija zdarzenia własnego autorstwa). |
| `WORKMATE_GITHUB_STATE` | `~/.workmate/github_state.json` | Watermarki + kursory notifiera/CI. |

## Drzwi Jira Server/DC lub Cloud / most (`JiraSettings`, extra `jira`)

Polling instancji Jira ([ADR 0030](../adr/0030-jira-server-read-door.md)) — **Server/DC** (PAT Bearer,
REST v2) lub **Cloud** ([ADR 0033](../adr/0033-jira-cloud-support.md); Basic email+API-token, REST
v3/ADF, `search/jql`) wg `WORKMATE_JIRA_DEPLOYMENT`; zapis (Gate 5) i tranzycja statusu za NIEZALEŻNYMI
bramkami. Procedura: [`how-to/jira-bridge.md`](../how-to/jira-bridge.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_JIRA_DEPLOYMENT` | `server` | Wariant: `server` (Server/DC, PAT Bearer, v2) lub `cloud` (Cloud, Basic, v3/ADF) — [ADR 0033](../adr/0033-jira-cloud-support.md). |
| `WORKMATE_JIRA_BASE_URL` | *(wymagane)* | URL instancji. Server/DC = własny host; Cloud = `https://<site>.atlassian.net`. |
| `WORKMATE_JIRA_TOKEN` | *(wymagane)* | **Sekret.** PAT (Server/DC) lub API token z id.atlassian.com (Cloud). |
| `WORKMATE_JIRA_EMAIL` | — | E-mail konta do Basic-auth. **Wymagany na Cloud** (`deployment=cloud`); pusty na Server/DC. |
| `WORKMATE_JIRA_WATCH_PROJECTS` | *(wymagane)* | Klucze projektów do nasłuchu (po przecinku, np. `WM,OPS`); mapowanie na projekt WorkMate z rejestru (`jira_project_key`, [ADR 0028](../adr/0028-project-repo-jira-mapping-and-event-dimension.md)). |
| `WORKMATE_JIRA_POLL_INTERVAL` | `60` (podłoga `30`) | Odstęp odpytań (s). |
| `WORKMATE_JIRA_PER_PAGE` | `50` | Rozmiar strony. |
| `WORKMATE_JIRA_STATE` | `~/.workmate/jira_state.json` | Watermark (`updated`) + kursor notifiera. |
| `WORKMATE_JIRA_SELF_ACCOUNT` | login/`accountId` | Strażnik pętli self-skip: login PAT (Server/DC) lub `accountId` (Cloud). **Wymagane** przy włączonym zapisie/tranzycji. |
| `WORKMATE_JIRA_ENABLE_WRITE` | `false` | Gate 5: zapis create-only `create_jira_issue`/`comment_jira_issue` ([ADR 0031](../adr/0031-jira-write-capability-gate-5.md)). Wymaga `_WRITE_PROJECT` + `_SELF_ACCOUNT`. |
| `WORKMATE_JIRA_WRITE_PROJECT` | — | Projekt tworzenia/tranzycji (z konfiguracji, nie z treści prośby). Wymagany przy zapisie/tranzycji. |
| `WORKMATE_JIRA_DEFAULT_ISSUE_TYPE` | `Task` | Domyślny typ tworzonego zgłoszenia. |
| `WORKMATE_JIRA_ENABLE_TRANSITION` | `false` | NIEZALEŻNA bramka tranzycji statusu `transition_jira_issue` ([ADR 0032](../adr/0032-jira-status-transition-capability.md)). Wymaga `_WRITE_PROJECT` + `_SELF_ACCOUNT`. |
| `WORKMATE_JIRA_MAX_TRANSITION_HOPS` | `1` | Sufit hopów walk (1 = single-hop; ≥2 = wielo-hop forced-advance; sufit `10`). |
| `WORKMATE_JIRA_ENABLE_WORKLOG` | `false` | **BRAMKA** ewidencji czasu ([ADR 0034](../adr/0034-jira-worklog-from-github-commits.md)) — niezależna od zapisu i tranzycji. Wymaga `WRITE_PROJECT` + `SELF_ACCOUNT` **oraz** GitHuba (`TOKEN`/`OWNER`/`REPO`). |
| `WORKMATE_JIRA_WORKLOG_AUTHOR_STRATEGY` | `self` | Strategia autorstwa: `self` (jedyna zaimplementowana), `per_user_token`, `tempo` (sloty — start padnie przy włączonej bramce). |
| `WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF` | `false` | **BRAMKA** zapisu w cudzym imieniu. Jira i tak zapisze autorem konto tokenu — atrybucja jest stratna. |
| `WORKMATE_JIRA_WORKLOG_MAX_HOURS` | `8.0` | Sufit godzin na jeden wpis (twardy backstop `24`). |
| `WORKMATE_JIRA_WORKLOG_MAX_BACKDATE_DAYS` | `14` | Ile dni wstecz wolno zapisać wpis (backstop `90`). |
| `WORKMATE_JIRA_WORKLOG_MAX_RANGE_DAYS` | `31` | Szerokość okna jednego `propose_worklog` (backstop `92`). |
| `WORKMATE_JIRA_WORKLOG_IDLE_GAP_MINUTES` | `90` | Przerwa między commitami kończąca sesję (5..720). |
| `WORKMATE_JIRA_WORKLOG_RAMP_UP_MINUTES` | `30` | Czas doliczany przed pierwszym commitem sesji (0..240, ≤ `IDLE_GAP`). |
| `WORKMATE_JIRA_WORKLOG_ROUND_MINUTES` | `15` | Kwant zaokrąglenia w górę (`1`/`5`/`10`/`15`/`30`/`60`). |
| `WORKMATE_JIRA_WORKLOG_TZ_OFFSET_MINUTES` | `120` | Strefa liczenia doby kalendarzowej (stały offset, bez DST). |
| `WORKMATE_JIRA_WORKLOG_DUPLICATE_GUARD` | `true` | Odrzuca drugi wpis tego konta na ten sam dzień w tym samym zgłoszeniu. |

Wątkowanie kanału dla Jiry (B2) korzysta ze wspólnej flagi `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING`
(sekcja *Push do Teams*) — resolver wątków kojarzy zdarzenia jednego zgłoszenia po `/browse/{KEY}`.

## Drzwi Telegram (`TelegramSettings`, extra `telegram`)

| Zmienna | Opis |
|---------|------|
| `WORKMATE_TELEGRAM_BOT_TOKEN` | **Sekret.** Token bota (long polling). |

## Drzwi Teams — Bot Framework (`TeamsSettings`, extra `teams`)

Lokalny wariant przez Bot Framework Emulator/Azure ([`how-to/teams-bot.md`](../how-to/teams-bot.md)).

| Zmienna | Opis |
|---------|------|
| `WORKMATE_TEAMS_APP_ID` / `_APP_PASSWORD` / `_TENANT_ID` | Rejestracja bota (Azure). |
| `WORKMATE_TEAMS_ANONYMOUS` | `true` = tryb bez uwierzytelniania (tylko loopback/Emulator). |
| `WORKMATE_TEAMS_BIND_HOST` / `_PORT` | Adres/port nasłuchu drzwi bota. |

---

## Jak wyznaczana jest ścieżka domyślna

`config.py` szuka korzenia repozytorium, idąc w górę do katalogu z `pyproject.toml`. Dzięki temu
serwer działa niezależnie od bieżącego katalogu roboczego, bez zaszywania ścieżek w kodzie.

## Cotygodniowe karty czasu ([ADR 0035](../adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md))

Drzwi `workmate-worklogi` (extra `worklogi`). Szczegóły: [`how-to/worklogi-weekly.md`](../how-to/worklogi-weekly.md).

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `WORKMATE_WORKLOGI_ENABLED` | `false` | **BRAMKA** drzwi. Wymaga `OUTPUT_DIR`, `IDENTITIES`, `TEAM_ID`. |
| `WORKMATE_WORKLOGI_DRY_RUN` | `true` | Tryb próbny: arkusze powstają, wiadomości NIE wychodzą, stan się nie zapisuje. |
| `WORKMATE_WORKLOGI_OUTPUT_DIR` | — | Katalog arkuszy. **Musi leżeć poza `data/`** (dane osobowe, nie baza wiedzy). |
| `WORKMATE_WORKLOGI_IDENTITIES` | — | Plik YAML `source_id → {aad_user_id, jira_user}`. Fail-closed. |
| `WORKMATE_WORKLOGI_TEAM_ID` | — | Zespół Teams do weryfikacji członkostwa (`TeamMember.Read.All`). |
| `WORKMATE_WORKLOGI_HOURS_SOURCE` | `json` | Źródło godzin. Na razie tylko atrapa `json`. |
| `WORKMATE_WORKLOGI_HOURS_PATH` | — | Ścieżka pliku ze źródłem godzin (dla `json`). |
| `WORKMATE_WORKLOGI_STATE` | `~/.workmate/worklogi_state.json` | Stan idempotencji per tydzień i osoba. |
| `WORKMATE_WORKLOGI_RUN_WEEKDAY` | `4` | Dzień przebiegu (0=poniedziałek, 4=piątek). |
| `WORKMATE_WORKLOGI_RUN_HOUR` / `_RUN_MINUTE` | `16` / `0` | Godzina przebiegu w strefie `TZ`. |
| `WORKMATE_WORKLOGI_TZ` | `Europe/Warsaw` | Strefa granic tygodnia (`ZoneInfo`; walidowana przy starcie). |
| `WORKMATE_WORKLOGI_START_HOUR` | `8` | Godzina stemplowania wpisu w arkuszu (domyślna WorklogPRO). |
| `WORKMATE_WORKLOGI_MAX_HOURS_PER_DAY` | `16.0` | Sufit zdrowego rozsądku na dobę (backstop `24`). |
| `WORKMATE_WORKLOGI_MAX_CATCHUP_DAYS` | `3` | Ile dni wstecz wolno nadrobić pominięty termin (backstop `14`). |
| `WORKMATE_WORKLOGI_ONLY_SOURCE_IDS` | — | Filtr pilotażowy (puste = wszyscy ze źródła). |
