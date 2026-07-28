# GAPS.md — audyt gotowości wdrożeniowej WorkMate (flota Docker/Ubuntu)

## 1. Potwierdzone i nierozstrzygalne

| ID | Status | Dowód (plik:linia) | Co się stanie na serwerze | Waga |
|----|--------|--------------------|----------------------------|------|
| R1 | POTWIERDZONA | `adapters/inbound/github/state.py:25` (zapis `write_text`, nieatomowy) + `:18` (`json.loads` bez try/except); `adapters/inbound/worklogi/state.py:7-8,46` (atomowy `os.replace` — docstring wprost: „świadomie NIE z github/state.py, gdzie write_text zostawia okno na ucięty plik"); brak handlera SIGTERM w całym `src/` (grep 0) | `docker stop`/reboot trafiający w zapis `github_state.json`/`jira_state.json` (a te zapisują się po każdym cyklu z watermarkiem) zostawia ucięty plik. Przy restarcie `load()` woła `json.loads` bez obsługi błędu → drzwi github/jira wpadają w **crash-loop** (`restart: unless-stopped` restartuje, `load()` znów pada) aż operator ręcznie usunie plik stanu. Dokładnie scenariusz „przetrwanie restartu kontenera / reboota maszyny". | DNI |
| A4 | POTWIERDZONA | `adapters/outbound/jira_api.py:194,199,206` (tylko `raise_for_status`, brak retry/429); `adapters/outbound/jira_cloud_api.py:215,220,227` (jw.). Kontrast: `github_api.py:221-281` (X-RateLimit/Retry-After/backoff) i wszystkie `graph_*.py` mają 429+Retry-After | Przy throttlingu Jiry (429) lub 5xx cykl pollingu rzuca. Pętla go łapie (`jira/poller.py:87-93`) i ponawia dopiero po `poll_interval` (30–60 s) **bez honorowania `Retry-After`** → wydłużony throttling i hałaśliwe nieudane cykle aż okno limitu minie. Nie zabija procesu (samonaprawialne). | DNI |
| R3 | POTWIERDZONA | `github/app.py:39`, `jira/app.py:42`, `teams_graph/app.py:63`, `worklogi/app.py:59`, `telegram/app.py:36` (zaszyte `logging.basicConfig(level=logging.INFO)`) vs `server.py:133` (jedyne miejsce czytające `settings.log_level`) | Poziom logów drzwi-pollerów jest zaszyty na INFO; `WORKMATE_LOG_LEVEL` działa TYLKO dla serwera MCP. Nie da się env-em podnieść do DEBUG do diagnozy ani wyciszyć. Logi idą na stderr (docker je łapie) — NIE do pliku. | DROBNE |
| R5 | POTWIERDZONA | `docker-compose.yml:59-65` (healthcheck tylko usługi `mcp`: socket-connect na :8000); brak `healthcheck:` w `github`/`jira`/`teams-graph`/`worklogi`/`telegram` | Jedyny healthcheck dowodzi tylko nasłuchu portu, nie ważności tokenu ani połączenia z Graph/GitHub/Jirą. Drzwi-pollery nie mają healthchecku wcale — „zawieszony ale żywy" poller albo wygasły refresh-token (pętla jałowo loguje `AuthExpiredError`) nie jest wykrywany; restart wyzwala dopiero śmierć procesu. | DROBNE |
| L1 | POTWIERDZONA | `config.py:24` `_DEFAULT_TOKENS_FILE = Path("C:/ProgramData/WorkMate/tokens.json")` | Zaszyta domyślna ścieżka Windows dla magazynu tokenów MCP. Nadpisywana przez `WORKMATE_TOKENS_FILE` (env.example:40); przy trybie HTTP start jest fail-fast, gdy plik zły/nieobecny (`server.py:135-137`) — nie ma cichej awarii, ale bez nadpisania na Linuksie ścieżka jest bez sensu. | DROBNE |
| C1 | NIEROZSTRZYGALNA | brak w obrazie floty — `Powiadomienia_teams/` wykluczone (`.dockerignore:37`), ma własny `Dockerfile`/`docker-compose.yml` | Nudge Shifts, watermark z czasu serwera, „jedna prośba na osobę/tydzień" i okno wygasające po dowodzie pustego odczytu należą do POD-PROJEKTU `Powiadomienia_teams` — osobny artefakt wdrożenia, nieoceniany w tym audycie. Dla floty (`worklogi`) strefę liczy jawnie `ZoneInfo(Europe/Warsaw)` niezależnie od TZ kontenera (patrz OBALONE). | — |
| A8 | NIEROZSTRZYGALNA | brak w repo skonsolidowanej listy bramek-do-włączenia; poszczególne bramki mają fail-fast (`config.py` `validate`) | Nie istnieje jedna lista „co MUSI być ON, by bot pełnił funkcję". Per-bramka walidacja to kompensuje (włączona-ale-martwa bramka = twardy błąd startu), ale macierz trzeba wyprowadzić z kodu — dostarczona w sekcji 3 „Bramki". | — |

## 2. Obalone

OBALONE: T1, T2, T3, T4, T5, T6, T7, O1, O2, O3, O4, O5, O6, L2, L3, L4, L5, A1, A2, A3, A5, A6, A7, C2, C3, R2, R4

Skróty dowodowe najistotniejszych: **A1/A2** `teams_graph/auth.py:87` (`acquire_token_silent` z cache przed device-code) + `:68-75` (`_save_cache` na skonfigurowaną ścieżkę = wolumen) → headless po jednorazowym primingu, refresh przeżywa restart. **A3** compose wdraża TYLKO `teams-graph` (delegowany polling, brak `teams`/Bot Framework, brak publicznego endpointu); async /notatka (ADR 0043) odsyła wynik do wątku przez `graph_thread_reply` (OUTBOUND) — nie wymaga adresu osiągalnego z zewnątrz. **A5/R6** każda pętla łapie `except Exception` + `logger.exception` + kontynuuje: `jira/poller.py:87-93`, `github/poller.py:88-94`, `notifier.py:124-129`, `github/app.py:242-249`, `worklogi/app.py:132-137`. **A6** wszystkie drzwi walidują na starcie: `github/app.py:43,46`, `jira/app.py:46,49`, `teams_graph/app.py:67,84,86,88`, `worklogi/app.py:64`, `telegram/app.py:41,43,45`. **C2/C3** `worklogi/app.py:97-119` (scheduler wewnętrzny `next_run`+sleep 900 s), `:140-164` (`_missed_deadline` catchup ≤ `max_catchup_days` + dedup po stanie na wolumenie → brak podwójnego wysłania). **R2** `Dockerfile:104` exec-form `ENTRYPOINT ["/usr/bin/tini","--"]` (PID 1, forwarduje sygnały). **R4** auth w nagłówkach nie w URL (`jira_cloud_api.py:60` Basic w headerze; github Bearer), sekrety `repr=False`, brak logowania treści DM (grep w notifier/graph/teams_graph = 0). **T4** `sqlite_events.py:65-66`, `sqlite_conversations.py:144-145`, `sqlite_thread_links.py:46-47` (WAL+busy_timeout+Lock). **O4** `python:3.11-slim` ma systemową bazę stref — `ZoneInfo("Europe/Warsaw")` zweryfikowane empirycznie w kontenerze.

## 3. Kontrakt wdrożenia (wyłącznie z kodu)

### Procesy (usługa compose → entrypoint → profil)
| Usługa | Komenda | Profil | restart | Uwaga |
|--------|---------|--------|---------|-------|
| mcp | `workmate` (streamable-http) | mcp | unless-stopped | serwer wiedzy; save_note KONSTRUKCYJNIE OFF na HTTP (`server.py:108`) |
| nginx | `nginx:1.27-alpine` | mcp | unless-stopped | terminacja TLS + proxy SSE |
| github | `workmate-github` | bridge | unless-stopped | polling PAT → events.db (+ push Teams jeśli cel ON) |
| jira | `workmate-jira` | bridge | unless-stopped | polling PAT/Basic → events.db |
| teams-graph | `workmate-teams-graph` | bridge | unless-stopped | agent na kanale; WYMAGA `WATCH` (inaczej discovery-exit → restart-loop) |
| worklogi | `workmate-worklogi` | worklogi | on-failure | domyślnie OFF (exit 0 = poprawne wyłączenie) |
| telegram | `workmate-telegram` | telegram | unless-stopped | long polling + agent |
| worklog-selfservice | `workmate-worklog-selfservice` | tools | no | NA ŻĄDANIE (`compose run`), NIE autostart |

### Porty wystawiane na zewnątrz
- **443** i **80** — tylko usługa `nginx` (`docker-compose.yml:78-79`).
- `mcp` ma `expose: 8000` — sieć wewnętrzna compose, NIE publikowane na host.
- Drzwi bridge/worklogi/telegram — brak portów (polling wychodzący).

### Wolumeny (ścieżka → co ginie bez niej)
| Wolumen | Montaż | Bez niego ginie |
|---------|--------|-----------------|
| `workmate-state` | `/var/lib/workmate` | `events.db` (dedup → **powtórne powiadomienia i komentarze**), `conversations.db` (pamięć rozmów agenta), `*_state.json` (watermarki pollerów → re-poll od zera), cache MSAL (→ ponowny device-code priming), `tokens.json` (→ brak auth do MCP). Cały stan operacyjny przepada przy podmianie obrazu. |
| `workmate-worklogi-out` | `/var/lib/workmate-out` | imienne arkusze WorklogPRO (PII), `hours.json`, katalog `summaries` — znikają lub puchną w warstwie kontenera. |
| bind `../../data:ro` | `/app/data` | baza wiedzy (notatki + `registry.yaml`); RO, zarządzana `git pull`. Bez montażu serwer MCP czyta pusty katalog. |

### Zmienne środowiskowe — wymagane (fail-fast `validate`) / opcjonalne
**Zawsze (profil mcp, HTTP):** WYMAGANE `WORKMATE_TRANSPORT=streamable-http` (compose), `WORKMATE_TOKENS_FILE`, `WORKMATE_ALLOWED_HOSTS` (bez publicznego hosta → 421). OPCJONALNE `WORKMATE_ALLOWED_ORIGINS`, `WORKMATE_TLS_CERTFILE`/`_KEYFILE` (oba-albo-żaden, `server.py:126`; przy nginx PUSTE), `WORKMATE_LOG_LEVEL` (dot. tylko MCP, patrz R3), `WORKMATE_BIND_HOST/_PORT` (compose ustawia).

**github (bridge):** WYMAGANE `WORKMATE_GITHUB_TOKEN`, `_OWNER`, `_REPO` (`config.py:1003-1017`). OPCJONALNE `_API_BASE`, `_POLL_INTERVAL`(≥30), `_PER_PAGE`(1–100), `_WATCH_KINDS`, `_STATE`, `_SELF_LOGIN`, `_WORKLOG_*`.

**jira (bridge):** WYMAGANE `WORKMATE_JIRA_BASE_URL`, `_TOKEN`, `_WATCH_PROJECTS` (`config.py:1245-1262`); przy `_DEPLOYMENT=cloud` dodatkowo `_EMAIL` (`:1213`). OPCJONALNE `_DEPLOYMENT`(server/cloud, dom. server), `_POLL_INTERVAL`(≥30), `_PER_PAGE`, `_STATE`, `_SELF_ACCOUNT`.

**teams-graph (bridge):** WYMAGANE `WORKMATE_TEAMS_GRAPH_CLIENT_ID`, `_TENANT_ID` (`config.py:658-673`), `WORKMATE_TEAMS_GRAPH_WATCH` (puste → discovery-exit → restart-loop), `ANTHROPIC_API_KEY` (`AgentSettings.validate`). OPCJONALNE `_SCOPES`, `_POLL_INTERVAL`, `_TOP_ROOTS/_REPLIES`, `_ACTIVE_IDLE_HOURS`, limity załączników, `WORKMATE_AGENT_MODEL/_MAX_TOKENS/_THINKING`, `WORKMATE_CONV_*`, `WORKMATE_COMPACTION_*`.

**teams push (notifier github/jira, gdy włączony cel):** WYMAGANE (gdy `enabled`) `WORKMATE_TEAMS_PUSH_CLIENT_ID`, `_TENANT_ID`; `_CHAT_USER_ID` (dla chat) / `_TEAM_ID`+`_CHANNEL_ID` (dla channel) (`config.py:1377-1400`). Gdy żaden cel — drzwi są ingest-only (bez błędu).

**telegram:** WYMAGANE `WORKMATE_TELEGRAM_BOT_TOKEN` + `ANTHROPIC_API_KEY`.

**worklogi (gdy `_ENABLED=true`):** WYMAGANE `WORKMATE_WORKLOGI_OUTPUT_DIR`, `_IDENTITIES` (istniejący plik), `_TEAM_ID`, `WORKMATE_TEAMS_PUSH_CLIENT_ID`/`_TENANT_ID` (`config.py:1493-1523`); `hours_source=json` → `_HOURS_PATH`; `=shifts` → `_FALLBACK_ISSUE`+`_SUMMARY_DIR`; tryb bojowy → `_DRY_RUN=false`+`_HEADERS_CONFIRMED=true`. Zakresy liczbowe/strefa walidowane ZAWSZE (nawet przy OFF).

**Nieudokumentowane w `deploy/docker/env.example`:** to CELOWO podzbiór (env.example:4 wskazuje `../../.env.example` jako pełny katalog). W deploy-env brak m.in. pokręteł strojących: `WORKMATE_AGENT_*`, `WORKMATE_CONV_*`, `WORKMATE_COMPACTION_*`, `WORKMATE_GITHUB_WORKLOG_*`, `WORKMATE_JIRA_MAX_TRANSITION_HOPS`, bramek plikowych (`ENABLE_FILE_REPLY`/`ENABLE_USER_FILE_PUSH`/`ENABLE_USER_DOC_PUSH`), transkryptu/notatki (`ENABLE_MEETING_*`), `WORKMATE_RETRIEVAL_ENABLE_DENSE`, `WORKMATE_LOG_LEVEL` — wszystkie OPCJONALNE z domyślnymi w `config.py`.

### Bramki zapisu (A8) — domyślnie OFF; co WŁĄCZYĆ wg funkcji
- **Most ingest → powiadomienia Teams:** `WORKMATE_TEAMS_PUSH_ENABLE_CHAT=true` LUB `_ENABLE_CHANNEL=true` (inaczej github/jira są ingest-only — nic nie pcha do Teams).
- **Agent odpowiadający na kanale:** `teams-graph` z `_WATCH` (odczyt zawsze ON; poniższe zapisy OFF).
- **Auto-komentarz CI (GitHub):** `_ENABLE_WRITE=true` + `_ENABLE_CI_AUTO_COMMENT=true` + `'ci'` w `_WATCH_KINDS`.
- **Zapis/tranzycja Jira:** `_ENABLE_WRITE` (+`_WRITE_PROJECT`+`_SELF_ACCOUNT`) i/lub `_ENABLE_TRANSITION`.
- **Karty czasu bojowo:** `_ENABLED=true` + `_DRY_RUN=false` + `_HEADERS_CONFIRMED=true`.
- **/notatka z Teams:** `_ENABLE_MEETING_NOTE_WRITE` (+`_ENABLE_MEETING_TRANSCRIPT`+`_IDENTITIES`) — WYMAGA też RW-montażu `data/` (env.example:77-79 ostrzega, domyślny montaż jest RO).
- **Odpowiedź/push plikiem:** `_ENABLE_FILE_REPLY` / `_ENABLE_USER_FILE_PUSH` / `_ENABLE_USER_DOC_PUSH` (+ wymagane scope'y Graph, walidowane).
- **Zapis bazy wiedzy przez MCP HTTP:** NIEMOŻLIWY env-em (wymuszone `enable_write=False`, `server.py:108`).

## 4. Do sprawdzenia ręcznie (bez serwera i kont niedostępne)

- **Realny `docker compose build` na Linuksie** — pierwsze uruchomienie, obraz jeszcze nie budowany; etap `test` (Dockerfile:46-57) musi przejść na docelowym Pythonie/architekturze (gałąź POSIX `fcntl`).
- **Priming device-code (MSAL)** dla `teams-graph` i `teams-push` — interaktywny, jednorazowy (README §3); potem headless (`auth.py:87`).
- **Ważność refresh-tokenu po dłuższym przestoju** — MSAL rolling expiry; po zbyt długiej przerwie `AuthExpiredError` → ponowny `--login` (README troubleshooting). Behawioralne, wymaga upływu czasu.
- **Zgody admina Entra** na zakresy Graph (`Files.Read.All`/`Sites.Read.All`; szersze przy bramkach zapisu/transkryptu).
- **Realny host + certyfikat TLS** w `nginx.conf` + `certs/` (poza repo).
- **`WORKMATE_TEAMS_GRAPH_WATCH`** — pary `team:channel` zdobywane trybem discovery na serwerze.
- **Wariant Jira (server vs cloud)** i zgodność `self_account` (accountId vs login, `config.py:1223-1233`) z realną instancją; realne zachowanie przy **429 z Jiry** (A4) pod obciążeniem.
- **Reakcja na `docker stop`/reboot w trakcie zapisu stanu** (R1) — potwierdzić okno uszkodzenia `*_state.json` i crash-loop na docelowej maszynie; ustalić czy grace-period compose (10 s) + brak handlera SIGTERM daje realne ucięcia.
- **Pod-projekt `Powiadomienia_teams`** (nudge Shifts, okno per-user, watermark serwera — C1) — osobny obraz/compose, własny audyt.
- **Poprawność nagłówków WorklogPRO** (`_HEADERS_CONFIRMED`) — porównanie z realnym kreatorem importu tej instancji.
