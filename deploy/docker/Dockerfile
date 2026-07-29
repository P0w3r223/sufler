# syntax=docker/dockerfile:1.7
#
# Obraz floty WorkMate (serwer MCP + drzwi-pollery). JEDEN obraz, wiele entrypointów:
# ENTRYPOINT to `tini --`, a każda usługa docker-compose podaje własną komendę
# (`workmate`, `workmate-github`, `workmate-jira`, …). ADR 0044.
#
# Kontekst budowania = KORZEŃ repo (patrz docker-compose.yml: build.context: ../..).
# Budowanie ręczne:  docker build -f deploy/docker/Dockerfile -t workmate:1.3.0 .
# Testy biegną W TRAKCIE budowania (etap `test`) — obraz nie powstanie z czerwonego drzewa.

# Extras całej floty — jedno źródło prawdy dla wszystkich etapów.
# agent (runtime Claude), github/jira/teams-graph (drzwi mostu), worklogi (+file-reply dla
# self-service), telegram, retrieval (lematyzacja PL). Dense retrieval (ADR 0039) celowo POMINIĘTY
# — wchodzi dopiero za bramką mikro-evalu.

# ---------------------------------------------------------------------------
# 1. Zależności — z uv.lock, bez pakietów deweloperskich
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS deps

# Wersja uv przypięta CELOWO: musi być NIE STARSZA niż ta, która wygenerowała uv.lock,
# inaczej `--locked` staje się twardym błędem budowania.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Najpierw same metadane — warstwa zależności przeżyje w cache dopóki nie zmieni się lock.
# README.md musi tu wejść: pyproject deklaruje `readme = "README.md"`, więc hatchling wymaga go
# przy budowaniu projektu (drugi `uv sync` niżej, bez `--no-install-project`) — inaczej build pada
# na `OSError: Readme file does not exist: README.md`.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev \
        --extra agent --extra github --extra jira --extra teams-graph \
        --extra worklogi --extra telegram --extra file-reply --extra retrieval

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
        --extra agent --extra github --extra jira --extra teams-graph \
        --extra worklogi --extra telegram --extra file-reply --extra retrieval

# ---------------------------------------------------------------------------
# 2. Testy — bramka jakości wpięta w budowanie
# ---------------------------------------------------------------------------
FROM deps AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked \
        --extra agent --extra github --extra jira --extra teams-graph \
        --extra worklogi --extra telegram --extra file-reply --extra retrieval
COPY tests/ ./tests/
COPY eval/ ./eval/
COPY data/ ./data/
# `tests/deploy/test_manage_tokens.py` ładuje moduł z deploy/http/manage_tokens.py przy kolekcji —
# bez tego COPY pytest przerywa się na `FileNotFoundError` (etap `runtime` kopiuje go osobno, niżej).
COPY deploy/http/ ./deploy/http/
# Bieg NA TYM Pythonie i TEJ architekturze, na której obraz pojedzie — w szczególności
# potwierdza gałąź POSIX `fcntl` w single_instance.py, nieuruchamialną na maszynie deweloperskiej.
RUN uv run --no-sync pytest -q && touch /app/.tests-passed

# ---------------------------------------------------------------------------
# 3. Obraz docelowy
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# UTF-8 nie jest kosmetyką: notatki/wiadomości mają polskie znaki i emoji, a cache MSAL trzyma
# polski `name` w claimie. Bez tego locale to POSIX → ASCII i zapis logu/rotacja tokenu wywala
# UnicodeEncodeError o objawie niezwiązanym z przyczyną.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# `upgrade` dociąga łatki wydane PO obrazie bazowym — usługa chodzi miesiącami bez przebudowy.
# ca-certificates: TLS do api.anthropic.com / graph.microsoft.com / api.github.com / Jira.
# tini: poprawny PID 1 (przekazuje SIGTERM do Pythona, `docker stop` kończy pętlę czysto).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 workmate \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin workmate \
    && mkdir -p /var/lib/workmate /var/lib/workmate-out /app/data \
    && chown 10001:10001 /var/lib/workmate /var/lib/workmate-out

WORKDIR /app

# Marker z etapu `test` wymusza przejście testów PRZED zbudowaniem obrazu docelowego.
COPY --from=test /app/.tests-passed /app/.tests-passed
COPY --from=deps --chown=root:root /app/.venv /app/.venv
COPY --from=deps --chown=root:root /app/src /app/src
# Baza wiedzy NIE jest zapiekana w obrazie (notatki bywają PII, a obraz może iść do rejestru).
# Katalog /app/data powstaje PUSTY (RUN wyżej); compose montuje realne ./data z hosta (ro),
# zarządzane przez `git pull`.
# Narzędzie operacyjne wydawania tokenów HTTP — uruchamiane przez `docker compose run --rm mcp
# python deploy/http/manage_tokens.py …`. Bez tego COPY krok 4 runbooka nie miałby czego wykonać.
COPY --chown=root:root deploy/http/manage_tokens.py /app/deploy/http/manage_tokens.py

# Kod należy do root, proces biegnie jako workmate — skompromitowany proces nie podmieni własnego
# kodu. Zapisywalne są tylko wolumeny stanu (/var/lib/workmate, /var/lib/workmate-out).
USER 10001:10001

# tini jako PID 1; domyślna komenda = serwer MCP. Każda usługa compose podaje własną `command`.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["workmate"]

ARG WERSJA=1.3.0
LABEL org.opencontainers.image.title="workmate" \
      org.opencontainers.image.description="Serwer MCP pionu + drzwi mostu GitHub/Jira/Teams (flota)" \
      org.opencontainers.image.version="${WERSJA}" \
      org.opencontainers.image.source="https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate"
