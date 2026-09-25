"""Fabryka narzędzia ``Bash`` — wykonawca zawężony do jednej rozmowy (ADR 0012)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sufler.adapters.outbound.filesystem_outbox import (
    OUTBOX_DIRNAME,
)
from sufler.core.application.tools import (
    build_shell_catalog,
)
from sufler.core.ports.command import CommandResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from sufler.config import (
        ShellSettings,
        WorkspaceSettings,
    )
    from sufler.core.application.shell_authz import ShellAuthorizer
    from sufler.core.application.tools import ToolSpec
    from sufler.core.domain.workspace import WorkspaceScope
    from sufler.core.ports.command import CommandRunner

logger = logging.getLogger(__name__)


class _ScopedRunner:
    """``CommandRunner`` zapewniający istnienie katalogu rozmowy przed wysłaniem polecenia.

    Wykonawca, gdy podany ``cwd`` nie istnieje, degraduje do swojego katalogu domyślnego —
    rozsądnie, bo ``Popen`` z nieistniejącym ``cwd`` rzuca błędem mówiącym o katalogu zamiast
    o poleceniu. Skutkiem ubocznym byłaby jednak UTRATA IZOLACJI: katalog rozmowy powstaje
    leniwie, przy pierwszym ``CreateFile``, więc do tego czasu wszystkie rozmowy dzieliłyby
    wspólny korzeń brudnopisu i widziały nawzajem swoje pliki. Zmierzone: ``pwd`` w świeżej
    rozmowie zwracało ``/home/scratchpad``, nie ``/home/scratchpad/<kanał>/<hash>``.

    Katalog zakłada APLIKACJA, nie wykonawca: „rozmowa" to pojęcie aplikacji, a wykonawca ma
    zostać procesem bez wiedzy o tym, co znaczą ścieżki, które dostaje. Nieudany zapis wraca
    jako wynik z niezerowym kodem — jak każda inna porażka polecenia (ADR 0057).
    """

    def __init__(self, inner: CommandRunner, *, with_outbox: bool = False) -> None:
        self._inner = inner
        # Skrzynkę zakładamy TYLKO, gdy jest kto ją opróżnia. Katalog tworzony przy wyłączonej
        # dostawie byłby zaproszeniem do zapisu, którego nikt nie odbiera — i rósłby bez końca.
        self._with_outbox = with_outbox

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult:
        if cwd:
            try:
                # Skrzynkę nadawczą zakłada aplikacja razem z katalogiem roboczym, bo opis
                # narzędzia każe modelowi pisać do ``outputs/`` ścieżką WZGLĘDNĄ — a
                # przekierowanie powłoki do nieistniejącego katalogu kończy się błędem,
                # nie utworzeniem go.
                target = Path(cwd, OUTBOX_DIRNAME) if self._with_outbox else Path(cwd)
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"Nie udało się przygotować katalogu roboczego {cwd}: {exc}",
                )
        return self._inner.run(command, cwd=cwd, timeout_s=timeout_s)


def _build_shell_factory(
    shell_settings: ShellSettings,
    workspace_settings: WorkspaceSettings,
    *,
    outbox_enabled: bool = False,
    authorizer: ShellAuthorizer | None = None,
) -> Callable[[WorkspaceScope, str], list[ToolSpec]] | None:
    """Fabryka narzędzia ``Bash`` (ADR 0057) wiążącego polecenia z katalogiem rozmowy.

    Zwraca ``None``, gdy powłoka jest wyłączona ALBO gdy klienta wykonawcy nie da się
    zaimportować — jest POSIX-only (gniazda unix), więc na maszynie deweloperskiej z Windows
    degradujemy do „brak narzędzia" zamiast wywracać start drzwi. Import jest leniwy z tego
    samego powodu co Claude API.

    ``workspace_settings.workspace_dir`` jest korzeniem ścieżek dla OBU stron: aplikacja pisze
    tam pliki narzędziem ``CreateFile``, a wykonawca dostaje ten sam katalog jako ``cwd``.
    Rozjazd tych dwóch wartości oznaczałby, że model tworzy plik narzędziem i nie widzi go
    powłoką — dlatego korzeń bierzemy z jednej konfiguracji, a nie z dwóch.

    ``authorizer`` (ADR 0063) bramkuje powłokę członkostwem NADAWCY na drzwiach wieloużytkownikowych
    (Teams): fabryka bierze więc też ``sender_id``. Nierozpoznany nadawca → powłoki NIE dokładamy
    (build-time omission, jak Jira ADR 0054). ``None`` (drzwi zaufane — jeden operator CLI, brak
    przychodzącego ``sender_id``, jak w ADR 0042/0062) → powłoka bez bramki, jak przed ADR 0063.

    Od ADR infra 0012 wykonawca jest stawiany PER ROZMOWA: zamiast jednego stałego gniazda budujemy
    KLIENTA MENEDŻERA (raz), a runner per scope pyta go ``ensure(scope)`` o gniazdo wykonawcy TEJ
    rozmowy przed każdym poleceniem. Izolacja przenosi się z konwencji ``cwd`` na granicę montażu:
    wykonawca scope'a widzi wyłącznie swój podkatalog brudnopisu.
    """
    if not shell_settings.enabled:
        return None
    try:
        from sufler.adapters.outbound.exec_client import ManagedCommandRunner
        from sufler.adapters.outbound.exec_manager_client import SocketExecManagerClient
    except ImportError:
        logger.info(
            "Klient wykonawcy jest POSIX-only — narzędzie powłoki pomijam na tej platformie."
        )
        return None

    manager = SocketExecManagerClient(shell_settings.manager_socket_path)
    workspace_root = workspace_settings.workspace_dir.as_posix()

    def factory(scope: WorkspaceScope, sender_id: str) -> list[ToolSpec]:
        # Bramka członkostwa (ADR 0063): na drzwiach wieloużytkownikowych nierozpoznany nadawca nie
        # dostaje powłoki. Przy powłoce ON narzędzia odczytu i tak schodzą z katalogu bazowego
        # (``shell_available``), więc pominięta powłoka = brak JAKIEJKOLWIEK drogi do bazy wiedzy
        # dla gościa (fail-closed). Bez autoryzatora (CLI) — powłoka jak dawniej, bez bramki.
        if authorizer is not None and authorizer.resolve(sender_id) is None:
            return []
        # Runner per scope: ``ensure(str(scope.dirpath()))`` u menedżera zwraca gniazdo wykonawcy
        # TEJ rozmowy. ``_ScopedRunner`` dalej zakłada po stronie APLIKACJI podkatalog roboczy (i
        # ``outputs/``), bo aplikacja montuje cały wolumen brudnopisu — a menedżer robi to samo po
        # swojej stronie przed startem wykonawcy (idempotentnie, ADR 0012 §4).
        runner = _ScopedRunner(
            ManagedCommandRunner(manager, str(scope.dirpath())), with_outbox=outbox_enabled
        )
        return build_shell_catalog(
            scope,
            runner,
            workspace_root=workspace_root,
            default_timeout_s=shell_settings.default_timeout_s,
            outbox_enabled=outbox_enabled,
        )

    return factory
