"""Migawki notatek przed mutacją na dysku (port ``NoteSnapshots``, ADR 0065).

Migawki leżą POZA drzewem notatek — w katalogu stanu, nie w bazie wiedzy. Trzy powody i każdy
wystarcza: agent czyta ``notes_dir`` zachłannie (``MarkdownNotesRepository.all``), więc kopie
w środku wracałyby jako wyniki wyszukiwania i mnożyły odpowiedzi; wykonawca montuje notatki
``ro``, a stan nie jest mu w ogóle widoczny; a odwracalność ma przetrwać także wtedy, gdy ktoś
pomyli się na całym katalogu projektu.

Nazwa pliku niesie identyfikator notatki i chwilę — po to, żeby cofnięcie było czynnością
mechaniczną (znajdź, skopiuj z powrotem), a nie śledztwem po znacznikach czasu inode'ów.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sufler.core.errors import WriteError


class FilesystemNoteSnapshots:
    """Zapisuje kopię treści notatki do ``snapshots_dir/<id>/<znacznik czasu>.md``."""

    def __init__(self, snapshots_dir: Path) -> None:
        self._root = snapshots_dir

    def save(self, note_id: str, content: str) -> str:
        """Zapisz migawkę atomowo i zwróć jej ścieżkę; ``WriteError``, gdy się nie uda.

        Błąd jest GŁOŚNY rozmyślnie — bramka mutacji zamienia go na odmowę operacji. Migawka,
        która „czasem się nie uda", nie jest zabezpieczeniem, tylko złudzeniem zabezpieczenia.

        Identyfikator notatki idzie przez ``_bezpieczny_segment``: pochodzi wprawdzie ze
        slugifikacji serwisu, ale to jedyne miejsce, które składa z niego ŚCIEŻKĘ, a obrona
        w głąb kosztuje tu jedną linię.
        """
        # ``content`` to BAJTY PLIKU podane przez wołającego, nie render notatki. Migawka ma
        # być plikiem, który wystarczy skopiować z powrotem — a render odtwarzał tylko pola znane
        # schematowi, więc cicho gubił wszystko dopisane ręcznie (patrz port).
        katalog = self._root / _bezpieczny_segment(note_id)
        znacznik = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        cel = katalog / f"{znacznik}.md"
        tmp = katalog / f"{znacznik}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            katalog.mkdir(parents=True, exist_ok=True)
            # BAJTY, NIE TEKST: migawka ma dać się skopiować z powrotem CO DO BAJTU, a tryb
            # tekstowy zamieniał na Windows każdy LF na CRLF — odtworzona notatka różniłaby się
            # od tej sprzed zmiany, czyli dokładnie w punkcie, na którym stoi odwracalność
            # mutacji z ADR 0065. Port typuje ``content`` jako ``str``, choć docstring mówi
            # „bajty pliku z dysku" — kodowanie stoi więc tutaj, na ostatnim kroku przed
            # dyskiem, zamiast liczyć na tryb otwarcia pliku (#146).
            tmp.write_bytes(content.encode("utf-8"))
            os.replace(tmp, cel)
        except OSError as exc:
            raise WriteError(f"nie udało się zapisać migawki notatki {note_id}: {exc}") from exc
        finally:
            # Sprzątanie nie może PRZYKRYĆ właściwego błędu: gdy katalog kopii nie jest
            # katalogiem, samo ``unlink`` rzuca i wołający dostaje wyjątek o pliku
            # tymczasowym zamiast o tym, że migawki nie da się zapisać.
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
        return str(cel)


def _bezpieczny_segment(note_id: str) -> str:
    """Zamień identyfikator notatki na JEDEN bezpieczny segment ścieżki.

    Identyfikator zawiera ukośniki (``firma/projekt/plik``), więc bez spłaszczenia migawki
    tworzyłyby drzewo lustrzane do bazy wiedzy — a ``..`` w środku wyprowadziłby zapis poza
    katalog migawek.

    Kropka zostaje na białej liście znaków (rozszerzenia plików), więc samo filtrowanie
    przepuszczało segmenty ``.`` i ``..`` w całości — a to dokładnie te dwa, które ta funkcja ma
    odciąć. Dziś nieosiągalne, bo ``NoteMutationService`` żąda istnienia notatki przed migawką,
    ale funkcja istnieje wyłącznie po to, żeby tego nie zakładać. ``filesystem_outbox._entry_path``
    odrzuca tę parę wprost od początku.
    """
    plaski = note_id.replace("/", "__").replace("\\", "__")
    bezpieczny = "".join(znak for znak in plaski if znak.isalnum() or znak in "-_.")[:200]
    return bezpieczny if bezpieczny not in {"", ".", ".."} else "bez-nazwy"
