"""Skrzynka nadawcza rozmowy na dysku (port ``OutboxRepository``).

Leży pod ``workspace_root/<kanał>/<hash rozmowy>/outputs`` — czyli WEWNĄTRZ katalogu roboczego
rozmowy, tego samego, który wykonawca dostaje jako ``cwd``. Dzięki temu model wskazuje ją ścieżką
WZGLĘDNĄ (``outputs/raport.pdf``), a izolacja rozmów bierze się z tego samego mechanizmu, co
izolacja brudnopisu — nie z drugiego, równoległego.

Zbieramy WYŁĄCZNIE zwykłe pliki leżące bezpośrednio w skrzynce. Dowiązanie symboliczne jest
pomijane świadomie i jest to granica bezpieczeństwa, nie porządek: model ma bazę wiedzy
zamontowaną do odczytu, więc ``ln -s /mnt/system/notes/…/tajne.md outputs/`` byłby drogą wyniesienia
treści, której nie wolno mu wysłać. ``resolve()`` + ``relative_to`` domykają to samo od drugiej
strony — dokładnie jak w ``filesystem_workspace``.
"""

from __future__ import annotations

from pathlib import Path

from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import Deliverable

# Nazwa podkatalogu skrzynki wewnątrz katalogu roboczego rozmowy. Jedno źródło dla adaptera
# (gdzie szukać) i dla opisu narzędzia ``Bash`` (gdzie kazać zapisywać).
OUTBOX_DIRNAME = "outputs"

_FALLBACK_CONTENT_TYPE = "application/octet-stream"


class FilesystemOutboxRepository:
    """Odczyt i sprzątanie skrzynki ``<korzeń>/<katalog rozmowy>/outputs``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def collect(self, dirpath: str) -> list[Deliverable]:
        directory = _resolve_within(self._root, f"{dirpath}/{OUTBOX_DIRNAME}")
        if not directory.is_dir():
            return []
        items: list[Deliverable] = []
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink() or not entry.is_file() or entry.name.endswith(".tmp"):
                continue
            items.append(
                Deliverable(
                    name=entry.name,
                    content=entry.read_bytes(),
                    content_type=_content_type(entry.name),
                )
            )
        return items

    def discard(self, dirpath: str, name: str) -> None:
        # Granicą jest SKRZYNKA, nie korzeń workspace'u. Samo ``resolve()`` względem korzenia
        # przepuściłoby ``../plik`` (katalog roboczy tej rozmowy — materiał źródłowy modelu)
        # i ``../../inna-rozmowa/outputs/plik`` (CUDZA skrzynka), bo obie ścieżki leżą wewnątrz
        # korzenia. Dziś nazwy pochodzą z ``collect``, więc separatora tam nie ma — guard jest
        # obroną w głąb, bo ``discard`` jest metodą portu i wołający może się zmienić.
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise WriteError(f"niedozwolona nazwa pliku w skrzynce: {name!r}")
        outbox = _resolve_within(self._root, f"{dirpath}/{OUTBOX_DIRNAME}")
        path = _resolve_within(outbox, name)
        # ``missing_ok`` czyni operację idempotentną: ponowiona dostawa nie wywraca się na pliku,
        # który zdążył już zniknąć (sprzątanie TTL, ręczna interwencja na wolumenie).
        path.unlink(missing_ok=True)


def _content_type(name: str) -> str:
    """Typ MIME z rozszerzenia wg jednoźródłowej mapy formatów; nieznane → strumień bajtów.

    Nieznanego rozszerzenia nie odrzucamy TUTAJ — o tym, co wolno wysłać, decyduje rdzeń
    (``OutboxDelivery``), żeby powód odrzucenia trafił do raportu zamiast zniknąć w adapterze.
    """
    _, _, ext = name.rpartition(".")
    return FILE_REPLY_FORMATS.get(ext.strip().lower(), _FALLBACK_CONTENT_TYPE)


def _resolve_within(root: Path, relpath: str) -> Path:
    """Rozwiąż ścieżkę względną WEWNĄTRZ korzenia; ``WriteError`` przy ucieczce poza niego."""
    candidate = (root / relpath).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise WriteError(f"ścieżka poza katalogiem roboczym: {relpath!r}") from exc
    return candidate
