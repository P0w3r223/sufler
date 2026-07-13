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

# Dozwolone transporty serwera MCP. "stdio" to lokalny tryb dla Claude Code
# (Fazy 1 tyg. 1-3); "streamable-http" to wdrożenie sieciowe (tyg. 4, Bramka 3).
_ALLOWED_TRANSPORTS = ("stdio", "streamable-http")

# Domyślny magazyn tokenów drzwi HTTP (ADR 0007): POZA repo i poza data/, żeby
# sekrety były poza zasięgiem narzędzi. Nadpisywalny przez WORKMATE_TOKENS_FILE.
_DEFAULT_TOKENS_FILE = Path("C:/ProgramData/WorkMate/tokens.json")

# Domyślna baza rozmów (SQLite, ADR 0010): POZA repo i poza data/ — to dane
# operacyjne (historia czatu), nie baza wiedzy. Katalog domowy (pisemny bez
# uprawnień administratora — drzwi lokalne). Nadpisywalna przez WORKMATE_CONVERSATIONS_DB.
_DEFAULT_CONVERSATIONS_DB = Path.home() / ".workmate" / "conversations.db"

# Domyślne allowed_hosts trybu HTTP: wyłącznie loopback. Wdrożenie za IIS MUSI
# dołożyć publiczny host (np. "workmate.firma.pl:*") przez WORKMATE_ALLOWED_HOSTS —
# inaczej realny nagłówek Host daje 421 (ochrona przed DNS-rebinding).
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


def _find_repo_root(start: Path) -> Path:
    """Znajdź korzeń repozytorium, idąc w górę do katalogu z ``pyproject.toml``.

    Pozwala uruchamiać serwer niezależnie od bieżącego katalogu roboczego
    (np. przez ``uv run`` z dowolnego miejsca), bez zaszywania ścieżek w kodzie.
    """
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


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


@dataclass(frozen=True)
class Settings:
    """Niemutowalny zestaw ustawień serwera."""

    data_dir: Path
    notes_dir: Path
    projects_registry: Path
    transport: str
    log_level: str
    # Profil uprawnień per drzwi (Bramka 2, ADR 0006): czy narzędzie zapisu
    # (save_note) jest wystawione. Lokalne drzwi dev domyślnie ufane (True);
    # mniej zaufane drzwi (przyszły Teams/GitHub) ustawiają False.
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
            enable_write=_bool_from_env("WORKMATE_ENABLE_WRITE", default=True),
            tokens_file=_path_from_env("WORKMATE_TOKENS_FILE", _DEFAULT_TOKENS_FILE),
            bind_host=os.environ.get("WORKMATE_BIND_HOST", "127.0.0.1"),
            bind_port=_int_from_env("WORKMATE_BIND_PORT", 8000),
            allowed_hosts=_list_from_env("WORKMATE_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS),
            allowed_origins=_list_from_env("WORKMATE_ALLOWED_ORIGINS", ()),
            tls_certfile=_optional_path_from_env("WORKMATE_TLS_CERTFILE"),
            tls_keyfile=_optional_path_from_env("WORKMATE_TLS_KEYFILE"),
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
            raise ValueError(
                f"WORKMATE_AGENT_MAX_TOKENS musi być >= 1, jest: {self.max_tokens}."
            )
        if self.thinking_type not in ("adaptive", "disabled"):
            raise ValueError(
                "WORKMATE_AGENT_THINKING musi być 'adaptive' albo 'disabled', jest: "
                f"{self.thinking_type!r}."
            )


@dataclass(frozen=True)
class TelegramSettings:
    """Konfiguracja drzwi Telegram (Faza 2, spike echo) — long polling, bez tunelu.

    Token bota to sekret (``repr=False``) — wyłącznie z env
    ``WORKMATE_TELEGRAM_BOT_TOKEN`` (od @BotFather); nigdy w repo. ``validate`` to
    twardy błąd startu, gdy brak — lepiej nie ruszać bez tokenu niż wołać API z pustym.
    """

    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback nie ujawniło tokenu.
    bot_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> TelegramSettings:
        return cls(bot_token=os.environ.get("WORKMATE_TELEGRAM_BOT_TOKEN", ""))

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak tokenu — nie wołamy API Telegrama z pustym."""
        if not self.bot_token:
            raise ValueError(
                "Drzwi Telegram wymagają WORKMATE_TELEGRAM_BOT_TOKEN (token z @BotFather) "
                "w środowisku/.env."
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
            db_path=_path_from_env(
                "WORKMATE_CONVERSATIONS_DB", _DEFAULT_CONVERSATIONS_DB
            ),
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
                "WORKMATE_CONV_MAX_TOKENS musi być >= 1, jest: "
                f"{self.max_context_tokens}."
            )
        # 0 = kryterium bezczynności wyłączone; ujemne nie ma sensu (fail fast).
        if self.idle_timeout_minutes < 0:
            raise ValueError(
                "WORKMATE_CONV_IDLE_MINUTES musi być >= 0 (0 wyłącza), jest: "
                f"{self.idle_timeout_minutes}."
            )
        if self.context_window_tokens < 1:
            raise ValueError(
                "WORKMATE_CONTEXT_WINDOW_TOKENS musi być >= 1, jest: "
                f"{self.context_window_tokens}."
            )
        if not 0.0 < self.compaction_threshold_fraction <= 1.0:
            raise ValueError(
                "WORKMATE_COMPACTION_THRESHOLD_FRACTION musi być w (0, 1], jest: "
                f"{self.compaction_threshold_fraction}."
            )
        if self.compaction_keep_turns < 1:
            raise ValueError(
                "WORKMATE_COMPACTION_KEEP_TURNS musi być >= 1, jest: "
                f"{self.compaction_keep_turns}."
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
    max_attachments_per_message: int = 5
    max_total_attachment_mb: int = 20  # sumaryczny budżet base64 — chroni sufit 32 MB żądania API
    max_extract_mb: int = 50  # sufit pliku ekstrahowanego do tekstu (docx/xlsx/pptx/txt)
    max_image_edge_px: int = 2048  # dłuższa krawędź obrazu (px) — powyżej downscaling

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @classmethod
    def from_env(cls) -> TeamsGraphSettings:
        return cls(
            client_id=os.environ.get("WORKMATE_TEAMS_GRAPH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_GRAPH_TENANT_ID", ""),
            scopes=_list_from_env(
                "WORKMATE_TEAMS_GRAPH_SCOPES", _DEFAULT_TEAMS_GRAPH_SCOPES
            ),
            token_cache_path=_path_from_env(
                "WORKMATE_TEAMS_GRAPH_TOKEN_CACHE", _DEFAULT_TEAMS_GRAPH_CACHE
            ),
            state_path=_path_from_env(
                "WORKMATE_TEAMS_GRAPH_STATE", _DEFAULT_TEAMS_GRAPH_STATE
            ),
            watch=_parse_watch_pairs(os.environ.get("WORKMATE_TEAMS_GRAPH_WATCH", "")),
            poll_interval_s=_int_from_env("WORKMATE_TEAMS_GRAPH_POLL_INTERVAL", 10),
            top_roots=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_ROOTS", 20),
            top_replies=_int_from_env("WORKMATE_TEAMS_GRAPH_TOP_REPLIES", 50),
            active_idle_hours=_int_from_env("WORKMATE_TEAMS_GRAPH_ACTIVE_IDLE_HOURS", 24),
            max_attachment_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENT_MB", 8),
            max_attachments_per_message=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MAX_ATTACHMENTS", 5
            ),
            max_total_attachment_mb=_int_from_env(
                "WORKMATE_TEAMS_GRAPH_MAX_TOTAL_ATTACHMENT_MB", 20
            ),
            max_extract_mb=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_EXTRACT_MB", 50),
            max_image_edge_px=_int_from_env("WORKMATE_TEAMS_GRAPH_MAX_IMAGE_EDGE", 2048),
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
                "WORKMATE_TEAMS_GRAPH_POLL_INTERVAL musi być >= 1, jest: "
                f"{self.poll_interval_s}."
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
