"""Katalog roboczy agenta: izolacja per rozmowa i bezpieczne nazwy plików (czyste, bez I/O).

Zdolność ZAPISU dowolnych plików roboczych (ADR 0018) — osobna od notatek bazy wiedzy. Tu żyje
logika domenowa: gdzie fizycznie ląduje plik (izolowany podkatalog per rozmowa) i jak z tytułu
podanego przez MODEL powstaje bezpieczna nazwa pliku.

Granice bezpieczeństwa (zapis z NIEZAUFANYCH drzwi — treść może być prompt-injection):
- ``WorkspaceScope`` bierze segmenty ZAUFANE (kanał drzwi + id rozmowy z pollera, NIE od modelu);
  id rozmowy heszujemy na stały, FS-bezpieczny katalog (unikalny, bez kolizji, bez znaków `:@/`).
- Nazwa pliku od modelu przechodzi przez ``slugify`` (biała lista ``[a-z0-9-]``) + białą listę
  rozszerzeń — ``/``, ``..`` i ścieżki absolutne nie przeżyją (jak tytuł notatki w ``paths.py``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from sufler.core.domain.paths import slugify
from sufler.core.errors import WriteError


@dataclass(frozen=True)
class WorkspaceScope:
    """Izolowany podkatalog roboczy dla (kanał, rozmowa). Segmenty ZAUFANE (poller, nie model).

    ``channel`` to literał drzwi (np. ``teams_graph``); ``conversation`` to id rozmowy
    (np. ``team/channel/root``) — heszowane, bo niesie znaki niedozwolone w nazwie katalogu
    (``:``, ``@``, ``/``) i różne rozmowy MUSZĄ trafić do różnych katalogów (izolacja).
    """

    channel: str
    conversation: str

    def dirpath(self) -> PurePosixPath:
        """Ścieżka katalogu rozmowy względem korzenia workspace (posix, FS-bezpieczna)."""
        safe_channel = slugify(self.channel) or "door"
        digest = hashlib.sha256(self.conversation.encode("utf-8")).hexdigest()[:32]
        return PurePosixPath(safe_channel) / digest


@dataclass(frozen=True)
class WorkspaceFile:
    """Lekki opis pliku roboczego: nazwa, ścieżka względem korzenia workspace i rozmiar (bajty)."""

    name: str
    relpath: str
    size: int


def safe_filename(name: str, *, allowed_ext: frozenset[str]) -> str:
    """Zamień nazwę od MODELU w bezpieczną ``<slug>.<ext>``; ``WriteError`` gdy się nie da.

    Rozszerzenie bierzemy z nazwy (po ostatniej kropce), sprawdzamy wobec białej listy;
    rdzeń nazwy slugujemy do ``[a-z0-9-]``. Brak rozszerzenia / spoza listy / pusty slug → błąd.
    """
    stem, dot, ext = name.rpartition(".")
    if not dot:
        raise WriteError(f"nazwa pliku musi mieć rozszerzenie: {name!r}")
    ext_norm = ext.strip().lower()
    if ext_norm not in allowed_ext:
        allowed = ", ".join(sorted(allowed_ext))
        raise WriteError(f"niedozwolone rozszerzenie {ext_norm!r} (dozwolone: {allowed})")
    slug = slugify(stem)
    if not slug:
        raise WriteError(f"nazwa pliku nie daje się przekształcić w slug: {name!r}")
    return f"{slug}.{ext_norm}"


def relpath_in_scope(scope: WorkspaceScope, filename: str) -> str:
    """Złóż ścieżkę pliku względem korzenia workspace: ``<kanał>/<hash rozmowy>/<plik>``."""
    return str(scope.dirpath() / filename)
