"""Czysta logika wątkowania kanału (ADR 0024, Faza 3) — mapowanie zdarzenia GitHub na CEL wątku.

Bez I/O. Cel wątku (issue/PR + numer) wyłuskujemy z ``url`` zdarzenia — dla wszystkich rodzajów
GitHub url niesie kanoniczny wzorzec (``/pull/{n}`` lub ``/issues/{n}``), a zdarzenia CI mają url
KANONIZOWANY na stronę PR już przy mapowaniu (``selection.map_ci_run``). Zdarzenie bez celu (np.
CI niezwiązane z PR — url wskazuje przebieg) zwraca ``None`` → notifier potraktuje je jako osobny
root, nie dołączy do wątku.
"""

from __future__ import annotations

import re

_PULL_RE = re.compile(r"/pull/(\d+)")
_ISSUE_RE = re.compile(r"/issues/(\d+)")


def resolve_thread_target(url: str) -> tuple[str, str] | None:
    """Cel wątku ``(kind, number)`` z ``url``: ``("pr", n)`` / ``("issue", n)`` albo ``None``.

    Kolejność ma znaczenie: PR sprawdzamy pierwsze, bo komentarze/recenzje/CI na PR mają w url
    ``/pull/{n}`` (a nie ``/issues/{n}``). Brak dopasowania → zdarzenie nie ma celu wątku.
    """
    match = _PULL_RE.search(url or "")
    if match:
        return ("pr", match.group(1))
    match = _ISSUE_RE.search(url or "")
    if match:
        return ("issue", match.group(1))
    return None
