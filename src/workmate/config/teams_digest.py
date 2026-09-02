"""Ustawienia proaktywnego digestu tygodniowego (ADR 0053, F6)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from workmate.config._env import (
    _bool_from_env,
    _int_from_env,
    _list_from_env,
    _path_from_env,
)

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
