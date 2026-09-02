"""Dostawa plików z brudnopisu wykonawcy do rozmówcy (outbox tury)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from workmate.adapters.outbound.filesystem_outbox import (
    FilesystemOutboxRepository,
)
from workmate.core.application.outbox import OutboxDelivery, OutboxLimits
from workmate.core.ports.outbox import Deliverable

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.config import (
        WorkspaceSettings,
    )
    from workmate.core.domain.workspace import WorkspaceScope

logger = logging.getLogger(__name__)


def _build_outbox_delivery(
    workspace_settings: WorkspaceSettings,
    send_factory: Callable[[str], Callable[[Deliverable], None] | None],
    *,
    max_file_bytes: int,
    max_files_per_turn: int,
    max_seconds: float,
) -> _ScopedOutbox:
    """Zbuduj dostawę ze skrzynki nadawczej rozmowy — wołaną PO turze, zwracającą zdanie raportu.

    Korzeń bierzemy z ``workspace_settings``, tego samego, z którego liczy się ``cwd`` poleceń
    (``_build_shell_factory``) — rozjazd tych dwóch wartości oznaczałby, że model zapisuje plik
    w skrzynce, której drzwi nie czytają, i to bez żadnego objawu poza brakiem załącznika.
    """
    delivery = OutboxDelivery(
        FilesystemOutboxRepository(workspace_settings.workspace_dir),
        OutboxLimits(
            max_file_bytes=max_file_bytes,
            max_files_per_turn=max_files_per_turn,
            max_total_seconds=max_seconds,
        ),
    )

    return _ScopedOutbox(delivery, send_factory)


class _ScopedOutbox:
    """Dwufazowa dostawa dla respondera: migawka na starcie tury, wysyłka po niej.

    Fazy są DWIE, bo migawka jest granicą pochodzenia plików — musi powstać, zanim model
    dostanie powłokę. Zwinięcie ich w jedno wywołanie po turze znaczyłoby, że nie umiemy
    odróżnić pliku wytworzonego w tej turze od podłożonego wcześniej z innej rozmowy.
    """

    def __init__(
        self,
        delivery: OutboxDelivery,
        send_factory: Callable[[str], Callable[[Deliverable], None] | None],
    ) -> None:
        self._delivery = delivery
        self._send_factory = send_factory

    def snapshot(self, scope: WorkspaceScope) -> None:
        self._delivery.snapshot(str(scope.dirpath()))

    def deliver(self, scope: WorkspaceScope) -> str:
        send = self._send_factory(scope.conversation)
        if send is None:
            # Wątek bez celu dostawy (np. rozmowa spoza kanału): zostawiamy skrzynkę nietkniętą,
            # bo plik nie jest odrzucony — po prostu nie ma dokąd pójść z TYCH drzwi.
            return ""
        return self._delivery.deliver(str(scope.dirpath()), send).notice()
