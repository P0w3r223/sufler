"""Centralna, typowana konfiguracja serwera.

Wszystkie wartości pochodzą ze zmiennych środowiskowych (patrz ``.env.example``)
i mają sensowne wartości domyślne wyznaczane względem korzenia repozytorium.
Dzięki temu ``python -m workmate`` działa bez żadnej konfiguracji, a wdrożenie
może nadpisać ścieżki pojedynczą zmienną środowiskową.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Dozwolone transporty serwera MCP. "stdio" to lokalny tryb dla Claude Code
# (Fazy 1 tyg. 1-3); "streamable-http" to wdrożenie sieciowe (tyg. 4, Bramka 3).
_ALLOWED_TRANSPORTS = ("stdio", "streamable-http")

# Domyślny magazyn tokenów drzwi HTTP (ADR 0007): POZA repo i poza data/, żeby
# sekrety były poza zasięgiem narzędzi. Nadpisywalny przez WORKMATE_TOKENS_FILE.
_DEFAULT_TOKENS_FILE = Path("C:/ProgramData/WorkMate/tokens.json")

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
    """Konfiguracja runtime'u agenta (Faza 2, M1 / ADR 0008).

    Klucz Claude API to sekret — czytany z env, nigdy z repo ani z folderu
    indeksowanego przez rdzeń (``data/``). Domyślny model to ``claude-sonnet-5``
    (większe wymagania projektu wobec syntezy), nadpisywalny przez ``WORKMATE_AGENT_MODEL``.
    """

    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback go nie ujawniło.
    api_key: str = field(default="", repr=False)
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    max_tool_iterations: int = 8

    @classmethod
    def from_env(cls) -> AgentSettings:
        # Priorytet: WORKMATE_AGENT_API_KEY (jawnie dla WorkMate) > ANTHROPIC_API_KEY (nazwa SDK).
        api_key = os.environ.get("WORKMATE_AGENT_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        return cls(
            api_key=api_key,
            model=os.environ.get("WORKMATE_AGENT_MODEL", "claude-sonnet-5"),
            max_tokens=_int_from_env("WORKMATE_AGENT_MAX_TOKENS", 4096),
            max_tool_iterations=_int_from_env("WORKMATE_AGENT_MAX_TOOL_ITERATIONS", 8),
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
