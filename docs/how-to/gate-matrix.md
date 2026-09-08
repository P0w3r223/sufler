# How-to: macierz bramek — żeby działała funkcja X, włącz Y

WorkMate jest **read-first**: odczyt (notatki, status, zdarzenia) działa domyślnie, a każda zdolność
**mutująca lub proaktywna jest bramkowana i domyślnie OFF** (Bramka 2, [ADR 0006](../adr/0006-write-capability-gate-2.md)).
Ten dokument zbiera bramki w jedną macierz „funkcja → co włączyć": każdy wiersz zweryfikowany wobec
`src/workmate/config/` (kolumna *Egzekwuje*). Nazwy zmiennych i domyślne wartości ustawiasz w
[`deploy/docker/env.example`](../../deploy/docker/env.example) (flota) lub `.env` (dev); pełny wykaz:
[`reference/config.md`](../reference/config.md).

**Zasada wspólna dla większości bramek:** bramka `ON` bez kompletnego celu to **twardy błąd startu**
(fail-fast), a nie cicha, „włączona-ale-martwa" konfiguracja. Jeśli włączysz zapis bez projektu/konta/
zakresu, drzwi nie wstaną i powiedzą czego brakuje.

**Jeden znany wyjątek — `WORKMATE_TEAMS_GRAPH_ENABLE_TRUST_LABELS`.** `validate()` nie wiąże go
z niczym, więc `ENABLE_TRUST_LABELS=true` bez `ENABLE_NOTE_READ_AUTHZ=true` **przechodzi start**
i daje połowę [ADR 0066](../adr/0066-content-trust-classes-and-sticky-conversation-taint.md):
koperty pochodzenia wchodzą, ale rozszczepienie nadawcy na T1/T2 nie — bo liczy je ten sam
autoryzator co bramka odczytu notatek, a bez niej każdy nadawca jest nierozpoznany. Konfiguracja
jest legalna (koperty same w sobie mają sens), ale **nie jest tym, co ADR 0066 opisuje** — włączaj
obie naraz, chyba że świadomie chcesz same koperty.

---

## Macierz

| Chcę, żeby… | Włącz (wszystkie naraz) | Egzekwuje (`config/`) |
|-------------|--------------------------|--------------------------|
| **Zdarzenia mostu lądowały w Teams** (github → czat/kanał) | `WORKMATE_TEAMS_PUSH_ENABLE_CHAT=true` (+`_CHAT_USER_ID`) **LUB** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL=true` (+`_TEAM_ID`+`_CHANNEL_ID`); zawsze `_CLIENT_ID`+`_TENANT_ID` | `TeamsPushSettings.validate` |
| **Jedno zgłoszenie = jeden wątek na kanale** | `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` (wymaga `_ENABLE_CHANNEL=true`) | `TeamsPushSettings.validate` |
| **Agent odpowiadał na kanale Teams** | profil `bridge` z `teams-graph`; `WORKMATE_TEAMS_GRAPH_WATCH` = pary `team:channel`; `_CLIENT_ID`/`_TENANT_ID`; `ANTHROPIC_API_KEY` | `TeamsGraphSettings.validate` (odczyt zawsze ON) |
| **Auto-komentarz CI na GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` + `WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true` + `ci` w `WORKMATE_GITHUB_WATCH_KINDS` (poller i zapis dzielą ten sam PAT — warunek echa, ADR 0071 decyzja 6) | `GithubSettings.validate` |
| **Agent tworzył issue/komentarze GitHub** | `WORKMATE_GITHUB_ENABLE_WRITE=true` na drzwiach `teams-graph` (wspólny `events.db`) | `_build_bridge_catalog` (teams_graph) |
| **Agent odpowiadał na GitHub Z WĄTKU Teams** (`Activity(action='comment')` bez podawania numeru) | jak wyżej **oraz** `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` na drzwiach `github` (to notifier zapełnia mapę wątków) | `ThreadLinkStore` + dispatcher `Activity` |
| **Agent/komenda pokazywały "moje zadania" z Jiry** | `WORKMATE_JIRA_BASE_URL` + `_TOKEN` (+ Cloud: `_EMAIL`); tożsamość: mapa `WORKMATE_TEAMS_GRAPH_IDENTITIES` (Teams, pole `jira_user`) albo `WORKMATE_JIRA_MY_ACCOUNT` (serwer MCP stdio). **Bez bramki** — czysty odczyt, zawężony server-side do jednego konta (ADR 0054). Pole `jira_user` jest opcjonalne ([ADR 0070](../adr/0070-teams-only-identity-and-what-a-map-entry-grants.md)): jego brak zdejmuje CAŁE narzędzie `Jira` tej osobie, przy zachowanym członkostwie w pozostałych wierszach tej tabeli | `_build_my_jira_tasks_factory` / `_my_jira_tasks_service_if_present` |
| **Komenda `/notatka` z Teams (zapis notatki ze spotkania)** | `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true` + `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true` + `WORKMATE_TEAMS_GRAPH_IDENTITIES` (istniejący plik) + zakresy transkryptu w `_SCOPES` + **RW-montaż `data/`** | `TeamsGraphSettings.validate` |
| **Odczyt notatek tylko dla rozpoznanych nadawców (fail-closed)** | `WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ=true` + `WORKMATE_TEAMS_GRAPH_IDENTITIES` (istniejący plik) ([ADR 0062](../adr/0062-note-read-authorization.md)) | `TeamsGraphSettings.validate` |
| **To samo na drzwiach Bot Framework** (`workmate-teams`, poza flotą) | `WORKMATE_TEAMS_ENABLE_NOTE_READ_AUTHZ=true` + `WORKMATE_TEAMS_IDENTITIES` (istniejący plik; ten sam format i zwykle ten sam plik co wyżej). Tożsamość z `activity.from.aadObjectId` — gość bez AAD id jest nierozpoznany | `TeamsSettings.validate` |
| **Model podawał sobie plik do wglądu (`File(action='read')`) + odkładanie załączników na dysk rozmowy** | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_TOOL=true` ([ADR 0064](../adr/0064-file-tool-and-model-initiated-materialization.md)); wyłączona gasi OBIE strony naraz | `TeamsGraphSettings.validate` |
| **Agent POPRAWIAŁ istniejącą notatkę (`File(action='edit')`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_MUTATION=true` + `_ENABLE_FILE_TOOL=true` (mutacja to akcja narzędzia `File`) + `WORKMATE_TEAMS_GRAPH_IDENTITIES` (istniejący plik) + **RW-montaż `data/`** + zapisywalny `WORKMATE_NOTE_SNAPSHOTS_DIR` poza `data/` ([ADR 0065](../adr/0065-mutable-knowledge-base-and-model-judged-writes.md)) | `TeamsGraphSettings.validate` + `Settings.persistent_paths` (`require_writable` na drzwiach) |
| **Agent USUWAŁ notatkę (`File(action='delete')`)** | jak wyżej **oraz** `WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_DELETE=true` — osobna bramka, bo ADR wiąże kasowanie z **działającą** nocną kopią wolumenu (`systemd/workmate-backup.timer` z infry); tego warunku kod nie sprawdzi | `TeamsGraphSettings.validate` (zależność od `_ENABLE_NOTE_MUTATION`) + procedura kopii |
| **Treść obca jechała w kopercie z etykietą pochodzenia (klasy zaufania T0–T3)** | `WORKMATE_TEAMS_GRAPH_ENABLE_TRUST_LABELS=true` ([ADR 0066](../adr/0066-content-trust-classes-and-sticky-conversation-taint.md)); rozszczepienie T1/T2 wymaga **dodatkowo** `_ENABLE_NOTE_READ_AUTHZ=true` — patrz wyjątek nad macierzą | `TeamsGraphSettings.validate` (samego `_TRUST_LABELS` nie waliduje) |
| **Odpowiedź plikiem w wątku (`ReplyWithFile`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true` + `Files.ReadWrite.All` w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push obrazu 1:1 (`SendImage`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH=true` + zakresy czatu (`Chat.Create`, `ChatMessage.Send`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Push dokumentu 1:1 (`SendDocument`)** | `WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH=true` + zakresy czatu + `Files.ReadWrite` (lub `Files.ReadWrite.All`) w `_SCOPES` | `TeamsGraphSettings.validate` |
| **Brief projektu na @wzmiankę** (F4, "ogarnij mnie na \<projekt\>") | `WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF=true` | `TeamsGraphSettings.validate` |
| **Digest "co się zmieniło od \<data\>" na @wzmiankę** (F5) | `WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST=true` | `TeamsGraphSettings.validate` |
| **Proaktywny cotygodniowy DM z digestem** (F6) | `WORKMATE_TEAMS_DIGEST_ENABLED=true` (+ lista odbiorców + mechanizm opt-out — WARUNEK KONIECZNY, patrz uwaga niżej); zacznij od `_DRY_RUN=true` | `TeamsDigestSettings.validate` |
| **Agent uruchamiał polecenia powłoki (`Bash`)** | `WORKMATE_ENABLE_SHELL=true` + podniesiony `exec-manager` (profil `shell`, gniazdo kontrolne `WORKMATE_EXEC_MANAGER_SOCKET`) + obraz **zawierający entrypoint `workmate-exec-manager`** (Main po §2, ADR infra 0012 — sam tag „1.6.0 lub nowszy" NIE wystarcza: powłoka na 1.6.0 nie ma menedżera wykonawców). Bramka członkostwa (ADR 0063): nie-członek pionu → pusta lista narzędzi powłoki. **Odwrotnie też**: dopisanie wiersza do `WORKMATE_TEAMS_GRAPH_IDENTITIES` wręcza tej osobie powłokę od razu, bez żadnej dodatkowej flagi ([ADR 0070](../adr/0070-teams-only-identity-and-what-a-map-entry-grants.md) §3) — mapa jest rosterem członkostwa, nie katalogiem kont Jiry. Wykonawca per rozmowa montuje TYLKO podkatalog brudnopisu (ADR 0012 znosi wymóg wzajemnego zaufania z ADR 0010) | `ShellSettings`/`ExecManagerSettings` (`config/`) + `ShellAuthorizer` (`shell_authz.py`) + `_build_shell_factory` (`agent_wiring/shell.py`) |
| **Agent widział grafik zespołu (`Schedule`)** | `WORKMATE_SCHEDULE_ENABLED` (`auto` = wchodzi, gdy grafik Shifts jest skonfigurowany; `false` chowa mimo konfiguracji, `true` wymusza) | `ScheduleSettings` (`config/schedule.py`) |
| **Model widział procedury powtarzalnej pracy** | montaż `/mnt/skills` (paczka) + `WORKMATE_SKILLS_DIR`; `WORKMATE_SKILLS_MAX_IN_HEADER` ogranicza, ile wchodzi do nagłówka sesji | `SkillsSettings` (`config/skills.py`) |
| **Skrzynka nadawcza `outputs/`** (model kładzie plik, drzwi go wysyłają) | `WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true` — **ta sama bramka co `ReplyWithFile`**; przy powłoce skrzynka pokrywa dostawę, przy jej braku zostaje narzędzie | `TeamsGraphSettings.validate` |
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

- **Strażnik pętli (self-skip).** Poller pomija zdarzenie wtedy i tylko wtedy, gdy w `events.db`
  leży **echo, które mogły zostawić wyłącznie nasze drzwi zapisu** — a nie wtedy, gdy autorem jest
  konto PAT ([ADR 0071](../adr/0071-issue-closures-and-what-self-skip-was-actually-skipping.md)
  decyzja 6, zmiana z 2026-09-07). Poprzednia reguła opierała się na przesłance „nasze konto ⇒
  nasze narzędzie", zmierzonej jako FAŁSZYWA: z ośmiu zgłoszeń założonych kontem bota tylko dwa
  powstały przez narzędzie, a pozostałych sześciu (`gh` CLI, WWW) filtr nie wpuszczał **i nie
  miały echa**, więc nie istniały nigdzie. Zmienna `WORKMATE_GITHUB_SELF_LOGIN` **została
  usunięta**: po tej zmianie nie ma wołającego, a wcześniej i tak nie robiła tego, na co
  wyglądała — pusta wartość nie wyłączała filtru, tylko kazała ustalić konto z `GET /user`.
  Zapis GitHub nadal wymaga, by poller i zapis dzieliły ten sam token
  ([ADR 0021](../adr/0021-github-write-capability-gate-4.md)) — to warunek echa, nie filtru.
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
- **Mutacja notatek zamienia odwracalność strukturalną na proceduralną.** Do ADR 0065 bazy nie dało
  się zepsuć, bo jedyny pisarz był create-only. Po włączeniu `_ENABLE_NOTE_MUTATION` odwracalność
  stoi na dwóch rzeczach spoza kodu: **migawce przed operacją** (`WORKMATE_NOTE_SNAPSHOTS_DIR` musi
  być zapisywalny i leżeć POZA `data/` — bo agent czyta katalog notatek zachłannie, więc kopie
  w środku wracałyby jako wyniki wyszukiwania; do tego dochodzi powód zależny od wdrożenia:
  w tym repo `data/` jest `:ro`, a na flocie wdrożeniowej jest RW, ale `restore-notes.sh` czyści ten
  wolumen w całości — patrz [`reference/config.md`](../reference/config.md)) i **nocnej kopii
  wolumenu**. Kasowanie ma
  osobną bramkę właśnie dlatego, że tej drugiej kod nie zweryfikuje — włączenie `_ENABLE_NOTE_DELETE`
  bez sprawdzenia kopii jest dokładnie tym, przed czym ta bramka ma chronić. Notatki ze spotkań
  i wątków (`-mtg-`/`-thr-`) zostają niezmienne — ich niezmienność jest mechanizmem idempotencji.
- **Nowe zakresy Graph = zgoda admina + reset cache.** Po dodaniu zakresu do `_SCOPES` i nadaniu go
  przez admina **usuń cache tokenu MSAL** z wolumenu, by wymusić ponowną zgodę device-code — inaczej
  token nadal nie ma zakresu i bramka jest martwa.
- **Karty czasu (WorklogPRO) wycofane z projektu (2026-07-30).** Moduł generowania i wysyłki
  cotygodniowych arkuszy usunięto z kodu w całości — decyzja trwała, nie pauza
  ([ADR 0055](../adr/0055-withdraw-worklogpro-timesheets.md), supersedes 0035/0036). `propose_worklog`
  (odczytowa estymacja z commitów, ADR 0034) zostaje bez zmian — to inna, niewycofana zdolność.
