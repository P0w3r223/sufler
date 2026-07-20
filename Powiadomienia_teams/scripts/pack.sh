#!/usr/bin/env bash
# Zbierz paczkę wdrożeniową do wysłania na serwer (tarball + scp).
#
# Uruchamiać z katalogu projektu (Powiadomienia_teams), np. w Git Bash na Windows:
#     bash scripts/pack.sh
#
# Produkuje ../powiadomienia-teams-<YYYYMMDD-HHMM>.tar.gz z katalogiem najwyższego poziomu
# `powiadomienia-teams/`, żeby rozpakowanie na serwerze nie zaśmiecało bieżącego katalogu.
set -euo pipefail

cd "$(dirname "$0")/.."
PROJEKT="$(pwd)"
STAMP="$(date +%Y%m%d-%H%M)"
NAZWA="powiadomienia-teams-${STAMP}"
ARCHIWUM="${PROJEKT}/../${NAZWA}.tar.gz"

# Weryfikacja przed pakowaniem — lepiej stanąć tutaj niż odkryć brak na serwerze.
for wymagany in pyproject.toml uv.lock src/powiadomienia_teams/app.py; do
    [[ -e "$wymagany" ]] || { echo "BŁĄD: brak $wymagany — czy jesteś w katalogu projektu?" >&2; exit 1; }
done

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
CEL="${STAGING}/powiadomienia-teams"
mkdir -p "$CEL"

# ZAWIERAMY:
#   Dockerfile    — obraz budowany JEST NA SERWERZE z tej paczki (wariant podstawowy)
#   .dockerignore — bez niego kontekst budowania wciągnąłby .venv i cache
#   src/          — kod
#   tests/        — WYMAGANE: etap `test` w Dockerfile uruchamia je w trakcie budowania obrazu.
#                   Bez nich `docker build` padnie. Przy okazji weryfikują gałąź POSIX `fcntl`
#                   w single_instance.py, której na Windows nie da się uruchomić.
#   uv.lock       — jedyne źródło prawdy o wersjach; `uv sync --locked` odtwarza je co do commita
#   scripts/      — lista_czlonkow.py (ONLY_USER_IDS i konto bota), build-image.sh
#   deploy/       — compose, wzorzec env, README operatora + zapasowa ścieżka systemd
for element in Dockerfile .dockerignore src tests scripts deploy \
               pyproject.toml uv.lock README.md .env.example; do
    [[ -e "$element" ]] && cp -r "$element" "$CEL/"
done

# POMIJAMY (nawet jeśli wpadły przez cp -r): środowiska, cache, sekrety, dokumentacja robocza.
#   .env / roster.yaml — WYKLUCZONE JAWNIE, nie tylko przez .gitignore: tarball nie przechodzi
#   przez git, więc poleganie na regułach ignorowania wysłałoby sekrety na serwer.
find "$CEL" \( -name '__pycache__' -o -name '.venv' -o -name '.pytest_cache' \
    -o -name '.mypy_cache' -o -name '.ruff_cache' \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$CEL" \( -name '*.pyc' -o -name '.env' -o -name 'roster.yaml' \) -delete 2>/dev/null || true

tar -czf "$ARCHIWUM" -C "$STAGING" powiadomienia-teams

echo "Paczka: $(cd "$(dirname "$ARCHIWUM")" && pwd)/$(basename "$ARCHIWUM")"
echo "Rozmiar: $(du -h "$ARCHIWUM" | cut -f1)"
echo
echo "Kontrola — .env i roster.yaml NIE mogą się pojawić poniżej:"
tar -tzf "$ARCHIWUM" | grep -E '\.env$|roster\.yaml$' && {
    echo "BŁĄD: sekret trafił do paczki!" >&2; exit 1
} || echo "  czysto (w paczce jest tylko .env.example)"
echo
echo "Wyślij na serwer:"
echo "  scp ${ARCHIWUM##*/} uzytkownik@serwer:/tmp/"
echo
echo "Na serwerze (budowanie obrazu — patrz deploy/README-docker.md):"
echo "  tar -xzf /tmp/${ARCHIWUM##*/} -C /tmp && cd /tmp/powiadomienia-teams"
echo "  bash scripts/build-image.sh"
