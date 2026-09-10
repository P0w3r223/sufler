"""Protokół zdarzeń — warstwy niższe nie znają `rich` ani konsoli.

Kształt skopiowany z `ceidg-tool/ceidg_tool/progress.py` (kopia z 2026-09-10), **człony
wymienione od nowa**.

Czego tu nie ma i dlaczego: `on_request`, `on_wait`, `on_page`, `on_details`, `on_download`.
W `ceidg-tool` obowiązuje doktryna „cisza jest defektem, mierzonym w żądaniach" — operacja
biegnie tam pół godziny przy odstępie 3,75 s, więc chwila bez wyjścia czyta się jak zawieszenie.
Etap 1 nie wysyła żądań; jego najdłuższą operacją jest odczyt pliku. Skopiowanie tamtej
doktryny zaprosiłoby kogoś do dorobienia pulsu wokół czterdziestu milisekund parsowania.

`close()` zostaje razem ze swoim powodem: żywy pasek `rich` nadpisuje wszystko wydrukowane
po nim, więc musi zgasnąć, zanim na ekran trafi cokolwiek innego.
"""

from __future__ import annotations

from typing import Protocol


class Events(Protocol):
    """Odbiorca zdarzeń etapu 1."""

    def on_message(self, text: str) -> None: ...

    def on_file(self, name: str, digest: str) -> None: ...

    def on_signal(self, code: str, level: int) -> None: ...

    def close(self) -> None:
        """Kończy żywy element ekranu, zanim trafi na niego cokolwiek innego."""
        ...


class NullEvents:
    """Odbiorca, który nic nie robi — domyślny w testach i w bibliotece."""

    def on_message(self, text: str) -> None:
        return None

    def on_file(self, name: str, digest: str) -> None:
        return None

    def on_signal(self, code: str, level: int) -> None:
        return None

    def close(self) -> None:
        return None
