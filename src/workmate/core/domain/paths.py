"""Wyznaczanie miejsca notatki: slug tytułu i identyfikator (czyste, bez I/O).

Rozdzielone od modeli, bo to *logika domenowa* (gdzie trafia notatka), a nie
kształt danych. Identyfikator ma postać ``<firma>/<projekt>/<data>-<slug>`` i —
zgodnie z ADR 0005 — jest jednocześnie ścieżką pliku względem katalogu notatek.

``slugify`` jest też granicą bezpieczeństwa (Bramka 2, ADR 0006): tytuł pochodzi
od wołającego i trafia do ścieżki pliku, więc wynik jest zawężony do białej listy
``[a-z0-9-]`` — to eliminuje ``/``, ``..`` i ścieżki absolutne (ochrona przed
path traversal). Polskie znaki są transliterowane do ASCII, spójnie z konwencją
istniejących nazw plików (``przegląd`` → ``przeglad``).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

# Górny limit długości sluga — długie tytuły nie mają puchnąć nazwy pliku.
_SLUG_MAX_LENGTH = 80

# Znaki, których NFKD nie rozkłada na bazę + znak łączący (Ł/ł nie dekomponują).
_TRANSLITERATION = str.maketrans({"ł": "l", "Ł": "l", "đ": "d", "ø": "o"})


def slugify(text: str) -> str:
    """Zamień dowolny tekst w bezpieczny slug ``[a-z0-9-]`` (może być pusty).

    Pusty wynik oznacza tytuł bez znaków dających się przetłumaczyć na ASCII —
    wołający (przypadek użycia zapisu) musi to potraktować jako błąd wejścia.
    """
    folded = text.translate(_TRANSLITERATION)
    ascii_only = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return hyphenated[:_SLUG_MAX_LENGTH].strip("-")


def note_id(company: str, project: str, on: date, title: str) -> str:
    """Zbuduj identyfikator notatki ``<firma>/<projekt>/<data>-<slug>``.

    Podnosi ``ValueError``, gdy tytuł nie daje się przekształcić w niepusty slug.
    ``company``/``project`` pochodzą z rejestru (kontrolowany plik), ale
    weryfikujemy je defensywnie: zabłąkany ``/`` czy ``..`` w rejestrze nie może
    wpłynąć do ścieżki notatki.
    """
    for segment in (company, project):
        if not segment or "/" in segment or "\\" in segment or ".." in segment:
            raise ValueError(f"niedozwolony segment ścieżki (firma/projekt): {segment!r}")
    slug = slugify(title)
    if not slug:
        raise ValueError(
            f"tytuł nie daje się przekształcić w slug (same znaki spoza [a-z0-9]): {title!r}"
        )
    return f"{company}/{project}/{on.isoformat()}-{slug}"
