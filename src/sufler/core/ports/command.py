"""Port wykonania polecenia powłoki (ADR 0057) — kontrakt narzędzia ``Bash``.

Rdzeń zna WYNIK polecenia, nie sposób jego wykonania: implementacja produkcyjna wysyła
je do OSOBNEGO kontenera-wykonawcy bez sieci, a testy podstawiają atrapę. Dzięki temu
pętla agenta i katalog narzędzi nie wiedzą nic o gniazdach ani o procesach.

Podział na dwa kontenery jest granicą bezpieczeństwa, nie optymalizacją: kod pisany przez
model biegnie tam, gdzie NIE MA sieci ani zapisywalnej ścieżki do bazy wiedzy, a wyjście na
zewnątrz zostaje wyłącznie w narzędziach typowanych aplikacji.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Wynik polecenia: kody i strumienie, plus dwie flagi degradacji.

    ``truncated`` i ``timed_out`` są OSOBNE od ``exit_code``, bo znaczą co innego dla modelu:
    kod wyjścia mówi, jak skończył program, a te dwie — że wynik jest niepełny z powodu
    limitów harnessu. Bez nich model widziałby ucięte wyjście jako kompletne i wyciągał
    z niego wnioski.
    """

    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    timed_out: bool = False


class CommandRunner(Protocol):
    """Kontrakt: z polecenia powłoki → wynik. Wykonanie jest poza rdzeniem."""

    def run(self, command: str, *, cwd: str = "", timeout_s: float = 0) -> CommandResult: ...
