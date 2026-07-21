# How-to: uruchomić most Jira ↔ Teams

Drzwi `workmate-jira` odpytują instancję Jira Server/Data Center tokenem PAT (REST v2), zapisują
zdarzenia do wspólnego `EventStore`, a (opcjonalnie) w tym samym procesie wypychają je do Teams;
z drzwi agenta (`teams_graph`) można też — za osobnymi bramkami — tworzyć zgłoszenia/komentarze
i przesuwać status. Bliźniak mostu GitHub. Decyzje: [ADR 0030](../adr/0030-jira-server-read-door.md)
(read), [ADR 0031](../adr/0031-jira-write-capability-gate-5.md) (zapis Gate 5),
[ADR 0032](../adr/0032-jira-status-transition-capability.md) (tranzycja),
[ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md) (wątkowanie
kanału). Pełny wykaz zmiennych: [`reference/config.md`](../reference/config.md).


> **Propozycja czasu z commitów** (ADR 0034) ma własną instrukcję: [`worklog-from-commits.md`](worklog-from-commits.md). Nie dotyczy już Jiry: ścieżkę zapisu worklogu usunięto (2026-07-21), został sam odczyt commitów po stronie GitHuba. Godziny wprowadza człowiek arkuszem WorklogPRO — [`worklogi-weekly.md`](worklogi-weekly.md).

## Wymagania

- **PAT Jira** — token osobisty (Bearer), konto z dostępem do projektów docelowych. **Sekret** —
  trzymaj w `.env` (gitignorowany), nigdy w repo.
- **Extras**: `uv sync --extra jira` (sam ingest) lub `--extra jira --extra teams-graph` (z push do Teams).
- Do push i zapisu z Teams: aplikacja Entra (public client, device-code) — jak w [`teams-graph.md`](teams-graph.md).
- **Rejestr projektów**: żeby zdarzenia miały atrybucję do projektu WorkMate, wpisz `jira_project_key`
  na projekcie w `data/projects/registry.yaml` (ADR 0028).

## Wariant wdrożenia: Server/DC vs Cloud (ADR 0033)

`WORKMATE_JIRA_DEPLOYMENT` wybiera wariant (domyślnie `server` — wstecznie zgodne):

| | `server` (Server/Data Center) | `cloud` (Jira Cloud) |
|---|---|---|
| Auth | PAT **Bearer** (`WORKMATE_JIRA_TOKEN`) | **Basic** `email:api_token` (`WORKMATE_JIRA_EMAIL` + `WORKMATE_JIRA_TOKEN`) |
| REST | v2 | v3 (treść jako **ADF**, spłaszczana do tekstu na wejściu) |
| Wyszukiwanie | `/search` (`startAt`) | `/search/jql` (kursor; changelog/komentarze inline ucięte do 20) |
| URL | własny host `https://jira.firma.pl` | `https://<site>.atlassian.net` |
| `SELF_ACCOUNT` | login/klucz PAT | `accountId` (z `/rest/api/3/myself`) |

**Cloud — jak zdobyć sekrety:** API token utwórz w `id.atlassian.com` → Security → **Create API token**
(widoczny raz). `accountId` (do `SELF_ACCOUNT`) odczytasz z `GET /rest/api/3/myself` lub z URL-a profilu.
Cała reszta procedury (push do Teams, wątkowanie, zapis, tranzycja) jest identyczna dla obu wariantów —
provider jest niewidoczny powyżej adaptera.

> **Uwaga strefowa (Cloud):** JQL bez strefy interpretuje daty w strefie **konta usługowego**, nie
> instancji. Ustaw strefę konta PAT/API tak, by pasowała do hosta pollera (albo licz się z drobnym
> przesunięciem okna — dedup i kliencki filtr świeżości to domykają).

## Krok 1 — ingest zdarzeń (Jira → EventStore)

`.env` w korzeniu repo (przykład Server/DC; dla Cloud dodaj `WORKMATE_JIRA_DEPLOYMENT=cloud` +
`WORKMATE_JIRA_EMAIL`, a URL wskaż na `*.atlassian.net`):

```bash
WORKMATE_JIRA_DEPLOYMENT=server
WORKMATE_JIRA_BASE_URL=https://jira.firma.pl
WORKMATE_JIRA_TOKEN=<PAT lub API token>
# WORKMATE_JIRA_EMAIL=me@firma.pl   # tylko Cloud
WORKMATE_JIRA_WATCH_PROJECTS=WM
```

```powershell
uv run workmate-jira
```

Oczekiwane: poller wystartuje i przy każdej rundzie zapisze nowe zdarzenia (`jira_issue_created`,
`jira_transition`, `jira_comment`) do `~/.workmate/events.db`. Podejrzyj je z dowolnych drzwi agenta
narzędziem `read_recent_events` (filtr `source="jira"`). **Strażnik pętli**: zmiany autorstwa konta
PAT są pomijane (self-skip). Watermark po polu `updated` (JQL, minutowa precyzja) chroni przed
gubieniem zdarzeń po restarcie; dedup po kluczu / id wpisu changelogu / id komentarza.

## Krok 2 — push do Teams (EventStore → kanał / czat)

Dołóż konfigurację celu Teams (nie-sekrety w powłoce; tożsamość Entra jak w `teams-graph.md`):

```powershell
$env:WORKMATE_TEAMS_PUSH_CLIENT_ID = "<client_id>"
$env:WORKMATE_TEAMS_PUSH_TENANT_ID = "<tenant_id>"
$env:WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL = "true"
$env:WORKMATE_TEAMS_PUSH_TEAM_ID    = "<team_id>"
$env:WORKMATE_TEAMS_PUSH_CHANNEL_ID = "<channel_id>"
# opcjonalnie czat 1:1:
# $env:WORKMATE_TEAMS_PUSH_ENABLE_CHAT = "true"; $env:WORKMATE_TEAMS_PUSH_CHAT_USER_ID = "<aad_user_id>"
uv run workmate-jira
```

Pierwsze uruchomienie może wymagać jednorazowego logowania device-code (cache MSAL w
`~/.workmate/teams_token_cache.bin`). Oczekiwane: nowe zgłoszenie / zmiana statusu / komentarz
pojawiają się na kanale z etykietą źródła **`[Jira]`** (treść zescapowana, bez żywego HTML).

## Krok 3 — wątkowanie kanału (jeden wątek na zgłoszenie, B2)

```powershell
$env:WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING = "true"   # wymaga ENABLE_CHANNEL=true
```

Utworzenie/tranzycja/komentarz tego samego zgłoszenia dokładają się wtedy do JEDNEGO wątku na kanale
(resolver kojarzy je po `/browse/{KEY}` w URL-u; mapa `ThreadLinkStore` w `events.db`). To ta sama
flaga i mechanizm co dla GitHuba; klucz `kind="jira"` nie koliduje z wątkami issue/PR. Wątkowanie
Jiry jest **samowystarczalne** w drzwiach `workmate-jira` (to ten notifier zapełnia mapę).

## Krok 4 — bramkowany zapis z Teams (Teams → Jira, Gate 5)

Zapis włącza się **na drzwiach Teams** (`workmate-teams-graph`), nie tutaj — patrz [`teams-graph.md`](teams-graph.md).
Ustaw tam:

```powershell
$env:WORKMATE_JIRA_ENABLE_WRITE  = "true"
$env:WORKMATE_JIRA_WRITE_PROJECT = "WM"
$env:WORKMATE_JIRA_SELF_ACCOUNT  = "<login-konta-PAT>"
```

Agent dostanie `create_jira_issue` / `comment_jira_issue` (create-only; projekt z konfiguracji, nie
z treści). Komentarz waliduje PEŁNY kształt klucza (`PROJ-123`) i zgodność projektu — klucz spoza
projektu lub path-traversal (`WM-1/../OPS-1`) jest odrzucony. Echo zapisu idzie do `EventStore` jako
`source="teams"`, więc notifier (wypycha `source="jira"`) go nie odsyła (drugi strażnik pętli).

> **Inwariant cross-proces:** drzwi `workmate-jira` (poller) i `workmate-teams-graph` (zapis) MUSZĄ
> mieć TEN SAM `WORKMATE_JIRA_TOKEN` i `WORKMATE_JIRA_SELF_ACCOUNT` na WSPÓLNYM `events.db` — inaczej
> self-skip nie zadziała i własne zapisy wrócą jako zbędne powiadomienia (walidacja fail-fast).

## Krok 5 — tranzycja statusu (Teams → Jira, ADR 0032)

NIEZALEŻNA bramka (możliwy profil „tylko-tranzycja" bez zapisu). Na drzwiach Teams:

```powershell
$env:WORKMATE_JIRA_ENABLE_TRANSITION  = "true"   # + WRITE_PROJECT + SELF_ACCOUNT jak wyżej
$env:WORKMATE_JIRA_MAX_TRANSITION_HOPS = "1"      # 1 = single-hop; podnieś dla wielo-hop
```

Agent dostanie `transition_jira_issue(issue_key, target_status)` — **best-effort walk** po workflow:
Jira pokazuje tylko tranzycje z bieżącego statusu, więc walk idzie greedy, przez stany WYMUSZONE,
ZATRZYMUJE się na rozgałęzieniu (bez zgadywania), wykrywa cykle i ma sufit hopów. **Brak rollbacku** —
narzędzie zawsze zwraca strukturalny raport (`reached`/`path`/`stop_reason`/`available_next`), nigdy
gołego błędu. Domyślny cap=1 = zachowanie single-hop (zero blast radius wielo-hopowego); wielo-hop
włączasz świadomie, podnosząc `MAX_TRANSITION_HOPS`, gdy workflow projektu jest dostatecznie liniowy.

## Uwagi eksploatacyjne

- **Jedna instancja** drzwi Jira (kursor notifiera i watermark zakładają jednego pisarza).
- `POLL_INTERVAL ≥ 30 s`.
- Stan operacyjny (`~/.workmate/events.db`, `jira_state.json`, cache tokenu) trzymaj **poza repo**;
  na serwerze wskaż trwałą, wspólną ścieżkę `WORKMATE_EVENTS_DB` (współdzieloną z drzwiami zapisu).
- Weryfikacja end-to-end bez zgadywania: [`live-smoke-checklist.md`](live-smoke-checklist.md) (poz. #11–#15).
