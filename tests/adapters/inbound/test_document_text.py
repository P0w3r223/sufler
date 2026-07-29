"""Testy współdzielonej ekstrakcji tekstu (``document_text``) — bytes/Path → str.

Dokumenty binarne budujemy w pamięci (python-docx/openpyxl/python-pptx) albo składamy ręcznie
(minimalny, deterministyczny PDF), więc testy są samowystarczalne — bez fixture'ów binarnych w
repo. Sprawdzamy: happy-path per format, dyspozytor po rozszerzeniu, nieobsługiwany typ i
degradację uszkodzonego pliku do ``DocumentExtractionError`` (a nie wywrócenie procesu).
"""

from __future__ import annotations

import io

import pytest

from workmate.adapters.inbound.document_text import (
    DocumentExtractionError,
    extract_docx,
    extract_pdf,
    extract_pptx,
    extract_text,
    extract_text_from_bytes,
    extract_text_from_path,
    extract_xlsx,
)

# --- budowniczowie dokumentów w pamięci ----------------------------------------


def _docx_bytes(*, paragraph: str, table_cells: list[list[str]]) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(paragraph)
    table = doc.add_table(rows=len(table_cells), cols=len(table_cells[0]))
    for r, row in enumerate(table_cells):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _xlsx_bytes(*, sheet: str, rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    ws = workbook.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _pptx_bytes(*, texts: list[str]) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # pusty layout
    for i, text in enumerate(texts):
        box = slide.shapes.add_textbox(Inches(1), Inches(1 + i), Inches(4), Inches(1))
        box.text_frame.text = text
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()


def _pdf_bytes(text: str) -> bytes:
    """Zbuduj minimalny, jednostronicowy PDF z jedną linią tekstu (poprawna tablica xref)."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 720 Td (" + text.encode("latin-1") + b") Tj ET"
    header = b"<< /Length " + str(len(stream)).encode() + b" >>\n"
    objs.append(header + b"stream\n" + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    count = len(objs) + 1
    out += b"xref\n0 " + str(count).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size " + str(count).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    return bytes(out)


# --- ekstraktory per format ----------------------------------------------------


def test_extract_docx_reads_paragraphs_and_table_cells():
    data = _docx_bytes(paragraph="Ustalenia spotkania", table_cells=[["Zadanie", "Termin"]])
    text = extract_docx(data)
    assert "Ustalenia spotkania" in text
    assert "Zadanie | Termin" in text  # komórki tabeli łączone separatorem


def test_extract_xlsx_reads_sheet_header_and_rows():
    data = _xlsx_bytes(sheet="Budżet", rows=[["Pozycja", "Kwota"], ["Licencje", "1000"]])
    text = extract_xlsx(data)
    assert "# Arkusz: Budżet" in text
    assert "Pozycja | Kwota" in text
    assert "Licencje | 1000" in text


def test_extract_pptx_reads_per_slide_shape_text():
    text = extract_pptx(_pptx_bytes(texts=["Agenda", "Wnioski"]))
    assert "# Slajd 1" in text
    assert "Agenda" in text
    assert "Wnioski" in text


def test_extract_pdf_reads_page_text():
    text = extract_pdf(_pdf_bytes("Seed PDF tekst probny"))
    assert "# Strona 1" in text
    assert "Seed PDF tekst probny" in text


def test_extract_text_decodes_utf8_and_strips():
    assert extract_text("  zażółć gęślą\n".encode()) == "zażółć gęślą"


def test_extract_text_replaces_undecodable_bytes_without_raising():
    # Samotny bajt 0xFF nie jest poprawnym UTF-8 — errors="replace", nie wyjątek.
    assert extract_text(b"abc\xff") is not None


# --- dyspozytor extract_text_from_bytes / _from_path ---------------------------


def test_dispatch_routes_by_extension():
    docx = _docx_bytes(paragraph="Treść", table_cells=[["a", "b"]])
    assert "Treść" in extract_text_from_bytes(docx, "docx")
    assert extract_text_from_bytes("# Tytuł\n".encode(), "md").startswith("# Tytuł")


def test_dispatch_is_case_insensitive_on_extension():
    assert extract_text_from_bytes(b"tekst", "TXT") == "tekst"


def test_dispatch_unsupported_extension_raises():
    with pytest.raises(DocumentExtractionError, match="nieobsługiwane"):
        extract_text_from_bytes(b"\x00\x01", "png")


def test_dispatch_corrupt_docx_raises_extraction_error_not_arbitrary():
    """Uszkodzony docx (nie-zip) → DocumentExtractionError, a nie surowy wyjątek biblioteki."""
    with pytest.raises(DocumentExtractionError):
        extract_text_from_bytes(b"to nie jest zip", "docx")


def test_dispatch_corrupt_pdf_raises_extraction_error():
    """Uszkodzony PDF (bez nagłówka) → DocumentExtractionError, nie surowy PyPdfError."""
    with pytest.raises(DocumentExtractionError):
        extract_text_from_bytes(b"to nie jest pdf", "pdf")


def test_dispatch_missing_pdf_library_degrades_gracefully(monkeypatch):
    """Brak extra 'seed' (pypdf) NIE kładzie partii — leci DocumentExtractionError, nie ImportError.

    Regres: wcześniej gałąź PDF omijała wspólny wrapper, więc ``ModuleNotFoundError`` z leniwego
    importu uciekał surowy i kładł cały import (łamiąc niezmiennik ADR 0050 „skipped, never crash").
    """
    import sys

    monkeypatch.setitem(sys.modules, "pypdf", None)  # `import pypdf` → ImportError
    with pytest.raises(DocumentExtractionError):
        extract_text_from_bytes(_pdf_bytes("cokolwiek"), "pdf")


def test_extract_text_from_path_reads_and_dispatches(tmp_path):
    p = tmp_path / "notatka.md"
    p.write_text("# Nagłówek\n\ntreść", encoding="utf-8")
    assert extract_text_from_path(p).startswith("# Nagłówek")
