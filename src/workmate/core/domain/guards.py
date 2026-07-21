"""Wspólne strażniki zapisu — kształt klucza Jiry, sufity długości, izolacja osób.

Wydzielone z ``application/jira.py``, gdy zdolności mutujące urosły do DWÓCH serwisów
(``JiraWriteService`` — create/tranzycja, ``WorklogService`` — ewidencja czasu, ADR 0034).
Strażnik path-traversal na kluczu jest kontrolą BEZPIECZEŃSTWA, więc dwie kopie to dokładnie
ten rodzaj dryfu, którego nie chcemy: jedna implementacja, dwóch konsumentów.

ADR 0035 dokłada ``assert_single_person`` — kontrolę tej samej klasy, ale o innej osi: nie
„czy wolno pisać w tym projekcie", lecz „czy te dane na pewno należą do tej osoby".

Czyste funkcje domenowe (bez I/O). Komunikaty błędów są częścią kontraktu — testy serwisu
asertują na ich treść, a użytkownik widzi je przez kopertę narzędzia.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from workmate.core.errors import WriteError

# Pełny kształt klucza Jira (``PROJ-123``). Walidujemy CAŁY klucz, nie sam prefiks — inaczej
# ``WM-1/../OPS-1`` przeszedłby test projektu, a adapter (httpx normalizuje ``..``) dopisałby
# treść w CUDZYM projekcie. ``reject_dangerous_content`` nie pomoże (``/`` i ``.`` są ok).
JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


class CrossPersonLeak(WriteError):
    """Dane jednej osoby trafiły do artefaktu innej — zawsze defekt, nigdy stan dopuszczalny."""


def assert_single_person(source_id: str, entry_source_ids: Iterable[str]) -> None:
    """Wymuś, że WSZYSTKIE wpisy należą do ``source_id``; inaczej ``CrossPersonLeak``.

    Ostatnia linia obrony przed najgorszym możliwym błędem tej funkcji (ADR 0035): wysłaniem
    komuś cudzych godzin albo, gorzej, wpisaniem ich do cudzego konta Jiry przy imporcie —
    czego (ADR 0034) nie da się cofnąć narzędziem. Filtrowanie po ``source_id`` dzieje się
    wcześniej w ``build_timesheet``; ten strażnik istnieje po to, żeby przyszła refaktoryzacja
    tamtego filtra nie przeszła po cichu. Wołamy go DWA razy: po agregacji i na granicy zapisu.
    """
    foreign = sorted({found for found in entry_source_ids if found != source_id})
    if foreign:
        raise CrossPersonLeak(
            f"przerwano: zestawienie dla {source_id!r} zawiera wpisy innych osób "
            f"({', '.join(foreign)}) — nic nie zostało zapisane ani wysłane."
        )


def bounded(text: str, limit: int, label: str) -> str:
    """Zwróć ``text`` w granicy ``limit``; inaczej ``WriteError`` (nie tniemy po cichu)."""
    if len(text) > limit:
        raise WriteError(f"{label} przekracza limit {limit} znaków — zapis odrzucony.")
    return text


def require_jira_key(issue_key: str, project: str) -> str:
    """Zwaliduj PEŁNY kształt klucza i wymuś zgodność projektu; inaczej ``WriteError``.

    Walidacja całego klucza (nie prefiksu) zamyka obejście path-traversal ``WM-1/../OPS-1``:
    taki „klucz" nie pasuje do ``PROJ-123``, więc odrzucamy go, zanim trafi do ścieżki REST.
    ``project`` pochodzi z KONFIGURACJI drzwi — niezaufana treść nie przekieruje zapisu indziej.
    """
    key = issue_key.strip().upper()
    if not JIRA_KEY_RE.fullmatch(key):
        raise WriteError(
            f"klucz odrzucony: {issue_key!r} nie jest poprawnym kluczem Jira (PROJ-123)."
        )
    prefix = key.split("-", 1)[0]
    if prefix != project:
        raise WriteError(
            f"klucz odrzucony: {issue_key!r} jest spoza skonfigurowanego projektu {project!r}."
        )
    return key
