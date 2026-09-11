"""Konsolowa implementacja protokołu zdarzeń.

Kształt z `ceidg-tool/ceidg_tool/console.py` (kopia z 2026-09-10), zawężony do protokołu
etapu 1. `close()` zostaje razem ze swoim powodem — patrz `progress.py`.
"""

from __future__ import annotations

from rich.console import Console

from .richtext import make_console, safe


class ConsoleEvents:
    """Wypisuje zdarzenia na konsolę programu."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else make_console()

    def on_message(self, text: str) -> None:
        self._console.print(safe(text))

    def on_file(self, name: str, digest: str) -> None:
        self._console.print(safe(f"plik: {name} ({digest[:12]}…)"))

    def on_signal(self, code: str, level: int) -> None:
        self._console.print(safe(f"sygnał {code} (poziom {level})"))

    def close(self) -> None:
        """Nic tu jeszcze nie żyje na ekranie, ale człon protokołu musi mieć implementację."""
        return None
