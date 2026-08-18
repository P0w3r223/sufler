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

Izolacja rozmów w powłoce jest KONWENCJĄ, nie zamknięciem — stan faktyczny, ryzyko szczątkowe
i model zaufania opisuje ADR 0010 paczki (`izolacja-rozmow-w-powloce`), jedno źródło dla całego
kodu. Pochodzenia plików pilnuje tutaj migawka w ``OutboxDelivery.snapshot``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import Deliverable, OutboxEntry, OutboxReadError

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

# Sufit BAJTÓW wczytywanych jedną pozycją. Rozmiar z ``list_entries`` jest mierzony przy SKANIE,
# a treść pisze model z powłoką — plik rosnący między skanem a dostawą wszedłby do pamięci
# w całości, mimo że rdzeń odrzucił go już jako „ponad limit". Sufit stoi POWYŻEJ najwyższego
# limitu dostawy, jaki da się skonfigurować (``WORKMATE_WORKSPACE_MAX_FILE_MB`` ma w konfiguracji
# sufit 24 MB), więc nie odbiera niczego, co i tak byłoby wysłane.
_READ_CEILING_BYTES = 24 * 1024 * 1024


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
            # ``is_file()`` i ``stat()`` to DWA podejścia do dysku, a skrzynkę zapełnia i opróżnia
            # POWŁOKA modelu biegnąca obok drzwi (plus sprzątanie TTL po tym samym drzewie).
            # Pozycja zniknięta w tym oknie ma wypaść ze skanu, a nie zabrać całej dostawy tury —
            # wypis leci też na starcie tury (``snapshot``), więc wywrócony skan kosztuje sąsiedni
            # plik, który dało się wysłać.
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            entries.append(OutboxEntry(name=entry.name, size=size))
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
        # Czytamy o bajt WIĘCEJ niż sufit: nadmiar rozpoznajemy po długości wyniku, nie po
        # ``stat`` sprzed odczytu (ten sam wyścig, który psuł zaufanie do rozmiaru ze skanu).
        #
        # Osłona łapie WYŁĄCZNIE zniknięcie pliku między ``is_file`` a odczytem — rdzeń dostawy
        # ma na to gotową gałąź („nie ma czego wysyłać ani sprzątać"), a wyjątek stąd przewracał
        # całą turę zamiast pominąć jedną pozycję. Każdy INNY błąd (odmowa dostępu po ``chmod``
        # z powłoki modelu, błąd I/O) znaczy coś przeciwnego: plik JEST. Oddany jako ``None``
        # wypadał ze zbioru pozycji zatrzymanych i następna tura kasowała go jako podłożony
        # z innej rozmowy — cicha utrata pracy modelu.
        try:
            with path.open("rb") as handle:
                content = handle.read(_READ_CEILING_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(
                "Nie udało się odczytać pozycji %s w skrzynce %s: %s", name, dirpath, exc
            )
            # Powód bez ścieżki bezwzględnej: trafia do wiadomości rozmówcy, a ta nie ma
            # ujawniać struktury katalogów serwera.
            raise OutboxReadError(f"błąd odczytu: {exc.strerror or type(exc).__name__}") from exc
        if len(content) > _READ_CEILING_BYTES:
            logger.warning(
                "Pozycja %s w skrzynce %s przekracza sufit odczytu (%d B) — odrzucam.",
                name,
                dirpath,
                _READ_CEILING_BYTES,
            )
            # TRWAŁE: sufit odczytu stoi powyżej najwyższego konfigurowalnego limitu dostawy,
            # więc pozycja i tak nigdy nie pojedzie. Ponawianie jej kosztowałoby odczyt 24 MB
            # w każdej turze tej rozmowy.
            raise OutboxReadError(
                f"przekracza sufit odczytu {_READ_CEILING_BYTES // (1024 * 1024)} MB",
                permanent=True,
            )
        return Deliverable(name=name, content=content, content_type=_content_type(name))

    def discard(self, dirpath: str, name: str) -> None:
        path = self._entry_path(dirpath, name)
        if path is None:
            return
        # ``missing_ok`` czyni operację idempotentną wobec BRAKU pliku: ponowiona dostawa nie
        # wywraca się na pozycji, która zdążyła zniknąć (sprzątanie TTL, ręczna interwencja).
        # Odmowa dostępu do KATALOGU (``chmod 500 outputs`` z powłoki modelu, uchwyt na pliku
        # na Windows) to inna sprawa i nie wolno jej wypuścić: ``deliver`` nie ma na to gałęzi,
        # więc wyjątek uciekał do respondera już PO zdjęciu migawki startowej i przed
        # aktualizacją stanu ponawiania — dostawa dla tej rozmowy cichła na stałe. Nic tu nie
        # ginie: pozycja zostawiona na wolumenie wraca w następnej turze jako OBCA (nie ma jej
        # w ``_ours``), więc nie zostanie wysłana drugi raz.
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "Nie udało się sprzątnąć pozycji %s ze skrzynki %s: %s", name, dirpath, exc
            )

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
    """Rozwiąż ścieżkę względną WEWNĄTRZ korzenia; ``WriteError`` przy ucieczce poza niego.

    Korzeń rozwijamy RAZ i dopiero do niego doklejamy ``relpath`` — jak w
    ``filesystem_workspace``/``markdown_notes_writer``. Dwa niezależne ``resolve()`` potrafią dać
    różne zapisy TEGO SAMEGO katalogu (krótka nazwa 8.3 w ``TEMP`` na Windows, ``/var`` →
    ``/private/var`` na macOS), a wtedy ``relative_to`` odrzuca legalną ścieżkę jako ucieczkę.
    """
    base = root.resolve()
    candidate = (base / relpath).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise WriteError(f"ścieżka poza katalogiem roboczym: {relpath!r}") from exc
    return candidate
