"""Testy komendy ``workmate-extract`` (ADR 0064) — ekstrakcja pliku dla POWŁOKI agenta.

Ekstraktory mają własne testy (``test_document_text``); tutaj sprawdzamy sam adapter powłoki:
że wypisuje tekst na STDOUT, że każdy powód niepowodzenia jest ROZRÓŻNIALNY (model widzi tylko
kod wyjścia i STDERR, więc „pusto" i „nie umiem" nie mogą wyglądać tak samo) oraz że komenda
oddaje DOKŁADNIE ten sam tekst, co ekstraktor w procesie drzwi — rozjazd między powłoką a
``File(read)`` byłby błędem niewidocznym aż do rozmowy z użytkownikiem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workmate.adapters.inbound.cli.extract import main
from workmate.adapters.inbound.document_text import extract_html

_HTML = b"<html><body><p>Kwota: 12 300 zl</p><img src='b.png' alt='wykres'></body></html>"


def _run(monkeypatch, path: str) -> None:
    monkeypatch.setattr("sys.argv", ["workmate-extract", path])
    main()


def test_prints_extracted_text_to_stdout(tmp_path: Path, monkeypatch, capsys):
    p = tmp_path / "faktura.html"
    p.write_bytes(_HTML)
    _run(monkeypatch, str(p))
    assert "Kwota: 12 300 zl" in capsys.readouterr().out


def test_output_matches_the_in_process_extractor_byte_for_byte(tmp_path: Path, monkeypatch, capsys):
    """Powłoka i drzwi muszą widzieć ten sam tekst — inaczej model dostaje dwie wersje pliku."""
    p = tmp_path / "faktura.html"
    p.write_bytes(_HTML)
    _run(monkeypatch, str(p))
    assert capsys.readouterr().out.rstrip("\n") == extract_html(_HTML)


def test_text_file_goes_through_the_same_dispatcher(tmp_path: Path, monkeypatch, capsys):
    p = tmp_path / "notatka.md"
    p.write_text("# Nagłówek\n\ntreść", encoding="utf-8")
    _run(monkeypatch, str(p))
    assert capsys.readouterr().out.startswith("# Nagłówek")


def test_missing_file_exits_one_with_a_reason(tmp_path: Path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(tmp_path / "nie-ma.pdf"))
    assert exc.value.code == 1
    assert "Nie ma pliku" in capsys.readouterr().err


def test_directory_is_reported_as_such(tmp_path: Path, monkeypatch, capsys):
    """Katalog to inna pomyłka niż uszkodzony plik — model musi je odróżnić, żeby się poprawić."""
    d = tmp_path / "outputs.txt"
    d.mkdir()
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(d))
    assert exc.value.code == 1
    assert "katalog" in capsys.readouterr().err


def test_unsupported_extension_exits_one_with_a_reason(tmp_path: Path, monkeypatch, capsys):
    p = tmp_path / "zdjecie.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(p))
    assert exc.value.code == 1
    assert "nieobsługiwane" in capsys.readouterr().err


def test_corrupt_document_exits_one_instead_of_crashing(tmp_path: Path, monkeypatch, capsys):
    p = tmp_path / "umowa.docx"
    p.write_bytes(b"to nie jest zip")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, str(p))
    assert exc.value.code == 1
    assert "Ekstrakcja nie powiodła się" in capsys.readouterr().err


def test_readable_but_empty_file_is_success_with_an_explicit_note(
    tmp_path: Path, monkeypatch, capsys
):
    """Skan bez warstwy tekstowej to nie awaria — plik jest czytelny, po prostu bez tekstu."""
    p = tmp_path / "pusty.txt"
    p.write_bytes(b"   \n  ")
    _run(monkeypatch, str(p))  # brak SystemExit = kod 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nie zawiera tekstu" in captured.err


def test_missing_path_argument_is_a_usage_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["workmate-extract"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
