"""Repozytorium notatek oparte na plikach Markdown z frontmatter YAML.

Format pliku (patrz ``docs/reference/note-schema.md``)::

    ---
    title: ...
    project: mpwik
    date: 2025-06-12
    participants: [...]
    decisions: [...]
    action_items: [...]
    open_questions: [...]
    tags: [...]
    ---

    Treść notatki w Markdown...

Identyfikator notatki to jej ścieżka względem katalogu notatek, bez rozszerzenia
(np. ``mpwik/scada-integration/2025-06-12-przeglad-integracji``). Notatki są naszymi kontrolowanymi
danymi, więc błędny plik = błąd danych: podnosimy ``NoteParseError`` z kontekstem
(fail fast), a granica MCP zamienia go na czytelny komunikat.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sufler.core.domain.models import Note, NoteMetadata
from sufler.core.errors import RepositoryError

logger = logging.getLogger(__name__)

_FRONTMATTER_FENCE = "---"


class NoteParseError(RepositoryError):
    """Notatka ma niepoprawny frontmatter albo nie da się jej sparsować."""


class MarkdownNotesRepository:
    """Ładuje notatki z drzewa katalogów ``notes_dir/<firma>/<projekt>/<plik>.md``.

    ``all()`` cache'uje sparsowane notatki PER PLIK i unieważnia je fingerprintem
    ``(st_mtime_ns, st_size)``: spacer ``rglob`` i ``stat`` wykonujemy zawsze (tanie), ale
    drogie ``read_text``+parse tylko dla plików nowych/zmienionych — więc świeżo zapisana
    notatka (także z innego procesu) jest widziana, a usunięta eksmitowana. ``Lock``, bo
    repozytorium bywa wołane z pul wątków. ``get()`` zostaje bez cache (jeden plik, zawsze świeży).
    """

    def __init__(self, notes_dir: Path) -> None:
        self._notes_dir = notes_dir
        self._lock = threading.Lock()
        self._cache: dict[Path, tuple[tuple[int, int], Note]] = {}

    def all(self) -> list[Note]:
        if not self._notes_dir.is_dir():
            raise RepositoryError(f"Katalog notatek nie istnieje: {self._notes_dir}")
        notes: list[Note] = []
        fresh: dict[Path, tuple[tuple[int, int], Note]] = {}
        with self._lock:
            for path in sorted(self._notes_dir.rglob("*.md")):
                try:
                    stat = path.stat()
                    fingerprint = (stat.st_mtime_ns, stat.st_size)
                    cached = self._cache.get(path)
                    # Plik wadliwy rzuca w ``_load`` PRZED zapisem cache — jak dziś rzuca co
                    # wywołanie (``NoteParseError`` to nie ``OSError``, więc osłona go nie tłumi).
                    note = cached[1] if cached and cached[0] == fingerprint else self._load(path)
                except FileNotFoundError:
                    # Notatka zniknęła MIĘDZY spacerem a odczytem — od ADR 0065 kasowanie jest
                    # realną drogą, a wyścig z nim nie może wywracać całego odczytu bazy wiedzy
                    # surowym ``FileNotFoundError``. Pomijamy plik: następne ``all()`` zobaczy
                    # stan po zmianie.
                    continue
                except OSError as exc:
                    # Plik JEST, tylko nie da się go przeczytać (odmowa dostępu na notatce albo
                    # na katalogu firmy, błąd I/O). Pominięty po cichu wypada z korpusu, więc
                    # ``search_notes`` i ranking milcząco zwracają mniej — wynik nieodróżnialny
                    # od „nie ma takiej wiedzy". Wywracać całego odczytu nie ma po co, ale ślad
                    # ma zostać, jak w siostrzanym ``filesystem_workspace._entries_or_empty``.
                    logger.warning("Pomijam notatkę %s — nie udało się jej odczytać: %s", path, exc)
                    continue
                fresh[path] = (fingerprint, note)
                notes.append(note)
            self._cache = fresh  # tylko aktualne ścieżki → usunięte pliki eksmitowane
        return notes

    def get(self, note_id: str) -> Note | None:
        path = self._notes_dir / f"{note_id}.md"
        # Identyfikator pochodzi od wołającego: upewnij się, że złożona ścieżka
        # nie wychodzi poza katalog notatek (ochrona przed path traversal, np.
        # note_id="../../../../etc/hosts").
        try:
            path.resolve().relative_to(self._notes_dir.resolve())
        except ValueError:
            return None
        if not path.is_file():
            return None
        # ``is_file()`` i ``read_text()`` to DWA podejścia do dysku, a od ADR 0065 kasowanie
        # notatki jest realną drogą i biegnie OBOK bramki mutacji — która sama woła ``get``
        # (``note_mutation._require_mutable``) w tej samej turze, w której pisarz liczy
        # ``digest``. Tam osłona jest, tutaj jej nie było, choć wyścig ten sam.
        try:
            return self._load(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            # Notatka JEST, tylko nie da się jej przeczytać. ``None`` znaczyłoby „nie ma takiej
            # notatki" — a bramka mutacji odmawia wtedy słowami „notatka nie istnieje", czyli
            # kłamie o stanie bazy wiedzy dokładnie tam, gdzie ktoś pyta o jej zawartość.
            raise RepositoryError(f"nie udało się odczytać notatki {note_id}: {exc}") from exc

    def _load(self, path: Path) -> Note:
        raw = path.read_text(encoding="utf-8")
        # Ścieżka WZGLĘDNA wobec katalogu notatek w komunikatach błędów: nie ujawnia
        # bezwzględnej struktury serwera (drzwi HTTP) i jest przenośna między maszynami.
        location = path.relative_to(self._notes_dir).as_posix()
        meta_dict, body = _split_frontmatter(raw, location)
        try:
            metadata = NoteMetadata.model_validate(meta_dict)
        except ValidationError as exc:
            raise NoteParseError(f"{location}: nieprawidłowy frontmatter notatki: {exc}") from exc
        note_id = path.relative_to(self._notes_dir).with_suffix("").as_posix()
        return Note(id=note_id, metadata=metadata, body=body.strip())


def _split_frontmatter(raw: str, location: str) -> tuple[dict[str, Any], str]:
    """Rozdziel plik na słownik frontmatter (YAML) i treść (Markdown).

    ``location`` to ścieżka względna notatki — trafia do komunikatów błędów, więc
    musi być bezpieczna do pokazania klientowi (bez bezwzględnej ścieżki serwera).
    """
    if not raw.lstrip().startswith(_FRONTMATTER_FENCE):
        raise NoteParseError(f"{location}: brak bloku frontmatter ('---') na początku pliku")

    # maxsplit=2: dzielimy tylko na pierwszych dwóch '---', ewentualne '---'
    # w treści (linie poziome) zostają nietknięte w części z treścią.
    _, _, remainder = raw.partition(_FRONTMATTER_FENCE)
    front, fence, body = remainder.partition(f"\n{_FRONTMATTER_FENCE}")
    if not fence:
        raise NoteParseError(f"{location}: brak zamykającego '---' frontmatter")

    try:
        meta = yaml.safe_load(front)
    except yaml.YAMLError as exc:
        raise NoteParseError(f"{location}: błąd składni YAML we frontmatter: {exc}") from exc

    if not isinstance(meta, dict):
        raise NoteParseError(f"{location}: frontmatter musi być mapą klucz-wartość")
    return meta, body
