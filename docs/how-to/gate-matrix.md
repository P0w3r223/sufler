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
| **Zdarzenia mostu lądowały w Teams** (github/jira → czat/kanał) | `WORKMATE_TEAMS_PUSH_ENABLE_CHAT=true` (+`_CHAT_USER_ID`) **LUB** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL=true` (+`_TEAM_ID`+`_CHANNEL_ID`); zawsze `_CLIENT_ID`+`_TENANT_ID` | `TeamsPushSettings.validate` |
| **Jedno zgłoszenie = jeden wątek na kanale** | `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` (wymaga `_ENABLE_CHANNEL=true`) | `TeamsPushSettings.validate` |
| **Agent odpowiadał na kanale Teams** | profil `bridge` z `teams-graph`; `WORKMATE_TEAMS_GRAPH_WATCH` = pary `team:channel`; `_CLIENT_ID`/`_TENANT_ID`; `ANTHROPIC_API_KEY` | `TeamsGraphSettings.validate` (odczyt zawsze ON) |
| **Auto-komentarz CI na GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` + `WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true` + `ci` w `WORKMATE_GITHUB_WATCH_KINDS` (poller i zapis dzielą ten sam PAT; `_SELF_LOGIN`) | `GithubSettings.validate` |
| **Agent tworzył issue/komentarze GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` na drzwiach `teams-graph` (wspólny `events.db`) | `_build_bridge_catalog` (teams_graph) |
| **`reply_on_thread` (odpowiedź agenta na GitHub z wątku)** | jak wyżej **oraz** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` na drzwiach `github` (to notifier zapełnia mapę wątków) | `_make_thread_tool_factory` (teams_graph) |
| **Agent tworzył zgłoszenia/komentarze Jira** | `WORKMATE_JIRA_ENABLE_WRITE=true` + `WORKMATE_JIRA_WRITE_PROJECT` + `WORKMATE_JIRA_SELF_ACCOUNT` (+ Cloud: `WORKMATE_JIRA_EMAIL`) | `JiraSettings.validate` |
| **Agent przesuwał status Jira (tranzycja)** | `WORKMATE_JIRA_ENABLE_TRANSITION=true` + `WORKMATE_JIRA_WRITE_PROJECT` + `WORKMATE_JIRA_SELF_ACCOUNT` | `JiraSettings.validate` |
| **Karty czasu szły BOJOWO** (DM z arkuszem) | `WORKMATE_WORKLOGI_ENABLED=true` + `WORKMATE_WORKLOGI_DRY_RUN=false` + `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true` (+`_OUTPUT_DIR`, `_IDENTITIES`, `_TEAM_ID`, push `_CLIENT_ID`/`_TENANT_ID`) | `WorklogiSettings.validate` |
| **Komenda `/notatka` z Teams (zapis notatki ze spotkania)** | `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true` + `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true` + `WORKMATE_TEAMS_GRAPH_IDENTITIES` (istniejący plik) + zakresy transkryptu w `_SCOPES` + **RW-montaż `data/`** | `TeamsGraphSettings.validate` |
| **Odpowiedź plikiem w wątku (`reply_with_file`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true` + `Files.ReadWrite.All` w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push obrazu 1:1 (`send_image_to_user`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH=true` + zakresy czatu (`Chat.Create`, `ChatMessage.Send`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push dokumentu 1:1 (`send_document_to_user`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH=true` + zakresy czatu + `Files.ReadWrite` (lub `Files.ReadWrite.All`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Katalog roboczy agenta (workspace)** | `WORKMATE_ENABLE_WORKSPACE=true` + `WORKMATE_WORKSPACE_DIR` na wolumenie | `WorkspaceSettings.validate` |

---

## Bramki, których NIE otworzysz zmienną środowiskową

- **Zapis bazy wiedzy (`save_note`) przez drzwi HTTP** — **niemożliwy z założenia** (Bramka 3,
  [ADR 0007](../adr/0007-gate-3-http-auth-deployment.md)). Punkt składania drzwi sieciowych wymusza
  `enable_write=False` niezależnie od env (`server.py`, `_build_http_server`), więc `save_note` nie
  jest tam w ogóle rejestrowane. Realna powierzchnia HTTP na flocie to **4 odczyty + kursorowy
  odczyt zdarzeń** (`read_events_since`, [ADR 0040](../adr/0040-eventstore-to-mcp-session-cursor-read.md)) —
  bez `save_note`. Zapis notatek żyje wyłącznie na zaufanych drzwiach `stdio` (`workmate-agent`,
  lokalny MCP z `WORKMATE_ENABLE_WRITE`).
- **Dense retrieval (osadzenia)** — flaga `WORKMATE_RETRIEVAL_ENABLE_DENSE=true` istnieje, ale extra
  `retrieval-dense` **nie jest w obrazie floty** (`Dockerfile`), więc wiring łagodnie degraduje do
  samego BM25. Wchodzi dopiero za bramką mikro-evalu (ADR 0039) — poza tą flotą.

---

## Uwagi wiążące (dlaczego bramki mają dodatkowe warunki)

- **Strażnik pętli (self-skip).** Zapis GitHub/Jira wymaga, by poller i zapis dzieliły **ten sam
  token/konto** (`_SELF_LOGIN` / `_SELF_ACCOUNT`): echo `source` na wspólnym `events.db` powstrzymuje
  bota przed komentowaniem własnych zdarzeń ([ADR 0021](../adr/0021-github-write-capability-gate-4.md)/[0031](../adr/0031-jira-write-capability-gate-5.md)).
- **Cel push albo nic.** Bez `_ENABLE_CHAT`/`_ENABLE_CHANNEL` drzwi github/jira są **ingest-only** —
  piszą do `events.db`, ale niczego nie wypychają do Teams ([ADR 0022](../adr/0022-proactive-dual-target-teams-push.md)).
- **`/notatka` a montaż `data/`.** Domyślny montaż `../../data` jest **RO**; produkcyjny zapis notatki
  pisze do `data/notes/`, więc włączając bramkę zamontuj `teams-graph` z RW dostępem do notatek
  (osobny override compose) — inaczej zapis padnie w runtime na „Read-only file system"
  (patrz [`env.example`](../../deploy/docker/env.example), sekcja teams-graph;
  [ADR 0041](../adr/0041-production-m3-meeting-note-write-from-teams-door.md)).
- **Nowe zakresy Graph = zgoda admina + reset cache.** Po dodaniu zakresu do `_SCOPES` i nadaniu go
  przez admina **usuń cache tokenu MSAL** z wolumenu, by wymusić ponowną zgodę device-code — inaczej
  token nadal nie ma zakresu i bramka jest martwa.
- **Karty czasu — nagłówki to hipoteza.** Tryb bojowy nie wystartuje bez
  `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`: nagłówki arkusza WorklogPRO pochodzą z dokumentacji
  producenta, nie z kreatora importu Twojej instancji — porównaj je najpierw
  ([ADR 0035](../adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md)).
