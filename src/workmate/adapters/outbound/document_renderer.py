"""Renderer dokumentów do bajtów (implementacja ``DocumentRenderer``, ADR 0026, A′2).

``md``/``txt`` to czyste bajty UTF-8 — bez żadnej zależności. ``docx`` idzie przez ``python-docx``
(już obecny w extra ``teams-graph`` do EKSTRAKCJI Worda, ADR 0016 — tu używamy go w drugą stronę,
do ZAPISU), ``pdf`` przez ``fpdf2`` (extra ``file-reply``). Oba importujemy LENIWIE: sam serwer i
formaty tekstowe nie wymagają tych bibliotek, a brak extry kończy się czytelnym błędem, nie surowym
``ImportError`` w środku tury agenta.

PDF a polskie znaki: wbudowane fonty ``fpdf2`` (Helvetica/…) kodują tylko latin-1, więc ``ą``,
``ł``, ``ż`` wywróciłyby renderowanie. Dlatego osadzamy Unicode-owy ``DejaVuSans.ttf`` (licencja
DejaVu — redystrybucja dozwolona) leżący w ``assets/`` pakietu; ``fpdf2`` wstawia do wynikowego PDF
tylko PODZBIÓR użytych glifów, więc plik zostaje mały mimo pełnego pokrycia UTF-8.
"""

from __future__ import annotations

import io
from pathlib import Path

from workmate.core.ports.document import FILE_REPLY_FORMATS, RenderedDocument

# Font Unicode osadzany w PDF (patrz docstring). Leży obok modułu, w zasobach pakietu.
_PDF_FONT_PATH = Path(__file__).with_name("assets") / "DejaVuSans.ttf"
_PDF_FONT_FAMILY = "DejaVu"
# Wysokość wiersza (pt) i rozmiar czcionki dla renderowania PDF — czytelny, gęsty tekst.
_PDF_LINE_H = 6
_PDF_FONT_SIZE = 11

_MISSING_PDF = "Format 'pdf' wymaga extra 'file-reply'. Zainstaluj: uv sync --extra file-reply"
_MISSING_DOCX = "Format 'docx' wymaga extra 'teams-graph'. Zainstaluj: uv sync --extra teams-graph"
_MISSING_FONT = (
    f"Brakuje osadzonego fontu PDF ({_PDF_FONT_PATH.name}) — defekt pakowania/instalacji, nie błąd "
    "wejścia. Sprawdź, czy zasób assets/ trafił do dystrybucji (pyproject: artifacts)."
)


class DefaultDocumentRenderer:
    """``DocumentRenderer``: ``md``/``txt`` czysto, ``docx``→python-docx, ``pdf``→fpdf2+DejaVu."""

    def render(self, body: str, fmt: str) -> RenderedDocument:
        content_type = FILE_REPLY_FORMATS.get(fmt)
        if content_type is None:
            raise ValueError(f"Nieobsługiwany format dokumentu: {fmt!r}")
        if fmt in ("md", "txt"):
            return RenderedDocument(body.encode("utf-8"), content_type)
        if fmt == "docx":
            return RenderedDocument(_render_docx(body), content_type)
        return RenderedDocument(_render_pdf(body), content_type)


def _render_docx(body: str) -> bytes:
    """Zapisz treść jako .docx: każdy wiersz = akapit (python-docx radzi sobie z UTF-8 natywnie)."""
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - zależy od zainstalowanej extry
        raise RuntimeError(_MISSING_DOCX) from exc
    document = Document()
    for line in body.split("\n"):
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _render_pdf(body: str) -> bytes:
    """Zapisz treść jako PDF z osadzonym fontem Unicode (obsługa polskich znaków)."""
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover - zależy od zainstalowanej extry
        raise RuntimeError(_MISSING_PDF) from exc
    if not _PDF_FONT_PATH.is_file():
        # Czytelny sygnał zamiast obskurnego FileNotFoundError z wnętrza fpdf: to defekt pakowania
        # (font nie trafił do dystrybucji), więc podnosimy się GŁOŚNO — nie degradujemy po cichu.
        raise RuntimeError(_MISSING_FONT)
    pdf = FPDF()
    pdf.add_page()
    # uni=True nie jest już potrzebne (fpdf2 auto), ale font MUSI być zarejestrowany przed użyciem.
    pdf.add_font(_PDF_FONT_FAMILY, "", str(_PDF_FONT_PATH))
    pdf.set_font(_PDF_FONT_FAMILY, size=_PDF_FONT_SIZE)
    for line in body.split("\n"):
        if line.strip():
            # w=0 → cała szerokość; multi_cell zawija długie wiersze zamiast wychodzić poza stronę.
            # new_x=LMARGIN/new_y=NEXT resetuje kursor na początek NASTĘPNEGO wiersza — bez tego
            # kolejny multi_cell startuje przy prawym marginesie (szerokość ≈ 0 → FPDFException).
            pdf.multi_cell(0, _PDF_LINE_H, line, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(_PDF_LINE_H)  # pusty wiersz to odstęp, nie pusta komórka (fpdf2 nie lubi "")
    return bytes(pdf.output())
