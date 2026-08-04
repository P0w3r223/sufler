"""Centralna, typowana konfiguracja serwera.

Wszystkie wartości pochodzą ze zmiennych środowiskowych (patrz ``.env.example``)
i mają sensowne wartości domyślne wyznaczane względem korzenia repozytorium.
Dzięki temu ``python -m workmate`` działa bez żadnej konfiguracji, a wdrożenie
może nadpisać ścieżki pojedynczą zmienną środowiskową.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Dozwolone transporty serwera MCP. "stdio" to lokalny tryb dla Claude Code
# (Fazy 1 tyg. 1-3); "streamable-http" to wdrożenie sieciowe (tyg. 4, Bramka 3).
_ALLOWED_TRANSPORTS = ("stdio", "streamable-http")

# Domyślny magazyn tokenów drzwi HTTP (ADR 0007): POZA repo i poza data/, żeby sekrety były poza
# zasięgiem narzędzi. Domyślna ZALEŻNA OD PLATFORMY (L1): na Windows katalog systemowy ProgramData,
# na POSIX wolumen stanu floty (/var/lib/workmate — spójne z docker-compose). Bez tego windowsowa
# ścieżka "C:/..." na Linuksie stawała się KATALOGIEM WZGLĘDNYM pod CWD (bez sensu). Nadpisywalna
# przez WORKMATE_TOKENS_FILE; przy złym/nieobecnym pliku start HTTP jest fail-fast (server.py,
# TokenVerifier.from_file).
_DEFAULT_TOKENS_FILE = (
    Path("C:/ProgramData/WorkMate/tokens.json")
    if os.name == "nt"
    else Path("/var/lib/workmate/tokens.json")
)

# Domyślna baza rozmów (SQLite, ADR 0010): POZA repo i poza data/ — to dane
# operacyjne (historia czatu), nie baza wiedzy. Katalog domowy (pisemny bez
# uprawnień administratora — drzwi lokalne). Nadpisywalna przez WORKMATE_CONVERSATIONS_DB.
_DEFAULT_CONVERSATIONS_DB = Path.home() / ".workmate" / "conversations.db"

# Domyślny wspólny magazyn zdarzeń (EventStore, ADR 0019): OSOBNY plik od conversations.db
# (tamten robi rebuild tabeli przy migracji FK), POZA repo i data/ — to dane operacyjne
# (warstwa spajająca drzwi), nie baza wiedzy. Nadpisywalny przez WORKMATE_EVENTS_DB.
_DEFAULT_EVENTS_DB = Path.home() / ".workmate" / "events.db"

# Domyślny magazyn wektorów retrievalu dense (ADR 0039, Faza B): POZA repo i data/ — dane
# operacyjne (osadzenia pochodne notatek), nie baza wiedzy. Env: WORKMATE_RETRIEVAL_INDEX.
_DEFAULT_RETRIEVAL_INDEX = Path.home() / ".workmate" / "retrieval_index.db"

# Domyślne allowed_hosts trybu HTTP: wyłącznie loopback. Wdrożenie za IIS MUSI
# dołożyć publiczny host (np. "workmate.firma.pl:*") przez WORKMATE_ALLOWED_HOSTS —
# inaczej realny nagłówek Host daje 421 (ochrona przed DNS-rebinding).
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


def _repo_root_or_none(start: Path) -> Path | None:
    """Korzeń repozytorium (katalog z ``pyproject.toml``) albo ``None``, gdy go nie ma.

    ``None`` znaczy „kod nie leży w drzewie repozytorium" — tak jest po instalacji z wheela
    albo w obrazie kontenera. Wołający, który pilnuje ścieżek WZGLĘDEM repo, nie ma wtedy
    czego pilnować i musi kontrolę pominąć, zamiast podstawiać byle katalog.
    """
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def _find_repo_root(start: Path) -> Path:
    """Korzeń repozytorium do wyznaczania ścieżek domyślnych; awaryjnie katalog roboczy.

    Pozwala uruchamiać serwer niezależnie od bieżącego katalogu roboczego
    (np. przez ``uv run`` z dowolnego miejsca), bez zaszywania ścieżek w kodzie. Degradacja
    do ``cwd`` jest tu w porządku (chodzi o miejsce na dane), ale NIE nadaje się do kontroli
    bezpieczeństwa — te używają ``_repo_root_or_none``.
    """
    return _repo_root_or_none(start) or Path.cwd()


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int_from_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} musi być liczbą całkowitą, jest: {value!r}") from exc


def _float_from_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} musi być liczbą, jest: {value!r}") from exc


def _list_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


def _optional_path_from_env(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def require_writable(path: Path, env_var: str) -> None:
    """Twardy błąd startu, gdy katalog dla TRWAŁEJ ścieżki nie przyjmie zapisu (R/L1, GAPS).

    Domyślne ścieżki stanu i baz (stałe ``_DEFAULT_*``) celują w ``~/.workmate``, a konto
    kontenera ma ``--no-create-home`` i rootfs ``read_only`` (Dockerfile/compose) — bez nadpisania
    env (``deploy/docker/env.example``) katalog jest niezapisywalny. Bez tej kontroli poller
    odkrywa to dopiero przy pierwszym zapisie, w pętli łapiącej wyjątki: cichy crash-loop bez
    utrwalonego watermarku (stan „w próżnię"). Sprawdzamy WCZEŚNIE i głośno — tworzymy katalog
    docelowy i piszemy plik próbny; ``env_var`` w komunikacie wskazuje, co nadpisać.

    Wołać na starcie drzwi (obok ``settings.validate()``), a NIE w samym ``validate`` — tam efekt
    uboczny ``mkdir`` zaśmiecałby testy konfiguracji, które wołają ``validate`` z domyślnymi
    ścieżkami. Zapis próbny jest szczery (tak samo pisze ``state.save``): łapie też rootfs
    ``read_only``, którego same bity uprawnień nie ujawniają.
    """
    target_dir = path.expanduser().parent
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / f".workmate-writetest-{os.getpid()}"
        probe.write_text("", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        raise ValueError(
            f"Trwała ścieżka {path} nie jest zapisywalna: {exc}. Ustaw {env_var} na katalog "
            "dostępny do zapisu (na flocie wolumen /var/lib/workmate — patrz "
            "deploy/docker/env.example)."
        ) from exc


@dataclass(frozen=True)
class Settings:
    """Niemutowalny zestaw ustawień serwera."""

    data_dir: Path
    notes_dir: Path
    projects_registry: Path
    transport: str
    log_level: str
    # Profil uprawnień per drzwi (Bramka 2, ADR 0006): czy narzędzie zapisu
    # (save_note) jest wystawione. Domyślnie WYŁĄCZONE na wszystkich drzwiach — bez wyjątku dla
    # lokalnego dev (amendment ADR 0006, 2026-07-31); operator włącza świadomie
    # (WORKMATE_ENABLE_WRITE=true) albo per drzwi przekazuje ``enable_write=True`` w wiringu.
    enable_write: bool
    # Ustawienia trybu HTTP (streamable-http, Bramka 3 / ADR 0007). W trybie stdio
    # nieużywane — mają bezpieczne wartości domyślne i nie wymagają niczego od
    # lokalnego deva. Uwierzytelnianie bearer per osoba wpina się dopiero w gałęzi
    # HTTP w server.py (warstwa drzwi, rdzeń nietknięty).
    tokens_file: Path
    bind_host: str
    bind_port: int
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    tls_certfile: Path | None
    tls_keyfile: Path | None
    # Metryki użycia (Tor A): ścieżka pliku SQLite licznika wywołań. ``None`` (brak
    # WORKMATE_METRICS_DB) = metryki wyłączone (drzwi nie zapisują nic). Osobny plik od
    # events.db/conversations.db — dane operacyjne poza bazą wiedzy; pseudonim zamiast tożsamości.
    metrics_db: Path | None

    @classmethod
    def from_env(cls) -> Settings:
        repo_root = _find_repo_root(Path(__file__).resolve())
        data_dir = _path_from_env("WORKMATE_DATA_DIR", repo_root / "data")
        notes_dir = _path_from_env("WORKMATE_NOTES_DIR", data_dir / "notes")
        projects_registry = _path_from_env(
            "WORKMATE_PROJECTS_REGISTRY", data_dir / "projects" / "registry.yaml"
        )

        transport = os.environ.get("WORKMATE_TRANSPORT", "stdio")
        if transport not in _ALLOWED_TRANSPORTS:
            raise ValueError(
                f"Nieobsługiwany WORKMATE_TRANSPORT: {transport!r}. "
                f"Dozwolone: {', '.join(_ALLOWED_TRANSPORTS)}"
            )

        return cls(
            data_dir=data_dir,
            notes_dir=notes_dir,
            projects_registry=projects_registry,
            transport=transport,
            log_level=os.environ.get("WORKMATE_LOG_LEVEL", "INFO"),
            enable_write=_bool_from_env("WORKMATE_ENABLE_WRITE", default=False),
            tokens_file=_path_from_env("WORKMATE_TOKENS_FILE", _DEFAULT_TOKENS_FILE),
            bind_host=os.environ.get("WORKMATE_BIND_HOST", "127.0.0.1"),
            bind_port=_int_from_env("WORKMATE_BIND_PORT", 8000),
            allowed_hosts=_list_from_env("WORKMATE_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS),
            allowed_origins=_list_from_env("WORKMATE_ALLOWED_ORIGINS", ()),
            tls_certfile=_optional_path_from_env("WORKMATE_TLS_CERTFILE"),
            tls_keyfile=_optional_path_from_env("WORKMATE_TLS_KEYFILE"),
            metrics_db=_optional_path_from_env("WORKMATE_METRICS_DB"),
        )


@dataclass(frozen=True)
class TeamsSettings:
    """Konfiguracja drzwi Teams (Faza 2) — osobny proces od serwera MCP.

    Tryb anonimowy (bez ``app_id``/``app_password``) służy lokalnemu testowi w Bot
    Framework Emulator bez Azure. Tryb uwierzytelniony wymaga tożsamości
    single-tenant z Azure (``app_id`` + ``app_password`` + ``tenant_id``) — brak
    ``tenant_id`` przy single-tenant to klasyczna przyczyna 401 w Teams.
    """

    app_id: str = ""
    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback nie ujawniło hasła.
    app_password: str = field(default="", repr=False)
    tenant_id: str = ""
    bind_host: str = "localhost"
    bind_port: int = 3978
    anonymous_auth: bool = False

    @classmethod
    def from_env(cls) -> TeamsSettings:
        return cls(
            app_id=os.environ.get("WORKMATE_TEAMS_APP_ID", ""),
            app_password=os.environ.get("WORKMATE_TEAMS_APP_PASSWORD", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_TENANT_ID", ""),
            bind_host=os.environ.get("WORKMATE_TEAMS_BIND_HOST", "localhost"),
            bind_port=_int_from_env("WORKMATE_TEAMS_PORT", 3978),
            anonymous_auth=_bool_from_env("WORKMATE_TEAMS_ANONYMOUS", default=False),
        )

    def validate(self) -> None:
        """Twardy błąd startowy, gdy konfiguracja jest niebezpieczna albo niepełna.

        Lepiej nie wystartować niż ruszyć bez działającego uwierzytelniania. Do
        lokalnego testu w Emulatorze użyj ``WORKMATE_TEAMS_ANONYMOUS=true`` — ale
        tylko na loopbacku, żeby nie wystawić nieuwierzytelnionego bota na sieć.
        """
        loopback = ("localhost", "127.0.0.1", "::1")
        if self.anonymous_auth:
            if self.bind_host not in loopback:
                raise ValueError(
                    "Tryb anonimowy (WORKMATE_TEAMS_ANONYMOUS=true) jest dozwolony "
                    "tylko na loopbacku (localhost/127.0.0.1/::1), a "
                    f"WORKMATE_TEAMS_BIND_HOST={self.bind_host!r}. Nie wystawiaj "
                    "nieuwierzytelnionego bota na sieć."
                )
            return
        missing = [
            name
            for name, value in (
                ("WORKMATE_TEAMS_APP_ID", self.app_id),
                ("WORKMATE_TEAMS_APP_PASSWORD", self.app_password),
                ("WORKMATE_TEAMS_TENANT_ID", self.tenant_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi Teams wymagają tożsamości single-tenant: brakuje "
                + ", ".join(missing)
                + ". Do lokalnego testu w Emulatorze ustaw WORKMATE_TEAMS_ANONYMOUS=true."
            )


@dataclass(frozen=True)
class AgentSettings:
    """Konfiguracja runtime'u agenta (Faza 2, M1 / ADR 0008; ADR 0011).

    Klucz Claude API to sekret — czytany z env, nigdy z repo ani z folderu
    indeksowanego przez rdzeń (``data/``). Domyślny model to ``claude-sonnet-5``
    (większe wymagania projektu wobec syntezy), nadpisywalny przez ``WORKMATE_AGENT_MODEL``.
    ``thinking_type`` steruje rozszerzonym myśleniem (ADR 0011): ``adaptive`` (domyślnie,
    model sam decyduje ile myśleć) albo ``disabled`` (np. dla ograniczenia kosztu).
    ``max_tokens`` (128000 — pełny sufit wyjścia modelu) dzieli budżet między myślenie
    i odpowiedź; adapter woła Claude API STREAMINGIEM (``messages.stream``), więc duży
    sufit nie odpala limitu czasu SDK, który odrzuca duże żądania non-streaming.
    """

    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback go nie ujawniło.
    api_key: str = field(default="", repr=False)
    model: str = "claude-sonnet-5"
    max_tokens: int = 128000
    max_tool_iterations: int = 8
    thinking_type: str = "adaptive"
    # Druga przelotka-krytyk notatki M3 (ADR 0047): domyślnie OFF (zachowuje jednoprzelotowe 0041),
    # ~2× koszt gdy ON. To toggle jakości, NIE bramka zapisu — flip nie wymaga zgody zespołu.
    verify_meeting_note: bool = False

    @classmethod
    def from_env(cls) -> AgentSettings:
        # Priorytet: WORKMATE_AGENT_API_KEY (jawnie dla WorkMate) > ANTHROPIC_API_KEY (nazwa SDK).
        api_key = os.environ.get("WORKMATE_AGENT_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        return cls(
            api_key=api_key,
            model=os.environ.get("WORKMATE_AGENT_MODEL", "claude-sonnet-5"),
            max_tokens=_int_from_env("WORKMATE_AGENT_MAX_TOKENS", 128000),
            max_tool_iterations=_int_from_env("WORKMATE_AGENT_MAX_TOOL_ITERATIONS", 8),
            thinking_type=os.environ.get("WORKMATE_AGENT_THINKING", "adaptive"),
            verify_meeting_note=_bool_from_env("WORKMATE_AGENT_VERIFY_MEETING_NOTE", False),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy konfiguracja jest niepełna albo bez sensu.

        Lepiej nie ruszać bez uwierzytelniania; a niedodatnie limity dają cichy
        no-op (pętla pomija model), więc też je odrzucamy fail-fast.
        """
        if not self.api_key:
            raise ValueError(
                "Runtime agenta wymaga klucza Claude API: ustaw ANTHROPIC_API_KEY "
                "(lub WORKMATE_AGENT_API_KEY) w środowisku/.env."
            )
        if self.max_tool_iterations < 1:
            raise ValueError(
                "WORKMATE_AGENT_MAX_TOOL_ITERATIONS musi być >= 1, jest: "
                f"{self.max_tool_iterations}."
            )
        if self.max_tokens < 1:
            raise ValueError(f"WORKMATE_AGENT_MAX_TOKENS musi być >= 1, jest: {self.max_tokens}.")
        if self.thinking_type not in ("adaptive", "disabled"):
            raise ValueError(
                "WORKMATE_AGENT_THINKING musi być 'adaptive' albo 'disabled', jest: "
                f"{self.thinking_type!r}."
            )




@dataclass(frozen=True)
class ConversationSettings:
    """Konfiguracja pamięci rozmów (wątkowość + limit kontekstu, Faza 2 / ADR 0010, 0012).

    Baza SQLite leży poza ``data/`` (folder indeksowany przez rdzeń) i poza repo —
    to dane operacyjne, nie baza wiedzy. Limit kontekstu bramkuje REALNY rozmiar kontekstu
    ostatniej tury (z pola ``usage`` odpowiedzi, Design 2) — po osiągnięciu rollover startuje
    nowy, tańszy wątek. Domyślnie 128000 (przy 1M oknie): realne ``input_tokens`` (system +
    schematy narzędzi + cała historia wysyłana ponownie co turę) są O RZĘDY większe niż dawna
    estymata, więc próg musi być duży. Nadpisywalny przez ``WORKMATE_CONV_MAX_TOKENS``.

    ``idle_timeout_minutes`` (ADR 0012) domyka wątkowość w czasie: po tylu minutach
    bezczynności kolejna wiadomość zaczyna NOWY wątek (osobne rozmowy = osobne wątki,
    zamiast jednej ciągnącej się nici). ``0`` wyłącza to kryterium. Nadpisywalny przez
    ``WORKMATE_CONV_IDLE_MINUTES``.

    Kompaktowanie (ADR 0014) ZASTĘPUJE rollover-na-rozmiarze, gdy włączone: przy
    ``last_input_tokens`` > ``compaction_threshold_tokens()`` (domyślnie 70% okna modelu)
    stare tury zastępujemy podsumowaniem (osobne wywołanie modelu ``compaction_model``,
    domyślnie = model agenta), zachowując ostatnie ``compaction_keep_turns`` verbatim.
    """

    db_path: Path
    max_context_tokens: int = 128000
    idle_timeout_minutes: int = 30
    compaction_enabled: bool = True
    context_window_tokens: int = 1_000_000  # okno Sonnet 5
    compaction_threshold_fraction: float = 0.70
    compaction_keep_turns: int = 4
    compaction_model: str = ""  # "" → użyj modelu agenta (Sonnet 5)

    @classmethod
    def from_env(cls) -> ConversationSettings:
        return cls(
            db_path=_path_from_env("WORKMATE_CONVERSATIONS_DB", _DEFAULT_CONVERSATIONS_DB),
            max_context_tokens=_int_from_env("WORKMATE_CONV_MAX_TOKENS", 128000),
            idle_timeout_minutes=_int_from_env("WORKMATE_CONV_IDLE_MINUTES", 30),
            compaction_enabled=_bool_from_env("WORKMATE_COMPACTION_ENABLED", default=True),
            context_window_tokens=_int_from_env("WORKMATE_CONTEXT_WINDOW_TOKENS", 1_000_000),
            compaction_threshold_fraction=_float_from_env(
                "WORKMATE_COMPACTION_THRESHOLD_FRACTION", 0.70
            ),
            compaction_keep_turns=_int_from_env("WORKMATE_COMPACTION_KEEP_TURNS", 4),
            compaction_model=os.environ.get("WORKMATE_COMPACTION_MODEL", ""),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy limit, próg bezczynności lub kompaktowanie są bez sensu."""
        if self.max_context_tokens < 1:
            raise ValueError(
                f"WORKMATE_CONV_MAX_TOKENS musi być >= 1, jest: {self.max_context_tokens}."
            )
        # 0 = kryterium bezczynności wyłączone; ujemne nie ma sensu (fail fast).
        if self.idle_timeout_minutes < 0:
            raise ValueError(
                "WORKMATE_CONV_IDLE_MINUTES musi być >= 0 (0 wyłącza), jest: "
                f"{self.idle_timeout_minutes}."
            )
        if self.context_window_tokens < 1:
            raise ValueError(
                f"WORKMATE_CONTEXT_WINDOW_TOKENS musi być >= 1, jest: {self.context_window_tokens}."
            )
        if not 0.0 < self.compaction_threshold_fraction <= 1.0:
            raise ValueError(
                "WORKMATE_COMPACTION_THRESHOLD_FRACTION musi być w (0, 1], jest: "
                f"{self.compaction_threshold_fraction}."
            )
        if self.compaction_keep_turns < 1:
            raise ValueError(
                f"WORKMATE_COMPACTION_KEEP_TURNS musi być >= 1, jest: {self.compaction_keep_turns}."
            )

    def idle_timeout(self) -> timedelta | None:
        """Próg bezczynności jako ``timedelta`` do wstrzyknięcia w ``ConversationService``.

        ``0`` (wyłączone) mapujemy na ``None`` — jedno miejsce konwersji dla wszystkich
        drzwi, żeby wiring nie powtarzał warunku ``> 0``.
        """
        return timedelta(minutes=self.idle_timeout_minutes) if self.idle_timeout_minutes else None

    def compaction_threshold_tokens(self) -> int:
        """Próg triggera kompaktowania w tokenach = ułamek okna kontekstu modelu (ADR 0014)."""
        return int(self.context_window_tokens * self.compaction_threshold_fraction)


@dataclass(frozen=True)
class EventsSettings:
    """Konfiguracja wspólnego magazynu zdarzeń (EventStore, ADR 0019) — warstwa spajająca drzwi.

    Baza SQLite leży POZA ``data/`` (folder indeksowany przez rdzeń) i poza repo — to dane
    operacyjne (zdarzenia z drzwi), nie baza wiedzy; poisoned zdarzenie nie może trafić do
    notatek, które agent czyta. Osobny plik od ``conversations.db`` (patrz ``_DEFAULT_EVENTS_DB``).
    """

    db_path: Path = _DEFAULT_EVENTS_DB

    @classmethod
    def from_env(cls) -> EventsSettings:
        return cls(db_path=_path_from_env("WORKMATE_EVENTS_DB", _DEFAULT_EVENTS_DB))


@dataclass(frozen=True)
class RetrievalSettings:
    """Konfiguracja retrievalu notatek (ADR 0023 Faza A + ADR 0039 Faza B) — ranking wyszukiwania.

    ``lemmatize`` włącza lematyzację PL (BM25 nad lematami, odporność na fleksję) — domyślnie ON,
    o ile dostępny extra ``retrieval`` (``simplemma``); brak extra degraduje do dawnego rankingu
    podłańcuchowego (wiring łapie ``ImportError``). Wyłączalne env do debugowania/porównań.

    Warstwa DENSE (ADR 0039, Faza B) jest za bramką mikro-evalu i domyślnie WYŁĄCZONA
    (``enable_dense=False``). Włączona (tylko na długożyjących drzwiach, nigdy MCP stdio) dokłada
    osadzenia semantyczne fuzowane z BM25 przez RRF; wymaga extra ``retrieval-dense`` (fastembed).
    Ma sens tylko z ``lemmatize`` (fuzja żyje w gałęzi BM25) — wiring drzwi buduje ranker dense
    wyłącznie obok lematyzatora. ``dense_model`` to nazwa modelu osadzeń (fastembed), ``index_path``
    — magazyn wektorów (SQLite, poza repo/data), ``rrf_k`` — dyskonto rang RRF, ``dense_top_n``
    (0 = całość) opcjonalnie przycina ogon rankingu dense; ``dense_min_similarity`` (0.0) odcina
    notatki poniżej progu cosinusa.
    """

    lemmatize: bool = True
    lang: str = "pl"
    enable_dense: bool = False
    dense_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    index_path: Path = _DEFAULT_RETRIEVAL_INDEX
    rrf_k: int = 60
    dense_top_n: int = 0
    dense_min_similarity: float = 0.0

    @classmethod
    def from_env(cls) -> RetrievalSettings:
        return cls(
            lemmatize=_bool_from_env("WORKMATE_RETRIEVAL_LEMMATIZE", default=True),
            lang=os.environ.get("WORKMATE_RETRIEVAL_LANG", "pl"),
            enable_dense=_bool_from_env("WORKMATE_RETRIEVAL_ENABLE_DENSE", default=False),
            dense_model=os.environ.get(
                "WORKMATE_RETRIEVAL_DENSE_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ),
            index_path=_path_from_env("WORKMATE_RETRIEVAL_INDEX", _DEFAULT_RETRIEVAL_INDEX),
            rrf_k=_int_from_env("WORKMATE_RETRIEVAL_RRF_K", 60),
            dense_top_n=_int_from_env("WORKMATE_RETRIEVAL_DENSE_TOP_N", 0),
            dense_min_similarity=_float_from_env("WORKMATE_RETRIEVAL_DENSE_MIN_SIM", 0.0),
        )


# Domyślne ścieżki drzwi Teams w trybie delegowanym (ADR 0015): POZA repo i data/.
# Cache tokenu MSAL to SEKRET; plik stanu (watermark wątków) — dane operacyjne, nie sekret.
_DEFAULT_TEAMS_GRAPH_CACHE = Path.home() / ".workmate" / "teams_token_cache.bin"
_DEFAULT_TEAMS_GRAPH_STATE = Path.home() / ".workmate" / "teams_graph_state.json"
# Zakresy delegowane, których potrzebuje poller kanału (wymagają zgody admina). NIE wpisuj
# offline_access/openid/profile — MSAL dokłada je sam (inaczej błąd "reserved scope").
_DEFAULT_TEAMS_GRAPH_SCOPES = (
    "ChannelMessage.Read.All",
    "ChannelMessage.Send",
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "User.Read",
    # Pobieranie plików-załączników (PDF/Word/obrazy) leżących w SharePoint (ADR 0016).
    # Obrazy wklejane inline (hostedContents) NIE wymagają tych zakresów.
    "Files.Read.All",
    "Sites.Read.All",
)
# Sufit rozmiaru (RAW) załącznika. Request API to max 32 MB, ale base64 puchnie ~1.33×,
# więc 24 MB surowych ≈ 32 MB zakodowane — twardy limit, by pojedynczy blok nie przekroczył
# żądania. Domyślne wartości (8/20 MB) zostawiają zapas na historię.
_MAX_ATTACHMENT_MB_CEILING = 24
# Górny cap liczby załączników na wiadomość (chroni przed absurdalną wartością operatora).
_MAX_ATTACHMENTS_PER_MESSAGE_CEILING = 20
# Sufit dłuższej krawędzi obrazu (px). 2576 to maksymalna użyteczna rozdzielczość modeli
# high-res (Sonnet 5) — powyżej model i tak skaluje po stronie serwera, więc nie ma sensu
# wysyłać więcej. Domyślne 2048 zostawia zapas na koszt tokenów (bloki wracają co turę).
_MAX_IMAGE_EDGE_PX_CEILING = 2576
_MIN_IMAGE_EDGE_PX = 256
# Sufit rozmiaru pliku EKSTRAHOWANEGO do tekstu (docx/xlsx/pptx/txt). Nie wysyłamy z nich
# bajtów do API (tylko tekst), więc sufit 32 MB base64 ich nie dotyczy — limit jest tylko
# barierą na ekstrakcję/pamięć. Prezentacje z obrazkami rutynowo mają 10–50 MB.
_MAX_EXTRACT_MB_CEILING = 100
# Zakres zapisu Graph wymagany do wgrania pliku na dysk kanału (ADR 0026, bramka enable_file_reply).
# Nadany 2026-07-27 (zgoda admina). Włączona bramka BEZ tego zakresu = martwa (403 przy uploadzie),
# więc walidacja żąda go WPROST — fail-fast zamiast cichej bramki „włączonej, ale niedziałającej".
_FILE_REPLY_WRITE_SCOPE = "Files.ReadWrite.All"
# Sufit rozmiaru ZRENDEROWANEGO pliku odpowiedzi (KB). Treść pochodzi od modelu (ograniczona
# max_tokens), ale twardy limit domyka powierzchnię eksfiltracji przy wstrzyknięciu (ADR 0026).
_MAX_FILE_REPLY_KB_CEILING = 4096
# Zakresy czatu wymagane do wysłania OBRAZU 1:1 do usera (ADR 0027, bramka enable_user_file_push).
# Obraz idzie INLINE (hostedContents) — bez zakresu Files.* — ale dostawa na czat 1:1 wymaga tych
# zakresów. Są JUŻ skonsentowane przez admina (używa ich Powiadomienia_teams/TeamsPush) i dzielą
# cache MSAL z tymi drzwiami; brakuje ich tylko na TOKENIE pollera kanału, więc włączenie bramki =
# dodanie ich do WORKMATE_TEAMS_GRAPH_SCOPES + ponowna zgoda device-code (bez nowej zgody admina).
# Walidacja żąda ich WPROST — fail-fast zamiast cichej bramki „włączonej, ale niedziałającej" (403).
_USER_PUSH_CHAT_SCOPES = ("Chat.Create", "ChatMessage.Send")
# Sufit rozmiaru obrazu push-owanego do usera (KB). Bajty pochodzą od modelu; twardy limit domyka
# powierzchnię eksfiltracji i mieści się w limicie żądania Graph (~32 MB po base64) (ADR 0027).
_MAX_USER_IMAGE_KB_CEILING = 4096
# Zapis wymagany do wysłania DOKUMENTU 1:1 (ADR 0027, wariant PLIKOWY, bramka enable_user_doc_push).
# Wariant plikowy wgrywa TYLKO na WŁASNY OneDrive bota (``PUT /me/drive/root``) i udostępnia WŁASNY
# item (``invite``) — do tego wystarcza WĘŻSZY ``Files.ReadWrite`` (pełny dostęp do plików
# ZALOGOWANEGO usera). ``Files.ReadWrite.All`` (też cudze pliki i witryny SharePoint) jest SZERSZY
# niż tu trzeba, ale AKCEPTOWANY, bo ADR 0026 (upload na dysk KANAŁU) i tak go konsentuje — operator
# chcący TYLKO push dokumentu może nadać węższy (least-privilege). Wymagamy co najmniej JEDNEGO z
# pary; zakresy CZATU (dostawa 1:1) osobno w ``_USER_PUSH_CHAT_SCOPES``. (Że ``invite`` działa pod
# węższym ``Files.ReadWrite`` — do potwierdzenia na live-smoke; patrz research-doc.)
_USER_DOC_PUSH_WRITE_SCOPES = ("Files.ReadWrite", _FILE_REPLY_WRITE_SCOPE)
# Sufit rozmiaru ZRENDEROWANEGO dokumentu push-owanego do usera (KB). Treść od modelu; twardy limit
# domyka powierzchnię eksfiltracji przy wstrzyknięciu (jak odpowiedź plikiem, ADR 0026/0027).
_MAX_USER_DOC_KB_CEILING = 4096
# Zakresy delegowane wymagane do produkcyjnego M3 (ADR 0009, B1, bramka enable_meeting_transcript):
# odczyt TREŚCI transkryptu (``OnlineMeetingTranscript.Read.All``) oraz rozwiązanie spotkania po
# ``joinWebUrl`` (``OnlineMeetings.Read``). Oba wymagają ZGODY ADMINA (nowe zakresy, jeszcze nie
# skonsentowane — w przeciwieństwie do zakresów czatu/plików). Włączona bramka BEZ nich = martwa
# (403 przy pobraniu), więc walidacja żąda ich WPROST — fail-fast zamiast cichej, martwej bramki.
_MEETING_TRANSCRIPT_SCOPES = ("OnlineMeetingTranscript.Read.All", "OnlineMeetings.Read")


def _parse_watch_pairs(value: str) -> tuple[tuple[str, str], ...]:
    """Sparsuj ``team:channel,team:channel`` na krotki par (pomija niepełne wpisy)."""
    pairs: list[tuple[str, str]] = []
    for part in value.split(","):
        team, _, channel = part.strip().partition(":")
        if team.strip() and channel.strip():
            pairs.append((team.strip(), channel.strip()))
    return tuple(pairs)


@dataclass(frozen=True)
class TeamsGraphSettings:
    """Konfiguracja drzwi Teams w trybie DELEGOWANYM (ADR 0015) — polling kanału przez Graph.

    Bot działa jako ZALOGOWANY UŻYTKOWNIK (device-code MSAL), bez publicznego endpointu i
    bez rejestracji bota. ``client_id``/``tenant_id`` (ze strony aplikacji w Entra) są
    wymagane — ``validate`` odrzuca brak (koniec z placeholderem w kodzie). Sam obiekt nie
    trzyma sekretu (sekretem jest CACHE tokenu na dysku), więc bez pola ``repr=False``.

    ``watch`` (pary ``team_id:channel_id``) wyznacza nasłuchiwane kanały; pusty → tryb
    odkrywania (wypisz zespoły/kanały i zakończ). ``top_roots``/``top_replies`` ograniczają
    koszt API na rundę; wątek bez aktywności dłużej niż ``active_idle_hours`` przestaje być
    odpytywany o odpowiedzi (eksmisja).
    """

    client_id: str = ""
    tenant_id: str = ""
    scopes: tuple[str, ...] = _DEFAULT_TEAMS_GRAPH_SCOPES
    token_cache_path: Path = _DEFAULT_TEAMS_GRAPH_CACHE
    state_path: Path = _DEFAULT_TEAMS_GRAPH_STATE
    watch: tuple[tuple[str, str], ...] = ()
    poll_interval_s: int = 10
    top_roots: int = 20
    top_replies: int = 50
    active_idle_hours: int = 24
    # Limity załączników (ADR 0016): rozmiar pliku, liczba i ŁĄCZNY budżet na wiadomość.
    max_attachment_mb: int = 8
    max_attachments_per_message: int = 20  # równolegle wysłane pliki na wiadomość (= sufit MAX)
    max_total_attachment_mb: int = 20  # sumaryczny budżet base64 — chroni sufit 32 MB żądania API
    max_extract_mb: int = 50  # sufit pliku ekstrahowanego do tekstu (docx/xlsx/pptx/txt)
    max_image_edge_px: int = 2048  # dłuższa krawędź obrazu (px) — powyżej downscaling
    # Odpowiedź plikiem w wątku (ADR 0026, A′2) — OSOBNA bramka zapisu, domyślnie OFF (ADR 0006).
    enable_file_reply: bool = False
    max_file_reply_kb: int = 512  # sufit rozmiaru zrenderowanego pliku odpowiedzi
    # Push OBRAZU do rozmówcy 1:1 (ADR 0027, A′3) — OSOBNA bramka zapisu, domyślnie OFF (ADR 0006).
    enable_user_file_push: bool = False
    max_user_image_kb: int = 1024  # sufit rozmiaru obrazu push-owanego do usera
    # Push DOKUMENTU (md/txt/pdf/docx) do rozmówcy 1:1 (ADR 0027, plik) — OSOBNA bramka od
    # obrazowej, bo wymaga SZERSZEGO zakresu (Files.ReadWrite.All), domyślnie OFF (ADR 0006).
    enable_user_doc_push: bool = False
    max_user_doc_kb: int = 512  # sufit rozmiaru zrenderowanego dokumentu push-owanego do usera
    # Produkcyjne M3: pobranie transkryptu spotkania z Graph (ADR 0009, B1) — OSOBNA bramka,
    # domyślnie OFF, bo wymaga NOWYCH zakresów admina (patrz ``_MEETING_TRANSCRIPT_SCOPES``).
    enable_meeting_transcript: bool = False
    # Produkcyjne M3 — ZAPIS notatki komendą ``/notatka`` z drzwi Teams (ADR 0009 §4 / 0041). To
    # decyzja zaufania Gate-2 (zapis z mniej zaufanych drzwi), więc OSOBNA bramka, domyślnie OFF
    # (ADR 0006). Wymaga włączonego ``enable_meeting_transcript`` (skąd wziąć transkrypt).
    enable_meeting_note_write: bool = False
    # Autoryzacja nadawcy ``/notatka`` (B2 / ADR 0042): plik mapy tożsamości (może być TEN SAM
    # co worklogów). Rozwiązuje AAD id nadawcy na członka pionu; nieznany → odmowa (fail-closed).
    # Przy włączonej bramce zapisu WYMAGANY (walidacja) — bez niego „każdy pisze do wszystkiego".
    meeting_note_identities: Path = Path()
    # Async ``/notatka`` (B3 / ADR 0043): zamiast składać notatkę inline (blokuje poller na czas
    # transkrypt+Claude), router odsyła ACK natychmiast i liczy w tle, a wynik wrzuca do wątku.
    # OSOBNA bramka, domyślnie OFF (ADR 0006); wymaga włączonej bramki zapisu (to jej tryb).
    enable_meeting_note_async: bool = False
    # Górny limit RÓWNOLEGŁYCH notatek w tle (pula wątków) — sufit jednoczesnych wywołań Claude.
    meeting_note_async_workers: int = 2
    # Przechwycenie „zapisz to" z WĄTKU po @wzmiance bota (ADR 0048, F2). OSOBNA bramka zapisu,
    # domyślnie OFF (ADR 0006). Niezależna od transkryptu spotkania (materiałem jest treść wątku),
    # ale — jak /notatka — wymaga mapy tożsamości (autoryzacja B2 wbudowana w bramkę zapisu).
    # Tryb async współdzieli przełącznik ``enable_meeting_note_async`` (ta sama pula/poster).
    enable_thread_note_capture: bool = False
    # One-pager „ogarnij mnie na <projekt>" po @wzmiance bota (ADR 0051, F4). READ-ONLY (status +
    # notatki), więc NIE bramka zapisu — flaga staged rolloutu, domyślnie OFF. Bez wymogu mapy
    # tożsamości ani RW-montażu (nic nie zapisuje). Dostawa PDF ``| pdf`` reużywa kanału file-reply:
    # aktywna tylko przy dodatkowo włączonym ``enable_file_reply`` (jego zakres/sender); inaczej
    # ``| pdf`` degraduje do odpowiedzi tekstem.
    enable_project_brief: bool = False
    # Digest „co się zmieniło od <data>" po @wzmiance bota (ADR 0052, F5). READ-ONLY (fold zdarzeń
    # z warstwy spajającej), więc NIE bramka zapisu — flaga staged rolloutu, domyślnie OFF. Bez
    # wymogu tożsamości/RW-montażu. Dostawa PDF ``| pdf`` reużywa kanał file-reply (jak brief).
    enable_change_digest: bool = False
    # Polityka „czy w ogóle odpowiadać" (SZKIELET pod wielokanałowe wdrożenie WorkMate).
    # ``all`` (domyślnie) = zachowanie sprzed tej zmiany: odpowiedź na każdą wiadomość od
    # innego człowieka w kanałach z ``watch``. ``mention`` odpowiada tylko po @wzmiance bota
    # (lub gdy bot już jest aktywny w danym wątku — patrz ``selection.ReplyPolicy``), z
    # wyjątkiem kanałów z ``always_reply``, które zawsze zachowują się jak ``all``. Domyślne
    # ``all`` gwarantuje, że sam deploy tej zmiany NIC nie zmienia w produkcji.
    reply_policy: str = "all"
    # Kanały, które ZAWSZE odpowiadają (jak ``mode=all``), niezależnie od ``reply_policy`` —
    # ten sam format co ``watch`` (``team:channel,team:channel``); patrz ``_parse_watch_pairs``.
    always_reply: tuple[tuple[str, str], ...] = ()

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @classmethod
    def from_env(cls) -> TeamsGraphSettings:
        return cls(
            client_id=os.environ.get("WORKMATE_TEAMS_GRAPH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_GRAPH_TENANT_ID", ""),
            scopes=_list_from_env("WORKMATE_TEAMS_GRAPH_SCOPES", _DEFAULT_TEAMS_GRAPH_SCOPES),
            token_cache_path=_path_from_env(
                "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE", _DEFAULT_TEAMS_GRAPH_CACHE
            ),
            state_path=_path_from_env("WORKMATE_TEAMS_GRAPH_STATE", _DEFAULT_TEAMS_GRAPH_STATE),
            watch=_parse_watch_pairs(os.environ.get("WORKMATE_TEAMS_GRAPH_WATCH", "")),
            poll_interval_s=_int_from_env("WORKMATE_TEAMS_GRAPH_POLL_INTERVAL", 10),
            top_roots=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_ROOTS", 20),
            top_replies=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_REPLIES", 50),
            active_idle_hours=_int_from_env("WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS", 24),
            max_attachment_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB", 8),
            max_attachments_per_message=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS", 20),
            max_total_attachment_mb=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB", 20
            ),
            max_extract_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB", 50),
            max_image_edge_px=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE", 2048),
            enable_file_reply=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY", default=False
            ),
            max_file_reply_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_FILE_REPLY_KB", 512),
            enable_user_file_push=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH", default=False
            ),
            max_user_image_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB", 1024),
            enable_user_doc_push=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH", default=False
            ),
            max_user_doc_kb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB", 512),
            enable_meeting_transcript=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT", default=False
            ),
            enable_meeting_note_write=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE", default=False
            ),
            meeting_note_identities=_path_from_env("WORKMATE_TEAMS_GRAPH_IDENTITIES", Path()),
            enable_meeting_note_async=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC", default=False
            ),
            meeting_note_async_workers=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS", 2
            ),
            enable_thread_note_capture=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE", default=False
            ),
            enable_project_brief=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_PROJECT_BRIEF", default=False
            ),
            enable_change_digest=_bool_from_env(
                "WORKMATE_TEAMS_GRAPH_ENABLE_CHANGE_DIGEST", default=False
            ),
            reply_policy=os.environ.get("WORKMATE_TEAMS_GRAPH_REPLY_POLICY", "all"),
            always_reply=_parse_watch_pairs(
                os.environ.get("WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY", "")
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak tożsamości aplikacji albo bezsensowne limity."""
        missing = [
            name
            for name, value in (
                ("WORKMATE_TEAMS_GRAPH_CLIENT_ID", self.client_id),
                ("WORKMATE_TEAMS_GRAPH_TENANT_ID", self.tenant_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi Teams (delegowane) wymagają tożsamości aplikacji Entra: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )
        if not self.scopes:
            raise ValueError("WORKMATE_TEAMS_GRAPH_SCOPES nie może być puste.")
        if self.poll_interval_s < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_POLL_INTERVAL musi być >= 1, jest: {self.poll_interval_s}."
            )
        if self.top_roots < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_TOP_ROOTS musi być >= 1, jest: {self.top_roots}."
            )
        if self.top_replies < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_GRAPH_TOP_REPLIES musi być >= 1, jest: {self.top_replies}."
            )
        # 0 eksmitowałoby każdy wątek natychmiast (koniec wielotury) — wymagamy >= 1.
        if self.active_idle_hours < 1:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS musi być >= 1, jest: "
                f"{self.active_idle_hours}."
            )
        if not 1 <= self.max_attachment_mb <= _MAX_ATTACHMENT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB musi być w zakresie "
                f"1..{_MAX_ATTACHMENT_MB_CEILING} (sufit API), jest: {self.max_attachment_mb}."
            )
        # Górny cap liczby chroni przed absurdalną wartością operatora (np. 1000).
        if not 1 <= self.max_attachments_per_message <= _MAX_ATTACHMENTS_PER_MESSAGE_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS musi być w zakresie "
                f"1..{_MAX_ATTACHMENTS_PER_MESSAGE_CEILING}, jest: "
                f"{self.max_attachments_per_message}."
            )
        # Łączny budżet też w 1..32 (sufit żądania API). Nie wiążemy go z ``max_attachment_mb``:
        # plik większy niż budżet materializer i tak łagodnie zdegraduje do notki.
        if not 1 <= self.max_total_attachment_mb <= _MAX_ATTACHMENT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB musi być w zakresie "
                f"1..{_MAX_ATTACHMENT_MB_CEILING}, jest: {self.max_total_attachment_mb}."
            )
        # Pliki ekstrahowane do tekstu nie zjadają budżetu base64, ale trzymamy górną barierę.
        if not 1 <= self.max_extract_mb <= _MAX_EXTRACT_MB_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB musi być w zakresie "
                f"1..{_MAX_EXTRACT_MB_CEILING}, jest: {self.max_extract_mb}."
            )
        # Próg downscalingu obrazu: poniżej 256 px obraz byłby nieczytelny, powyżej 2576 px
        # model i tak skaluje po swojej stronie — trzymamy się użytecznego zakresu.
        if not _MIN_IMAGE_EDGE_PX <= self.max_image_edge_px <= _MAX_IMAGE_EDGE_PX_CEILING:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE musi być w zakresie "
                f"{_MIN_IMAGE_EDGE_PX}..{_MAX_IMAGE_EDGE_PX_CEILING} (px), jest: "
                f"{self.max_image_edge_px}."
            )
        if self.enable_file_reply:
            # Bramka ON bez zakresu zapisu = 403 przy pierwszym uploadzie. Fail-fast na starcie
            # (jak przy Jira/GitHub write): włączona, ale martwa bramka byłaby footgunem (ADR 0026).
            if _FILE_REPLY_WRITE_SCOPE not in self.scopes:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY=true wymaga zakresu "
                    f"'{_FILE_REPLY_WRITE_SCOPE}' w WORKMATE_TEAMS_GRAPH_SCOPES (po nadaniu przez "
                    "admina usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_file_reply_kb <= _MAX_FILE_REPLY_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_FILE_REPLY_KB musi być w zakresie "
                    f"1..{_MAX_FILE_REPLY_KB_CEILING}, jest: {self.max_file_reply_kb}."
                )
        if self.enable_user_file_push:
            # Bramka ON bez zakresów czatu = 403 przy pierwszym push-u. Fail-fast (ADR 0027):
            # zakresy są skonsentowane przez admina, ale muszą być na TOKENIE tych drzwi.
            missing = [s for s in _USER_PUSH_CHAT_SCOPES if s not in self.scopes]
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_USER_FILE_PUSH=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (skonsentowane przez "
                    "admina; usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_user_image_kb <= _MAX_USER_IMAGE_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_USER_IMAGE_KB musi być w zakresie "
                    f"1..{_MAX_USER_IMAGE_KB_CEILING}, jest: {self.max_user_image_kb}."
                )
        if self.enable_user_doc_push:
            # Bramka ON bez zakresów = 403 przy uploadzie/wysyłce. Fail-fast (ADR 0027, wariant
            # plikowy): zakresy CZATU (dostawa 1:1) ORAZ co najmniej JEDEN zapis do plików (upload
            # na własny OneDrive) — węższy Files.ReadWrite lub szerszy Files.ReadWrite.All.
            missing = [s for s in _USER_PUSH_CHAT_SCOPES if s not in self.scopes]
            if not any(s in self.scopes for s in _USER_DOC_PUSH_WRITE_SCOPES):
                missing.append("Files.ReadWrite (lub Files.ReadWrite.All)")
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_USER_DOC_PUSH=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (skonsentowane przez "
                    "admina; usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
            if not 1 <= self.max_user_doc_kb <= _MAX_USER_DOC_KB_CEILING:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_MAX_USER_DOC_KB musi być w zakresie "
                    f"1..{_MAX_USER_DOC_KB_CEILING}, jest: {self.max_user_doc_kb}."
                )
        if self.enable_meeting_transcript:
            # Bramka ON bez zakresów transkryptu = 403 przy pobraniu. Fail-fast (ADR 0009, B1):
            # to NOWE zakresy admina — dopóki nie nadane i nie ma ich na TOKENIE tych drzwi, bramka
            # byłaby martwa. Po nadaniu przez admina usuń cache tokenu, by wymusić ponowną zgodę.
            missing = [s for s in _MEETING_TRANSCRIPT_SCOPES if s not in self.scopes]
            if missing:
                raise ValueError(
                    "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true wymaga zakresów "
                    f"{', '.join(missing)} w WORKMATE_TEAMS_GRAPH_SCOPES (nadaje ADMIN w Entra; "
                    "po nadaniu usuń cache tokenu, by wymusić ponowną zgodę device-code)."
                )
        if self.enable_meeting_note_write and not self.enable_meeting_transcript:
            # Zapis notatki komendą /notatka bez źródła transkryptu = martwa bramka. Fail-fast
            # (ADR 0009/0041): najpierw włącz transkrypt (i jego zakresy), potem zapis z drzwi.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true (skąd wziąć transkrypt)."
            )
        if self.enable_meeting_note_async and not self.enable_meeting_note_write:
            # Async to TRYB ścieżki zapisu /notatka, nie samodzielna zdolność — bez włączonego
            # zapisu nie ma czego wykonywać w tle. Fail-fast (ADR 0043).
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC=true wymaga też "
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true (async = tryb zapisu)."
            )
        if self.enable_meeting_note_async and self.meeting_note_async_workers < 1:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS musi być ≥ 1, jest: "
                f"{self.meeting_note_async_workers}."
            )
        if self.enable_meeting_note_write and not self.meeting_note_identities.is_file():
            # Autoryzacja jest WBUDOWANA w bramkę zapisu (B2 / ADR 0042): bez mapy tożsamości
            # każdy nadawca pisałby do dowolnego projektu (ryzyko KRYTYCZNE). Fail-fast — nie
            # pozwalamy włączyć zapisu bez źródła autoryzacji (nie osobny toggle, bo domyślne
            # „OFF autoryzacji" = „każdy pisze"). Może wskazywać TEN SAM plik co worklogi.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje zapis, ADR 0042); brak pliku: {self.meeting_note_identities}."
            )
        if self.enable_thread_note_capture and not self.meeting_note_identities.is_file():
            # „Zapisz to" pisze notatkę z drzwi Teams (ADR 0048) → autoryzacja WBUDOWANA w bramkę
            # (B2 / ADR 0042), jak /notatka: bez mapy tożsamości każdy nadawca zapisałby wątek do
            # dowolnego projektu. Fail-fast (ten sam plik co /notatka i worklogi). Transkryptu NIE
            # wymaga — materiałem jest treść wątku, nie WebVTT spotkania.
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ENABLE_THREAD_NOTE_CAPTURE=true wymaga "
                "WORKMATE_TEAMS_GRAPH_IDENTITIES = ścieżka do mapy tożsamości (członkostwo "
                f"autoryzuje zapis, ADR 0042/0048); brak pliku: {self.meeting_note_identities}."
            )
        if self.reply_policy not in ("all", "mention"):
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_REPLY_POLICY musi być 'all' albo 'mention', jest: "
                f"{self.reply_policy!r}."
            )
        # ``always_reply`` ma sens tylko dla kanałów faktycznie nasłuchiwanych — para spoza
        # ``watch`` to najczęściej literówka (fail-fast zamiast cichej, martwej konfiguracji).
        stray = [pair for pair in self.always_reply if pair not in self.watch]
        if stray:
            raise ValueError(
                "WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY zawiera pary spoza WORKMATE_TEAMS_GRAPH_WATCH: "
                + ", ".join(f"{team}:{channel}" for team, channel in stray)
                + "."
            )


# Katalog roboczy agenta (ADR 0018): POZA repo i data/ — dane operacyjne/scratch, nie baza wiedzy.
_DEFAULT_WORKSPACE_DIR = Path.home() / ".workmate" / "workspace"
# Domyślna biała lista rozszerzeń: wyłącznie tekstowe. Wykonywalne (exe/bat/ps1/sh/py/js…) są
# świadomie poza listą — pliki tworzy niezaufany model z niezaufanych drzwi.
_DEFAULT_WORKSPACE_ALLOWED_EXT = ("md", "txt", "csv", "json")
# Twarda deny-lista: rozszerzenia wykonywalne/skryptowe NIGDY nie mogą być na białej liście,
# nawet gdy operator poda je w env — pliki tworzy niezaufany model z niezaufanych drzwi.
_DANGEROUS_WORKSPACE_EXT = frozenset(
    {"exe", "bat", "cmd", "com", "ps1", "sh", "py", "js", "vbs", "scr", "msi", "dll", "jar"}
)
_MAX_WORKSPACE_FILE_MB_CEILING = 24
_MAX_WORKSPACE_TOTAL_MB_CEILING = 200
_MAX_WORKSPACE_FILES_CEILING = 500


@dataclass(frozen=True)
class WorkspaceSettings:
    """Konfiguracja katalogu roboczego agenta (ADR 0018) — tworzenie plików per rozmowa.

    Osobna bramka ``enabled`` (``WORKMATE_ENABLE_WORKSPACE``), NIEZALEŻNA od ``enable_write``
    (notatki bazy wiedzy) — inny profil zaufania (scratch vs baza). Limity chronią przed DoS
    z niezaufanych drzwi; biała lista rozszerzeń wyklucza pliki wykonywalne.
    """

    enabled: bool = False
    workspace_dir: Path = _DEFAULT_WORKSPACE_DIR
    max_file_mb: int = 5
    max_files_per_scope: int = 50
    max_total_mb: int = 50
    allowed_ext: tuple[str, ...] = _DEFAULT_WORKSPACE_ALLOWED_EXT
    retention_days: int = 30  # TTL sprzątania katalogów rozmów bez aktywności

    @classmethod
    def from_env(cls) -> WorkspaceSettings:
        return cls(
            enabled=_bool_from_env("WORKMATE_ENABLE_WORKSPACE", default=False),
            workspace_dir=_path_from_env("WORKMATE_WORKSPACE_DIR", _DEFAULT_WORKSPACE_DIR),
            max_file_mb=_int_from_env("WORKMATE_WORKSPACE_MAX_FILE_MB", 5),
            max_files_per_scope=_int_from_env("WORKMATE_WORKSPACE_MAX_FILES", 50),
            max_total_mb=_int_from_env("WORKMATE_WORKSPACE_MAX_TOTAL_MB", 50),
            allowed_ext=tuple(
                e.lower().lstrip(".")
                for e in _list_from_env(
                    "WORKMATE_WORKSPACE_ALLOWED_EXT", _DEFAULT_WORKSPACE_ALLOWED_EXT
                )
            ),
            retention_days=_int_from_env("WORKMATE_WORKSPACE_RETENTION_DAYS", 30),
        )

    def validate(self, *, data_dir: Path) -> None:
        """Twardy błąd startu przy bezsensownych limitach albo złej lokalizacji katalogu roboczego.

        ``data_dir`` wstrzykiwany, by wymusić inwariant bezpieczeństwa z ADR 0018: katalog roboczy
        (scratch, pliki od niezaufanego modelu) MUSI leżeć POZA bazą wiedzy — inaczej poisoned
        artefakt trafiłby do notatek, które agent czyta (wzorzec ``TokenVerifier.from_file``).
        """
        resolved_ws = self.workspace_dir.resolve()
        resolved_data = data_dir.resolve()
        if resolved_ws == resolved_data or resolved_data in resolved_ws.parents:
            raise ValueError(
                f"WORKMATE_WORKSPACE_DIR nie może leżeć wewnątrz katalogu danych ({resolved_data}) "
                f"— katalog roboczy to scratch poza bazą wiedzy, jest: {resolved_ws}."
            )
        dangerous = set(self.allowed_ext) & _DANGEROUS_WORKSPACE_EXT
        if dangerous:
            raise ValueError(
                "WORKMATE_WORKSPACE_ALLOWED_EXT zawiera niedozwolone (wykonywalne) rozszerzenia: "
                f"{sorted(dangerous)}."
            )
        if not 1 <= self.max_file_mb <= _MAX_WORKSPACE_FILE_MB_CEILING:
            raise ValueError(
                "WORKMATE_WORKSPACE_MAX_FILE_MB musi być w zakresie "
                f"1..{_MAX_WORKSPACE_FILE_MB_CEILING}, jest: {self.max_file_mb}."
            )
        if not 1 <= self.max_total_mb <= _MAX_WORKSPACE_TOTAL_MB_CEILING:
            raise ValueError(
                "WORKMATE_WORKSPACE_MAX_TOTAL_MB musi być w zakresie "
                f"1..{_MAX_WORKSPACE_TOTAL_MB_CEILING}, jest: {self.max_total_mb}."
            )
        if not 1 <= self.max_files_per_scope <= _MAX_WORKSPACE_FILES_CEILING:
            raise ValueError(
                "WORKMATE_WORKSPACE_MAX_FILES musi być w zakresie "
                f"1..{_MAX_WORKSPACE_FILES_CEILING}, jest: {self.max_files_per_scope}."
            )
        if not self.allowed_ext:
            raise ValueError("WORKMATE_WORKSPACE_ALLOWED_EXT nie może być puste.")
        if self.retention_days < 1:
            raise ValueError(
                f"WORKMATE_WORKSPACE_RETENTION_DAYS musi być >= 1, jest: {self.retention_days}."
            )


# Domyślny stan pollera GitHub (watermark ``since``): POZA repo i data/ — dane operacyjne.
_DEFAULT_GITHUB_STATE = Path.home() / ".workmate" / "github_state.json"
# Dolny sufit interwału pollingu GitHub (świadomość limitu 5000 żądań/h uwierzytelnionych).
_GITHUB_POLL_FLOOR_S = 30
_MAX_GITHUB_PER_PAGE = 100
# Dozwolone rodzaje zdarzeń nasłuchiwanych w repo (ADR 0024: PR/CI/recenzje wchodzą opcjonalnie).
_ALLOWED_GITHUB_WATCH_KINDS = (
    "issues",
    "comments",
    "pulls",
    "reviews",
    "ci",
    "pull_state",
    "branches",
)
# Domyślny zestaw (wsteczna zgodność): tylko issue i komentarze; nowe rodzaje włącza się jawnie
# przez ``WORKMATE_GITHUB_WATCH_KINDS`` — patrz ADR 0024.
_DEFAULT_GITHUB_WATCH_KINDS = ("issues", "comments")

# Strojenie estymacji czasu z commitów (ADR 0034, część odczytowa). Pokrętła mieszkały w
# ``JiraSettings``, dopóki zdolność miała ścieżkę zapisu do Jiry; po jej wycięciu dotyczą
# WYŁĄCZNIE czytania commitów, więc stoją przy źródle danych. Twarde backstopy chronią przed
# absurdem wpisanym do ``.env``; egzekwuje je ``validate_worklog_limits`` — wołane przez poller
# i przez wpięcie drzwi Teams, czyli wszędzie tam, gdzie te wartości są w ogóle czytane.
MAX_WORKLOG_RANGE_DAYS = 92
_MAX_WORKLOG_SESSION_HOURS = 24.0
_MAX_WORKLOG_IDLE_GAP_MIN = 720
_MAX_WORKLOG_RAMP_UP_MIN = 240
# Ewidencja czasu jest kwantowana — dopuszczamy tylko dzielniki godziny mające sens w praktyce.
_ALLOWED_ROUND_MINUTES = (1, 5, 10, 15, 30, 60)


@dataclass(frozen=True)
class GithubSettings:
    """Konfiguracja drzwi GitHub w trybie DELEGOWANYM (ADR 0020) — polling repo przez PAT.

    Bot odpytuje GitHub REST tokenem osobistym (PAT), bez webhooka i publicznego endpointu.
    ``token`` to SEKRET (``repr=False``, env ``WORKMATE_GITHUB_TOKEN``) — nigdy w repo/``data/``.
    Zapis do GitHub jest OSOBNO bramkowany (``enable_github_write``, Gate 4 / ADR 0021),
    domyślnie wyłączony — drzwi startują read-only (ingest zdarzeń), zgodnie z ADR 0006.
    ``enable_ci_auto_comment`` (ADR 0024, domyślnie OFF) włącza JEDYNY autonomiczny zapis mostu —
    deterministyczny komentarz przy porażce CI na PR; wymaga też ``enable_github_write``.

    Pola ``worklog_*`` stroją ESTYMACJĘ czasu z commitów (ADR 0034) — czysty odczyt, bez bramki
    (odczyt jest domyślny, ADR 0006). ``worklog_tz`` to nazwa strefy IANA, nie offset: doba
    kalendarzowa dzieli sesje pracy, a stały offset mylił się o godzinę przez pół roku.
    """

    token: str = field(default="", repr=False)
    owner: str = ""
    repo: str = ""
    api_base: str = "https://api.github.com"
    poll_interval_s: int = 60
    per_page: int = 50
    watch_kinds: tuple[str, ...] = _DEFAULT_GITHUB_WATCH_KINDS
    enable_github_write: bool = False
    enable_ci_auto_comment: bool = False
    state_path: Path = _DEFAULT_GITHUB_STATE
    self_login: str = ""
    worklog_idle_gap_minutes: int = 90
    worklog_ramp_up_minutes: int = 30
    worklog_round_minutes: int = 15
    worklog_max_session_hours: float = 8.0
    worklog_max_range_days: int = 31
    worklog_tz: str = "Europe/Warsaw"

    @classmethod
    def from_env(cls) -> GithubSettings:
        return cls(
            token=os.environ.get("WORKMATE_GITHUB_TOKEN", ""),
            owner=os.environ.get("WORKMATE_GITHUB_OWNER", ""),
            repo=os.environ.get("WORKMATE_GITHUB_REPO", ""),
            api_base=os.environ.get("WORKMATE_GITHUB_API_BASE", "https://api.github.com"),
            poll_interval_s=_int_from_env("WORKMATE_GITHUB_POLL_INTERVAL", 60),
            per_page=_int_from_env("WORKMATE_GITHUB_PER_PAGE", 50),
            watch_kinds=_list_from_env("WORKMATE_GITHUB_WATCH_KINDS", _DEFAULT_GITHUB_WATCH_KINDS),
            enable_github_write=_bool_from_env("WORKMATE_GITHUB_ENABLE_WRITE", default=False),
            enable_ci_auto_comment=_bool_from_env(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT", default=False
            ),
            state_path=_path_from_env("WORKMATE_GITHUB_STATE", _DEFAULT_GITHUB_STATE),
            self_login=os.environ.get("WORKMATE_GITHUB_SELF_LOGIN", ""),
            worklog_idle_gap_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES", 90),
            worklog_ramp_up_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES", 30),
            worklog_round_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_ROUND_MINUTES", 15),
            worklog_max_session_hours=_float_from_env(
                "WORKMATE_GITHUB_WORKLOG_MAX_SESSION_HOURS", 8.0
            ),
            worklog_max_range_days=_int_from_env("WORKMATE_GITHUB_WORKLOG_MAX_RANGE_DAYS", 31),
            worklog_tz=os.environ.get("WORKMATE_GITHUB_WORKLOG_TZ", "Europe/Warsaw").strip(),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak tożsamości repo/tokenu albo bezsensowne limity."""
        self.validate_worklog_limits()
        missing = [
            name
            for name, value in (
                ("WORKMATE_GITHUB_TOKEN", self.token),
                ("WORKMATE_GITHUB_OWNER", self.owner),
                ("WORKMATE_GITHUB_REPO", self.repo),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi GitHub wymagają tokenu i repo: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )
        if self.poll_interval_s < _GITHUB_POLL_FLOOR_S:
            raise ValueError(
                f"WORKMATE_GITHUB_POLL_INTERVAL musi być >= {_GITHUB_POLL_FLOOR_S} "
                f"(limit API GitHub), jest: {self.poll_interval_s}."
            )
        if not 1 <= self.per_page <= _MAX_GITHUB_PER_PAGE:
            raise ValueError(
                "WORKMATE_GITHUB_PER_PAGE musi być w zakresie "
                f"1..{_MAX_GITHUB_PER_PAGE}, jest: {self.per_page}."
            )
        unknown = [k for k in self.watch_kinds if k not in _ALLOWED_GITHUB_WATCH_KINDS]
        if unknown:
            raise ValueError(
                "WORKMATE_GITHUB_WATCH_KINDS zawiera nieznane rodzaje: "
                f"{unknown}. Dozwolone: {', '.join(_ALLOWED_GITHUB_WATCH_KINDS)}."
            )
        if not self.watch_kinds:
            raise ValueError("WORKMATE_GITHUB_WATCH_KINDS nie może być puste.")
        # Recenzje odpytujemy per-PR, a kandydatów (otwarte PR) odkrywamy z ``/issues`` — bez
        # „issues"/„pulls" nie byłoby skąd; odrzucamy cichą, funkcjonalnie martwą konfigurację.
        if "reviews" in self.watch_kinds and not (
            "issues" in self.watch_kinds or "pulls" in self.watch_kinds
        ):
            raise ValueError(
                "WORKMATE_GITHUB_WATCH_KINDS='reviews' wymaga też 'issues' lub 'pulls' "
                "(otwarte PR do odpytania o recenzje odkrywamy z endpointu /issues)."
            )
        # Auto-komentarz CI to ZAPIS do GitHub — bez ogólnej bramki zapisu byłby martwy (nic nie
        # dopisze), a użytkownik myślałby, że działa; odrzucamy tę cichą, sprzeczną konfigurację.
        if self.enable_ci_auto_comment and not self.enable_github_write:
            raise ValueError(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true wymaga też "
                "WORKMATE_GITHUB_ENABLE_WRITE=true (auto-komentarz dopisuje na GitHub)."
            )
        # …a także musi mieć CO komentować: bez „ci" w WATCH_KINDS poller nie pobiera przebiegów CI,
        # więc auto-komentarz nigdy nie zobaczy porażki do skomentowania (ta sama klasa cichej,
        # funkcjonalnie martwej konfiguracji co powyżej — odrzucamy fail-fast).
        if self.enable_ci_auto_comment and "ci" not in self.watch_kinds:
            raise ValueError(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true wymaga też 'ci' w "
                "WORKMATE_GITHUB_WATCH_KINDS (bez zdarzeń CI nie ma czego komentować)."
            )

    def validate_worklog_limits(self) -> None:
        """Strojenie estymacji czasu (ADR 0034) — kontrole NIEZALEŻNE od tokenu i repozytorium.

        Wydzielone z ``validate()`` z tego samego powodu co ``JiraSettings.validate_limits``:
        pełnego ``validate()`` nie da się zawołać z drzwi Teams, bo bezwarunkowo żąda tokenu
        i repo, więc wywróciłoby każde wdrożenie bez GitHuba. Sufity muszą jednak obowiązywać
        po stronie, która faktycznie liczy estymację — inaczej absurd z ``.env`` egzekwowałby
        wyłącznie proces pollera, czyli nie ten, który go używa.
        """
        if not 5 <= self.worklog_idle_gap_minutes <= _MAX_WORKLOG_IDLE_GAP_MIN:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES musi być w zakresie "
                f"5..{_MAX_WORKLOG_IDLE_GAP_MIN}, jest: {self.worklog_idle_gap_minutes}."
            )
        if not 0 <= self.worklog_ramp_up_minutes <= _MAX_WORKLOG_RAMP_UP_MIN:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES musi być w zakresie "
                f"0..{_MAX_WORKLOG_RAMP_UP_MIN}, jest: {self.worklog_ramp_up_minutes}."
            )
        # Rozbieg dłuższy niż przerwa kończąca sesję dawałby estymacje NACHODZĄCE na siebie
        # (doliczony czas sprzed sesji sięgałby w poprzednią) — cichy bezsens, więc odrzucamy.
        if self.worklog_ramp_up_minutes > self.worklog_idle_gap_minutes:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES nie może przekraczać "
                "WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES (estymacje sesji zachodziłyby "
                f"na siebie): {self.worklog_ramp_up_minutes} > {self.worklog_idle_gap_minutes}."
            )
        if self.worklog_round_minutes not in _ALLOWED_ROUND_MINUTES:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_ROUND_MINUTES musi być jedną z "
                f"{_ALLOWED_ROUND_MINUTES}, jest: {self.worklog_round_minutes}."
            )
        if not 0 < self.worklog_max_session_hours <= _MAX_WORKLOG_SESSION_HOURS:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_MAX_SESSION_HOURS musi być w zakresie "
                f"0..{_MAX_WORKLOG_SESSION_HOURS}, jest: {self.worklog_max_session_hours}."
            )
        if not 1 <= self.worklog_max_range_days <= MAX_WORKLOG_RANGE_DAYS:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_MAX_RANGE_DAYS musi być w zakresie "
                f"1..{MAX_WORKLOG_RANGE_DAYS}, jest: {self.worklog_max_range_days}."
            )
        # Nazwę strefy sprawdzamy próbą zbudowania ``ZoneInfo``: literówka (``Europe/Warszawa``)
        # inaczej wywróciłaby pierwsze wywołanie narzędzia, a nie start procesu.
        try:
            ZoneInfo(self.worklog_tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"WORKMATE_GITHUB_WORKLOG_TZ={self.worklog_tz!r} nie jest znaną strefą IANA "
                "(np. 'Europe/Warsaw')."
            ) from exc


# Warianty wdrożenia Jiry (ADR 0033): ``server`` = Server/Data Center (PAT Bearer, REST v2,
# paginacja startAt); ``cloud`` = Jira Cloud (Basic email+API-token, REST v3, search/jql).
# Domyślnie ``server`` — wstecznie zgodne.
JIRA_DEPLOYMENTS = ("server", "cloud")


@dataclass(frozen=True)
class JiraSettings:
    """Konfiguracja odczytu Jiry (Server/DC lub Cloud, ADR 0030/0033/0054) — WYŁĄCZNIE odczyt.

    ``deployment`` wybiera wariant (ADR 0033): ``server`` (Server/Data Center — PAT Bearer, REST v2)
    lub ``cloud`` (Jira Cloud — Basic ``email``+API-token, REST v3, ``search/jql``). Domyślnie
    ``server`` (wstecznie zgodne). ``email`` jest wymagany tylko na Cloud (konto Basic-auth); na
    Server/DC pozostaje pusty. ``token`` to SEKRET (``repr=False``, env ``WORKMATE_JIRA_TOKEN`` —
    PAT na Server/DC, API token na Cloud) — nigdy w repo/``data/``.

    Most push/ingest (ADR 0030) i zapis/tranzycja (ADR 0031/0032) zostały USUNIĘTE (ADR 0054) —
    jedyna zdolność to "moje zadania": ``my_account`` (login/e-mail/accountId JEDNEGO, z góry
    skonfigurowanego operatora) zasila narzędzie na SERWERZE MCP (stdio, Claude Code/CLI — brak
    tożsamości Teams; patrz ``server._my_jira_tasks_service_if_present``). Na drzwiach Teams
    tożsamość rozwiązuje się inaczej — z mapy AAD→Jira (``teams_graph.app``), niezależnie od tego
    pola.
    """

    base_url: str = ""
    token: str = field(default="", repr=False)
    deployment: str = "server"
    email: str = ""
    my_account: str = ""

    @classmethod
    def from_env(cls) -> JiraSettings:
        return cls(
            base_url=os.environ.get("WORKMATE_JIRA_BASE_URL", "").rstrip("/"),
            token=os.environ.get("WORKMATE_JIRA_TOKEN", ""),
            deployment=os.environ.get("WORKMATE_JIRA_DEPLOYMENT", "server").strip().lower(),
            email=os.environ.get("WORKMATE_JIRA_EMAIL", "").strip(),
            my_account=os.environ.get("WORKMATE_JIRA_MY_ACCOUNT", "").strip(),
        )

    def validate_limits(self) -> None:
        """Kontrola wariantu wdrożenia — NIEZALEŻNA od tego, kto buduje klienta Jiry."""
        deployment = self.deployment.strip().lower()
        if deployment not in JIRA_DEPLOYMENTS:
            raise ValueError(
                "WORKMATE_JIRA_DEPLOYMENT musi być 'server' lub 'cloud', jest: "
                f"{self.deployment!r}."
            )
        # Cloud uwierzytelnia się Basic auth (email + API token); Server/DC — PAT Bearer (bez
        # e-maila). Brak e-maila na Cloud = niedziałające auth — twardy błąd startu (fail-fast).
        if deployment == "cloud" and not self.email:
            raise ValueError(
                "WORKMATE_JIRA_DEPLOYMENT=cloud wymaga WORKMATE_JIRA_EMAIL "
                "(e-mail konta Atlassian do Basic-auth z API tokenem)."
            )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak URL/tokenu albo zły wariant wdrożenia."""
        self.validate_limits()
        missing = [
            name
            for name, value in (
                ("WORKMATE_JIRA_BASE_URL", self.base_url),
                ("WORKMATE_JIRA_TOKEN", self.token),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Odczyt Jiry wymaga URL i tokenu: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )


# Zakresy delegowane proaktywnego push do Teams (ADR 0022): tworzenie/pisanie czatu 1:1 oraz
# wysyłka na kanał. MSAL dokłada offline_access/openid/profile sam (nie wpisujemy ich).
_DEFAULT_TEAMS_PUSH_SCOPES = (
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "ChannelMessage.Send",
    "User.Read",
    # Lista członków zespołu dla drzwi kart czasu (ADR 0035). NIE wymaga nowej zgody admina:
    # to ta sama rejestracja aplikacji i ten sam cache MSAL co ``Powiadomienia_teams``, gdzie
    # scope jest skonsentowany od 2026-07-14 — tu po prostu też o niego prosimy.
    "TeamMember.Read.All",
)


@dataclass(frozen=True)
class TeamsPushSettings:
    """Konfiguracja proaktywnego push do Teams (dual-target, ADR 0022) — notifier zdarzeń → Teams.

    Tożsamość = zalogowany użytkownik (device-code MSAL, jak ``teams_graph``); może współdzielić
    ten sam ``token_cache_path`` (jedno logowanie). Sam obiekt nie trzyma sekretu (sekretem jest
    CACHE tokenu na dysku). OBA cele są konfigurowalne (decyzja użytkownika): czat 1:1 i kanał —
    włączane niezależnie flagami ``enable_chat``/``enable_channel``. Gdy oba wyłączone, notifier
    nie startuje (drzwi GitHub działają wtedy jako ingest-only). ``enable_channel_threading``
    (ADR 0024, domyślnie OFF) dokłada zdarzenia tego samego issue/PR do JEDNEGO wątku na kanale
    (zamiast nowego roota za każdym razem); wymaga włączonego celu kanału.
    """

    client_id: str = ""
    tenant_id: str = ""
    scopes: tuple[str, ...] = _DEFAULT_TEAMS_PUSH_SCOPES
    token_cache_path: Path = _DEFAULT_TEAMS_GRAPH_CACHE
    chat_user_id: str = ""
    team_id: str = ""
    channel_id: str = ""
    enable_chat: bool = False
    enable_channel: bool = False
    enable_channel_threading: bool = False

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def enabled(self) -> bool:
        """Czy notifier ma w ogóle wystartować (włączony co najmniej jeden cel)."""
        return self.enable_chat or self.enable_channel

    @classmethod
    def from_env(cls) -> TeamsPushSettings:
        return cls(
            client_id=os.environ.get("WORKMATE_TEAMS_PUSH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_PUSH_TENANT_ID", ""),
            scopes=_list_from_env("WORKMATE_TEAMS_PUSH_SCOPES", _DEFAULT_TEAMS_PUSH_SCOPES),
            token_cache_path=_path_from_env(
                "WORKMATE_TEAMS_PUSH_TOKEN_CACHE", _DEFAULT_TEAMS_GRAPH_CACHE
            ),
            chat_user_id=os.environ.get("WORKMATE_TEAMS_PUSH_CHAT_USER_ID", ""),
            team_id=os.environ.get("WORKMATE_TEAMS_PUSH_TEAM_ID", ""),
            channel_id=os.environ.get("WORKMATE_TEAMS_PUSH_CHANNEL_ID", ""),
            enable_chat=_bool_from_env("WORKMATE_TEAMS_PUSH_ENABLE_CHAT", default=False),
            enable_channel=_bool_from_env("WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL", default=False),
            enable_channel_threading=_bool_from_env(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING", default=False
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy włączony cel jest niekompletny (brak id celu/aplikacji).

        Gdy notifier wyłączony (żaden cel), nie wymagamy niczego — drzwi GitHub są ingest-only.
        """
        if not self.enabled:
            return
        if not self.client_id or not self.tenant_id:
            raise ValueError(
                "Proaktywny push do Teams wymaga tożsamości aplikacji: ustaw "
                "WORKMATE_TEAMS_PUSH_CLIENT_ID i WORKMATE_TEAMS_PUSH_TENANT_ID."
            )
        if self.enable_chat and not self.chat_user_id:
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHAT wymaga WORKMATE_TEAMS_PUSH_CHAT_USER_ID "
                "(AAD user id adresata)."
            )
        if self.enable_channel and (not self.team_id or not self.channel_id):
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL wymaga WORKMATE_TEAMS_PUSH_TEAM_ID "
                "i WORKMATE_TEAMS_PUSH_CHANNEL_ID."
            )
        # Wątkowanie dotyczy WYŁĄCZNIE kanału (czat 1:1 nie ma wątków) — bez celu kanału byłoby
        # martwe; odrzucamy cichą, sprzeczną konfigurację (ADR 0024, Faza 3).
        if self.enable_channel_threading and not self.enable_channel:
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING wymaga "
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL=true (wątki są tylko na kanale)."
            )


# --- grafik zmian z Teams Shifts (ADR 0056) -----------------------------------
# Zespół „BIAP – Pion Inteligentnych Technologii" — jedyny w tenancie z działającym grafikiem.
_DEFAULT_SCHEDULE_TEAM_ID = "c0ffee00-0000-4000-8000-000000000007"
# Cudzy cache MSAL bota powiadomienia-teams — montowany RO, czytany po cichu, NIGDY pisany.
_DEFAULT_SCHEDULE_CACHE = Path("/var/lib/powiadomienia-teams/teams_token_cache.bin")
_DEFAULT_SCHEDULE_TZ = "Europe/Warsaw"
# Zakresy delegowane grafiku: odczyt grafiku + lista członków zespołu (translacja userId→nazwisko).
# Ta sama rejestracja aplikacji co push/powiadomienia-teams (TeamMember.Read.All skonsentowany).
_DEFAULT_SCHEDULE_SCOPES = ("Schedule.Read.All", "TeamMember.Read.All")


@dataclass(frozen=True)
class ScheduleSettings:
    """Konfiguracja grafiku Teams Shifts (ADR 0056) — WYŁĄCZNIE odczyt, cichy token z cudzego cache.

    Tożsamość pożyczamy z cache MSAL bota powiadomienia-teams (ta sama rejestracja aplikacji co
    ``TeamsPushSettings``): ``client_id``/``tenant_id`` domyślnie SPADAJĄ na
    ``WORKMATE_TEAMS_PUSH_*``, żeby nie duplikować konfiguracji. Cache jest montowany RO i NIGDY nie
    zapisywany. ``enabled`` = ``auto`` (domyślnie): włącz, gdy jest client_id + tenant_id + istnieje
    plik cache — zero konfiguracji tam, gdzie mont jest, ciche wyłączenie tam, gdzie go nie ma.
    ``true``/``false`` wymuszają stan.
    """

    client_id: str = ""
    tenant_id: str = ""
    team_id: str = _DEFAULT_SCHEDULE_TEAM_ID
    token_cache_path: Path = _DEFAULT_SCHEDULE_CACHE
    timezone: str = _DEFAULT_SCHEDULE_TZ
    scopes: tuple[str, ...] = _DEFAULT_SCHEDULE_SCOPES
    enabled: str = "auto"  # "auto" | "true" | "false"

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    def is_enabled(self) -> bool:
        """Czy narzędzie grafiku ma w ogóle powstać (patrz semantyka ``enabled``)."""
        mode = self.enabled.strip().lower()
        if mode == "false":
            return False
        if mode == "true":
            return True
        # auto: aplikacja skonfigurowana ORAZ cudzy cache tokenu jest zamontowany.
        return bool(self.client_id and self.tenant_id and self.token_cache_path.is_file())

    @classmethod
    def from_env(cls) -> ScheduleSettings:
        return cls(
            # Fallback na push app: ta sama rejestracja i ten sam cache MSAL (jedno logowanie).
            client_id=os.environ.get("WORKMATE_SCHEDULE_CLIENT_ID")
            or os.environ.get("WORKMATE_TEAMS_PUSH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_SCHEDULE_TENANT_ID")
            or os.environ.get("WORKMATE_TEAMS_PUSH_TENANT_ID", ""),
            team_id=os.environ.get("WORKMATE_SCHEDULE_TEAM_ID", _DEFAULT_SCHEDULE_TEAM_ID).strip(),
            token_cache_path=_path_from_env(
                "WORKMATE_SCHEDULE_TOKEN_CACHE", _DEFAULT_SCHEDULE_CACHE
            ),
            timezone=os.environ.get("WORKMATE_SCHEDULE_TZ", _DEFAULT_SCHEDULE_TZ).strip(),
            scopes=_list_from_env("WORKMATE_SCHEDULE_SCOPES", _DEFAULT_SCHEDULE_SCOPES),
            enabled=os.environ.get("WORKMATE_SCHEDULE_ENABLED", "auto").strip().lower(),
        )

    def validate(self) -> None:
        """Kontrola strefy czasowej — ZAWSZE (jak digest). Reszta jest miękka (auto-wyłączenie)."""
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError(
                f"WORKMATE_SCHEDULE_TZ={self.timezone!r} nie jest znaną strefą czasową "
                "(na Windows wymaga pakietu 'tzdata')."
            ) from exc


# --- proaktywny cotygodniowy digest zmian (ADR 0053, F6) ----------------------
_DEFAULT_TEAMS_DIGEST_STATE = Path.home() / ".workmate" / "teams_digest_state.json"
_DEFAULT_TEAMS_DIGEST_TZ = "Europe/Warsaw"
MAX_TEAMS_DIGEST_CATCHUP_DAYS = 14


@dataclass(frozen=True)
class TeamsDigestSettings:
    """Konfiguracja drzwi proaktywnego digestu tygodniowego (ADR 0053, F6).

    Przebieg: w ``run_weekday`` (domyślnie poniedziałek) o ``run_hour`` składa digest zmian z
    ostatnich ``window_days`` (reuse ``ChangeDigestService``, F5) i wysyła go PRYWATNĄ wiadomością
    do każdego odbiorcy z jawnej listy ``recipients``. Dwustopniowa bramka: domyślnie WYŁĄCZONY
    (``enabled``) i dodatkowo PRÓBNY (``dry_run``) — przebieg renderuje i loguje digest, ale nie
    wysyła ani nie zapisuje stanu. Wysyłka realna wymaga ``enabled=true`` ORAZ ``dry_run=false``.

    Odbiorcy to JAWNA lista AAD user id (nie mapa pionu) — proaktywny DM to świadomy wybór
    audytorium, który nie może po cichu urosnąć. Tożsamości Graph (token push) tu nie ma —
    egzekwuje je wiring drzwi (``TeamsPushSettings``), jak w ``worklogi``.
    """

    enabled: bool = False
    dry_run: bool = True
    recipients: tuple[str, ...] = ()
    run_weekday: int = 0  # poniedziałek (0=poniedziałek, jak worklogi)
    run_hour: int = 8
    run_minute: int = 0
    window_days: int = 7
    tz_name: str = _DEFAULT_TEAMS_DIGEST_TZ
    state_path: Path = _DEFAULT_TEAMS_DIGEST_STATE
    max_catchup_days: int = 3

    @classmethod
    def from_env(cls) -> TeamsDigestSettings:
        return cls(
            enabled=_bool_from_env("WORKMATE_TEAMS_DIGEST_ENABLED", default=False),
            dry_run=_bool_from_env("WORKMATE_TEAMS_DIGEST_DRY_RUN", default=True),
            recipients=_list_from_env("WORKMATE_TEAMS_DIGEST_RECIPIENTS", ()),
            run_weekday=_int_from_env("WORKMATE_TEAMS_DIGEST_RUN_WEEKDAY", 0),
            run_hour=_int_from_env("WORKMATE_TEAMS_DIGEST_RUN_HOUR", 8),
            run_minute=_int_from_env("WORKMATE_TEAMS_DIGEST_RUN_MINUTE", 0),
            window_days=_int_from_env("WORKMATE_TEAMS_DIGEST_WINDOW_DAYS", 7),
            tz_name=os.environ.get("WORKMATE_TEAMS_DIGEST_TZ", _DEFAULT_TEAMS_DIGEST_TZ).strip(),
            state_path=_path_from_env("WORKMATE_TEAMS_DIGEST_STATE", _DEFAULT_TEAMS_DIGEST_STATE),
            max_catchup_days=_int_from_env("WORKMATE_TEAMS_DIGEST_MAX_CATCHUP_DAYS", 3),
        )

    def validate(self) -> None:
        """Twardy błąd startu przy absurdach; zakresy i strefę sprawdzamy ZAWSZE (jak worklogi)."""
        try:
            ZoneInfo(self.tz_name)
        except Exception as exc:
            raise ValueError(
                f"WORKMATE_TEAMS_DIGEST_TZ={self.tz_name!r} nie jest znaną strefą czasową "
                "(na Windows wymaga pakietu 'tzdata')."
            ) from exc
        if not 0 <= self.run_weekday <= 6:
            raise ValueError(
                f"WORKMATE_TEAMS_DIGEST_RUN_WEEKDAY musi być 0..6 (pon.=0), jest {self.run_weekday}"
            )
        if not 0 <= self.run_hour <= 23:
            raise ValueError(
                f"WORKMATE_TEAMS_DIGEST_RUN_HOUR musi być 0..23, jest: {self.run_hour}."
            )
        if not 0 <= self.run_minute <= 59:
            raise ValueError(
                f"WORKMATE_TEAMS_DIGEST_RUN_MINUTE musi być 0..59, jest: {self.run_minute}."
            )
        if self.window_days < 1:
            raise ValueError(
                f"WORKMATE_TEAMS_DIGEST_WINDOW_DAYS musi być >= 1, jest: {self.window_days}."
            )
        if self.max_catchup_days < 0 or self.max_catchup_days > MAX_TEAMS_DIGEST_CATCHUP_DAYS:
            raise ValueError(
                "WORKMATE_TEAMS_DIGEST_MAX_CATCHUP_DAYS musi być 0.."
                f"{MAX_TEAMS_DIGEST_CATCHUP_DAYS}, jest: {self.max_catchup_days}."
            )
        if not self.enabled:
            return
        # Bramka ON bez odbiorców = drzwi, które nie mają do kogo wysłać → fail-fast (nie cicha
        # martwa bramka). Odbiorcy to świadoma, jawna lista (ADR 0053).
        if not self.recipients:
            raise ValueError(
                "WORKMATE_TEAMS_DIGEST_ENABLED=true wymaga WORKMATE_TEAMS_DIGEST_RECIPIENTS "
                "(lista AAD user id oddzielona przecinkami) — bez niej digest nie ma adresata."
            )
