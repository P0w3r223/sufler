"""Ustawienia katalogu roboczego agenta (``CreateFile``/``ReadFile``/``ListFiles``)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import (
    _bool_from_env,
    _int_from_env,
    _list_from_env,
    _path_from_env,
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
        # DRUGI kierunek, dopisany razem z bezwarunkowym sprzątaczem TTL. Dopóki sprzątanie
        # wisiało na bramce narzędzi, wskazanie tu korzenia systemu, katalogu domowego albo
        # wolumenu stanu było bezobjawową pomyłką konfiguracji. Od chwili, w której sprzątacz
        # biegnie przy KAŻDYM starcie drzwi, ta sama pomyłka kasuje wszystko na drugim poziomie
        # tej ścieżki, czego nikt nie tknął od `retention_days`. Kierunek pomyłki jest
        # nieodwracalny, więc bramka stoi tam, gdzie wartość jest jeszcze konfiguracją.
        if resolved_ws in (Path(resolved_ws.anchor), Path.home().resolve()):
            raise ValueError(
                "WORKMATE_WORKSPACE_DIR nie może być korzeniem systemu ani katalogiem domowym "
                f"— sprzątanie TTL kasuje w nim katalogi rozmów, jest: {resolved_ws}."
            )
        if resolved_ws in resolved_data.parents:
            raise ValueError(
                f"WORKMATE_WORKSPACE_DIR ({resolved_ws}) zawiera katalog danych ({resolved_data}) "
                "— sprzątanie TTL sięgnęłoby bazy wiedzy."
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
