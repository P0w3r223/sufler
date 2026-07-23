"""Centralna, typowana konfiguracja narzędzia — wszystko ze zmiennych ``CLAUDE_SUMMARY_*``.

Sensowne wartości domyślne pozwalają uruchomić ``claude-summary --consent`` bez żadnej dodatkowej
konfiguracji. Klucz API to sekret (``repr=False``) — nigdy nie trafia do repo ani do wyniku.
Zgoda na czytanie prywatnej historii promptów jest twardo bramkowana (fail-closed): ``consent``
musi być prawdą (z env albo z flagi ``--consent``), inaczej ``app`` odmawia odczytu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
# Wynik może zawierać prywatną treść promptów → domyślnie POZA repo (jak dane operacyjne WorkMate).
_DEFAULT_OUTPUT_DIR = Path.home() / ".claude-summary"
_DEFAULT_TZ = "Europe/Warsaw"
_DEFAULT_MODEL = "claude-sonnet-5"
_DEFAULT_DAYS = 7
_DEFAULT_MAX_TOKENS = 1024


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


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass(frozen=True)
class Settings:
    """Niemutowalny zestaw ustawień narzędzia."""

    projects_dir: Path
    output_dir: Path
    tz_name: str
    default_days: int
    author: str
    enable_llm: bool
    model: str
    max_tokens: int
    consent: bool
    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback nie ujawniło klucza.
    api_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        # Priorytet: CLAUDE_SUMMARY_API_KEY (jawnie) > ANTHROPIC_API_KEY (nazwa SDK).
        api_key = os.environ.get("CLAUDE_SUMMARY_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        return cls(
            projects_dir=_path_from_env("CLAUDE_SUMMARY_PROJECTS_DIR", _DEFAULT_PROJECTS_DIR),
            output_dir=_path_from_env("CLAUDE_SUMMARY_OUTPUT_DIR", _DEFAULT_OUTPUT_DIR),
            tz_name=os.environ.get("CLAUDE_SUMMARY_TZ", _DEFAULT_TZ).strip() or _DEFAULT_TZ,
            default_days=_int_from_env("CLAUDE_SUMMARY_DEFAULT_DAYS", _DEFAULT_DAYS),
            author=os.environ.get("CLAUDE_SUMMARY_AUTHOR", "").strip(),
            enable_llm=_bool_from_env("CLAUDE_SUMMARY_LLM", default=False),
            model=os.environ.get("CLAUDE_SUMMARY_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL,
            max_tokens=_int_from_env("CLAUDE_SUMMARY_MAX_TOKENS", _DEFAULT_MAX_TOKENS),
            consent=_bool_from_env("CLAUDE_SUMMARY_CONSENT", default=False),
            api_key=api_key,
        )

    def validate(self) -> None:
        """Twardy błąd startu przy niepoprawnej strefie lub bezsensownych limitach."""
        try:
            ZoneInfo(self.tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"CLAUDE_SUMMARY_TZ: nieznana strefa czasowa {self.tz_name!r}."
            ) from exc
        if self.default_days < 1:
            raise ValueError(
                f"CLAUDE_SUMMARY_DEFAULT_DAYS musi być >= 1, jest: {self.default_days}."
            )
        if self.max_tokens < 1:
            raise ValueError(f"CLAUDE_SUMMARY_MAX_TOKENS musi być >= 1, jest: {self.max_tokens}.")

    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)
