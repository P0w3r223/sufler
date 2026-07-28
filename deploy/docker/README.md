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

## 2. Budowanie obrazu

```bash
# Kontekst = korzeń repo; testy biegną W TRAKCIE budowania (obraz nie powstanie z czerwonego drzewa).
docker compose build
```

## 3. Priming logowań MSAL (device-code) — JEDNORAZOWO, przed startem drzwi

Drzwi delegowane działają jako zalogowany użytkownik. Cache tokenu trzeba założyć raz, interaktywnie —
persystuje potem na wolumenie `state`. Każda komenda wypisze URL i kod; otwórz je w przeglądarce.

```bash
# Tożsamość push (github/jira/worklogi wysyłają zdarzenia do Teams):
docker compose run --rm worklog-selfservice --login          # zakłada teams_push_token_cache.bin

# Tożsamość kanału teams-graph (odkrycie zespołów/kanałów wyzwala device-code i wypisuje id do WATCH):
docker compose run --rm -e WORKMATE_TEAMS_GRAPH_WATCH= teams-graph
```

Po zdobyciu par `team_id:channel_id` wpisz je do `WORKMATE_TEAMS_GRAPH_WATCH` w `env`.
Zmiana zakresów (SCOPES) wymaga ponownego `--login` (usuń odpowiedni `*_token_cache.bin` z wolumenu).

## 4. Magazyn tokenów HTTP (profil `mcp`)

```bash
# Wygeneruj token per osoba (surowy token przekaż bezpiecznym kanałem; magazyn trzyma tylko sha256):
docker compose run --rm mcp python deploy/http/manage_tokens.py \
    --store /var/lib/workmate/tokens.json issue --person anna.kowalska
docker compose run --rm mcp python deploy/http/manage_tokens.py \
    --store /var/lib/workmate/tokens.json verify
```

## 5. Start

```bash
docker compose up -d          # startuje usługi z profili w COMPOSE_PROFILES
docker compose ps
docker compose logs -f
```

Serwer MCP odpowiada na `https://<host>/` (nginx → mcp:8000). Klient dev używa `.mcp.json` typu `http`
z tokenem bearer ze zmiennej środowiskowej.

## Worklog self-service (na żądanie — profil `tools`)

Nie startuje automatycznie. Uruchamiaj per zgłoszenie:

```bash
docker compose run --rm worklog-selfservice \
    --submission /var/lib/workmate-out/zgloszenie.json --source-id ANNA --send
```

## Aktualizacja wersji

```bash
cd /opt/sufler && git pull            # kod + baza wiedzy
cd deploy/docker
docker compose build && docker compose up -d
```

Wolumeny (`workmate-state`, `workmate-worklogi-out`) przeżywają podmianę obrazu — stan pollerów,
pamięć rozmów i cache tokenów zostają.

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
| `worklogi` kończy się od razu | `WORKMATE_WORKLOGI_ENABLED=false` (domyślnie) | włącz świadomie po potwierdzeniu nagłówków (ADR 0035) |

## Uwaga: sekrety

`env`, `certs/` i zawartość wolumenu `state` (cache MSAL, `tokens.json`) to sekrety. Nigdy nie trafiają
do obrazu (`.dockerignore` wyklucza `deploy/**/env`, `env.*` i `deploy/docker/certs/`; żaden `COPY` ich
nie wnosi) ani do repo. Konfiguracja wchodzi wyłącznie przez `env_file` w czasie uruchomienia.
