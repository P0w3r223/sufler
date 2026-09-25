"""Port dziennika audytu (Faza 0, ADR 0067) — ``Protocol`` jak pozostałe porty rdzenia.

Wąski magazyn wpisów: zapisz jedno wywołanie narzędzia (pseudonim nadawcy, pseudonim rozmowy,
drzwi, nazwa narzędzia, ZREDAGOWANE argumenty, status, klasa zaufania tury, opcjonalny werdykt
sędziego) i oddaj ostatnie wpisy. Rdzeń zna TYLKO ten interfejs — pseudonimizacja i redakcja stoją
w ``core/domain`` i ``core/application``, fizyczny zapis w adapterze (SQLite). Reguła
``core ↛ adapters`` stoi.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class AuditStore(Protocol):
    """Magazyn dziennika audytu: dopisz wpis wywołania narzędzia + odczyt ostatnich wpisów."""

    def record_tool_call(
        self,
        *,
        occurred_at: datetime,
        actor_key: str,
        conversation_key: str,
        door: str,
        tool_name: str,
        arg_summary: str,
        status: str,
        trust_class: str,
        judge_verdict: str | None = None,
    ) -> None:
        """Dopisz wpis. ``arg_summary`` jest już zredagowany (bez treści); klucze pseudonimowe."""
        ...

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Ostatnie wpisy (najnowsze pierwsze) — do odtworzenia przebiegu po incydencie i testów."""
        ...
