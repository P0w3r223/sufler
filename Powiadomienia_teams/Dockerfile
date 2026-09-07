# syntax=docker/dockerfile:1.7
#
# Obraz usługi powiadomienia-teams. Wielostopniowy: zależności i testy zostają w warstwach
# pośrednich, do finalnego obrazu trafia tylko gotowe środowisko i kod.
#
# Budowanie:  docker build -t powiadomienia-teams:0.2.1 .
# Testy biegną W TRAKCIE budowania — obraz nie powstanie, jeśli któryś padnie (patrz etap `test`).

# ---------------------------------------------------------------------------
# 1. Zależności — instalowane z uv.lock, bez pakietów deweloperskich
# ---------------------------------------------------------------------------
# Python 3.11 — ta sama wersja, na której przechodzi bramka na maszynie deweloperskiej. Skoro
# obrazu nie da się tam zbudować (brak Dockera), trzymanie identycznego interpretera usuwa jedyną
# nieprzetestowaną zmienną. Podniesienie do 3.12+ = zmiana OBU `FROM`; etap `test` to zweryfikuje.
FROM python:3.11-slim AS deps

# Wersja przypięta CELOWO. Musi być NIE STARSZA niż ta, która wygenerowała uv.lock (`revision = 3`)
# — starsze uv odmówiłoby odczytu formatu, a `--locked` zamieniłoby to w twardy błąd budowania.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Najpierw same metadane — warstwa z zależnościami przetrwa w cache dopóki nie zmieni się lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --extra agent

# `--extra agent` jest OBOWIĄZKOWE: `anthropic` jest formalnie opcjonalne, ale app.py zawsze buduje
# klienta Claude, a import jest leniwy — bez tego obraz wstałby i padł dopiero przy pierwszej
# odpowiedzi pracownika, w piątek wieczorem.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra agent

# ---------------------------------------------------------------------------
# 2. Testy — bramka jakości wpięta w budowanie
# ---------------------------------------------------------------------------
FROM deps AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --extra agent
COPY tests/ ./tests/
# Szablony konfiguracji — `test_config.py` sprawdza, czy `.env.example` i `deploy/env.example`
# nie rozjechały się z `config.py`. To weryfikacja ARTEFAKTU, nie prozy repozytorium, więc bramka
# ma je widzieć w obu środowiskach. Bez tych dwóch plików etap `test` kończył się
# `FileNotFoundError`, czyli obrazu NIE DAŁO SIĘ zbudować — a że CI buduje tylko obraz floty,
# nikt tego nie zauważył od czasu, gdy zestaw testów napisano na nowo po odzyskaniu źródeł 0.2.19.
COPY .env.example ./
COPY deploy/env.example ./deploy/env.example
# Bieg NA TYM Pythonie i NA TEJ architekturze, na której obraz pojedzie — w szczególności
# potwierdza gałąź POSIX `fcntl` w single_instance.py, nieuruchamialną na maszynie deweloperskiej.
RUN uv run --no-sync pytest -q && touch /app/.testy-przeszly

# ---------------------------------------------------------------------------
# 3. Obraz docelowy
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# UTF-8 nie jest kosmetyką: wiadomości mają emoji (✅😕🙂🟢🔵), a cache MSAL zawiera polskie znaki
# w claimie `name`. Bez tego locale to POSIX → ASCII, a zapis logu albo rotacja refresh-tokenu
# kończą się UnicodeEncodeError o objawie niezwiązanym z przyczyną.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Ścieżki stanu JAWNIE — bez tego trafiłyby do Path.home() wewnątrz kontenera i zniknęły
# przy pierwszym restarcie, a wtedy każdy przebieg wysyłałby prośby od nowa.
ENV POWIADOMIENIA_TOKEN_CACHE=/var/lib/powiadomienia-teams/teams_token_cache.bin \
    POWIADOMIENIA_STATE_PATH=/var/lib/powiadomienia-teams/powiadomienia_state.json

# `upgrade` dociąga łatki bezpieczeństwa wydane PO zbudowaniu obrazu bazowego — dla usługi, która
# ma chodzić miesiącami bez przebudowy, ta luka bywa większa niż sam wiek tagu.
# ca-certificates: TLS do graph.microsoft.com / api.anthropic.com. tini: patrz ENTRYPOINT.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 powiadomienia \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin powiadomienia \
    && mkdir -p /var/lib/powiadomienia-teams \
    && chown 10001:10001 /var/lib/powiadomienia-teams

WORKDIR /app

# Kopiujemy z etapu `deps`, ale marker z `test` wymusza przejście testów przed zbudowaniem obrazu.
COPY --from=test /app/.testy-przeszly /app/.testy-przeszly
COPY --from=deps --chown=root:root /app/.venv /app/.venv
COPY --from=deps --chown=root:root /app/src /app/src
# Skrypt DIAGNOSTYCZNY, wyłącznie odczytowy. Wchodzi do obrazu, bo inaczej jedyną drogą
# uruchomienia go na serwerze jest montowanie pliku z hosta — a wtedy operator wykonuje kod spoza
# obrazu na sesji Graph bota. `lista_czlonkow` rozwiązuje identyfikatory na ludzi (A10).
COPY --chown=root:root scripts/lista_czlonkow.py /app/scripts/lista_czlonkow.py

# Kod należy do root, proces biegnie jako powiadomienia — skompromitowany proces nie podmieni
# własnego kodu. Zapisywalny jest tylko wolumen stanu.
USER 10001:10001

# tini jako PID 1: przekazuje SIGTERM do Pythona, więc `docker stop` kończy pętlę czysto
# zamiast czekać 10 s na SIGKILL w środku obsługi odpowiedzi.
ENTRYPOINT ["/usr/bin/tini", "--", "powiadomienia-teams"]

# Bez healthchecku `docker compose ps` pokazuje „Up" także dla procesu, który stoi — czyli
# najgorszy stan dla usługi bezobsługowej, bo wygląda jak zdrowie. Próg i ścieżkę wyprowadza sam
# moduł z konfiguracji. `start-period` obejmuje pierwszą pobudkę, zanim puls w ogóle powstanie.
HEALTHCHECK --interval=5m --timeout=15s --start-period=3m --retries=3 \
    CMD ["python", "-m", "powiadomienia_teams.healthcheck"]

# Wersja przekazywana z `scripts/build-image.sh` (WERSJA=…). Bez tego etykieta obrazu zostaje na
# wartości domyślnej niezależnie od tagu — a na serwerze `docker inspect` jest jedynym sposobem,
# żeby ustalić, co właściwie działa, gdy tagi zostały nadpisane albo zdublowane. Deklaracja stoi TUŻ przed
# LABEL celowo: `ARG` unieważnia cache wszystkich kolejnych warstw, więc wyżej kosztowałaby pełne
# `apt-get update && upgrade` przy każdym podbiciu wersji.
ARG WERSJA=0.2.22

LABEL org.opencontainers.image.title="powiadomienia-teams" \
      org.opencontainers.image.description="Cotygodniowe przypomnienia o zmianach (Microsoft Shifts)" \
      org.opencontainers.image.version="${WERSJA}" \
      org.opencontainers.image.source="https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate"
