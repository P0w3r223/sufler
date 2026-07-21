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
#     WERSJA=0.2.0 bash scripts/build-image.sh
#
# Budowanie wymaga dostępu do sieci: docker.io (python:3.11-slim + repozytoria Debiana),
# ghcr.io (uv), PyPI.
set -euo pipefail

cd "$(dirname "$0")/.."

WERSJA="${WERSJA:-0.1.0}"
OBRAZ="powiadomienia-teams:${WERSJA}"
EKSPORT="${EKSPORT:-0}"
# Serwer to linux/amd64. Przy budowaniu na miejscu platforma i tak się zgadza; zmienna ma znaczenie
# tylko wtedy, gdy budujesz gdzie indziej (np. Apple Silicon) i przenosisz obraz.
PLATFORMA="${PLATFORMA:-}"

for wymagany in Dockerfile pyproject.toml uv.lock src/powiadomienia_teams/app.py tests; do
    [[ -e "$wymagany" ]] || { echo "BŁĄD: brak $wymagany — czy jesteś w katalogu projektu?" >&2; exit 1; }
done

command -v docker >/dev/null || { echo "BŁĄD: brak dockera w PATH." >&2; exit 1; }

echo "==> Budowanie ${OBRAZ}"
echo "    Testy biegną w trakcie — obraz nie powstanie, jeśli któryś padnie."
if [[ -n "$PLATFORMA" ]]; then
    docker build --platform "$PLATFORMA" -t "$OBRAZ" .
else
    docker build -t "$OBRAZ" .
fi

echo
echo "==> Kontrola obrazu"
docker run --rm --entrypoint sh "$OBRAZ" -c '
    set -e
    python --version | grep -q "^Python 3.11" || { echo "  BŁĄD: oczekiwano Pythona 3.11, jest $(python --version 2>&1)" >&2; exit 1; }
    echo "  Python:      $(python --version 2>&1)"
    test "$(id -u)" = "10001" || { echo "  BŁĄD: proces biegnie jako $(id -u), oczekiwano 10001" >&2; exit 1; }
    echo "  Użytkownik:  $(id -u):$(id -g) (nie root)"
    python -c "import anthropic, msal, httpx, yaml, dotenv; print(\"  Zależności:  anthropic\", anthropic.__version__, \"| msal\", msal.__version__, \"| httpx\", httpx.__version__)"
    python -c "import sys; assert sys.stdout.encoding.lower().replace(\"-\",\"\") == \"utf8\", sys.stdout.encoding; print(\"  Kodowanie:  \", sys.stdout.encoding)"
    python -c "from zoneinfo import ZoneInfo; ZoneInfo(\"Europe/Warsaw\"); print(\"  Strefa:      Europe/Warsaw OK\")"
    python -c "print(\"  Emoji/PL:    ✅🟢 Cząstkiewicz Ukryty\")"
    test -d /var/lib/powiadomienia-teams && echo "  Katalog stanu: OK"
    # Narzędzia budowania NIE mogą zostać w obrazie docelowym.
    command -v uv >/dev/null && { echo "  BŁĄD: uv został w obrazie docelowym" >&2; exit 1; } || echo "  Bez uv:      OK (tylko warstwy pośrednie)"
'
# Bez konfiguracji `--help` musi zadziałać — dowód, że entrypoint i konsola są sprawne.
docker run --rm "$OBRAZ" --help > /dev/null && echo "  Entrypoint:  OK"
# Brak wymaganych ustawień MUSI dać czytelny błąd konfiguracji, a nie ślepy stacktrace.
if docker run --rm "$OBRAZ" --once 2>&1 | grep -q "Brak wymaganych ustawień"; then
    echo "  Walidacja konfiguracji: OK (fail-fast bez zmiennych)"
else
    echo "  UWAGA: brak oczekiwanego błędu walidacji przy pustej konfiguracji" >&2
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
