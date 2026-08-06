"""Sondy skrzynki nadawczej na dysku (``FilesystemOutboxRepository``, ADR 0009 paczki).

Sonda z dowiązaniem symbolicznym jest tu najważniejsza i jest sondą BEZPIECZEŃSTWA, nie
porządkową: model ma bazę wiedzy zamontowaną do odczytu, więc ``ln -s`` do notatki byłby drogą
wyniesienia treści, której nie wolno mu wysłać. Repozytorium zbierające „każdy plik" i takie,
które zbiera „każdy zwykły plik", różnią się wyłącznie tym przypadkiem.
"""

from __future__ import annotations

import sys

import pytest

from workmate.adapters.outbound.filesystem_outbox import FilesystemOutboxRepository
from workmate.core.errors import WriteError

_DIR = "teams-graph/abc123"


@pytest.fixture()
def outbox(tmp_path):
    directory = tmp_path / "teams-graph" / "abc123" / "outputs"
    directory.mkdir(parents=True)
    return directory


def test_brak_katalogu_skrzynki_to_pusta_lista_nie_blad(tmp_path):
    """Większość tur nic nie dostarcza — brak katalogu jest stanem normalnym."""
    assert FilesystemOutboxRepository(tmp_path).collect(_DIR) == []


def test_zbiera_zwykle_pliki_z_trescia_i_typem_mime(tmp_path, outbox):
    (outbox / "raport.md").write_bytes(b"# Raport")

    (item,) = FilesystemOutboxRepository(tmp_path).collect(_DIR)

    assert item.name == "raport.md"
    assert item.content == b"# Raport"
    assert item.content_type == "text/markdown; charset=utf-8"


def test_nieznane_rozszerzenie_dostaje_strumien_bajtow_a_nie_wypada(tmp_path, outbox):
    """O odrzuceniu decyduje rdzeń — adapter nie może połknąć powodu."""
    (outbox / "skrypt.sh").write_bytes(b"#!/bin/sh")

    (item,) = FilesystemOutboxRepository(tmp_path).collect(_DIR)

    assert item.name == "skrypt.sh"
    assert item.content_type == "application/octet-stream"


@pytest.mark.skipif(sys.platform == "win32", reason="dowiązania wymagają uprawnień na Windows")
def test_dowiazanie_do_bazy_wiedzy_NIE_jest_zbierane(tmp_path, outbox):
    tajne = tmp_path / "notatka-poufna.md"
    tajne.write_bytes(b"tresc, ktorej nie wolno wyslac")
    (outbox / "raport.md").symlink_to(tajne)

    assert FilesystemOutboxRepository(tmp_path).collect(_DIR) == []


def test_podkatalog_nie_jest_zbierany(tmp_path, outbox):
    (outbox / "podkatalog").mkdir()
    (outbox / "podkatalog" / "raport.md").write_bytes(b"gleboko")

    assert FilesystemOutboxRepository(tmp_path).collect(_DIR) == []


def test_plik_tymczasowy_jest_pomijany(tmp_path, outbox):
    (outbox / "raport.md.tmp").write_bytes(b"w trakcie zapisu")

    assert FilesystemOutboxRepository(tmp_path).collect(_DIR) == []


def test_kolejnosc_jest_deterministyczna(tmp_path, outbox):
    for name in ("c.md", "a.md", "b.md"):
        (outbox / name).write_bytes(b"x")

    items = FilesystemOutboxRepository(tmp_path).collect(_DIR)

    assert [i.name for i in items] == ["a.md", "b.md", "c.md"]


def test_discard_usuwa_plik_i_jest_idempotentny(tmp_path, outbox):
    (outbox / "raport.md").write_bytes(b"x")
    repo = FilesystemOutboxRepository(tmp_path)

    repo.discard(_DIR, "raport.md")
    repo.discard(_DIR, "raport.md")  # ponowienie nie może się wywrócić

    assert not (outbox / "raport.md").exists()


def test_ucieczka_poza_korzen_jest_bledem(tmp_path):
    repo = FilesystemOutboxRepository(tmp_path)

    with pytest.raises(WriteError):
        repo.discard(_DIR, "../../../../etc/passwd")


def test_discard_nie_siega_materialu_roboczego_pietro_wyzej(tmp_path, outbox):
    """``..`` z wnętrza skrzynki celuje w katalog roboczy rozmowy — źródła, z których model
    tworzył wynik. Ograniczenie do korzenia workspace'u by tego NIE zatrzymało."""
    sasiad = tmp_path / "teams-graph" / "abc123" / "prywatny.md"
    sasiad.write_bytes(b"material roboczy")

    with pytest.raises(WriteError):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "../prywatny.md")

    assert sasiad.exists()


def test_discard_nie_siega_skrzynki_innej_rozmowy(tmp_path, outbox):
    """Izolacja rozmów: cudza skrzynka leży wewnątrz tego samego korzenia workspace'u."""
    cudza = tmp_path / "teams-graph" / "inna" / "outputs"
    cudza.mkdir(parents=True)
    (cudza / "raport.md").write_bytes(b"cudzy wynik")

    with pytest.raises(WriteError):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "../../inna/outputs/raport.md")

    assert (cudza / "raport.md").exists()
