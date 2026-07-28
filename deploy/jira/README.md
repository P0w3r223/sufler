# Preflight i live-smoke drzwi Jira (poz. 11–15, ADR 0030/0033)

Narzędzie operatorskie do **live-smoke mostu Jira** — weryfikacji, których `pytest` świadomie nie
robi (cała automatyka biegnie na atrapach, bez sieci). Domyka poz. 11–15 z
[`live-smoke-checklist.md`](../../docs/how-to/live-smoke-checklist.md); pełna procedura mostu:
[`jira-bridge.md`](../../docs/how-to/jira-bridge.md).

## Zawartość

| Plik | Rola | Ruch sieciowy |
|------|------|---------------|
| `preflight.py` | Read-only preflight poz. 11: łączność + auth + zgodność `SELF_ACCOUNT` + pełny pipeline `search → select_events → atrybucja → ingest` do TYMCZASOWEGO `events.db`. | tylko **odczyt** Jiry; realny `~/.workmate/events.db` nietknięty |

## Poz. 11 — read-path (uruchamialne od zaraz)

```powershell
uv run --no-sync python deploy/jira/preflight.py
```

Wymaga w `.env`: `WORKMATE_JIRA_DEPLOYMENT`, `_BASE_URL`, `_TOKEN` (Cloud: + `_EMAIL`),
`_WATCH_PROJECTS`, `_SELF_ACCOUNT`. Preflight sprawdza:

1. **Auth** — `GET /myself`; `401 AUTHENTICATED_FAILED` = token wygasł/niewłaściwy email (Cloud:
   odśwież API token w `id.atlassian.com → Security`).
2. **Strażnik pętli** — `SELF_ACCOUNT` MUSI = realny `accountId`/login tokenu, inaczej poller i
   drzwi zapisu odsyłałyby sobie własne zdarzenia (poller robi ten sam fail-fast na starcie).
3. **Atrybucja** — mapa `Jira→WorkMate` z rejestru (`jira_project_key`, ADR 0028); pusta = zdarzenia
   bez projektu. Nasłuchiwany klucz projektu MUSI mieć wpis w `data/projects/registry.yaml`.
4. **Pipeline** — pełny round pollera do temp-db + kontrast „bez self-skip", żeby odróżnić poprawny
   self-skip (0 przyjętych, bo zmiany od konta tokenu) od gubienia zdarzeń.

> **Niepominięty ingest** (realne `jira_issue_created`/`_transition`/`_comment` w `events.db`)
> wymaga zmiany w nasłuchiwanym projekcie z **INNEGO konta Jira niż token** — konto tokenu jest
> celowo self-skipowane. To akcja operatora (drugie konto / kolega), nie kod.

## Poz. 12–15 — push do Teams i zapis z Teams (akcja operatora)

Ingest (poz. 11) jest niezależny. Push zdarzeń Jiry do Teams (poz. 12–13) oraz zapis/tranzycja
z Teams (poz. 14–15) wymagają:

1. **Konfiguracji celu** `WORKMATE_TEAMS_PUSH_*` (w nadrzędnym `.env`) — identyczność aplikacji BIAP
   już wpisana; do wyboru CEL: `CHAT_USER_ID` + `ENABLE_CHAT=true` (1:1) **albo** `CHANNEL_ID` +
   `ENABLE_CHANNEL=true` (kanał; `CHANNEL_ID` trzeba odkryć — `GET /teams/{team}/channels`).
2. **Jednorazowego device-code** (MSAL) przy pierwszym `uv run workmate-jira` z włączonym celem —
   głos bota Virtual WorkMate; cache w `~/.workmate/teams_token_cache.bin`.
3. **Realnego zdarzenia** — patrz nota o niepominiętym ingeście wyżej.

Cele domyślnie **wyłączone** (`ENABLE_CHAT/ENABLE_CHANNEL=false`) — nic nie wychodzi, dopóki
operator świadomie nie włączy jednego z nich (ochrona przed przypadkowym DM-em / postem).
Oczekiwane wyniki poszczególnych pozycji: [`live-smoke-checklist.md`](../../docs/how-to/live-smoke-checklist.md) #11–15.
