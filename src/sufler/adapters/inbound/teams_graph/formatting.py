"""Re-eksport ``to_teams_html`` z ``adapters/teams_html.py`` (wsteczna zgodność importów).

Funkcja przeniesiona na poziom ``adapters/`` (#9.4, przegląd kodu 2026-07-31) — używa jej też
``adapters/outbound/graph_teams_notifier.py``, a import outbound→inbound łamał regułę
``adapters.outbound ↛ adapters.inbound``. Ten moduł zostaje jako cienki alias, żeby istniejące
importy (``from sufler.adapters.inbound.teams_graph.formatting import to_teams_html``) i testy
nie wymagały zmian.
"""

from __future__ import annotations

from sufler.adapters.teams_html import to_teams_html

__all__ = ["to_teams_html"]
