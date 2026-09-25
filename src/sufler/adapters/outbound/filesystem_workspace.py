"""Katalog roboczy agenta na dysku (porty ``WorkspaceRepository``/``WorkspaceWriter``).

Zapis jest **atomowy i create-only** (``os.link`` — jak ``markdown_notes_writer``): kolizja to
błąd, nigdy ciche nadpisanie. KAŻDA ścieżka (zapis i odczyt) przechodzi przez ``resolve()`` +
``relative_to(root)`` — obrona w głąb przed path-traversal (nazwa pliku do odczytu bywa od modelu),
więc odczyt nigdy nie wyjdzie poza korzeń workspace (nie sięgnie ``.env``, cache tokenu itd.).
Korzeń workspace leży POZA repo i ``data/`` (dane operacyjne, ADR 0018) — poisoned artefakt nie
zanieczyści bazy wiedzy, którą agent czyta.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from sufler.core.domain.workspace import WorkspaceFile
from sufler.core.errors import RepositoryError, WriteError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class FilesystemWorkspaceWriter:
    """Tworzy pliki robocze w drzewie ``workspace_root/<kanał>/<hash rozmowy>/<plik>``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def exists(self, relpath: str) -> bool:
        return _resolve_within(self._root, relpath).is_file()

    def create(self, relpath: str, content: str) -> WorkspaceFile:
        path = _resolve_within(self._root, relpath)
        _prepare_dir(path)
        _atomic_create(path, content.encode("utf-8"))
        return WorkspaceFile(name=path.name, relpath=relpath, size=len(content.encode("utf-8")))

    def create_bytes(self, relpath: str, data: bytes) -> WorkspaceFile:
        path = _resolve_within(self._root, relpath)
        _prepare_dir(path)
        _atomic_create(path, data)
        return WorkspaceFile(name=path.name, relpath=relpath, size=len(data))


class FilesystemWorkspaceRepository:
    """Odczyt (list/read) plików roboczych rozmowy — tylko w obrębie korzenia workspace."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def list(self, scope_dir: str) -> list[WorkspaceFile]:
        directory = _resolve_within(self._root, scope_dir)
        if not directory.is_dir():
            return []
        files: list[WorkspaceFile] = []
        for entry in sorted(directory.iterdir()):
            # Dowiązania pomijamy jak wypis skrzynki (``filesystem_outbox.list_entries``): wpis
            # wskazujący poza katalog rozmowy i tak dostanie ``None`` przy odczycie, więc pokazany
            # w wypisie obiecywałby plik, którego ``read`` odmówi.
            if entry.is_symlink():
                continue
            if not entry.is_file() or entry.name.endswith(".tmp"):
                continue
            # ``is_file()`` i ``stat()`` to DWA podejścia do dysku, a między nimi biegnie powłoka
            # modelu — tego samego, który tym wypisem ogląda swój brudnopis i tą samą powłoką
            # kasuje w nim pliki. Pozycja zniknięta w tym oknie ma wypaść z wypisu, a nie zabrać
            # całej listy razem z plikami, które nadal są.
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            files.append(
                WorkspaceFile(name=entry.name, relpath=f"{scope_dir}/{entry.name}", size=size)
            )
        return files

    def read(self, scope_dir: str, name: str) -> str | None:
        path = _resolve_in_scope(self._root, scope_dir, name)
        if path is None:
            return None
        return _or_none(path, lambda p: p.read_text(encoding="utf-8"))

    def read_bytes(self, scope_dir: str, name: str) -> bytes | None:
        path = _resolve_in_scope(self._root, scope_dir, name)
        if path is None:
            return None
        return _or_none(path, lambda p: p.read_bytes())


def _or_none(path: Path, czytaj: Callable[[Path], _T]) -> _T | None:
    """Wynik odczytu albo ``None``, gdy pliku już NIE MA; nieczytelny plik zostaje GŁOŚNY.

    ``_resolve_in_scope`` woła ``is_file()``, a treść czytamy dopiero potem — między jednym
    a drugim biegnie powłoka tego samego modelu, który tym narzędziem czyta swój brudnopis,
    i ta sama powłoka kasuje w nim pliki. Zniknięcie w tym oknie ma dać „nie ma takiego pliku"
    (kontrakt portu), a nie surowy ``FileNotFoundError`` w środku tury.

    Odmowa dostępu albo błąd I/O to stan PRZECIWNY — plik jest — więc ``None`` mówiłoby modelowi,
    że pliku nie ma, i kazało szukać literówki w nazwie zamiast pokazać prawdziwą przyczynę.
    Koperta narzędzia zamienia ``RepositoryError`` na ``{"error": ...}``, więc tura się nie
    wywraca. W komunikacie zostaje sama nazwa pliku: ścieżka bezwzględna wolumenu nie ma po co
    jechać do modelu.
    """
    try:
        return czytaj(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("Nie udało się odczytać pliku roboczego %s: %s", path, exc)
        raise RepositoryError(
            f"nie udało się odczytać pliku {path.name}: {exc.strerror or type(exc).__name__}"
        ) from exc


def _prepare_dir(path: Path) -> None:
    """Utwórz katalog rozmowy pod zapis; odmowa systemu plików → ``WriteError``, nie ``OSError``.

    ``mkdir`` stał poza osłoną ``_atomic_create``, więc wolumen brudnopisu tylko do odczytu
    (albo wyczerpane i-node) wychodził do wołającego surowym wyjątkiem — czyli jako defekt kodu,
    mimo że to zwykły, oczekiwany stan infrastruktury.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WriteError(f"nie udało się przygotować katalogu pliku {path.name}: {exc}") from exc


def _resolve_in_scope(root: Path, scope_dir: str, name: str) -> Path | None:
    """Rozwiąż plik ``name`` W KATALOGU ROZMOWY; ``None``, gdy to nie jest tam zwykły plik.

    ``_resolve_within`` pilnuje wyłącznie KORZENIA brudnopisu, a to za mało dla odczytu:
    ``resolve()`` rozwija dowiązania, więc symlink ``../<hash innej rozmowy>/plik.pdf`` ląduje
    wewnątrz korzenia i przechodzi — czytelnik dostaje cudzy plik, mimo że nazwa jest czysta.
    Symlink da się założyć powłoką, a ``File`` zostaje na powierzchni WŁAŚNIE w układzie
    z powłoką, więc to jest droga realna, nie teoretyczna. Warunek jest tu ostrzejszy:
    rozwiązany rodzic musi być DOKŁADNIE rozwiązanym katalogiem tej rozmowy — czyli tą samą
    granicą, którą infra ADR 0012 wymusza montażem wyłącznie podkatalogu scope'a.
    """
    base = _resolve_within(root, scope_dir)
    # Dowiązany KATALOG ROZMOWY rozpoznajemy PRZED ``resolve`` (wzorem ``filesystem_outbox``):
    # ``resolve`` przepisuje wtedy RÓWNIEŻ ``base`` na cel dowiązania, więc porównanie rodzica
    # z ``base`` zachodzi i cały warunek niżej jest ślepy. Symlink na katalog zakłada się tą samą
    # powłoką co symlink na plik, więc to ta sama droga, nie inna.
    if (root / scope_dir).is_symlink():
        logger.warning("Katalog rozmowy %s jest dowiązaniem — odczyt odmówiony.", scope_dir)
        return None
    # Ucieczka POZA KORZEŃ zostaje głośna (``WriteError``, jak dotąd) — to jawna próba wyjścia
    # ścieżką i wołający ma o niej wiedzieć. Trafienie w INNĄ ROZMOWĘ (symlink w obrębie korzenia)
    # zwraca ``None``, czyli „nie ma takiego pliku": model nie ma się z czego dowiedzieć, czyj
    # plik istnieje obok, a odpowiedź jest nieodróżnialna od zwykłej pomyłki w nazwie.
    candidate = _resolve_within(root, f"{scope_dir}/{name}")
    if candidate.parent != base or not candidate.is_file():
        return None
    return candidate


def _resolve_within(root: Path, relpath: str) -> Path:
    """Rozwiąż ścieżkę względną do bezwzględnej WEWNĄTRZ korzenia; ``WriteError`` przy ucieczce.

    Korzeń rozwijamy RAZ i dopiero do niego doklejamy ``relpath``. Dwa niezależne ``resolve()``
    potrafią dać różne zapisy TEGO SAMEGO katalogu (krótka nazwa 8.3 w ``TEMP`` na Windows,
    ``/var`` → ``/private/var`` na macOS), a wtedy ``relative_to`` odrzuca legalny zapis jako
    ucieczkę.
    """
    base = root.resolve()
    candidate = (base / relpath).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise WriteError(f"ścieżka poza katalogiem roboczym: {relpath!r}") from exc
    return candidate


def prune_stale(root: Path, *, older_than: timedelta, now: datetime) -> int:
    """Usuń katalogi rozmów bez aktywności dłużej niż ``older_than`` (TTL sprzątanie, ADR 0018).

    „Aktywność" = najnowszy mtime pliku w katalogu rozmowy (albo mtime samego katalogu, gdy pusty).
    Wołane raz na starcie pollera — backstop przeciw nieograniczonemu rośnięciu scratcha. Zwraca
    liczbę FAKTYCZNIE usuniętych katalogów rozmów.

    Błędy I/O połykamy NA KAŻDYM POZIOMIE SPACERU (korzeń, katalog kanału, katalog rozmowy)
    i zawsze najwężej, jak się da: ``PermissionError`` na jednym kanale ma kosztować ten kanał,
    a nie resztę drzewa — i na pewno nie start drzwi, bo jedyną szkodą z nieudanego sprzątania
    jest niesprzątnięty brudnopis.
    """
    if not root.is_dir():
        return 0
    cutoff = now - older_than
    removed = 0
    for channel_dir in _entries_or_empty(root):
        if not channel_dir.is_dir():
            continue
        for scope_dir in _entries_or_empty(channel_dir):
            if not scope_dir.is_dir():
                continue
            if _prune_one(scope_dir, cutoff=cutoff):
                removed += 1
    return removed


def _entries_or_empty(directory: Path) -> list[Path]:
    """Zawartość katalogu albo pusta lista, gdy nie da się go przejrzeć.

    ``iterdir`` na korzeniu i na katalogu KANAŁU stał dotąd nago, mimo obietnicy z docstringu
    ``prune_stale``: odmowa dostępu (wolumen montowany z innym uid, katalog zapisany przez
    wykonawcę) leciała prosto do ``teams_graph.app``, gdzie nikt jej nie łapie — i drzwi nie
    wstawały.
    """
    try:
        return list(directory.iterdir())
    except OSError as exc:
        logger.warning("Nie udało się przejrzeć katalogu %s przy sprzątaniu: %s", directory, exc)
        return []


def _prune_one(scope_dir: Path, *, cutoff: datetime) -> bool:
    """Usuń JEDEN katalog rozmowy, gdy przeterminowany; zwróć, czy naprawdę zniknął.

    Licznik ma liczyć usunięcia, nie próby — nieudany ``rmtree`` (uprawnienia, plik w użyciu)
    zostawia katalog na dysku, a zliczony wyglądałby w logu jak sprzątnięty.
    """
    try:
        newest = max(
            (f.stat().st_mtime for f in scope_dir.rglob("*") if f.is_file()),
            default=scope_dir.stat().st_mtime,
        )
        if datetime.fromtimestamp(newest, tz=UTC) >= cutoff:
            return False
        shutil.rmtree(scope_dir)
    except OSError as exc:
        logger.warning("Nie udało się sprzątnąć katalogu rozmowy %s: %s", scope_dir, exc)
        return False
    return True


def _atomic_create(path: Path, data: bytes) -> None:
    """Zapis atomowy i create-only (UNIKALNY temp + ``os.link``); kolizja/I/O → ``WriteError``.

    Plik tymczasowy ma unikalną nazwę (``mkstemp``) — dwa równoległe ``create`` na tę samą nazwę
    docelową nie ścigają się o wspólny temp (istotne przy agencie w pętli / multi-user).
    Bajty, nie tekst: tą samą drogą idzie plik tekstowy modelu i odłożony załącznik binarny
    (ADR 0064), a dwie ścieżki zapisu oznaczałyby dwa miejsca na pomyłkę w atomowości.
    """
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        tmp.write_bytes(data)
        os.link(tmp, path)
    except FileExistsError as exc:
        raise WriteError(f"plik już istnieje: {path.name}") from exc
    except OSError as exc:
        raise WriteError(f"nie udało się zapisać pliku {path.name}: {exc}") from exc
    finally:
        # Sprzątanie nie może przykryć właściwego błędu (jak w ``markdown_notes_writer``):
        # ``unlink`` z ``finally`` rzucający ``OSError`` zastąpiłby ``WriteError`` o kolizji
        # wyjątkiem o pliku tymczasowym, którego wołający nie umie zinterpretować.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
