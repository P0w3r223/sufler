"""Katalog roboczy agenta na dysku (porty ``WorkspaceRepository``/``WorkspaceWriter``).

Zapis jest **atomowy i create-only** (``os.link`` — jak ``markdown_notes_writer``): kolizja to
błąd, nigdy ciche nadpisanie. KAŻDA ścieżka (zapis i odczyt) przechodzi przez ``resolve()`` +
``relative_to(root)`` — obrona w głąb przed path-traversal (nazwa pliku do odczytu bywa od modelu),
więc odczyt nigdy nie wyjdzie poza korzeń workspace (nie sięgnie ``.env``, cache tokenu itd.).
Korzeń workspace leży POZA repo i ``data/`` (dane operacyjne, ADR 0018) — poisoned artefakt nie
zanieczyści bazy wiedzy, którą agent czyta.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workmate.core.domain.workspace import WorkspaceFile
from workmate.core.errors import WriteError


class FilesystemWorkspaceWriter:
    """Tworzy pliki robocze w drzewie ``workspace_root/<kanał>/<hash rozmowy>/<plik>``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def exists(self, relpath: str) -> bool:
        return _resolve_within(self._root, relpath).is_file()

    def create(self, relpath: str, content: str) -> WorkspaceFile:
        path = _resolve_within(self._root, relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_create(path, content.encode("utf-8"))
        return WorkspaceFile(name=path.name, relpath=relpath, size=len(content.encode("utf-8")))

    def create_bytes(self, relpath: str, data: bytes) -> WorkspaceFile:
        path = _resolve_within(self._root, relpath)
        path.parent.mkdir(parents=True, exist_ok=True)
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
            if entry.is_file() and not entry.name.endswith(".tmp"):
                files.append(
                    WorkspaceFile(
                        name=entry.name,
                        relpath=f"{scope_dir}/{entry.name}",
                        size=entry.stat().st_size,
                    )
                )
        return files

    def read(self, scope_dir: str, name: str) -> str | None:
        path = _resolve_in_scope(self._root, scope_dir, name)
        return path.read_text(encoding="utf-8") if path is not None else None

    def read_bytes(self, scope_dir: str, name: str) -> bytes | None:
        path = _resolve_in_scope(self._root, scope_dir, name)
        return path.read_bytes() if path is not None else None


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
    # Ucieczka POZA KORZEŃ zostaje głośna (``WriteError``, jak dotąd) — to jawna próba wyjścia
    # ścieżką i wołający ma o niej wiedzieć. Trafienie w INNĄ ROZMOWĘ (symlink w obrębie korzenia)
    # zwraca ``None``, czyli „nie ma takiego pliku": model nie ma się z czego dowiedzieć, czyj
    # plik istnieje obok, a odpowiedź jest nieodróżnialna od zwykłej pomyłki w nazwie.
    candidate = _resolve_within(root, f"{scope_dir}/{name}")
    if candidate.parent != base or not candidate.is_file():
        return None
    return candidate


def _resolve_within(root: Path, relpath: str) -> Path:
    """Rozwiąż ścieżkę względną do bezwzględnej WEWNĄTRZ korzenia; ``WriteError`` przy ucieczce."""
    candidate = (root / relpath).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise WriteError(f"ścieżka poza katalogiem roboczym: {relpath!r}") from exc
    return candidate


def prune_stale(root: Path, *, older_than: timedelta, now: datetime) -> int:
    """Usuń katalogi rozmów bez aktywności dłużej niż ``older_than`` (TTL sprzątanie, ADR 0018).

    „Aktywność" = najnowszy mtime pliku w katalogu rozmowy (albo mtime samego katalogu, gdy pusty).
    Wołane raz na starcie pollera — backstop przeciw nieograniczonemu rośnięciu scratcha. Zwraca
    liczbę usuniętych katalogów rozmów. Błędy I/O połykamy (sprzątanie nie może kłaść startu).
    """
    if not root.is_dir():
        return 0
    cutoff = now - older_than
    removed = 0
    for channel_dir in root.iterdir():
        if not channel_dir.is_dir():
            continue
        for scope_dir in channel_dir.iterdir():
            if not scope_dir.is_dir():
                continue
            newest = max(
                (f.stat().st_mtime for f in scope_dir.rglob("*") if f.is_file()),
                default=scope_dir.stat().st_mtime,
            )
            if datetime.fromtimestamp(newest, tz=timezone.utc) < cutoff:
                shutil.rmtree(scope_dir, ignore_errors=True)
                removed += 1
    return removed


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
        tmp.unlink(missing_ok=True)
