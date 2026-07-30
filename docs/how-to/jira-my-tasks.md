# How-to: Jira — „moje zadania" (odczyt)

Jak skonfigurować i użyć jedynej zdolności Jiry w WorkMate: `get_my_jira_tasks` — zwraca TYLKO
otwarte zadania przypisane PYTAJĄCEMU, zero parametrów (nie da się podejrzeć cudzych zadań).
Decyzja: [ADR 0054](../adr/0054-reduce-jira-to-read-only-my-tasks.md) (supersedes
[0031](../adr/0031-jira-write-capability-gate-5.md) — zapis, [0032](../adr/0032-jira-status-transition-capability.md)
— tranzycja; amends [0028](../adr/0028-project-repo-jira-mapping-and-event-dimension.md),
[0030](../adr/0030-jira-server-read-door.md)).

> **Nie ma już** pollera (`workmate-jira`), pushu zdarzeń Jira→Teams, mostu Teams↔Jira ani żadnego
> narzędzia zapisu (`create_jira_issue`/`comment_jira_issue`/`transition_jira_issue`). Jira nie
> wchodzi wzorcem `EventStore` jak GitHub — wywołanie jest synchroniczne, w ramach tury agenta.

---

## Wymagania

- **Token Jira** — PAT (Server/DC) lub API token (Cloud), konto z uprawnieniem **wyłącznie do
  odczytu/wyszukiwania zgłoszeń** (`Browse projects`). Bez zakresu tworzenia/edycji/tranzycji —
  minimalny zakres jest częścią kontraktu, nie tylko dobrą praktyką. **Sekret** — trzymaj w `.env`
  (gitignorowany), nigdy w repo.
- **Extras**: `uv sync --extra jira`.
- **Rejestr projektów** nie jest wymagany dla tej zdolności (nie ma atrybucji zdarzeń do projektu
  WorkMate — `get_my_jira_tasks` nie dotyka `EventStore`).

## Wariant wdrożenia: Server/DC vs Cloud (ADR 0033)

`WORKMATE_JIRA_DEPLOYMENT` wybiera wariant (domyślnie `server` — wstecznie zgodne):

| | `server` (Server/Data Center) | `cloud` (Jira Cloud) |
|---|---|---|
| Auth | PAT **Bearer** (`WORKMATE_JIRA_TOKEN`) | **Basic** `email:api_token` (`WORKMATE_JIRA_EMAIL` + `WORKMATE_JIRA_TOKEN`) |
| REST | v2 | v3 |
| Wyszukiwanie | `/search` | `/search/jql` |
| URL | własny host `https://jira.firma.pl` | `https://<site>.atlassian.net` |

**Cloud — jak zdobyć sekrety:** API token utwórz w `id.atlassian.com` → Security → **Create API
token** (widoczny raz).

## Konfiguracja

```bash
WORKMATE_JIRA_DEPLOYMENT=server
WORKMATE_JIRA_BASE_URL=https://jira.firma.pl
WORKMATE_JIRA_TOKEN=<PAT lub API token>
# WORKMATE_JIRA_EMAIL=me@firma.pl   # tylko Cloud
```

Pełny wykaz zmiennych: [`reference/config.md`](../reference/config.md).

---

## Powierzchnia 1 — komenda Teams `/moje-zadania` (alias `/zadania`)

Działa na drzwiach `teams-graph`, przez wspólny router komend read-only
(`adapters/inbound/commands.py`) — ten sam plik, który autoryzuje `/notatka`. Tożsamość nadawcy
(AAD id wiadomości) mapowana jest na konto Jiry przez `WORKMATE_TEAMS_GRAPH_IDENTITIES` (pole
`jira_user`, ten sam plik co autoryzacja notatek, [ADR 0042](../adr/0042-meeting-note-sender-authorization.md)):

```dotenv
WORKMATE_TEAMS_GRAPH_IDENTITIES=/opt/sufler/identities.yaml
```

```yaml
# identities.yaml
mikolaj:
  aad_user_id: 712020-...
  jira_user: mikolaj@example.org        # e-mail (Cloud) albo login/accountId (Server/DC)
```

Na kanale/w czacie:

```
/moje-zadania
```

**Fail-closed:** nadawca spoza mapy tożsamości → odmowa, zero zgadywania. Zero parametrów —
agent nie może podać `assignee` z treści wiadomości, więc nie da się poprosić o cudze zadania.

## Powierzchnia 2 — narzędzie MCP na serwerze stdio (Claude Code/CLI)

Wchodzi na katalog narzędzi **TYLKO** gdy ustawiono `WORKMATE_JIRA_MY_ACCOUNT` — jeden, z góry
skonfigurowany principal (login/`accountId`), NIE tożsamość nadawcy (na stdio nie ma pojęcia
„nadawcy" — jedna sesja, jedna osoba):

```dotenv
WORKMATE_JIRA_MY_ACCOUNT=mikolaj@example.org
```

> **Nie na współdzielony serwer HTTP.** To narzędzie jest dla lokalnych drzwi stdio jednej osoby
> (Claude Code/CLI). Na drzwiach `streamable-http` z wieloma osobami na wspólnym tokenie
> `WORKMATE_JIRA_MY_ACCOUNT` zwracałoby zadania JEDNEJ, zaszytej osoby wszystkim pytającym — dlatego
> narzędzie nie jest wystawiane na tym transporcie.

## Preflight (`deploy/jira/preflight.py`)

Read-only skrypt operatorski — zastąpił stary, poller-owy preflight (poz. 11–15 dawnej checklisty):

```powershell
uv run --no-sync python deploy/jira/preflight.py
```

Sprawdza:

1. **Auth** — połączenie i ważność tokenu (`authenticated_account` / `GET /myself`).
2. **Próbne `search_issues`** — realne zapytanie JQL o otwarte zadania, bez zapisu.
3. **Opcjonalna weryfikacja tożsamości AAD** — jeśli podano `WORKMATE_TEAMS_GRAPH_IDENTITIES`,
   sprawdza, że `jira_user` z mapy faktycznie rozwiązuje się do konta w Jirze.

Wynik: `0` = auth + odczyt OK; `!= 0` = konfiguracja/auth odrzucone (szczegóły na stderr).

## Weryfikacja end-to-end

Bez zgadywania: [`live-smoke-checklist.md`](live-smoke-checklist.md) — preflight (skrypt wyżej) +
realne pytanie o „moje zadania" (Teams i/lub MCP stdio).
