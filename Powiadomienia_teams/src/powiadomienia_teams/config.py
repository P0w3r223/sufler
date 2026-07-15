"""Ustawienia projektu — frozen dataclass + from_env() + validate().

Wzorzec jak w WorkMate (`src/workmate/config.py`). Prefiks zmiennych: `POWIADOMIENIA_`.
Sekrety (klucz Claude) mają `repr=False`. Domyślnie `dry_run=True` — nic nie wysyła ani
nie zapisuje, dopóki nie zostanie jawnie wyłączone.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

_PREFIX = "POWIADOMIENIA_"

# Delegowane scope Graph — wszystkie nadane i potwierdzone na żywo (Smoke #1, 2026-07-14).
# MSAL sam dokłada openid/profile/offline_access — nie wpisywać ich tutaj.
# roster.py (YAML) zostaje jako opcjonalny fallback, gdyby TeamMember.Read.All było niedostępne.
_DEFAULT_SCOPES: tuple[str, ...] = (
    "User.Read",
    "User.ReadBasic.All",
    "TeamMember.Read.All",
    "Schedule.Read.All",
    "Schedule.ReadWrite.All",
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
)

_DEFAULT_TOKEN_CACHE = Path.home() / ".workmate" / "teams_token_cache.bin"
_DEFAULT_STATE = Path.home() / ".workmate" / "powiadomienia_state.json"


class ConfigError(ValueError):
    """Brak lub niepoprawna wartość wymaganego ustawienia."""


def _get(name: str, default: str = "") -> str:
    return os.environ.get(_PREFIX + name, default)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "tak"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{_PREFIX}{name} musi być liczbą całkowitą: {raw!r}") from exc


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(_PREFIX + name)
    return Path(raw).expanduser() if raw else default


def _list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(_PREFIX + name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    client_id: str
    tenant_id: str
    team_id: str
    scheduling_group_id: str | None = None
    scopes: tuple[str, ...] = _DEFAULT_SCOPES
    token_cache_path: Path = field(default_factory=lambda: _DEFAULT_TOKEN_CACHE)
    state_path: Path = field(default_factory=lambda: _DEFAULT_STATE)
    roster_path: Path | None = None
    run_weekday: int = 6  # niedziela
    run_hour: int = 16
    run_minute: int = 0
    timezone: str = "Europe/Warsaw"
    reply_window_hours: int = 48
    dry_run: bool = True
    only_user_ids: tuple[str, ...] = ()  # pusty = wszyscy; ustawiony = tryb pilotażowy
    llm_model: str = "claude-opus-4-8"
    anthropic_api_key: str = field(default="", repr=False)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def validate(self) -> None:
        missing = [n for n in ("client_id", "tenant_id", "team_id") if not getattr(self, n)]
        if missing:
            raise ConfigError(f"Brak wymaganych ustawień: {', '.join(missing)}")
        if not 0 <= self.run_weekday <= 6:
            raise ConfigError(f"run_weekday poza zakresem 0..6: {self.run_weekday}")
        if not 0 <= self.run_hour <= 23:
            raise ConfigError(f"run_hour poza zakresem 0..23: {self.run_hour}")
        if not 0 <= self.run_minute <= 59:
            raise ConfigError(f"run_minute poza zakresem 0..59: {self.run_minute}")
        if not self.dry_run and not self.scheduling_group_id:
            raise ConfigError(
                "scheduling_group_id jest wymagane, gdy dry_run=false (zapis zmian do Shifts)"
            )
        try:
            _ = self.tz  # walidacja nazwy strefy
        except Exception as exc:
            raise ConfigError(f"Nieznana strefa czasowa: {self.timezone!r}") from exc

    @classmethod
    def from_env(cls) -> Settings:
        roster = os.environ.get(_PREFIX + "ROSTER_PATH")
        return cls(
            client_id=_get("CLIENT_ID"),
            tenant_id=_get("TENANT_ID"),
            team_id=_get("TEAM_ID"),
            scheduling_group_id=(_get("SCHEDULING_GROUP_ID") or None),
            token_cache_path=_path("TOKEN_CACHE", _DEFAULT_TOKEN_CACHE),
            state_path=_path("STATE_PATH", _DEFAULT_STATE),
            roster_path=(Path(roster).expanduser() if roster else None),
            run_weekday=_int("RUN_WEEKDAY", 6),
            run_hour=_int("RUN_HOUR", 16),
            run_minute=_int("RUN_MINUTE", 0),
            timezone=_get("TIMEZONE", "Europe/Warsaw"),
            reply_window_hours=_int("REPLY_WINDOW_HOURS", 48),
            dry_run=_bool("DRY_RUN", True),
            only_user_ids=_list("ONLY_USER_IDS"),
            llm_model=_get("LLM_MODEL", "claude-opus-4-8"),
            anthropic_api_key=(os.environ.get("ANTHROPIC_API_KEY") or _get("AGENT_API_KEY")),
        )
