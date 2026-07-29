"""Port RENDEROWANIA dokumentu do bajtów w zadanym formacie (ADR 0026, konsument A′2).

Drugi klocek odpowiedzi plikiem w wątku Teams: ``TeamsFileSender`` (ADR 0026, A′1) umie już
wgrać bajty i załączyć je w odpowiedzi, ale nie wie, JAK zamienić treść na plik. Ten port dokłada
tę oś: treść (Markdown/tekst od modelu) → bajty dokumentu + typ MIME. ``md``/``txt`` to czysty
tekst (bajty UTF-8), a ``pdf``/``docx`` wymagają cięższych, zależnych od bibliotek rendererów —
dlatego całość stoi za portem, a implementacja żyje w adapterze outbound (``fpdf2`` w extra
``file-reply``, ``python-docx`` z extra ``teams-graph``). Rdzeń o tych bibliotekach nie wie —
testuje się go strukturalną atrapą tego kontraktu.

``FILE_REPLY_FORMATS`` to JEDNOŹRÓDŁOWA mapa ``format → typ MIME`` współdzielona przez rdzeń
(walidacja formatu od modelu + rozszerzenie nazwy pliku) i adapter (typ zwracany w
``RenderedDocument``). Rozszerzenie pliku == klucz formatu dla wszystkich czterech.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Format (== rozszerzenie pliku) → typ MIME. Zamrożona, wąska biała lista: model podaje ``format``,
# a każdy inny odrzucamy w rdzeniu, zanim dotknie renderera (treść to DANE, nie polecenia).
FILE_REPLY_FORMATS: dict[str, str] = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class RenderedDocument:
    """Wynik renderowania: surowe bajty pliku i jego typ MIME (do wgrania na dysk kanału)."""

    content: bytes
    content_type: str


class DocumentRenderer(Protocol):
    """Zamień treść na bajty dokumentu w zadanym formacie (``md``/``txt``/``pdf``/``docx``)."""

    def render(self, body: str, fmt: str) -> RenderedDocument:
        """Zrenderuj ``body`` do formatu ``fmt`` (klucz z ``FILE_REPLY_FORMATS``).

        ``fmt`` jest już zwalidowany przez wołającego (rdzeń), ale implementacja i tak odrzuca
        nieznany format twardym błędem — kontrakt jest wąski i samopilnujący.
        """
        ...
