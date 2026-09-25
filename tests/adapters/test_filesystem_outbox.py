"""Sondy skrzynki nadawczej na dysku (``FilesystemOutboxRepository``).

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`.

Sondy z dowiązaniami są tu najważniejsze i są sondami BEZPIECZEŃSTWA, nie porządkowymi.
Dwie różne dziury, jedna klasa: dowiązany WPIS w skrzynce wynosi treść, do której model ma
wyłącznie odczyt; dowiązana SAMA SKRZYNKA sięga katalogu innej rozmowy — na odczyt i na
skasowanie. Ograniczenie do korzenia workspace'u przepuszcza tę drugą, bo cel dowiązania leży
w obrębie korzenia; stąd rozwiązanie dwuetapowe.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sufler.adapters.outbound import filesystem_outbox
from sufler.adapters.outbound.filesystem_outbox import FilesystemOutboxRepository
from sufler.core.errors import WriteError
from sufler.core.ports.outbox import OutboxReadError

_DIR = "teams-graph/abc123"

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="dowiązania wymagają uprawnień na Windows"
)


@pytest.fixture()
def outbox(tmp_path):
    directory = tmp_path / "teams-graph" / "abc123" / "outputs"
    directory.mkdir(parents=True)
    return directory


def _names(tmp_path) -> list[str]:
    return [e.name for e in FilesystemOutboxRepository(tmp_path).list_entries(_DIR)]


def test_brak_katalogu_skrzynki_to_pusta_lista_nie_blad(tmp_path):
    """Większość tur nic nie dostarcza — brak katalogu jest stanem normalnym."""
    assert FilesystemOutboxRepository(tmp_path).list_entries(_DIR) == []


def test_wypis_daje_nazwe_i_rozmiar_bez_czytania_tresci(tmp_path, outbox):
    (outbox / "raport.md").write_bytes(b"# Raport")

    (entry,) = FilesystemOutboxRepository(tmp_path).list_entries(_DIR)

    assert (entry.name, entry.size) == ("raport.md", 8)


def test_odczyt_daje_tresc_i_typ_mime(tmp_path, outbox):
    (outbox / "raport.md").write_bytes(b"# Raport")

    item = FilesystemOutboxRepository(tmp_path).read(_DIR, "raport.md")

    assert item is not None
    assert item.content == b"# Raport"
    assert item.content_type == "text/markdown; charset=utf-8"


def test_nieznane_rozszerzenie_dostaje_strumien_bajtow_a_nie_wypada(tmp_path, outbox):
    """O odrzuceniu decyduje rdzeń — adapter nie może połknąć powodu."""
    (outbox / "skrypt.sh").write_bytes(b"#!/bin/sh")

    item = FilesystemOutboxRepository(tmp_path).read(_DIR, "skrypt.sh")

    assert item is not None
    assert item.content_type == "application/octet-stream"


@posix_only
def test_dowiazany_WPIS_do_bazy_wiedzy_NIE_jest_zbierany(tmp_path, outbox):
    tajne = tmp_path / "notatka-poufna.md"
    tajne.write_bytes(b"tresc, ktorej nie wolno wyslac")
    (outbox / "raport.md").symlink_to(tajne)

    assert _names(tmp_path) == []
    assert FilesystemOutboxRepository(tmp_path).read(_DIR, "raport.md") is None


@posix_only
def test_dowiazana_SKRZYNKA_nie_siega_innej_rozmowy(tmp_path):
    """Sedno pliku. `ln -s ../<hash innej rozmowy> outputs` rozwiązuje się w obrębie korzenia
    workspace'u, więc ograniczenie do korzenia to przepuszcza — a wtedy wypis oddaje cudze
    pliki do wysłania, a sprzątanie po udanej wysyłce je KASUJE."""
    cudza = tmp_path / "teams-graph" / "inna"
    cudza.mkdir(parents=True)
    (cudza / "cudzy-raport.md").write_bytes(b"material innej rozmowy")
    moja = tmp_path / "teams-graph" / "abc123"
    moja.mkdir(parents=True)
    (moja / "outputs").symlink_to(cudza, target_is_directory=True)

    assert _names(tmp_path) == []

    FilesystemOutboxRepository(tmp_path).discard(_DIR, "cudzy-raport.md")
    assert (cudza / "cudzy-raport.md").exists(), "cudzy plik nie może zostać skasowany"


@posix_only
def test_dowiazana_skrzynka_poza_korzen_tez_nie_przechodzi(tmp_path):
    obce = tmp_path.parent / f"obce-{tmp_path.name}"
    obce.mkdir()
    (obce / "tajne.md").write_bytes(b"x")
    moja = tmp_path / "teams-graph" / "abc123"
    moja.mkdir(parents=True)
    (moja / "outputs").symlink_to(obce, target_is_directory=True)

    assert _names(tmp_path) == []


def test_podkatalog_nie_jest_zbierany(tmp_path, outbox):
    (outbox / "podkatalog").mkdir()
    (outbox / "podkatalog" / "raport.md").write_bytes(b"gleboko")

    assert _names(tmp_path) == []


def test_plik_tymczasowy_jest_pomijany(tmp_path, outbox):
    (outbox / "raport.md.tmp").write_bytes(b"w trakcie zapisu")

    assert _names(tmp_path) == []


def test_kolejnosc_jest_deterministyczna(tmp_path, outbox):
    for name in ("c.md", "a.md", "b.md"):
        (outbox / name).write_bytes(b"x")

    assert _names(tmp_path) == ["a.md", "b.md", "c.md"]


def test_wypis_ma_sufit_zeby_jedna_tura_nie_sprzatala_bez_konca(tmp_path, outbox):
    """Zawartość skrzynki dyktuje model z powłoką — pętla tworząca dziesiątki tysięcy plików
    nie może zamienić jednej tury w wielominutowe sprzątanie. Nadmiar bierze tura następna."""
    for i in range(250):
        (outbox / f"p{i:04d}.md").write_bytes(b"x")

    assert len(_names(tmp_path)) == 200


def test_discard_usuwa_plik_i_jest_idempotentny(tmp_path, outbox):
    (outbox / "raport.md").write_bytes(b"x")
    repo = FilesystemOutboxRepository(tmp_path)

    repo.discard(_DIR, "raport.md")
    repo.discard(_DIR, "raport.md")  # ponowienie nie może się wywrócić

    assert not (outbox / "raport.md").exists()


def test_ucieczka_poza_korzen_jest_bledem(tmp_path):
    with pytest.raises(WriteError):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "../../../../etc/passwd")


def test_discard_nie_siega_materialu_roboczego_pietro_wyzej(tmp_path, outbox):
    """``..`` z wnętrza skrzynki celuje w katalog roboczy rozmowy — źródła, z których model
    tworzył wynik. Ograniczenie do korzenia workspace'u by tego NIE zatrzymało."""
    sasiad = tmp_path / "teams-graph" / "abc123" / "prywatny.md"
    sasiad.write_bytes(b"material roboczy")

    with pytest.raises(WriteError):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "../prywatny.md")

    assert sasiad.exists()


def test_discard_nie_siega_skrzynki_innej_rozmowy_przez_nazwe(tmp_path, outbox):
    cudza = tmp_path / "teams-graph" / "inna" / "outputs"
    cudza.mkdir(parents=True)
    (cudza / "raport.md").write_bytes(b"cudzy wynik")

    with pytest.raises(WriteError):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "../../inna/outputs/raport.md")

    assert (cudza / "raport.md").exists()


def test_odczyt_nie_ufa_rozmiarowi_ze_skanu_i_ma_wlasny_sufit(tmp_path, outbox, monkeypatch):
    """Rozmiar z ``list_entries`` jest mierzony przy SKANIE, a treść pisze model z powłoką.

    Rdzeń odrzuca pozycje ponad limit po metadanych — właśnie po to, żeby wielki plik nie wszedł
    do pamięci procesu. Plik rosnący MIĘDZY skanem a dostawą omija tę bramkę: ``read`` wczytywał
    go w całości, bo ufał rozmiarowi sprzed chwili. Sufit stoi więc również w kolektorze.
    """
    monkeypatch.setattr(filesystem_outbox, "_READ_CEILING_BYTES", 16)
    plik = outbox / "raport.md"
    plik.write_bytes(b"maly")

    (entry,) = FilesystemOutboxRepository(tmp_path).list_entries(_DIR)
    assert entry.size == 4  # tyle widział rdzeń, gdy przepuszczał pozycję

    plik.write_bytes(b"x" * 4096)  # model dopisał do pliku po skanie

    # ``None`` znaczy w porcie „pozycja zniknęła" i rdzeń przechodzi nad nim do porządku
    # dziennego — plik, który JEST, tylko za duży, wypadał wtedy ze zbioru zatrzymanych
    # i następna tura kasowała go jako obcy, bez słowa dla rozmówcy.
    with pytest.raises(OutboxReadError) as odmowa:
        FilesystemOutboxRepository(tmp_path).read(_DIR, "raport.md")

    assert odmowa.value.permanent, "sufit odczytu jest werdyktem trwałym, nie awarią do ponowienia"


def test_pozycji_ktorej_nie_da_sie_odczytac_NIE_wolno_udawac_znikniona(
    tmp_path, outbox, monkeypatch
):
    """Odmowa odczytu to stan PRZECIWNY do zniknięcia — plik jest i ma zostać na wolumenie.

    Skrzynkę zapełnia model powłoką, więc ``chmod 000 outputs/raport.md`` jest jego zwykłym
    ruchem, a nie egzotyką. Zwrócone stąd ``None`` trafiało w rdzeniu na gałąź „nie ma czego
    wysyłać ani sprzątać": pozycja nie szła ani do ``failed``, ani do ``deferred``, wypadała
    ze zbioru zatrzymanych — i w następnej turze była kasowana jako podłożona z innej rozmowy.
    """
    (outbox / "raport.md").write_bytes(b"# Raport")
    prawdziwy_open = Path.open

    def opener(self: Path, *args: object, **kwargs: object):
        if self.name == "raport.md":
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)

    with pytest.raises(OutboxReadError) as odmowa:
        FilesystemOutboxRepository(tmp_path).read(_DIR, "raport.md")

    assert not odmowa.value.permanent, "odmowa dostępu bywa przejściowa — plik ma czekać"
    assert "Permission denied" in str(odmowa.value)
    assert str(tmp_path) not in str(odmowa.value), "powód jedzie do rozmówcy — bez ścieżek serwera"


def test_odczyt_pozycji_w_granicy_sufitu_dziala_bez_zmian(tmp_path, outbox, monkeypatch):
    """Sufit nie może zabrać zwykłej dostawy — inaczej zamiast bramki mamy blokadę."""
    monkeypatch.setattr(filesystem_outbox, "_READ_CEILING_BYTES", 16)
    (outbox / "raport.md").write_bytes(b"0123456789abcdef")  # dokładnie sufit

    item = FilesystemOutboxRepository(tmp_path).read(_DIR, "raport.md")

    assert item is not None and item.content == b"0123456789abcdef"


def test_nieudane_sprzatniecie_nie_wywraca_dostawy(tmp_path, outbox, monkeypatch, caplog):
    """``discard`` jest idempotentne wobec BRAKU pliku, ale odmowa dostępu to inna sprawa.

    Rdzeń woła sprzątanie w środku pętli dostawy — już po zdjęciu migawki startowej i przed
    aktualizacją stanu ponawiania — i nie ma na wyjątek stąd żadnej gałęzi. ``chmod 500
    outputs`` z powłoki modelu (albo uchwyt na pliku na Windows) wypuszczał go do respondera:
    tura ocalała, ale dostawa dla tej rozmowy cichła na stałe, a przy sprzątaniu częściowo
    udanym zbiór „naszych" rozjeżdżał się z tym, co zostało na wolumenie.
    """
    (outbox / "raport.md").write_bytes(b"# Raport")
    prawdziwy_unlink = Path.unlink

    def unlink(self: Path, *args: object, **kwargs: object):
        if self.name == "raport.md":
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    with caplog.at_level("WARNING"):
        FilesystemOutboxRepository(tmp_path).discard(_DIR, "raport.md")

    assert (outbox / "raport.md").exists(), "sonda nic nie sprawdza, jeśli plik jednak zniknął"
    assert "raport.md" in caplog.text, "nieudane sprzątanie ma zostawić ślad w dzienniku"
