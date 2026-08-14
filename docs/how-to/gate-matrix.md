# How-to: macierz bramek — żeby działała funkcja X, włącz Y

WorkMate jest **read-first**: odczyt (notatki, status, zdarzenia) działa domyślnie, a każda zdolność
**mutująca lub proaktywna jest bramkowana i domyślnie OFF** (Bramka 2, [ADR 0006](../adr/0006-write-capability-gate-2.md)).
Ten dokument zbiera bramki w jedną macierz „funkcja → co włączyć": każdy wiersz zweryfikowany wobec
`src/workmate/config.py` (kolumna *Egzekwuje*). Nazwy zmiennych i domyślne wartości ustawiasz w
[`deploy/docker/env.example`](../../deploy/docker/env.example) (flota) lub `.env` (dev); pełny wykaz:
[`reference/config.md`](../reference/config.md).

**Zasada wspólna dla wszystkich bramek:** bramka `ON` bez kompletnego celu to **twardy błąd startu**
(fail-fast), a nie cicha, „włączona-ale-martwa" konfiguracja. Jeśli włączysz zapis bez projektu/konta/
zakresu, drzwi nie wstaną i powiedzą czego brakuje.

---

## Macierz

| Chcę, żeby… | Włącz (wszystkie naraz) | Egzekwuje (`config.py`) |
|-------------|--------------------------|--------------------------|
| **Zdarzenia mostu lądowały w Teams** (github → czat/kanał) | `WORKMATE_TEAMS_PUSH_ENABLE_CHAT=true` (+`_CHAT_USER_ID`) **LUB** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL=true` (+`_TEAM_ID`+`_CHANNEL_ID`); zawsze `_CLIENT_ID`+`_TENANT_ID` | `TeamsPushSettings.validate` |
| **Jedno zgłoszenie = jeden wątek na kanale** | `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` (wymaga `_ENABLE_CHANNEL=true`) | `TeamsPushSettings.validate` |
| **Agent odpowiadał na kanale Teams** | profil `bridge` z `teams-graph`; `WORKMATE_TEAMS_GRAPH_WATCH` = pary `team:channel`; `_CLIENT_ID`/`_TENANT_ID`; `ANTHROPIC_API_KEY` | `TeamsGraphSettings.validate` (odczyt zawsze ON) |
| **Auto-komentarz CI na GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` + `WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true` + `ci` w `WORKMATE_GITHUB_WATCH_KINDS` (poller i zapis dzielą ten sam PAT; `_SELF_LOGIN`) | `GithubSettings.validate` |
| **Agent tworzył issue/komentarze GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` na drzwiach `teams-graph` (wspólny `events.db`) | `_build_bridge_catalog` (teams_graph) |
| **Agent odpowiadał na GitHub Z WĄTKU Teams** (`GitHub(action='comment')` bez podawania numeru) | jak wyżej **oraz** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` na drzwiach `github` (to notifier zapełnia mapę wątków) | `ThreadLinkStore` + dispatcher `GitHub` |
| **Agent/komenda pokazywały "moje zadania" z Jiry** | `WORKMATE_JIRA_BASE_URL` + `_TOKEN` (+ Cloud: `_EMAIL`); tożsamość: mapa `WORKMATE_TEAMS_GRAPH_IDENTITIES` (Teams, pole `jira_user`) albo `WORKMATE_JIRA_MY_ACCOUNT` (serwer MCP stdio). **Bez bramki** — czysty odczyt, zawężony server-side do jednego konta (ADR 0054) | `_build_my_jira_tasks_factory` / `_my_jira_tasks_service_if_present` |
| **Komenda `/notatka` z Teams (zapis notatki ze spotkania)** | `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true` + `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true` + `WORKMATE_TEAMS_GRAPH_IDENTITIES` (istniejący plik) + zakresy transkryptu w `_SCOPES` + **RW-montaż `data/`** | `TeamsGraphSettings.validate` |
| **Odpowiedź plikiem w wątku (`reply_with_file`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true` + `Files.ReadWrite.All` w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push obrazu 1:1 (`send_image_to_user`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH=true` + zakresy czatu (`Chat.Create`, `ChatMessage.Send`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push dokumentu 1:1 (`send_document_to_user`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH=true` + zakresy czatu + `Files.ReadWrite` (lub `Files.ReadWrite.All`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Brief projektu na @wzmiankę** (F4, "ogarnij mnie na \<projekt\>") | `WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF=true` | `TeamsGraphSettings.validate` |
| **Digest "co się zmieniło od \<data\>" na @wzmiankę** (F5) | `WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST=true` | `TeamsGraphSettings.validate` |
| **Proaktywny cotygodniowy DM z digestem** (F6) | `WORKMATE_TEAMS_DIGEST_ENABLED=true` (+ lista odbiorców + mechanizm opt-out — WARUNEK KONIECZNY, patrz uwaga niżej); zacznij od `_DRY_RUN=true` | `TeamsDigestSettings.validate` |
| **Agent uruchamiał polecenia powłoki (`Bash`)** | `WORKMATE_ENABLE_SHELL=true` + podniesiony `exec-manager` (profil `shell`, gniazdo kontrolne `WORKMATE_EXEC_MANAGER_SOCKET`) + obraz **zawierający entrypoint `workmate-exec-manager`** (Main po §2, ADR infra 0012 — sam tag „1.6.0 lub nowszy" NIE wystarcza: powłoka na 1.6.0 nie ma menedżera wykonawców). Bramka członkostwa (ADR 0063): nie-członek pionu → pusta lista narzędzi powłoki. Wykonawca per rozmowa montuje TYLKO podkatalog brudnopisu (ADR 0012 znosi wymóg wzajemnego zaufania z ADR 0010) | `ShellSettings`/`ExecManagerSettings` (`config.py`) + `ShellAuthorizer` (`shell_authz.py`) + `_build_shell_factory` (`agent_wiring.py`) |
| **Agent widział grafik zespołu (`Schedule`)** | `WORKMATE_SCHEDULE_ENABLED` (`auto` = wchodzi, gdy grafik Shifts jest skonfigurowany; `false` chowa mimo konfiguracji, `true` wymusza) | `ScheduleSettings` (`config.py`) |
| **Model widział procedury powtarzalnej pracy** | montaż `/mnt/skills` (paczka) + `WORKMATE_SKILLS_DIR`; `WORKMATE_SKILLS_MAX_IN_HEADER` ogranicza, ile wchodzi do nagłówka sesji | `SkillsSettings` (`config.py`) |
| **Skrzynka nadawcza `outputs/`** (model kładzie plik, drzwi go wysyłają) | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true` — **ta sama bramka co `reply_with_file`**; przy powłoce skrzynka pokrywa dostawę, przy jej braku zostaje narzędzie | `TeamsGraphSettings.validate` |
| **Katalog roboczy agenta (workspace)** | `WORKMATE_ENABLE_WORKSPACE=true` + `WORKMATE_WORKSPACE_DIR` na wolumenie | `WorkspaceSettings.validate` |
| **Zapis notatki lokalnie (`save_note`, stdio — Claude Code/`workmate-agent`)** | `WORKMATE_ENABLE_WRITE=true` (amendment ADR 0006, 2026-07-31 — domyślnie OFF bez wyjątku, także na stdio) | `Settings` / `server.py` |

---

## Bramki, których NIE otworzysz zmienną środowiskową

- **Zapis bazy wiedzy (`save_note`) przez drzwi HTTP** — **niemożliwy z założenia** (Bramka 3,
  [ADR 0007](../adr/0007-gate-3-http-auth-deployment.md)). Punkt składania drzwi sieciowych wymusza
  `enable_write=False` niezależnie od env (`server.py`, `_build_http_server`), więc `save_note` nie
  jest tam w ogóle rejestrowane. Realna powierzchnia HTTP na flocie to **4 odczyty + kursorowy
  odczyt zdarzeń** (`read_events_since`, [ADR 0040](../adr/0040-eventstore-to-mcp-session-cursor-read.md)) —
  bez `save_note`. Zapis notatek żyje wyłącznie na zaufanych drzwiach `stdio` (`workmate-agent`,
  lokalny MCP), i tam też wymaga jawnego `WORKMATE_ENABLE_WRITE=true` — patrz wiersz wyżej.
- **Dense retrieval (osadzenia)** — flaga `WORKMATE_RETRIEVAL_ENABLE_DENSE=true` istnieje, ale extra
  `retrieval-dense` **nie jest w obrazie floty** (`Dockerfile`), więc wiring łagodnie degraduje do
  samego BM25. Wchodzi dopiero za bramką mikro-evalu (ADR 0039) — poza tą flotą. **Stan na
  2026-07-30:** korpus wciąż ~15 notatek, próg mikro-evalu NIE przechodzi — zostaje `False`.
  Warunek powrotu do rewizji: wzrost i zróżnicowanie korpusu ponad obecny rozmiar.

---

## Uwagi wiążące (dlaczego bramki mają dodatkowe warunki)

- **Strażnik pętli (self-skip).** Zapis GitHub wymaga, by poller i zapis dzieliły **ten sam
  token/konto** (`_SELF_LOGIN`): echo `source` na wspólnym `events.db` powstrzymuje
  bota przed komentowaniem własnych zdarzeń ([ADR 0021](../adr/0021-github-write-capability-gate-4.md)).
  Jira nie ma dziś żadnej zdolności mutującej ani mostu push — ta ochrona jej już nie dotyczy
  (ADR 0054 supersedes 0031/0032).
- **Cel push albo nic.** Bez `_ENABLE_CHAT`/`_ENABLE_CHANNEL` drzwi github są **ingest-only** —
  piszą do `events.db`, ale niczego nie wypychają do Teams ([ADR 0022](../adr/0022-proactive-dual-target-teams-push.md)).
- **F6 (digest proaktywny) wymaga jawnej listy odbiorców i opt-out — nie samego `ENABLED=true`.**
  Wysyłka niezamawiana do osób ma inny profil ryzyka niż odpowiedź na pytanie: zanim włączysz,
  potwierdź listę adresatów i mechanizm rezygnacji, i zacznij od `_DRY_RUN=true`
  ([ADR 0053](../adr/0053-proactive-weekly-change-digest.md)).
- **`/notatka` a montaż `data/`.** Domyślny montaż `../../data` jest **RO**; produkcyjny zapis notatki
  pisze do `data/notes/`, więc włączając bramkę zamontuj `teams-graph` z RW dostępem do notatek
  (osobny override compose) — inaczej zapis padnie w runtime na „Read-only file system"
  (patrz [`env.example`](../../deploy/docker/env.example), sekcja teams-graph;
  [ADR 0041](../adr/0041-production-m3-meeting-note-write-from-teams-door.md)).
- **Nowe zakresy Graph = zgoda admina + reset cache.** Po dodaniu zakresu do `_SCOPES` i nadaniu go
  przez admina **usuń cache tokenu MSAL** z wolumenu, by wymusić ponowną zgodę device-code — inaczej
  token nadal nie ma zakresu i bramka jest martwa.
- **Karty czasu (WorklogPRO) wycofane z projektu (2026-07-30).** Moduł generowania i wysyłki
  cotygodniowych arkuszy usunięto z kodu w całości — decyzja trwała, nie pauza
  ([ADR 0055](../adr/0055-withdraw-worklogpro-timesheets.md), supersedes 0035/0036). `propose_worklog`
  (odczytowa estymacja z commitów, ADR 0034) zostaje bez zmian — to inna, niewycofana zdolność.
