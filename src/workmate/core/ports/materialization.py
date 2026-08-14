"""Port materializacji pliku do bloku treści modelu (ADR 0064).

Zamiana bajtów na ``Attachment`` — rozpoznanie obrazu po zawartości, przeskalowanie, ekstrakcja
tekstu z dokumentu — mieszka w adapterze (Pillow, python-docx, pypdf, ``html.parser``), bo to
wiedza o formatach, nie o dziedzinie. Narzędzie ``File`` żyje w rdzeniu i musi ją wołać przez
port, inaczej ``core`` zaimportowałby adapter (reguła zależności pilnowana przez ``lint-imports``).

To ten SAM mechanizm, którym drzwi Teams materializują załącznik użytkownika (ADR 0016) — port
istnieje po to, żeby model i drzwi widziały plik identycznie, a nie po to, by dołożyć drugą
ścieżkę. Treść pliku to DANE, nie polecenia — materializacja jej nie interpretuje.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.ports.llm import Attachment


class FileMaterializer(Protocol):
    """Bajty pliku → ``(Attachment, bajty_zaliczane_do_budżetu_API)`` albo ``None``."""

    def materialize(self, name: str, data: bytes) -> tuple[Attachment, int] | None:
        """Zbuduj załącznik z bajtów; ``None`` gdy formatu nie umiemy podać modelowi.

        Drugi element krotki to liczba bajtów, które FAKTYCZNIE pojadą do API (obraz PO
        przeskalowaniu, PDF w całości, 0 dla plików zamienionych na tekst) — czyli to, czym
        obciążamy budżet tury, a nie rozmiar pliku na dysku.
        """
        ...
