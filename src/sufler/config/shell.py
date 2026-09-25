"""Ustawienia narzędzia ``Bash`` — gniazdo menedżera wykonawców i sufity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sufler.config._env import _bool_from_env, _int_from_env, _path_from_env

# Gniazdo KONTROLNE menedżera wykonawców (ADR infra 0012). Aplikacja nie łączy się już ze stałym
# gniazdem wykonawcy — pyta menedżera ``ensure(scope)`` o gniazdo wykonawcy TEJ rozmowy. Wolumen
# tego
# gniazda montują WYŁĄCZNIE aplikacja i menedżer. Ta sama wartość stoi po stronie menedżera
# (``ExecManagerSettings.control_socket``), bo jeden env (`SUFLER_EXEC_MANAGER_SOCKET`) opisuje
# oba
# końce jednego gniazda — rozjazd oznaczałby, że aplikacja puka pod inny adres, niż menedżer
# nasłuchuje.
_DEFAULT_MANAGER_SOCKET = Path("/var/run/sufler-manager/control.sock")
# Sufit czasu polecenia po stronie wykonawcy (``exec_server._MAX_TIMEOUT_S``) — tu wyłącznie
# po to, by walidacja odrzuciła konfigurację, którą wykonawca i tak by przyciął.
_MAX_SHELL_TIMEOUT_S = 300


@dataclass(frozen=True)
class ShellSettings:
    """Konfiguracja narzędzia ``Bash`` (ADR 0057 + infra 0012) — powłoka w wykonawcy per rozmowa.

    Bramka ``enabled`` jest OSOBNA od ``SUFLER_ENABLE_WORKSPACE`` (pliki robocze, ADR 0018).
    Profile zaufania są różne: tam model tworzy pliki narzędziem typowanym, o nazwie z białej
    listy rozszerzeń; tu uruchamia dowolny kod. Wspólna bramka włączałaby powłokę po cichu,
    przy okazji włączania plików.

    Powłoka biegnie w OSOBNYM kontenerze bez sieci, więc kod od modelu nie ma dokąd wynieść
    danych — bezpieczeństwo bierze się z tego, czego w tamtym kontenerze nie ma, a nie
    z oceniania treści polecenia. Od ADR 0012 wykonawca jest stawiany PER ROZMOWA (montuje tylko
    jej podkatalog brudnopisu), więc aplikacja adresuje go przez menedżera, nie przez stałe gniazdo:
    ``manager_socket_path`` to gniazdo KONTROLNE menedżera, nie samego wykonawcy.
    """

    enabled: bool = False
    manager_socket_path: Path = _DEFAULT_MANAGER_SOCKET
    default_timeout_s: int = 60

    @classmethod
    def from_env(cls) -> ShellSettings:
        return cls(
            enabled=_bool_from_env("SUFLER_ENABLE_SHELL", default=False),
            manager_socket_path=_path_from_env(
                "SUFLER_EXEC_MANAGER_SOCKET", _DEFAULT_MANAGER_SOCKET
            ),
            default_timeout_s=_int_from_env("SUFLER_SHELL_TIMEOUT_S", 60),
        )

    def validate(self) -> None:
        """Twardy błąd startu przy limicie czasu, którego wykonawca i tak by nie uszanował."""
        if not 1 <= self.default_timeout_s <= _MAX_SHELL_TIMEOUT_S:
            raise ValueError(
                f"SUFLER_SHELL_TIMEOUT_S musi być w zakresie 1..{_MAX_SHELL_TIMEOUT_S}, "
                f"jest: {self.default_timeout_s}."
            )
