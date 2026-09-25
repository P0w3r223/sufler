"""Testy komendy ``sufler-extract`` (ADR 0064) — ekstrakcja pliku dla POWŁOKI agenta.

Ekstraktory mają własne testy (``test_document_text``); tutaj sprawdzamy sam adapter powłoki:
że wypisuje tekst na STDOUT, że każdy powód niepowodzenia jest ROZRÓŻNIALNY (model widzi tylko
kod wyjścia i STDERR, więc „pusto" i „nie umiem" nie mogą wyglądać tak samo) oraz że komenda
oddaje DOKŁADNIE ten sam tekst, co ekstraktor w procesie drzwi — rozjazd między powłoką a
``File(read)`` byłby błędem niewidocznym aż do rozmowy z użytkownikiem.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from sufler.adapters.inbound.cli.extract import main
from sufler.adapters.inbound.document_text import extract_html

_HTML = b"<html><body><p>Kwota: 12 300 zl</p><img src='b.png' alt='wykres'></body></html>"


@pytest.fixture(autouse=True)
def _sigpipe_nie_wycieka_z_main():
    """``main()`` przestawia SIGPIPE na ``SIG_DFL`` dla CAŁEGO procesu — słusznie, bo komenda ma
    umierać cicho w potoku jak zwykły filtr uniksowy. W pakiecie testów to jednak przeciek stanu
    globalnego: od pierwszego wywołania ``main()`` każdy zapis do zamkniętego gniazda w DOWOLNYM
    późniejszym teście dostaje sygnał zamiast ``BrokenPipeError`` i zabija cały przebieg, zanim
    jakikolwiek ``except`` zdąży zadziałać (tak padło CI na ``test_exec_manager_client``: exit 141,
    bez ani jednej porażki asercji). Windows nie ma tego sygnału, więc lokalnie było zielono.
    """
    sigpipe = getattr(signal, "SIGPIPE", None)
    if sigpipe is None:  # nie-POSIX — nie ma czego przywracać
        yield
        return
    poprzedni = signal.getsignal(sigpipe)
    try:
        yield
    finally:
        signal.signal(sigpipe, poprzedni)


def _run(monkeypatch, path: str) -> None:
    monkeypatch.setattr("sys.argv", ["sufler-extract", path])
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
    monkeypatch.setattr("sys.argv", ["sufler-extract"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.skipif(not hasattr(signal, "SIGPIPE"), reason="brak SIGPIPE (nie-POSIX)")
def test_piping_into_head_does_not_produce_a_traceback(tmp_path: Path):
    """`sufler-extract plik | head` to wzorzec, do którego kieruje sam opis narzędzia.

    Bez ustawienia SIGPIPE na domyślną akcję kończył się `BrokenPipeError` na STDERR — potok
    działał, a model, który widzi wyłącznie kod wyjścia i strumienie, dostawał sygnał awarii.
    Sondujemy PRAWDZIWYM potokiem w podprocesie, bo tego stanu nie da się udać w procesie testu.
    """
    duzy = tmp_path / "duzy.html"
    duzy.write_bytes(b"<p>" + b"linia tekstu<br>" * 50_000 + b"</p>")

    proces = subprocess.run(
        f"{sys.executable} -c "
        f'\'import sys; sys.argv=["sufler-extract", "{duzy}"]; '
        "from sufler.adapters.inbound.cli.extract import main; main()' | head -3",
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert "BrokenPipeError" not in proces.stderr
    assert "Traceback" not in proces.stderr
    assert "linia tekstu" in proces.stdout
