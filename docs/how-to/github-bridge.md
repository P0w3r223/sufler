# How-to: uruchomić most GitHub ↔ Teams

Drzwi `workmate-github` odpytują repozytorium GitHub tokenem PAT, zapisują zdarzenia do wspólnego
`EventStore`, a (opcjonalnie) w tym samym procesie wypychają je do Teams i pozwalają odpowiadać
z Teams z powrotem na GitHub. Decyzje: [ADR 0019](../adr/0019-shared-event-store.md)–[0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md).
Pełny wykaz zmiennych: [`reference/config.md`](../reference/config.md).

## Wymagania

- **PAT GitHub** — klasyczny token, scope `repo`, konto z dostępem do repo docelowego. **Sekret** —
  trzymaj w `.env` (gitignorowany), nigdy w repo.
- **Extras**: `uv sync --extra github` (sam ingest) lub `--extra github --extra teams-graph` (z push do Teams).
- Do push i zapisu z Teams: aplikacja Entra (public client, device-code) — jak w [`teams-graph.md`](teams-graph.md).

## Krok 1 — ingest zdarzeń (GitHub → EventStore)

`.env` w korzeniu repo:

```bash
WORKMATE_GITHUB_TOKEN=<PAT>
WORKMATE_GITHUB_OWNER=<organizacja>
WORKMATE_GITHUB_REPO=<repo>
```

Opcjonalnie w powłoce (nie-sekrety):

```powershell
$env:WORKMATE_GITHUB_WATCH_KINDS   = "issues,comments,pulls,reviews,ci"   # domyślnie: issues,comments
$env:WORKMATE_GITHUB_POLL_INTERVAL = "30"                                  # podłoga 30 s (limit API)
uv run workmate-github
```

Oczekiwane: poller wystartuje i przy każdej rundzie zapisze nowe zdarzenia do
`~/.workmate/events.db`. Zdarzenia można podejrzeć z dowolnych drzwi agenta akcją `Activity(action='events')`
(do 1.6.0 było to osobne narzędzie `read_recent_events`). **Strażnik pętli**: pomijane są zdarzenia, dla których w magazynie leży echo NASZYCH drzwi
zapisu (ADR 0071 decyzja 6); zdarzenia autorstwa konta PAT powstałe poza narzędziem — `gh` CLI,
WWW — przepływają normalnie, tak jak CI, które autora-człowieka nie ma.

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
uv run workmate-github
```

Pierwsze uruchomienie może wymagać jednorazowego logowania device-code (cache MSAL zapisze się w
`~/.workmate/teams_token_cache.bin`). Oczekiwane: nowe issue/PR/CI/recenzja pojawiają się na kanale
(treść zescapowana, bez żywych linków — anty-phishing).

## Krok 3 — wątkowanie kanału (jeden wątek na issue/PR)

```powershell
$env:WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING = "true"
```

Zdarzenia tego samego issue/PR dokładają się wtedy do jednego wątku (mapa `ThreadLinkStore` w
`events.db`). To **warunek konieczny** odpowiadania z wątku po stronie Teams (krok 5).

## Krok 4 — zapis zwrotny z Teams (Teams → GitHub, Bramka 4)

Zapis włącza się **na drzwiach Teams** (`workmate-teams-graph`), nie tutaj — patrz [`teams-graph.md`](teams-graph.md).
Ustaw tam `WORKMATE_GITHUB_ENABLE_WRITE=true`; agent dostanie akcje `Activity(action='create_issue')`
i `Activity(action='comment')` (create-only, owner/repo z konfiguracji). Echo zapisu idzie do `EventStore`
jako `source="teams"`, więc notifier go nie odsyła (drugi strażnik pętli).

## Krok 5 — odpowiedź w wątku na właściwe issue/PR

Działa **tylko**, gdy: drzwi `workmate-github` biegną z `ENABLE_CHANNEL_THREADING=true` (krok 3),
drzwi Teams mają `ENABLE_GITHUB_WRITE=true`, oba na **wspólnym `events.db`** i **tej samej parze
team/channel** — to notifier zapełnia mapę wątków. Wtedy odpowiedź w wątku danego issue/PR trafia
komentarzem na właściwy numer (pobrany z zaufanej mapy, nie od modelu).

## Krok 6 (opcjonalnie) — auto-komentarz CI

Jedyna autonomiczna (bez potwierdzenia) ścieżka zapisu — deterministyczny komentarz przy porażce CI:

```powershell
$env:WORKMATE_GITHUB_ENABLE_WRITE           = "true"
$env:WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT = "true"   # wymaga też 'ci' w WATCH_KINDS
```

Warunki wstępne ([ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)):
brak workflowów wyzwalanych komentarzem, zaufane nazwy workflowów, **jedna** instancja procesu.

## Uwagi eksploatacyjne

- **Jedna instancja** drzwi GitHub (kursory notifiera/CI i idempotencja auto-komentarza zakładają
  jednego pisarza).
- `POLL_INTERVAL ≥ 30 s` (budżet 5000 żądań/h).
- Stan operacyjny (`~/.workmate/events.db`, `github_state.json`, cache tokenu) trzymaj **poza repo**;
  na serwerze wskaż trwałą, wspólną ścieżkę `WORKMATE_EVENTS_DB`.
- Weryfikacja end-to-end bez zgadywania: [`live-smoke-checklist.md`](live-smoke-checklist.md) (poz. #8–#10).
