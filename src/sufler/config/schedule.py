"""Ustawienia grafiku pionu (Shifts) — źródło narzędzia ``Schedule``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from sufler.config._env import _list_from_env, _path_from_env

# --- grafik zmian z Teams Shifts (ADR 0059) -----------------------------------
# Zespół „BIAP – Pion Inteligentnych Technologii" — jedyny w tenancie z działającym grafikiem.
_DEFAULT_SCHEDULE_TEAM_ID = "c0ffee00-0000-4000-8000-000000000007"
# Cudzy cache MSAL bota powiadomienia-teams — montowany RO, czytany po cichu, NIGDY pisany.
# Domyślna ZALEŻNA OD PLATFORMY, dokładnie jak ``_DEFAULT_TOKENS_FILE`` i z tego samego powodu:
# literał "/var/lib/…" na Windows nie jest ścieżką absolutną (brak dysku), więc stawał się
# ścieżką WZGLĘDNĄ wobec katalogu roboczego procesu. Ten konkretny plik jest sondowany na KAŻDEJ
# platformie (``ScheduleSettings.is_enabled()`` w trybie "auto" woła ``is_file()``), więc ścieżka
# musi mieć sens także lokalnie. Nadpisywalna przez SUFLER_SCHEDULE_TOKEN_CACHE.
_DEFAULT_SCHEDULE_CACHE = (
    Path("C:/ProgramData/powiadomienia-teams/teams_token_cache.bin")
    if os.name == "nt"
    else Path("/var/lib/powiadomienia-teams/teams_token_cache.bin")
)
_DEFAULT_SCHEDULE_TZ = "Europe/Warsaw"
# Zakresy delegowane grafiku: odczyt grafiku + lista członków zespołu (translacja userId→nazwisko).
# Ta sama rejestracja aplikacji co push/powiadomienia-teams (TeamMember.Read.All skonsentowany).
_DEFAULT_SCHEDULE_SCOPES = ("Schedule.Read.All", "TeamMember.Read.All")


@dataclass(frozen=True)
class ScheduleSettings:
    """Konfiguracja grafiku Teams Shifts (ADR 0059) — WYŁĄCZNIE odczyt, cichy token z cudzego cache.

    Tożsamość pożyczamy z cache MSAL bota powiadomienia-teams (ta sama rejestracja aplikacji co
    ``TeamsPushSettings``): ``client_id``/``tenant_id`` domyślnie SPADAJĄ na
    ``SUFLER_TEAMS_PUSH_*``, żeby nie duplikować konfiguracji. Cache jest montowany RO i NIGDY nie
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
            client_id=os.environ.get("SUFLER_SCHEDULE_CLIENT_ID")
            or os.environ.get("SUFLER_TEAMS_PUSH_CLIENT_ID", ""),
            tenant_id=os.environ.get("SUFLER_SCHEDULE_TENANT_ID")
            or os.environ.get("SUFLER_TEAMS_PUSH_TENANT_ID", ""),
            team_id=os.environ.get("SUFLER_SCHEDULE_TEAM_ID", _DEFAULT_SCHEDULE_TEAM_ID).strip(),
            token_cache_path=_path_from_env("SUFLER_SCHEDULE_TOKEN_CACHE", _DEFAULT_SCHEDULE_CACHE),
            timezone=os.environ.get("SUFLER_SCHEDULE_TZ", _DEFAULT_SCHEDULE_TZ).strip(),
            scopes=_list_from_env("SUFLER_SCHEDULE_SCOPES", _DEFAULT_SCHEDULE_SCOPES),
            enabled=os.environ.get("SUFLER_SCHEDULE_ENABLED", "auto").strip().lower(),
        )

    def validate(self) -> None:
        """Kontrola strefy czasowej — ZAWSZE (jak digest). Reszta jest miękka (auto-wyłączenie)."""
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError(
                f"SUFLER_SCHEDULE_TZ={self.timezone!r} nie jest znaną strefą czasową "
                "(na Windows wymaga pakietu 'tzdata')."
            ) from exc
