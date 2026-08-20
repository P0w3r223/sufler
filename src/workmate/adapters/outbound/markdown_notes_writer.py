"""Zapis notatek do plików Markdown z frontmatter YAML (implementacja ``NotesWriter``).

Odwrotność ``MarkdownNotesRepository``: bierze ``Note`` i zapisuje ją pod ścieżką
``notes_dir/<note.id>.md``, tworząc brakujące katalogi firmy/projektu. Zapis jest
**atomowy i create-only**: pełna treść ląduje w pliku tymczasowym, a ``os.link``
publikuje ją pod docelową nazwą tylko, gdy jeszcze nie istnieje. Daje to dwie
gwarancje z ADR 0006 naraz: ``MarkdownNotesRepository.all`` (czytający katalog
zachłannie) widzi albo nic, albo cały plik — nigdy częściowy; a istniejąca
notatka nigdy nie zostaje nadpisana (także przy równoległych pisarzach) —
kolizja to błąd, nie ciche skasowanie.

Serializacja frontmatter idzie przez ``yaml.safe_dump`` z ``sort_keys=False``,
więc kolejność pól odpowiada modelowi (title, project, date, …), a
``allow_unicode=True`` zachowuje polskie znaki w treści metadanych.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import uuid
from pathlib import Path

import yaml

from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.domain.sanitize import odrzuc_wlasny_frontmatter
from workmate.core.errors import NoteExistsError, WriteError

logger = logging.getLogger(__name__)

_FRONTMATTER_FENCE = "---"


class MarkdownNotesWriter:
    """Zapisuje notatki do drzewa ``notes_dir/<firma>/<projekt>/<plik>.md``."""

    def __init__(self, notes_dir: Path) -> None:
        self._notes_dir = notes_dir

    def exists(self, note_id: str) -> bool:
        return _resolve_within(self._notes_dir, f"{note_id}.md").is_file()

    def write(self, note: Note) -> None:
        # Strażnik nagłówka TU, a nie u wołających: pisarz jest ostatnią bramą przed dyskiem,
        # a ścieżek tworzenia jest kilka (`save_note`, `save_meeting_note`, `save_thread_note`,
        # seed korpusu). Reguła powtórzona przy każdej z nich rozjeżdża się przy pierwszej nowej.
        odrzuc_wlasny_frontmatter(note.body)
        path = _resolve_within(self._notes_dir, f"{note.id}.md")
        # ``mkdir`` POD osłoną, tak samo jak sam zapis: katalog firmy/projektu powstaje dopiero
        # przy pierwszej notatce, więc read-only wolumen bazy wiedzy odmawia WŁAŚNIE tutaj —
        # i dotąd wychodził surowym ``PermissionError``, czyli dla wołającego jak defekt kodu,
        # a nie jak oczekiwany stan infrastruktury, którym jest.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WriteError(
                f"nie udało się przygotować katalogu notatki {note.id}: {exc}"
            ) from exc
        _atomic_create(path, _render(note.metadata, note.body))

    def digest(self, note_id: str) -> str:
        """Skrót pliku notatki albo pusty napis, gdy notatki nie ma (patrz port)."""
        path = _resolve_within(self._notes_dir, f"{note_id}.md")
        if not path.is_file():
            return ""
        raw = _read_bytes_or_none(path)
        return hashlib.sha256(raw).hexdigest() if raw is not None else ""

    def content_with_digest(self, note_id: str) -> tuple[str, str]:
        """Treść pliku i jej skrót z jednego odczytu bajtów (patrz port)."""
        path = _resolve_within(self._notes_dir, f"{note_id}.md")
        if not path.is_file():
            return "", ""
        raw = _read_bytes_or_none(path)
        if raw is None:
            return "", ""
        try:
            tresc = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            # Fail-closed i GŁOŚNO w dzienniku: plik jest, ale nie jest tym, za co się podaje.
            # Cicha zgoda oznaczałaby tu mutację notatki, której kopii nie umiemy zapisać.
            logger.warning("Notatka %s nie jest poprawnym UTF-8: %s", path, exc)
            return "", ""
        return tresc, hashlib.sha256(raw).hexdigest()

    def overwrite_body(self, note_id: str, body: str, *, expected_sha256: str) -> None:
        """Podmień treść pod ZASTANYM nagłówkiem pliku, atomowo (ADR 0065) — nigdy w miejscu.

        Nagłówek przepisujemy BAJTOWO z pliku, zamiast składać go z modelu. Skład z modelu
        deklarował „metadane zostają nietknięte", a przy każdej edycji gubił komentarze YAML
        i pola spoza schematu ``NoteMetadata`` (``extra="ignore"``), dokładał puste pola
        schematu i zmieniał wcięcie list. Notatkę uzupełnioną ręcznie edycja agenta cicho
        okrawała do tego, co model umiał nazwać.

        ``os.replace`` na w pełni zapisanym pliku tymczasowym: czytelnik widzi albo starą, albo
        nową treść, nigdy połowy. Zapis „w miejscu" (truncate + write) zostawiałby przy awarii
        w połowie notatkę uciętą — czyli cichą utratę wiedzy pod pozorem udanej edycji.

        Odmawiamy, gdy notatki NIE MA: ten czasownik ma zmieniać, nie tworzyć. Gdyby tworzył,
        literówka w identyfikatorze rodziłaby po cichu nowy plik obok tego, który miał być
        poprawiony.
        """
        odrzuc_wlasny_frontmatter(body)
        path = _resolve_within(self._notes_dir, f"{note_id}.md")
        raw = _read_bytes_or_none(path) if path.is_file() else None
        if raw is None:
            raise WriteError(f"notatka nie istnieje, nie ma czego podmienić: {note_id}")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise WriteError(
                f"notatka {note_id} zmieniła się od odczytu — nie nadpisuję. "
                "Przeczytaj ją ponownie i powtórz zmianę."
            )
        try:
            tresc_pliku = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WriteError(
                f"notatka {note_id} nie jest poprawnym UTF-8 — nie podmieniam jej treści"
            ) from exc
        _atomic_replace(path, f"{_naglowek_pliku(tresc_pliku, note_id)}\n\n{body.strip()}\n")

    def delete(self, note_id: str, *, expected_sha256: str) -> None:
        """Usuń POJEDYNCZY plik notatki (ADR 0065). Katalogów nie ruszamy — nawet pustych.

        ``expected_sha256`` sprawdzamy TĄ SAMĄ drogą co w ``overwrite``: różnica skrótu znaczy
        „notatka zmieniła się od odczytu", a wtedy migawka zabezpiecza wersję sprzed zmiany —
        skasowanie zabrałoby wersję pośrednią bez kopii.
        """
        path = _resolve_within(self._notes_dir, f"{note_id}.md")
        raw = _read_bytes_or_none(path) if path.is_file() else None
        if raw is None:
            raise WriteError(f"notatka nie istnieje: {note_id}")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise WriteError(
                f"notatka {note_id} zmieniła się od odczytu — nie usuwam. "
                "Przeczytaj ją ponownie i powtórz operację."
            )
        try:
            path.unlink()
        except OSError as exc:
            raise WriteError(f"nie udało się usunąć notatki {note_id}: {exc}") from exc


def _read_bytes_or_none(path: Path) -> bytes | None:
    """Bajty notatki albo ``None``, gdy notatki NIE MA; nieczytelna notatka jest GŁOŚNA.

    ``is_file()`` i ``read_bytes()`` to DWA podejścia do dysku, a od ADR 0065 istnieje druga
    droga zapisu (``File(edit|delete)``) i biegnie ona równolegle do bramki mutacji. Notatka
    skasowana między sprawdzeniem a odczytem dawała surowy ``FileNotFoundError`` w środku tej
    bramki, choć jej kontrakt mówi „brak notatki → pusty skrót / odmowa", nie „wyjątek".

    Odmowa dostępu i błąd I/O to jednak stan PRZECIWNY: notatka jest, tylko nie da się jej
    przeczytać. Oddane jako ``None`` wychodziło z bramki komunikatem „notatka nie istnieje" —
    fail-closed, więc nic nie ginęło, ale zdanie było nieprawdziwe i nie zostawiało śladu.
    """
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Nie udało się odczytać notatki %s: %s", path, exc)
        raise WriteError(
            f"nie udało się odczytać notatki {path.name}: {exc.strerror or type(exc).__name__}"
        ) from exc


def render_note(note: Note) -> str:
    """Publiczny kształt pliku notatki — jedno źródło dla zapisu i dla MIGAWKI (ADR 0065).

    Migawka renderowana osobno rozjechałaby się z formatem zapisu przy pierwszej zmianie
    frontmatteru, a zauważono by to dopiero przy próbie odtworzenia skasowanej notatki.
    """
    return _render(note.metadata, note.body)


def _render(metadata: NoteMetadata, body: str) -> str:
    """Złóż plik notatki: frontmatter YAML + treść, spójnie z formatem odczytu."""
    front = yaml.safe_dump(
        metadata.model_dump(mode="python"),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"{_FRONTMATTER_FENCE}\n{front}{_FRONTMATTER_FENCE}\n\n{body.strip()}\n"


def _naglowek_pliku(raw: str, note_id: str) -> str:
    """Blok frontmatteru DOKŁADNIE tak, jak stoi w pliku — z komentarzami i polami spoza schematu.

    Dzielimy tak samo jak czytelnik (``_split_frontmatter``): na pierwszym ``---`` i pierwszym
    ``\n---``, więc poziome kreski w treści zostają nietknięte.
    """
    if not raw.startswith(_FRONTMATTER_FENCE):
        raise WriteError(
            f"notatka {note_id} nie zaczyna się od frontmatteru — nie podmieniam jej treści"
        )
    _, _, reszta = raw.partition(_FRONTMATTER_FENCE)
    front, fence, _ = reszta.partition(f"\n{_FRONTMATTER_FENCE}")
    if not fence:
        raise WriteError(f"notatka {note_id} nie ma zamykającego '---' — nie podmieniam treści")
    return f"{_FRONTMATTER_FENCE}{front}{fence}"


def _resolve_within(notes_dir: Path, relpath: str) -> Path:
    """Rozwiąż ścieżkę notatki WEWNĄTRZ ``notes_dir``; ``WriteError`` przy ucieczce poza katalog.

    Obrona w głąb (wzorem ``filesystem_workspace.py``/``MarkdownNotesRepository.get``): dziś
    ``note.id`` przechodzi przez slugifikację serwisu (ADR 0006), więc to nie jest dziura — ale
    ta gwarancja stała dotąd wyłącznie na dyscyplinie wołających, a to JEDYNE miejsce w systemie,
    które PISZE do bazy wiedzy czytanej przez agenta.

    Katalog bazy rozwijamy RAZ i dopiero do niego doklejamy ``relpath``. Dwa niezależne
    ``resolve()`` potrafią dać różne zapisy TEGO SAMEGO katalogu (krótka nazwa 8.3 w ``TEMP``
    na Windows, ``/var`` → ``/private/var`` na macOS), a wtedy legalny zapis bywa odrzucany
    jako ucieczka.
    """
    base = notes_dir.resolve()
    candidate = (base / relpath).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise WriteError(f"ścieżka notatki poza katalogiem bazy wiedzy: {relpath!r}") from exc
    return candidate


def _atomic_create(path: Path, content: str) -> None:
    """Zapis atomowy i create-only.

    Pełna treść trafia do pliku tymczasowego, a ``os.link`` publikuje ją pod docelową nazwą —
    atomowo i tylko, gdy cel nie istnieje. Nazwa tymczasowego jest UNIKALNA per zapis
    (``pid`` + ``uuid``): dwa RÓWNOLEGŁE pisarze tego samego id (deterministyczny id notatki ze
    spotkania + pula wątków async, ADR 0043) NIE mogą dzielić jednego pliku tymczasowego — inaczej
    ``write_text`` jednego wątku (truncate) nadpisałby inode, do którego drugi wątek właśnie
    dolinkował opublikowaną notatkę. Kolizja finalnej nazwy (``FileExists``) → ``NoteExistsError``
    (wyróżniona, by ścieżka idempotentna zaraportowała „już złożona"); inny I/O → ``WriteError``.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.link(tmp, path)
    except FileExistsError as exc:
        raise NoteExistsError(f"notatka już istnieje: {path.name}") from exc
    except OSError as exc:
        raise WriteError(f"nie udało się zapisać notatki {path.name}: {exc}") from exc
    finally:
        # Sprzątanie nie może przykryć właściwego błędu (jak w ``_atomic_replace`` niżej):
        # ``OSError`` z ``unlink`` zastąpiłby ``NoteExistsError``, a to na nim stoi idempotencja
        # notatki ze spotkania — „już złożona" zamieniłoby się w twardą porażkę zapisu.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _atomic_replace(path: Path, content: str) -> None:
    """Podmiana atomowa: pełny zapis do pliku tymczasowego, potem ``os.replace``.

    Odwrotność ``_atomic_create``: tam ``os.link`` chroni PRZED nadpisaniem, tu ``os.replace``
    nadpisanie wykonuje — świadomie i w jednym kroku widocznym dla czytelnika. Plik tymczasowy
    leży w tym samym katalogu, bo ``os.replace`` jest atomowe tylko w obrębie systemu plików.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise WriteError(f"nie udało się podmienić notatki {path.name}: {exc}") from exc
    finally:
        # Sprzątanie nie może przykryć właściwego błędu (patrz ``filesystem_snapshots``).
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
