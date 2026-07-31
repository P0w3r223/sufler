# Audyt gotowości wdrożeniowej WorkMate (flota Docker/Ubuntu)

## 1. Potwierdzone i nierozstrzygalne

| ID | Status | Dowód (plik:linia) | Co się stanie na serwerze | Waga |
|----|--------|--------------------|----------------------------|------|
| C1 | NIEROZSTRZYGALNA | brak w obrazie floty — `Powiadomienia_teams/` wykluczone (`.dockerignore:37`), ma własny `Dockerfile`/`docker-compose.yml` | Nudge Shifts, watermark z czasu serwera, „jedna prośba na osobę/tydzień" i okno wygasające po dowodzie pustego odczytu należą do POD-PROJEKTU `Powiadomienia_teams` — osobny artefakt wdrożenia, nieoceniany w tym audycie. | — |
| A8 | NIEROZSTRZYGALNA | brak w repo skonsolidowanej listy bramek-do-włączenia; poszczególne bramki mają fail-fast (`config.py` `validate`) | Nie istnieje jedna lista „co MUSI być ON, by bot pełnił funkcję". Per-bramka walidacja to kompensuje (włączona-ale-martwa bramka = twardy błąd startu), ale macierz trzeba wyprowadzić z kodu — dostarczona w sekcji 3 „Bramki". | — |

## 2. Obalone

OBALONE: T1, T2, T3, T4, T5, T6, T7, O1, O2, O3, O4, O5, O6, L1, L2, L3, L4, L5, A1, A2, A3, A5, A6, A7, C2, C3, R2, R4, R1, A4, R3, R5

Skróty dowodowe najistotniejszych: **R1** (naprawiona, 2026-07-30; rozszerzona 2026-07-31) zapis atomowy (temp + `os.replace`), odczyt tolerancyjny (uszkodzony/brakujący plik → pusty stan + ostrzeżenie, re-poll idempotentny po `events.db`) — dziś we WSZYSTKICH modułach stanu na wolumenie: `github/state.py` (pierwotna naprawa), `teams_graph/state.py` + `teams_graph/auth.py` (cache MSAL) i `teams_digest/state.py` (dołączone 2026-07-31, przegląd kodu wykrył rozjazd między modułami tej samej klasy); `github/app.py:103-106`, `teams_graph/app.py:955-958`, `teams_digest/app.py:95-103` — handler `SIGTERM`/`SIGINT` kończy pętlę PO utrwaleniu rundy. **A4** (naprawiona, 2026-07-30) `adapters/outbound/jira_http.py` (`request_with_retry`): retry z honorowaniem `Retry-After` (sufit 60 s) i wykładniczym backoffem w jego braku, na 429 (każda metoda) i 503 (tylko GET); dotyczy dziś wyłącznie odczytu Jiry ("moje zadania", ADR 0054) — most/zapis, dla którego ta ochrona pierwotnie powstała, jest usunięty. **A1/A2** `teams_graph/auth.py:87` (`acquire_token_silent` z cache przed device-code) + `:68-75` (`_save_cache` na skonfigurowaną ścieżkę = wolumen) → headless po jednorazowym primingu, refresh przeżywa restart. **A3** compose wdraża TYLKO `teams-graph` (delegowany polling, brak `teams`/Bot Framework, brak publicznego endpointu); async /notatka (ADR 0043) odsyła wynik do wątku przez `graph_thread_reply` (OUTBOUND) — nie wymaga adresu osiągalnego z zewnątrz. **A5/R6** każda pętla łapie `except Exception` + `logger.exception` + kontynuuje: `github/poller.py:88-94`, `notifier.py:124-129`, `github/app.py:242-249`. **A6** wszystkie drzwi walidują na starcie: `github/app.py:43,46`, `teams_graph/app.py:67,84,86,88`. **R2** `Dockerfile:104` exec-form `ENTRYPOINT ["/usr/bin/tini","--"]` (PID 1, forwarduje sygnały). **R4** auth w nagłówkach nie w URL (github Bearer), sekrety `repr=False`, brak logowania treści DM (grep w notifier/graph/teams_graph = 0). **T4** `sqlite_events.py:65-66`, `sqlite_conversations.py:144-145`, `sqlite_thread_links.py:46-47` (WAL+busy_timeout+Lock). **O4** `python:3.11-slim` ma systemową bazę stref — `ZoneInfo("Europe/Warsaw")` zweryfikowane empirycznie w kontenerze. **L1** (ta pozycja audytu była już nieaktualna w chwili spisania — kod ma fix wcześniej niż audyt) `config.py:22-32`: `_DEFAULT_TOKENS_FILE` jest jawnie zależna od platformy (`os.name == "nt"` → `C:/ProgramData/WorkMate/tokens.json`, inaczej `/var/lib/workmate/tokens.json`) — cytowana tu linia 24 to dziś komentarz, nie przypisanie; na Linuksie ścieżka windowsowa nigdy nie jest wybierana. **R3** (ta pozycja audytu była już nieaktualna w chwili spisania) `env.py:18-27` (`configure_logging`, wspólne dla drzwi inbound) czyta `WORKMATE_LOG_LEVEL`; wołane w `github/app.py:42`, `teams/app.py:66`, `teams_digest/app.py:56`, `teams_graph/app.py:70` — cytowane w audycie `logging.basicConfig(level=logging.INFO)` na sztywno dziś nie istnieje. **R5** (naprawiona, 2026-07-30) `docker-compose.yml`: healthcheck oparty o plik pulsu (`workmate-heartbeat-check`) zdefiniowany dla pozostałych pollerów — `mcp` (socket-connect), `github`, `teams-graph`. **2026-07-30 (D1-D3):** most Jira (`jira/poller.py`, `jira/app.py`, `jira/state.py`) i moduł WorklogPRO (`worklogi/*`, `worklog_selfservice/*`) usunięte z repo w całości (ADR 0054/0055) — powyższe cytaty dotyczące ich niezawodności są historyczne, nie opisują już istniejącego kodu.

## 3. Kontrakt wdrożenia (wyłącznie z kodu)

### Procesy (usługa compose → entrypoint → profil)
| Usługa | Komenda | Profil | restart | Uwaga |
|--------|---------|--------|---------|-------|
| mcp | `workmate` (streamable-http) | mcp | unless-stopped | serwer wiedzy; save_note KONSTRUKCYJNIE OFF na HTTP (`server.py:108`) |
| nginx | `nginx:1.27-alpine` | mcp | unless-stopped | terminacja TLS + proxy SSE |
| github | `workmate-github` | bridge | unless-stopped | polling PAT → events.db (+ push Teams jeśli cel ON) |
| teams-graph | `workmate-teams-graph` | bridge | unless-stopped | agent na kanale; WYMAGA `WATCH` (inaczej discovery-exit → restart-loop); woła odczyt Jiry "moje zadania" bezpośrednio (ADR 0054), bez osobnego procesu |

### Porty wystawiane na zewnątrz
- **443** i **80** — tylko usługa `nginx` (`docker-compose.yml:78-79`).
- `mcp` ma `expose: 8000` — sieć wewnętrzna compose, NIE publikowane na host.
- Drzwi bridge — brak portów (polling wychodzący).

### Wolumeny (ścieżka → co ginie bez niej)
| Wolumen | Montaż | Bez niego ginie |
|---------|--------|-----------------|
| `workmate-state` | `/var/lib/workmate` | `events.db` (dedup → **powtórne powiadomienia i komentarze**), `conversations.db` (pamięć rozmów agenta), `*_state.json` (watermarki pollerów → re-poll od zera), cache MSAL (→ ponowny device-code priming), `tokens.json` (→ brak auth do MCP). Cały stan operacyjny przepada przy podmianie obrazu. |
| bind `../../data:ro` | `/app/data` | baza wiedzy (notatki + `registry.yaml`); RO, zarządzana `git pull`. Bez montażu serwer MCP czyta pusty katalog. |

**2026-07-30 (D2):** wolumen `workmate-worklogi-out` (imienne arkusze WorklogPRO) usunięty razem
z modułem kart czasu (ADR 0055) — nie istnieje już w `docker-compose.yml`.

### Zmienne środowiskowe — wymagane (fail-fast `validate`) / opcjonalne
**Zawsze (profil mcp, HTTP):** WYMAGANE `WORKMATE_TRANSPORT=streamable-http` (compose), `WORKMATE_TOKENS_FILE`, `WORKMATE_ALLOWED_HOSTS` (bez publicznego hosta → 421). OPCJONALNE `WORKMATE_ALLOWED_ORIGINS`, `WORKMATE_TLS_CERTFILE`/`_KEYFILE` (oba-albo-żaden, `server.py:126`; przy nginx PUSTE), `WORKMATE_LOG_LEVEL` (dot. tylko MCP, patrz R3), `WORKMATE_BIND_HOST/_PORT` (compose ustawia).

**github (bridge):** WYMAGANE `WORKMATE_GITHUB_TOKEN`, `_OWNER`, `_REPO` (`config.py:1003-1017`). OPCJONALNE `_API_BASE`, `_POLL_INTERVAL`(≥30), `_PER_PAGE`(1–100), `_WATCH_KINDS`, `_STATE`, `_SELF_LOGIN`, `_WORKLOG_*`.

**Jira "moje zadania" (ADR 0054, bez osobnych drzwi):** WYMAGANE `WORKMATE_JIRA_BASE_URL`, `_TOKEN`; przy `_DEPLOYMENT=cloud` dodatkowo `_EMAIL`. OPCJONALNE `_DEPLOYMENT` (server/cloud, dom. server), `_MY_ACCOUNT` (principal serwera MCP stdio — NIE nadaje się na współdzielony fleet HTTP). Tożsamość na Teams idzie osobno, przez mapę AAD→Jira (`WORKMATE_TEAMS_GRAPH_IDENTITIES`).

**teams-graph (bridge):** WYMAGANE `WORKMATE_TEAMS_GRAPH_CLIENT_ID`, `_TENANT_ID` (`config.py:658-673`), `WORKMATE_TEAMS_GRAPH_WATCH` (puste → discovery-exit → restart-loop), `ANTHROPIC_API_KEY` (`AgentSettings.validate`). OPCJONALNE `_SCOPES`, `_POLL_INTERVAL`, `_TOP_ROOTS/_REPLIES`, `_ACTIVE_IDLE_HOURS`, limity załączników, `WORKMATE_AGENT_MODEL/_MAX_TOKENS/_THINKING`, `WORKMATE_CONV_*`, `WORKMATE_COMPACTION_*`.

**teams push (notifier github, gdy włączony cel):** WYMAGANE (gdy `enabled`) `WORKMATE_TEAMS_PUSH_CLIENT_ID`, `_TENANT_ID`; `_CHAT_USER_ID` (dla chat) / `_TEAM_ID`+`_CHANNEL_ID` (dla channel) (`config.py:1377-1400`). Gdy żaden cel — drzwi są ingest-only (bez błędu).

**Nieudokumentowane w `deploy/docker/env.example`:** to CELOWO podzbiór (env.example:4 wskazuje `../../.env.example` jako pełny katalog). W deploy-env brak m.in. pokręteł strojących: `WORKMATE_AGENT_*` (poza `WORKMATE_AGENT_VERIFY_MEETING_NOTE`, udokumentowanym od 2026-07-30), `WORKMATE_CONV_*`, `WORKMATE_COMPACTION_*`, `WORKMATE_GITHUB_WORKLOG_*`, `WORKMATE_JIRA_MY_ACCOUNT` (stdio, celowo poza fleetem), bramek plikowych (`ENABLE_FILE_REPLY`/`ENABLE_USER_FILE_PUSH`/`ENABLE_USER_DOC_PUSH`), `WORKMATE_RETRIEVAL_ENABLE_DENSE`, `WORKMATE_LOG_LEVEL` — wszystkie OPCJONALNE z domyślnymi w `config.py`. `ENABLE_MEETING_*` i `ENABLE_THREAD_NOTE_CAPTURE` SĄ już udokumentowane w env.example (od 2026-07-30, ADR 0041/0042/0043/0048 accepted).

### Bramki zapisu (A8) — domyślnie OFF; co WŁĄCZYĆ wg funkcji
- **Most ingest → powiadomienia Teams:** `WORKMATE_TEAMS_PUSH_ENABLE_CHAT=true` LUB `_ENABLE_CHANNEL=true` (inaczej github jest ingest-only — nic nie pcha do Teams).
- **Agent odpowiadający na kanale:** `teams-graph` z `_WATCH` (odczyt zawsze ON; poniższe zapisy OFF).
- **Auto-komentarz CI (GitHub):** `_ENABLE_WRITE=true` + `_ENABLE_CI_AUTO_COMMENT=true` + `'ci'` w `_WATCH_KINDS`.
- **Jira:** brak bramki zapisu — most/zapis/tranzycja usunięte (ADR 0054). Jedyna zdolność to odczyt
  "moje zadania", bez bramki (jak inne narzędzia read-only).
- **/notatka z Teams:** `_ENABLE_MEETING_NOTE_WRITE` (+`_ENABLE_MEETING_TRANSCRIPT`+`_IDENTITIES`) — WYMAGA też RW-montażu `data/` (env.example:77-79 ostrzega, domyślny montaż jest RO). **Stan 2026-07-30:** flagi WŁĄCZONE w `deploy/docker/env` (ADR 0041/0042/0043/0048 accepted), ale flotę trzeba odpalać z override `-f docker-compose.notatka.yml` (RW tylko na `data/notes`) — sam flip flagi w `env` bez tego overrida wywali zapis w runtime na "Read-only file system".
- **Odpowiedź/push plikiem:** `_ENABLE_FILE_REPLY` / `_ENABLE_USER_FILE_PUSH` / `_ENABLE_USER_DOC_PUSH` (+ wymagane scope'y Graph, walidowane).
- **Zapis bazy wiedzy przez MCP HTTP:** NIEMOŻLIWY env-em (wymuszone `enable_write=False`, `server.py:108`).

## 4. Do sprawdzenia ręcznie (bez serwera i kont niedostępne)

- **Realny `docker compose build` na Linuksie** — pierwsze uruchomienie, obraz jeszcze nie budowany; etap `test` (Dockerfile:46-57) musi przejść na docelowym Pythonie/architekturze (gałąź POSIX `fcntl`).
- **Priming device-code (MSAL)** dla `teams-graph` i `teams-push` — interaktywny, jednorazowy (README §3); potem headless (`auth.py:87`).
- **Ważność refresh-tokenu po dłuższym przestoju** — MSAL rolling expiry; po zbyt długiej przerwie `AuthExpiredError` → ponowny `--login` (README troubleshooting). Behawioralne, wymaga upływu czasu.
- **Zgody admina Entra** na zakresy Graph (`Files.Read.All`/`Sites.Read.All`; szersze przy bramkach zapisu/transkryptu).
- **Realny host + certyfikat TLS** w `nginx.conf` + `certs/` (poza repo).
- **`WORKMATE_TEAMS_GRAPH_WATCH`** — pary `team:channel` zdobywane trybem discovery na serwerze.
- **Wariant Jira (server vs cloud)** na realnej instancji — `deploy/jira/preflight.py --account <konto>`
  (ADR 0054) przed włączeniem "moich zadań" na produkcji; realne zachowanie przy **429 z Jiry** (A4)
  pod obciążeniem.
- **Reakcja na `docker stop`/reboot w trakcie zapisu stanu** (R1) — potwierdzić okno uszkodzenia `*_state.json` i crash-loop na docelowej maszynie; ustalić czy grace-period compose (10 s) + brak handlera SIGTERM daje realne ucięcia.
- **Pod-projekt `Powiadomienia_teams`** (nudge Shifts, okno per-user, watermark serwera — C1) — osobny obraz/compose, własny audyt.
- **Priming tożsamości push (github → Teams, ADR 0022)** — dawny `worklog-selfservice --login` zniknął
  z wycofaniem WorklogPRO (ADR 0055); brak dziś jawnego trybu `--login` dla `workmate-github` (patrz
  `deploy/docker/README.md` § Lista kontrolna dnia wdrożenia).
- **Live-smoke M3** (`/notatka`, "zapisz to") — kod i bramki kompletne od 2026-07-30 (ADR 0041/0042/
  0043/0047/0048); realny transkrypt/wątek testuje się dopiero PO uruchomieniu floty na serwerze —
  zeszło z listy otwartych pozycji do opcjonalnej weryfikacji powdrożeniowej (D3).
