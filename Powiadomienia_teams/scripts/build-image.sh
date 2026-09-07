#!/usr/bin/env bash
# Zbuduj obraz powiadomienia-teams i sprawdź, czy jest zdatny do użytku.
#
# Wariant podstawowy — budowanie NA SERWERZE z rozpakowanej paczki źródłowej:
#     bash scripts/build-image.sh
#
# Wariant z przenoszeniem obrazu na inną maszynę (`docker save` → scp → `docker load`):
#     EKSPORT=1 bash scripts/build-image.sh
#
# Inna wersja:
#     WERSJA=0.2.22 bash scripts/build-image.sh
#
# Budowanie wymaga dostępu do sieci: docker.io (python:3.11-slim + repozytoria Debiana),
# ghcr.io (uv), PyPI.
set -euo pipefail

cd "$(dirname "$0")/.."

WERSJA="${WERSJA:-0.2.21}"
OBRAZ="powiadomienia-teams:${WERSJA}"
EKSPORT="${EKSPORT:-0}"
# Serwer to linux/amd64. Przy budowaniu na miejscu platforma i tak się zgadza; zmienna ma znaczenie
# tylko wtedy, gdy budujesz gdzie indziej (np. Apple Silicon) i przenosisz obraz.
PLATFORMA="${PLATFORMA:-}"

for wymagany in Dockerfile pyproject.toml uv.lock src/powiadomienia_teams/app.py tests; do
    [[ -e "$wymagany" ]] || { echo "BŁĄD: brak $wymagany — czy jesteś w katalogu projektu?" >&2; exit 1; }
done

command -v docker >/dev/null || { echo "BŁĄD: brak dockera w PATH." >&2; exit 1; }

# Dockerfile używa `# syntax=` i `RUN --mount=type=cache` — jedno i drugie wymaga BuildKita.
# Domyślny od Dockera 23, ale bywa wyłączony globalnie w /etc/docker/daemon.json albo w środowisku;
# ze starym builderem `--mount` jest błędem składni, a komunikat nie wskazuje przyczyny.
export DOCKER_BUILDKIT=1

echo "==> Budowanie ${OBRAZ}"
echo "    Testy biegną w trakcie — obraz nie powstanie, jeśli któryś padnie."
if [[ -n "$PLATFORMA" ]]; then
    docker build --platform "$PLATFORMA" --build-arg "WERSJA=${WERSJA}" -t "$OBRAZ" .
else
    docker build --build-arg "WERSJA=${WERSJA}" -t "$OBRAZ" .
fi

echo
echo "==> Kontrola obrazu"
docker run --rm --entrypoint sh "$OBRAZ" -c '
    set -e
    python --version | grep -q "^Python 3.11" || { echo "  BŁĄD: oczekiwano Pythona 3.11, jest $(python --version 2>&1)" >&2; exit 1; }
    echo "  Python:      $(python --version 2>&1)"
    test "$(id -u)" = "10001" || { echo "  BŁĄD: proces biegnie jako $(id -u), oczekiwano 10001" >&2; exit 1; }
    echo "  Użytkownik:  $(id -u):$(id -g) (nie root)"
    python -c "import anthropic, msal, httpx, dotenv; print(\"  Zależności:  anthropic\", anthropic.__version__, \"| msal\", msal.__version__, \"| httpx\", httpx.__version__)"
    python -c "import sys; assert sys.stdout.encoding.lower().replace(\"-\",\"\") == \"utf8\", sys.stdout.encoding; print(\"  Kodowanie:  \", sys.stdout.encoding)"
    python -c "from zoneinfo import ZoneInfo; ZoneInfo(\"Europe/Warsaw\"); print(\"  Strefa:      Europe/Warsaw OK\")"
    # Sufit nasłuchu bierze się z KODU (nie z pliku env), więc obraz zbudowany ze starego źródła
    # cicho wróciłby do odpytywania co 5 min. Tu wychodzi to przy budowaniu, nie po tygodniu logów.
    python -c "from powiadomienia_teams.config import Settings; s = Settings(client_id=\"x\", tenant_id=\"x\", team_id=\"x\"); assert s.poll_max_interval_s == 3600, s.poll_max_interval_s; print(\"  Nasłuch:    \", s.poll_interval_s, \"s →\", s.poll_max_interval_s, \"s (sufit 1 h)\")"
    python -c "print(\"  Emoji/PL:    ✅🟢 Cząstkiewicz Ukryty\")"
    test -d /var/lib/powiadomienia-teams && echo "  Katalog stanu: OK"
    # Bez pulsu healthcheck MUSI zgłosić stan niezdrowy — czytelnym zdaniem, nie zrzutem stosu.
    # UWAGA: ten blok jest w apostrofach dla `sh -c`, więc NIE wolno tu użyć znaku apostrofu.
    if python -m powiadomienia_teams.healthcheck >/dev/null 2>&1; then
        echo "  BŁĄD: healthcheck zgłasza zdrowie mimo braku pliku pulsu" >&2; exit 1
    fi
    echo "  Healthcheck: OK (bez pulsu zgłasza niezdrowy)"
    # Narzędzia budowania NIE mogą zostać w obrazie docelowym.
    command -v uv >/dev/null && { echo "  BŁĄD: uv został w obrazie docelowym" >&2; exit 1; } || echo "  Bez uv:      OK (tylko warstwy pośrednie)"
'
# Bez konfiguracji `--help` musi zadziałać — dowód, że entrypoint i konsola są sprawne.
docker run --rm "$OBRAZ" --help > /dev/null && echo "  Entrypoint:  OK"
# Brak wymaganych ustawień MUSI dać czytelny błąd konfiguracji, a nie ślepy stacktrace.
# Wyjście przechwytujemy do zmiennej, a NIE potokujemy do `grep`: przy `set -o pipefail` kod wyjścia
# `docker run` (tutaj poprawne 1) przesłania wynik `grep` i asercja zawsze zgłaszała fałszywy alarm.
WYJSCIE_WALIDACJI="$(docker run --rm "$OBRAZ" --once 2>&1 || true)"
if ! grep -q "Brak wymaganych ustawień" <<<"$WYJSCIE_WALIDACJI"; then
    echo "  BŁĄD: pusta konfiguracja nie dała oczekiwanego komunikatu walidacji" >&2
    echo "$WYJSCIE_WALIDACJI" | tail -5 >&2
    exit 1
fi
if grep -q "Traceback" <<<"$WYJSCIE_WALIDACJI"; then
    echo "  BŁĄD: błąd konfiguracji wypisał ślad stosu zamiast czytelnego komunikatu" >&2
    exit 1
fi
echo "  Walidacja konfiguracji: OK (czytelny komunikat, bez śladu stosu)"

# HEALTHCHECK musi być wpięty w obraz, nie tylko dostępny jako moduł — inaczej `docker compose ps`
# pokazuje „Up" dla procesu, który stoi.
if [[ "$(docker inspect --format '{{if .Config.Healthcheck}}jest{{else}}brak{{end}}' "$OBRAZ")" != "jest" ]]; then
    echo "  BŁĄD: obraz nie ma wpiętego HEALTHCHECK" >&2
    exit 1
fi
echo "  Healthcheck: wpięty w obraz"

# Etykieta musi zgadzać się z tagiem — inaczej `docker inspect` na serwerze kłamie o tym, co działa.
ETYKIETA="$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$OBRAZ")"
if [[ "$ETYKIETA" == "$WERSJA" ]]; then
    echo "  Etykieta:    ${ETYKIETA} (zgodna z tagiem)"
else
    echo "  BŁĄD: etykieta obrazu to '${ETYKIETA}', a tag mówi '${WERSJA}'" >&2
    exit 1
fi

echo
echo "Obraz gotowy: ${OBRAZ}"
docker images "$OBRAZ" --format '  rozmiar: {{.Size}}, utworzony: {{.CreatedSince}}'

if [[ "$EKSPORT" == "1" ]]; then
    ARCHIWUM="../powiadomienia-teams-${WERSJA}-image.tar.gz"
    echo
    echo "==> Eksport do pliku"
    docker save "$OBRAZ" | gzip > "$ARCHIWUM"
    echo "  $(cd "$(dirname "$ARCHIWUM")" && pwd)/$(basename "$ARCHIWUM")  ($(du -h "$ARCHIWUM" | cut -f1))"
    echo
    echo "Na maszynie docelowej:"
    echo "  gunzip -c ${ARCHIWUM##*/} | docker load"
else
    echo
    echo "Dalej: deploy/README-docker.md, krok 2 (układ katalogów i konfiguracja)."
fi
