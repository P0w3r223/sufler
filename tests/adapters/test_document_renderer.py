"""Testy renderera dokumentów (DefaultDocumentRenderer, ADR 0026, A′2).

Sedno: ``md``/``txt`` to czyste bajty UTF-8; ``docx`` to zip Office (magic ``PK``) z treścią;
``pdf`` to plik ``%PDF`` z OSADZONYM fontem Unicode, więc polskie znaki nie wywracają renderowania
(regresja: wbudowana Helvetica koduje tylko latin-1). Nieznany format = twardy błąd.
"""

from __future__ import annotations

import io

import pytest

from sufler.adapters.outbound.document_renderer import DefaultDocumentRenderer
from sufler.core.ports.document import FILE_REPLY_FORMATS, RenderedDocument

_POLISH = "Zażółć gęślą jaźń — koszty i marże wydań."


def _render(fmt: str, body: str = _POLISH) -> RenderedDocument:
    return DefaultDocumentRenderer().render(body, fmt)


@pytest.mark.parametrize("fmt", ["md", "txt"])
def test_text_formats_are_raw_utf8_bytes(fmt: str):
    result = _render(fmt)
    # Czysty tekst: bajty to dokładnie UTF-8 treści, bez żadnego opakowania.
    assert result.content == _POLISH.encode("utf-8")
    assert result.content_type == FILE_REPLY_FORMATS[fmt]


def test_docx_is_office_zip_carrying_the_text():
    result = _render("docx")
    assert result.content[:2] == b"PK"  # kontener OOXML to zip
    assert result.content_type == FILE_REPLY_FORMATS["docx"]
    # Treść MUSI trafić do dokumentu — odczytujemy go z powrotem python-docx.
    from docx import Document

    doc = Document(io.BytesIO(result.content))
    assert _POLISH in "\n".join(p.text for p in doc.paragraphs)


def test_pdf_has_header_and_embeds_font_for_polish_glyphs():
    result = _render("pdf")
    assert result.content[:5] == b"%PDF-"
    assert result.content_type == FILE_REPLY_FORMATS["pdf"]
    # Osadzony podzbiór fontu DejaVu → w strukturze PDF pojawia się FontFile2 (TrueType). To dowód,
    # że NIE użyto wbudowanej Helvetiki (która wywróciłaby się na polskich znakach).
    assert b"FontFile2" in result.content


def test_pdf_wraps_long_lines_without_overflow_error():
    """Regresja: multi_cell(w=0) bez resetu kursora rzucał FPDFException na drugim wierszu."""
    body = "\n".join(["Bardzo długi wiersz " * 20, "", "krótki drugi wiersz"])
    result = _render("pdf", body)
    assert result.content[:5] == b"%PDF-"


def test_unknown_format_raises():
    with pytest.raises(ValueError, match="Nieobsługiwany format"):
        _render("rtf")
