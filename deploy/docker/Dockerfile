# syntax=docker/dockerfile:1.7
#
# Obraz floty Sufler (serwer MCP + drzwi-pollery). JEDEN obraz, wiele entrypointów:
# ENTRYPOINT to `tini --`, a każda usługa docker-compose podaje własną komendę
# (`sufler`, `sufler-github`, `sufler-teams-graph`, …). ADR 0044.
#
# Kontekst budowania = KORZEŃ repo (patrz docker-compose.yml: build.context: ../..).
# Budowanie ręczne — OBIE flagi są częścią komendy, nie dobrą wolą:
#   docker build -f deploy/docker/Dockerfile --no-cache-filter test \
#     --build-arg REWIZJA=$(git rev-parse HEAD) -t sufler:1.16.0 .
#
# `--build-arg REWIZJA` wypełnia `org.opencontainers.image.revision`; bez niej obraz mówi
# `nieznana` i łańcuch kontener→commit jest zerwany (U10 audytu 2026-09-08).
#
# `--no-cache-filter test` WYMUSZA etap testowy. Bez niej zdanie „obraz nie powstanie z czerwonego
# drzewa" jest gwarancją, której ten plik nie ma: marker `/app/.tests-passed` jest PUSTYM plikiem,
# więc BuildKit podstawia warstwę z cache'u razem ze starą datą i etap `test` nie biegnie wcale.
# Poprzednia redakcja tego komentarza obiecywała tamtą gwarancję bezwarunkowo.

# Extras całej floty — jedno źródło prawdy dla wszystkich etapów.
# agent (runtime Claude), github/jira/teams-graph (drzwi mostu; jira = WYŁĄCZNIE odczyt "moje
# zadania", ADR 0054), file-reply (odpowiedź plikiem w wątku Teams), retrieval (lematyzacja PL).
# Dense retrieval (ADR 0039) celowo POMINIĘTY — wchodzi dopiero za bramką mikro-evalu.

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
        --extra file-reply --extra retrieval

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
        --extra agent --extra github --extra jira --extra teams-graph \
        --extra file-reply --extra retrieval

# ---------------------------------------------------------------------------
# 2. Testy — bramka jakości wpięta w budowanie
# ---------------------------------------------------------------------------
FROM deps AS test

# `seed` (pypdf/openpyxl/python-pptx) dokładany WYŁĄCZNIE tu: testy ekstrakcji Office/PDF (ADR 0016/0050)
# importują te biblioteki, ale runtime celowo ich nie niesie (pyproject: importer korpusu to narzędzie
# operatorskie, poza środowiskiem serwera/drzwi). Runtime kopiuje venv z etapu `deps`, więc zostaje szczupły.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked \
        --extra agent --extra github --extra jira --extra teams-graph \
        --extra file-reply --extra retrieval --extra seed
COPY tests/ ./tests/
COPY eval/ ./eval/
COPY data/ ./data/
# `tests/deploy/test_manage_tokens.py` ładuje moduł z deploy/http/manage_tokens.py przy kolekcji —
# bez tego COPY pytest przerywa się na `FileNotFoundError` (etap `runtime` kopiuje go osobno, niżej).
COPY deploy/http/ ./deploy/http/
# Bieg NA TYM Pythonie i TEJ architekturze, na której obraz pojedzie — w szczególności wykonuje
# gałąź POSIX `fcntl` w single_instance.py, nieuruchamialną na maszynie deweloperskiej z Windows
# (`tests/adapters/inbound/test_single_instance.py::test_posix_branch_uses_fcntl`, tam `skipif`).
# Do 2026-10-01 to zdanie było fałszem dla tego obrazu: kopia `single_instance.py` w Suflerze
# nie miała ani jednego testu, więc ta gałąź nie była tu wykonywana.
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
#
# Narzędzia workspace'u agenta (ADR 0006 paczki wdrożeniowej) — obraz przestaje być minimalny
# i jest to koszt świadomy, zapisany w tamtej decyzji:
#   bash     — powłoka narzędzia `Bash`; `python:3.11-slim` niesie tylko dash, a ten nie zna
#              konstrukcji, których model używa odruchowo (tablice, `[[ ]]`, podstawianie procesu)
#   git      — praca z repozytoriami z powłoki agenta. Komentarz mówił tu wcześniej
#              o `sufler-note`, które miało commitować każdy zapis notatki — tej komendy
#              NIE MA i nie powstanie w tym kształcie (ADR 0008 paczki wdrożeniowej:
#              dziennik w kontenerze nie widziałby zdarzeń, które ma odwracać, a `git restore`
#              wprowadziłby pierwsze w systemie nadpisanie notatki). Odwracalność bazy wiedzy
#              zapewnia kopia wolumenu po stronie operatora.
#   ripgrep  — przeszukiwanie plików; dla POLSKIEGO korpusu i tak pierwszy wybór to
#              `sufler-search` (BM25 nad lematami), bo grep gubi fleksję
#
# ``curl`` świadomie POMINIĘTY (ADR 0057). Kod od modelu biegnie w kontenerze-wykonawcy, który
# ma ``network_mode: none``, więc klient HTTP nie miałby tam dokąd pójść; w kontenerze aplikacji
# model nie ma powłoki. Narzędzie, które istnieje wyłącznie dlatego, że inna kontrola je
# unieważnia, staje się zdolnością w dniu, w którym tamta kontrola się zmieni. Gdy pobieranie
# zasobów będzie potrzebne, wraca jako typowane narzędzie z allowlistą, nie jako surowy klient.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        ca-certificates tini bash git ripgrep \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sufler \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /bin/bash sufler \
    && mkdir -p /var/lib/sufler /app/data \
    && chown 10001:10001 /var/lib/sufler \
    # Punkty montażu workspace'u (ADR 0005/0006). Powstają PUSTE i należą do roota poza
    # scratchpadem — compose montuje w nie realne wolumeny. Katalogi istnieją w obrazie, żeby
    # uruchomienie BEZ montaży dawało puste katalogi (czytelne „brak danych") zamiast błędu
    # ścieżki, a prompt opisujący te ścieżki pozostawał prawdziwy w obu wariantach.
    && mkdir -p /home/scratchpad /mnt/system /mnt/user/inputs /mnt/user/outputs /mnt/skills \
    && chown 10001:10001 /home/scratchpad /mnt/user/inputs /mnt/user/outputs \
    # Katalog gniazda wykonawcy (ADR 0057) — dzielony wolumenem między aplikacją a wykonawcą.
    # Uprawnienia pliku gniazda są JEDYNĄ kontrolą dostępu do powłoki, więc katalog należy do
    # uid procesu, a wolumen montują wyłącznie te dwie usługi.
    && mkdir -p /var/run/sufler \
    && chown 10001:10001 /var/run/sufler \
    # Notatki przyjeżdżają wolumenem, więc ich właściciel bywa inny niż uid procesu; bez tego
    # git odmawia operacji w „cudzym" drzewie (`detected dubious ownership`). Dziś nic w obrazie
    # z tego nie korzysta (ADR 0008) — zostaje jako warunek wstępny dziennika atrybucji, gdyby
    # kiedyś powstał, i dlatego, że koszt jest zerowy. Zawężone do katalogu notatek, nie `*`.
    && git config --system --add safe.directory /var/lib/sufler/notes \
    && git config --system --add safe.directory /app/data/notes

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

# Kod należy do root, proces biegnie jako sufler — skompromitowany proces nie podmieni własnego
# kodu. Zapisywalne są tylko wolumeny stanu (/var/lib/sufler, /var/lib/sufler-out).
USER 10001:10001

# tini jako PID 1; domyślna komenda = serwer MCP. Każda usługa compose podaje własną `command`.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sufler"]

ARG WERSJA=1.16.0
# Commit, z którego powstał obraz. BEZ tego jedyną kotwicą wersji obrazu była PROZA CHANGELOG-a:
# tagi gita stały na `v1.3.0` przy pakiecie 1.14.0, więc z biegnącego kontenera nie dawało się
# dojść do kodu inaczej niż przez wiarę w dokumentację (U10 audytu 2026-09-08). Budowanie bez
# `--build-arg REWIZJA=...` zostawia `nieznana` — jawnie, zamiast udawać, że łańcuch jest cały.
ARG REWIZJA=nieznana
LABEL org.opencontainers.image.title="sufler" \
      org.opencontainers.image.revision="${REWIZJA}" \
      org.opencontainers.image.description="Serwer MCP pionu + drzwi mostu GitHub/Jira/Teams (flota)" \
      org.opencontainers.image.version="${WERSJA}" \
      org.opencontainers.image.source="https://github.com/P0w3r223/sufler"
