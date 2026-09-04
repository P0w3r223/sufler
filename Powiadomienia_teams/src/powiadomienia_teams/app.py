"""Punkt wejścia pakietu — fasada nad modułami obiegu.

Kod obiegu mieszka w ``runtime``: ``nudge`` (przebieg tygodniowy), ``listener`` (odpowiedzi
i zapis), ``service`` (terminarz, puls, nadrabianie), ``operator`` (alerty). Wiersz poleceń
i złożenie zależności są w ``cli``.

Ten moduł zostaje, bo jest historycznym punktem wejścia (``[project.scripts]`` w
``pyproject.toml`` wskazuje na ``powiadomienia_teams.app:main``) i bo trzyma w jednym miejscu
publiczną powierzchnię usługi. Nie ma tu logiki — wyłącznie re-eksport.
"""
from __future__ import annotations

from powiadomienia_teams.cli import main
from powiadomienia_teams.runtime.listener import PollOutcome, poll_replies
from powiadomienia_teams.runtime.nudge import run_once
from powiadomienia_teams.runtime.service import run_forever

__all__ = ["PollOutcome", "main", "poll_replies", "run_forever", "run_once"]


if __name__ == "__main__":
    main()
