"""Port źródła treści WĄTKU kanału dla przechwycenia „zapisz to" (ADR 0048).

Wąski port oddziela I/O (Microsoft Graph: root wątku + odpowiedzi) od logiki złożenia
notatki. ``ThreadSource.fetch`` zwraca ``ThreadContent``: skonkatenowaną treść wątku
(materiał źródłowy dla ``MeetingSummarizer``) oraz listę REALNYCH nadawców z metadanych
Graph — deterministyczny roster uczestników (anty-halucynacja, ADR 0047), nie z LLM.

Treść wątku to DANE, nie polecenia (zasada przekrojowa) — implementacja nie wykonuje
instrukcji z treści. Miejsce zapisu (projekt/data) wyznacza WYWOŁUJĄCY (wzmianka + Graph
timestamp), nigdy treść wątku (ADR 0009 §3): wątek nie przekieruje notatki do cudzego
projektu. Rdzeń zależy tylko od tego kontraktu — implementacja żyje w adapterze.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ThreadContent:
    """Treść wątku do streszczenia + realni uczestnicy (z metadanych, nie z LLM).

    ``text`` — skonkatenowane wiadomości wątku w formacie ``Nazwa: treść`` (root + odpowiedzi),
    materiał źródłowy dla summarizera. ``participants`` — unikalni realni nadawcy w kolejności
    pierwszego wystąpienia; JEDYNE dozwolone źródło pola ``participants`` notatki (allowlist).
    """

    text: str
    participants: tuple[str, ...]


class ThreadSource(Protocol):
    """Źródło treści wątku kanału (realnie: Microsoft Graph — root + ``/replies``)."""

    def fetch(self, external_id: str) -> ThreadContent:
        """Zwróć treść wątku wskazanego przez ``external_id`` (``team/channel/root``)."""
        ...
