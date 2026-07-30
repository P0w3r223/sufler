# Wdrożenie floty WorkMate na Ubuntu (Docker Compose)

Kontenerowe wdrożenie serwera MCP i drzwi mostu na Ubuntu (ADR 0044). Jeden obraz, wiele
entrypointów; segmenty włączane profilami compose. Odpowiednik linuksowy dla
windowsowego szkieletu z [`../http/`](../http/README.md) (IIS + usługa Windows).

> **Stan:** artefakty gotowe do użycia. Kroki zależne od serwera (realny host, certyfikat TLS,
> zgody admina na zakresy Graph) wykonuje się na maszynie docelowej.

## Wymagania na serwerze

- Docker Engine ≥ 24 z wtyczką `docker compose` (BuildKit domyślnie włączony).
- `git` (baza wiedzy `data/` jest zarządzana przez `git pull`, nie przez obraz).
- Dostęp wychodzący HTTPS: `api.anthropic.com`, `graph.microsoft.com`, `login.microsoftonline.com`,
  `api.github.com`, host Jiry, PyPI/ghcr.io (tylko podczas budowania).

## Układ katalogu docelowego

```
/opt/sufler/                      # git clone repo
  data/                             # baza wiedzy (notatki) — montowana RO do serwera MCP
  deploy/docker/
    docker-compose.yml
    env                             # z env.example, chmod 640 — SEKRETY
    nginx.conf                      # podmień host
    certs/                          # workmate.crt + workmate.key (poza repo, chmod 600)
```

## 1. Konfiguracja

```bash
cd /opt/sufler/deploy/docker
cp env.example env && chmod 640 env      # uzupełnij sekrety i host
$EDITOR env
$EDITOR nginx.conf                        # podmień workmate.firma.pl na realny host
mkdir -p certs && chmod 700 certs         # wgraj workmate.crt + workmate.key
```

W `env` wybierz segmenty przez `COMPOSE_PROFILES`. Domyślnie startuje sam `mcp`; **`bridge` dokładaj
DOPIERO po primingu device-code** (krok 3) i ustawieniu `WORKMATE_TEAMS_GRAPH_WATCH` — inaczej
teams-graph wpadnie w restart-loop. Wszystkie ścieżki stanu CELOWO wskazują wolumen `/var/lib/workmate`
— nie zmieniaj ich bez potrzeby: dzięki temu wszystkie drzwi widzą TEN SAM `events.db` (ADR 0019).

> ⚠️ **`docker compose` NIE czyta pliku `env` sam z siebie — tylko plik dosłownie nazwany `.env`.**
> `COMPOSE_PROFILES` w `env` steruje wyborem usług (`up`/`ps`/`logs`/`run`), ale bez jawnego
> `--env-file env` przy KAŻDYM wywołaniu `docker compose` w tym katalogu profil nie aktywuje się —
> **zero usług startuje, po cichu, bez błędu** (zweryfikowane: `docker compose config --services`
> bez flagi zwraca pustkę mimo `COMPOSE_PROFILES=mcp` w `env`). Poniższe komendy dlatego zawsze
> niosą `--env-file env`; nie pomijaj tej flagi przy własnych wywołaniach.

## 2. Budowanie obrazu

```bash
# Kontekst = korzeń repo; testy biegną W TRAKCIE budowania (obraz nie powstanie z czerwonego drzewa).
docker compose --env-file env build
```

## 3. Priming logowań MSAL (device-code) — JEDNORAZOWO, przed startem drzwi

Drzwi delegowane działają jako zalogowany użytkownik. Cache tokenu trzeba założyć raz, interaktywnie —
persystuje potem na wolumenie `state`. Każda komenda wypisze URL i kod; otwórz je w przeglądarce.

```bash
# Tożsamość kanału teams-graph (odkrycie zespołów/kanałów wyzwala device-code i wypisuje id do WATCH):
docker compose --env-file env run --rm -e WORKMATE_TEAMS_GRAPH_WATCH= teams-graph
```

Po zdobyciu par `team_id:channel_id` wpisz je do `WORKMATE_TEAMS_GRAPH_WATCH` w `env`.
Zmiana zakresów (SCOPES) wymaga ponownego `--login` (usuń odpowiedni `*_token_cache.bin` z wolumenu).

> ⚠️ **Priming tożsamości push (github → Teams, ADR 0022) — DO ZWERYFIKOWANIA na serwerze.**
> `worklog-selfservice --login` (dawny sposób zakładania `teams_push_token_cache.bin`) zniknął
> razem z wycofaniem WorklogPRO (ADR 0055). `workmate-github` nie ma dziś osobnego trybu
> `--login` — device-code dla pushu uruchomi się przy PIERWSZEJ próbie wysyłki zdarzenia, nie
> przy starcie procesu. Na dzień wdrożenia: uruchom `docker compose --env-file env run --rm github`
> na pierwszym planie, poczekaj na realne zdarzenie GitHub (albo wywołaj ręcznie) i potwierdź device-code w
> logach; jeśli to za wolne/nieprzewidywalne, dodaj do `workmate-github` jawny tryb `--login`
> (mała zmiana, analogiczna do tej w `workmate-teams-digest`) — patrz lista kontrolna niżej.

## 4. Magazyn tokenów HTTP (profil `mcp`)

```bash
# Wygeneruj token per osoba (surowy token przekaż bezpiecznym kanałem; magazyn trzyma tylko sha256):
docker compose --env-file env run --rm mcp python deploy/http/manage_tokens.py \
    issue --store /var/lib/workmate/tokens.json --person anna.kowalska
docker compose --env-file env run --rm mcp python deploy/http/manage_tokens.py \
    verify --store /var/lib/workmate/tokens.json
```

## 5. Start

```bash
docker compose --env-file env up -d          # startuje usługi z profili w COMPOSE_PROFILES
docker compose --env-file env ps
docker compose --env-file env logs -f
```

Serwer MCP odpowiada na `https://<host>/` (nginx → mcp:8000). Klient dev używa `.mcp.json` typu `http`
z tokenem bearer ze zmiennej środowiskowej.

## Zapis `/notatka` z Teams (opcjonalny override RW)

Domyślnie `data/` jest montowane RO wszędzie (secure-by-default, ADR 0007) — serwer MCP na HTTP nie
wystawia zapisu KONSTRUKCYJNIE. Produkcyjny zapis `/notatka` (ADR 0041) pisze do `data/notes/`, więc
drzwi `teams-graph` potrzebują tam dostępu RW. Zapewnia go JAWNY override `docker-compose.notatka.yml`
— nakłada wąski montaż RW **tylko** na `data/notes` (rejestr projektów i reszta bazy zostają RO):

```bash
docker compose --env-file env -f docker-compose.yml -f docker-compose.notatka.yml --profile bridge up -d
```

Wymaga też w `env`: `WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true` (+ `_ENABLE_MEETING_TRANSCRIPT`,
`_IDENTITIES`). To NIE `docker-compose.override.yml` — nie ładuje się automatycznie, RW włączasz świadomie.

## Aktualizacja wersji

```bash
cd /opt/sufler && git pull            # kod + baza wiedzy
cd deploy/docker
docker compose --env-file env build && docker compose --env-file env up -d
```

Wolumen `workmate-state` przeżywa podmianę obrazu — stan pollerów, pamięć rozmów i cache tokenów
zostają.

## Kopie zapasowe

Backupuj wolumen `workmate-state` (zawiera `events.db`, `conversations.db`, watermarki i cache MSAL):

```bash
docker run --rm -v workmate-state:/s -v "$PWD":/b alpine \
    tar czf /b/workmate-state-backup.tar.gz -C /s .
```

## Rozwiązywanie problemów

| Objaw | Przyczyna | Naprawa |
|-------|-----------|---------|
| Klient MCP wisi na 1. żądaniu | buforowanie proxy | potwierdź `proxy_buffering off` w `nginx.conf` |
| `421 Misdirected Request` | Host spoza allowlisty | dodaj host do `WORKMATE_ALLOWED_HOSTS` (bez portu **i** `:*`) |
| `teams-graph` restartuje się w kółko | brak `WORKMATE_TEAMS_GRAPH_WATCH` | ustaw pary `team:channel` po primingu discovery |
| `AuthExpiredError` w logach | wygasł refresh-token | powtórz `--login` dla danej tożsamości |

## Uwaga: sekrety

`env`, `certs/` i zawartość wolumenu `state` (cache MSAL, `tokens.json`) to sekrety. Nigdy nie trafiają
do obrazu (`.dockerignore` wyklucza `deploy/**/env`, `env.*` i `deploy/docker/certs/`; żaden `COPY` ich
nie wnosi) ani do repo. Konfiguracja wchodzi wyłącznie przez `env_file` w czasie uruchomienia.

## Lista kontrolna dnia wdrożenia (nie do zweryfikowania bez serwera/kont)

To jedyny krok dzielący projekt od kryterium Fazy 1 (hostowany punkt MCP). Poniższe da się
potwierdzić DOPIERO na maszynie docelowej — zaplanuj czas operatora na każdy punkt:

- [ ] **Priming device-code MSAL dla `teams-graph`** (krok 3) — wymaga przeglądarki i konta z
  dostępem do zespołu/kanału Teams.
- [ ] **Priming tożsamości push (github → Teams, ADR 0022)** — patrz uwaga w kroku 3; potwierdź,
  że `docker compose --env-file env run --rm github` na pierwszym planie realnie zakłada
  `teams_push_token_cache.bin`, albo dodaj jawny tryb `--login` do `workmate-github` przed
  wdrożeniem (mały patch, wzorem `workmate-teams-digest`).
- [x] **`docker compose --env-file env config`** dla profili `mcp`+`bridge` — zweryfikowane lokalnie
  z prawdziwym Docker Compose (nie tylko parserem YAML): rozwiązuje się czysto, 4 usługi
  (`mcp`, `nginx`, `github`, `teams-graph`). **Odkryta i naprawiona w tej sesji pułapka:**
  `docker compose` NIE czyta pliku nazwanego `env` automatycznie (tylko `.env`) — bez
  `--env-file env` `COMPOSE_PROFILES` w `env` jest ignorowany i **żadna usługa nie startuje, po
  cichu, bez błędu**. Wszystkie komendy w tym pliku mają już tę flagę — pozostaje potwierdzić na
  docelowej wersji Docker Compose na serwerze (inna wersja silnika, ta sama YAML-owa treść).
- [ ] **Realny build obrazu na Ubuntu** — `docker compose build` z pełną suitą testów w trakcie
  budowania (house guarantee, ADR 0044); lokalnie sprawdzone tylko poza kontenerem.
- [ ] **Zachowanie przy `docker stop` w trakcie zapisu stanu** — potwierdź, że `stop_grace_period:
  45s` realnie wystarcza na dokończenie rundy pollingu i zapis watermarku (R1); przetestuj przez
  `docker compose stop teams-graph` podczas aktywnego ruchu.
- [ ] **Wariant Jira server vs cloud na realnej instancji** — `WORKMATE_JIRA_DEPLOYMENT` dobrany
  zgodnie z instancją; uruchom `deploy/jira/preflight.py --account <konto> --aad <przykładowy_aad>`
  (ADR 0054) przed włączeniem "moich zadań" na produkcji.
- [ ] **429 pod obciążeniem** — retry/backoff (`jira_http.request_with_retry`,
  `github`-analog) zweryfikowany tylko na atrapach; potwierdź zachowanie pod realnym throttlingiem.
- [ ] **Magazyn tokenów HTTP + ACL** (krok 4 + `manage_tokens.py`) — wydaj token per osoba i
  potwierdź `smoke-transport.ps1`/`curl` 401/401/200/421 z [`../http/README.md`](../http/README.md).
