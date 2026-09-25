"""Testy komendy ``sufler-render`` (etap 7, ADR 0011 paczki).

Treść ze STDIN → plik pod ``--output``. ``md``/``txt`` czysto, ``pdf``/``docx`` przez
``DefaultDocumentRenderer`` (fpdf2/python-docx). Renderer ma własne testy; tu sprawdzamy sam
adapter powłoki: parsowanie flag, zapis, kody wyjścia i utworzenie katalogu ``outputs/``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from sufler.adapters.inbound.cli.render import main


def _run(monkeypatch, *, fmt: str, output: str, body: str) -> None:
    monkeypatch.setattr("sys.argv", ["sufler-render", "--format", fmt, "--output", output])
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    main()


def test_md_written_as_plain_utf8(tmp_path: Path, monkeypatch, capsys):
    out = tmp_path / "outputs" / "raport.md"
    _run(monkeypatch, fmt="md", output=str(out), body="Ustalenia: ąęł # nagłówek")

    assert out.read_text(encoding="utf-8") == "Ustalenia: ąęł # nagłówek"
    assert "raport.md" in capsys.readouterr().out  # sukces potwierdzony treścią, nie ciszą


def test_creates_the_outputs_directory(tmp_path: Path, monkeypatch, capsys):
    """Katalog ``outputs/`` powstaje, jeśli go nie ma — agent nie musi go zakładać osobno."""
    out = tmp_path / "swiezy" / "outputs" / "notatka.txt"
    _run(monkeypatch, fmt="txt", output=str(out), body="treść")
    assert out.is_file()


def test_pdf_has_pdf_magic(tmp_path: Path, monkeypatch, capsys):
    out = tmp_path / "outputs" / "raport.pdf"
    _run(monkeypatch, fmt="pdf", output=str(out), body="Raport z polskimi znakami: ąęłóż")
    assert out.read_bytes().startswith(b"%PDF")  # realny renderer fpdf2, nie atrapa


def test_docx_is_a_zip_container(tmp_path: Path, monkeypatch, capsys):
    out = tmp_path / "outputs" / "raport.docx"
    _run(monkeypatch, fmt="docx", output=str(out), body="Akapit pierwszy\nAkapit drugi")
    assert out.read_bytes().startswith(b"PK")  # .docx to kontener ZIP (OOXML)


def test_unknown_format_exits_with_usage_code(monkeypatch, tmp_path, capsys):
    """Format spoza białej listy zatrzymuje argparse kodem 2 — zanim dotknie renderera."""
    monkeypatch.setattr(
        "sys.argv",
        ["sufler-render", "--format", "xlsx", "--output", str(tmp_path / "x.xlsx")],
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("treść"))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_output_directory_that_cannot_be_created_exits_one(tmp_path: Path, monkeypatch, capsys):
    """Awaria zapisu (kod 1) jest odróżniana od błędu użycia (kod 2)."""
    blocker = tmp_path / "plik-nie-katalog"
    blocker.write_text("zajęte")
    out = blocker / "outputs" / "raport.md"  # rodzic to PLIK, mkdir się nie uda
    monkeypatch.setattr("sys.argv", ["sufler-render", "--format", "md", "--output", str(out)])
    monkeypatch.setattr("sys.stdin", io.StringIO("treść"))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "nie powiódł się" in capsys.readouterr().err
