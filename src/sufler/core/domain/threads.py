"""Czysta logika wątkowania kanału (ADR 0024, Faza 3) — mapowanie zdarzenia na CEL wątku.

Bez I/O. Cel wątku (issue/PR/zgłoszenie Jira + identyfikator) wyłuskujemy z ``url`` zdarzenia:
GitHub niesie kanoniczny wzorzec (``/pull/{n}`` lub ``/issues/{n}``, CI url KANONIZOWANY na stronę
PR już w ``selection.map_ci_run``), a Jira (ADR 0030/B2) — ``/browse/{KEY}`` dla WSZYSTKICH rodzajów
(utworzenie/tranzycja/komentarz przez ``jira/selection._browse_url``, komentarz z sufiksem
``?focusedCommentId=``). Wzorce się nie kolidują (GitHub nie ma ``/browse/``, Jira nie ma
``/pull/``|``/issues/``), więc jeden resolver obsługuje oba źródła. Zdarzenie bez celu (np. CI
niezwiązane z PR — url wskazuje przebieg) zwraca ``None`` → notifier potraktuje je jako osobny
root, nie dołączy do wątku.
"""

from __future__ import annotations

import re

_PULL_RE = re.compile(r"/pull/(\d+)")
_ISSUE_RE = re.compile(r"/issues/(\d+)")
# Klucz zgłoszenia Jira w URL-u ``/browse/{KEY}`` (np. ``WM-5``, ``OPS-123``) — ten sam kształt co
# ``_JIRA_KEY_RE`` w ``application/jira.py``. Case-insensitive w URL-u; klucz normalizujemy do
# wielkich liter, żeby ``WM-5`` i ``wm-5`` trafiły do TEGO SAMEGO wątku (klucze Jira są uppercase).
_JIRA_BROWSE_RE = re.compile(r"/browse/([A-Za-z][A-Za-z0-9]+-\d+)")


def resolve_thread_target(url: str) -> tuple[str, str] | None:
    """Cel wątku ``(kind, id)`` z ``url``: pr/issue (numer) lub jira (KEY); brak dopasowania → None.

    Kolejność ma znaczenie: PR sprawdzamy pierwsze, bo komentarze/recenzje/CI na PR mają w url
    ``/pull/{n}`` (a nie ``/issues/{n}``). Jira sprawdzamy na końcu (osobny, niekolidujący wzorzec).
    Brak dopasowania → zdarzenie nie ma celu wątku.
    """
    text = url or ""
    match = _PULL_RE.search(text)
    if match:
        return ("pr", match.group(1))
    match = _ISSUE_RE.search(text)
    if match:
        return ("issue", match.group(1))
    match = _JIRA_BROWSE_RE.search(text)
    if match:
        return ("jira", match.group(1).upper())
    return None
