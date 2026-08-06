"""Skrzynka nadawcza rozmowy na dysku (port ``OutboxRepository``).

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`.

Leży pod ``workspace_root/<kanał>/<hash rozmowy>/outputs`` — czyli WEWNĄTRZ katalogu roboczego
rozmowy, tego samego, który wykonawca dostaje jako ``cwd``. Dzięki temu model wskazuje ją ścieżką
WZGLĘDNĄ (``outputs/raport.pdf``), a izolacja rozmów bierze się z tego samego mechanizmu, co
izolacja brudnopisu.

**Granicą jest katalog rozmowy, nie korzeń workspace'u** — i to jest różnica, którą widać dopiero
przy uruchomieniu. Ograniczenie do korzenia przepuszcza `ln -s ../<hash innej rozmowy> outputs`:
dowiązanie rozwiązuje się w obrębie korzenia, więc wypis oddawałby pliki CUDZEJ rozmowy do
wysłania, a sprzątanie po udanej wysyłce by je skasowało. Stąd rozwiązanie dwuetapowe (katalog
rozmowy, potem skrzynka w nim) plus odrzucenie skrzynki będącej dowiązaniem.

Zbieramy wyłącznie zwykłe pliki leżące bezpośrednio w skrzynce, a dowiązania pomijamy — ale
**to nie jest granica poufności i nie należy jej tak czytać**: `cp /mnt/system/notes/…/tajne.md
outputs/raport.md` daje ten sam skutek i żaden guard tutaj go nie dotyka. Te sprawdzenia mówią
dokładnie tyle: kolektor nie wychodzi poza skrzynkę i nie kasuje plików spoza niej. O tym, czego
model NIE wyniesie, decyduje warstwa wyżej (pre-wiązany cel dostawy, ADR 0026) i to, czego
w kontenerze wykonawcy nie ma (ADR 0007).

Izolacja rozmów w powłoce jest KONWENCJĄ, nie zamknięciem: wolumen brudnopisu jest wspólny,
`cwd` tylko ustawiany. Pochodzenia plików pilnuje migawka w ``OutboxDelivery.snapshot``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import Deliverable, OutboxEntry

logger = logging.getLogger(__name__)

# Nazwa podkatalogu skrzynki wewnątrz katalogu roboczego rozmowy. Jedno źródło dla adaptera
# (gdzie szukać) i dla opisu narzędzia ``Bash`` (gdzie kazać zapisywać).
OUTBOX_DIRNAME = "outputs"

_FALLBACK_CONTENT_TYPE = "application/octet-stream"

# Ile pozycji oglądamy w jednej turze. Zawartość skrzynki dyktuje model z powłoką, więc pętla
# tworząca dziesiątki tysięcy plików nie może zamienić jednej tury w wielominutowe sprzątanie.
# Nadmiar zostaje i obsłuży go tura następna — praca per tura jest ograniczona, a skrzynka
# i tak się opróżnia.
_SCAN_CEILING = 200


class FilesystemOutboxRepository:
    """Wypis, odczyt i sprzątanie skrzynki ``<korzeń>/<katalog rozmowy>/outputs``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def list_entries(self, dirpath: str) -> list[OutboxEntry]:
        directory = self._outbox(dirpath)
        if directory is None:
            return []
        entries: list[OutboxEntry] = []
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink() or not entry.is_file() or entry.name.endswith(".tmp"):
                continue
            entries.append(OutboxEntry(name=entry.name, size=entry.stat().st_size))
            if len(entries) == _SCAN_CEILING:
                logger.warning(
                    "Skrzynka %s ma ponad %d pozycji — resztę obsłuży kolejna tura.",
                    dirpath,
                    _SCAN_CEILING,
                )
                break
        return entries

    def read(self, dirpath: str, name: str) -> Deliverable | None:
        path = self._entry_path(dirpath, name)
        if path is None or not path.is_file():
            return None
        return Deliverable(name=name, content=path.read_bytes(), content_type=_content_type(name))

    def discard(self, dirpath: str, name: str) -> None:
        path = self._entry_path(dirpath, name)
        if path is None:
            return
        # ``missing_ok`` czyni operację idempotentną: ponowiona dostawa nie wywraca się na pliku,
        # który zdążył już zniknąć (sprzątanie TTL, ręczna interwencja na wolumenie).
        path.unlink(missing_ok=True)

    def _outbox(self, dirpath: str) -> Path | None:
        """Katalog skrzynki albo ``None``, gdy go nie ma lub nie należy do TEJ rozmowy."""
        conversation = _resolve_within(self._root, dirpath)
        outbox = conversation / OUTBOX_DIRNAME
        # ``is_symlink`` sprawdzamy PRZED ``resolve``, bo ``resolve`` podąża za dowiązaniem
        # i zwróciłby cel, który wobec korzenia wygląda niewinnie.
        if outbox.is_symlink() or not outbox.is_dir():
            return None
        resolved = outbox.resolve()
        if resolved.parent != conversation.resolve():
            logger.warning("Skrzynka %s wskazuje poza katalog rozmowy — pomijam.", dirpath)
            return None
        return resolved

    def _entry_path(self, dirpath: str, name: str) -> Path | None:
        """Ścieżka pozycji; ``WriteError`` przy nazwie ze ścieżką, ``None`` przy braku skrzynki."""
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise WriteError(f"niedozwolona nazwa pliku w skrzynce: {name!r}")
        outbox = self._outbox(dirpath)
        if outbox is None:
            return None
        # Dowiązanie WPISU rozpoznajemy przed ``resolve``, bo tamto podąża za celem: ścieżka do
        # notatki wyszłaby wtedy poza skrzynkę i dostalibyśmy twardy ``WriteError`` zamiast
        # łagodnej odmowy. Wypis takich pozycji nie zwraca, więc to obrona w głąb — ale ma
        # odmawiać, a nie wywracać dostawy całej tury.
        if (outbox / name).is_symlink():
            return None
        return _resolve_within(outbox, name)


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
