"""Testy adaptera katalogu roboczego na dysku (ADR 0018) — create-only i ochrona path-traversal.

Realny FS (``tmp_path``): sedno to atomowy zapis create-only (``os.link``) oraz ``resolve()`` +
``relative_to(root)`` na każdej ścieżce (zapis i odczyt) — nic nie wyjdzie poza korzeń workspace.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from workmate.adapters.outbound import filesystem_workspace
from workmate.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
    prune_stale,
)
from workmate.core.errors import WriteError

# Ta sama bramka co w ``test_filesystem_outbox`` (siostrzana sonda tej samej granicy): założenie
# dowiązania na Windows wymaga uprawnień administratora albo trybu dewelopera, więc bez niej sonda
# pada na ``OSError`` z ``symlink_to`` — czyli na PRZYGOTOWANIU, nie na asercji. ASERCJA ZOSTAJE
# NIETKNIĘTA i biega na CI (Linux) oraz na Windows z trybem dewelopera.
posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="dowiązania wymagają uprawnień na Windows"
)


def test_create_writes_file_and_returns_descriptor(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)

    created = writer.create("teams_graph/abc/raport.md", "treść")

    assert (tmp_path / "teams_graph" / "abc" / "raport.md").read_text(encoding="utf-8") == "treść"
    assert created.name == "raport.md"
    assert created.size == len("treść".encode())


def test_create_is_create_only_collision_raises(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/raport.md", "pierwsza")

    with pytest.raises(WriteError, match="już istnieje"):
        writer.create("teams_graph/abc/raport.md", "druga")

    # Oryginał nietknięty (nigdy nie nadpisujemy).
    target = tmp_path / "teams_graph" / "abc" / "raport.md"
    assert target.read_text(encoding="utf-8") == "pierwsza"


def test_write_path_traversal_is_rejected(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        writer.create("../ucieczka.md", "x")


def test_create_bytes_writes_binary_verbatim_and_is_create_only(tmp_path):
    """Załącznik binarny (ADR 0064) idzie TĄ SAMĄ drogą co plik tekstowy modelu — celowo, bo dwie
    ścieżki zapisu to dwa miejsca na pomyłkę w atomowości. Bajty muszą przejść bez rekodowania
    (PDF/PNG po ``str``/UTF-8 byłby uszkodzony), a create-only obowiązuje tak samo.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    dane = b"%PDF-1.7\x00\xff\xfe binarne"

    created = writer.create_bytes("teams_graph/abc/umowa.pdf", dane)

    assert (tmp_path / "teams_graph" / "abc" / "umowa.pdf").read_bytes() == dane
    assert created.size == len(dane)
    with pytest.raises(WriteError, match="już istnieje"):
        writer.create_bytes("teams_graph/abc/umowa.pdf", b"podmiana")


def test_writer_exists_reports_only_files_it_created(tmp_path):
    """``exists`` bramkuje ponowne odłożenie załącznika — fałszywe „tak" gubiłoby plik po cichu."""
    writer = FilesystemWorkspaceWriter(tmp_path)

    assert writer.exists("teams_graph/abc/raport.md") is False
    writer.create("teams_graph/abc/raport.md", "treść")
    assert writer.exists("teams_graph/abc/raport.md") is True
    assert writer.exists("teams_graph/abc") is False  # katalog to nie plik


def test_list_and_read_within_scope(tmp_path):
    writer = FilesystemWorkspaceWriter(tmp_path)
    repo = FilesystemWorkspaceRepository(tmp_path)
    writer.create("teams_graph/abc/a.md", "AAA")
    writer.create("teams_graph/abc/b.txt", "BB")

    names = [f.name for f in repo.list("teams_graph/abc")]
    assert names == ["a.md", "b.txt"]  # posortowane
    assert repo.read("teams_graph/abc", "a.md") == "AAA"
    assert repo.read("teams_graph/abc", "niema.md") is None


def test_list_hides_the_temporary_files_of_an_interrupted_write(tmp_path):
    """``.tmp`` to PÓŁPRODUKT ``_atomic_create`` (mkstemp → link → unlink). Zabity proces zostawia
    go na dysku; pokazany modelowi wygląda jak zwykły plik roboczy, a jego treść bywa ucięta.

    Dotychczasowy test deklarował to w komentarzu, ale ŻADEN ``.tmp`` w nim nie powstawał —
    asercja przechodziła i bez filtru.
    """
    repo = FilesystemWorkspaceRepository(tmp_path)
    scope = tmp_path / "teams_graph" / "abc"
    scope.mkdir(parents=True)
    (scope / "raport.md").write_text("gotowy", encoding="utf-8")
    (scope / "raport.md.a1b2.tmp").write_text("ucięty półprodukt", encoding="utf-8")

    assert [f.name for f in repo.list("teams_graph/abc")] == ["raport.md"]


def test_list_skips_subdirectories(tmp_path):
    """Wypis to PLIKI rozmowy; katalog w wyniku obiecywałby odczyt, którego ``read`` odmówi."""
    repo = FilesystemWorkspaceRepository(tmp_path)
    scope = tmp_path / "teams_graph" / "abc"
    (scope / "podkatalog").mkdir(parents=True)
    (scope / "raport.md").write_text("x", encoding="utf-8")

    assert [f.name for f in repo.list("teams_graph/abc")] == ["raport.md"]


def test_read_of_a_name_pointing_into_a_subdirectory_returns_none(tmp_path):
    """Granicą odczytu jest KATALOG ROZMOWY, nie korzeń: ``podkatalog/plik`` zostaje w korzeniu
    i przeszedłby samo ``relative_to(root)`` — odmowę daje dopiero warunek na rodzicu.

    Nazwa pliku bywa OD MODELU, więc każdy separator w niej to droga w bok.
    """
    repo = FilesystemWorkspaceRepository(tmp_path)
    scope = tmp_path / "teams_graph" / "abc"
    (scope / "podkatalog").mkdir(parents=True)
    (scope / "podkatalog" / "tajne.txt").write_text("nie dla modelu", encoding="utf-8")

    assert repo.read("teams_graph/abc", "podkatalog/tajne.txt") is None
    assert repo.read_bytes("teams_graph/abc", "podkatalog/tajne.txt") is None


def test_read_of_a_directory_name_returns_none_instead_of_raising(tmp_path):
    """Katalog podany jako nazwa pliku to zwykła pomyłka modelu — ma wrócić „nie ma takiego
    pliku", nie ``IsADirectoryError`` wywracające turę."""
    repo = FilesystemWorkspaceRepository(tmp_path)
    (tmp_path / "teams_graph" / "abc" / "podkatalog").mkdir(parents=True)

    assert repo.read("teams_graph/abc", "podkatalog") is None


def test_list_missing_dir_is_empty(tmp_path):
    repo = FilesystemWorkspaceRepository(tmp_path)
    assert repo.list("teams_graph/nieistnieje") == []


def test_read_path_traversal_is_rejected(tmp_path):
    repo = FilesystemWorkspaceRepository(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        repo.read("teams_graph/abc", "../../../secret.env")


@pytest.mark.skipif(os.name != "nt", reason="separator '\\' i drive-relative to specyfika Windows")
def test_read_backslash_traversal_is_rejected_on_windows(tmp_path):
    """REGRESJA Windows: separator '\\' też jest ochroniony przez resolve().relative_to."""
    repo = FilesystemWorkspaceRepository(tmp_path)
    with pytest.raises(WriteError, match="poza katalogiem"):
        repo.read("teams_graph\\abc", "..\\..\\..\\secret.env")


def test_prune_stale_removes_idle_conversation_dirs(tmp_path):
    """TTL (ADR 0018): katalog rozmowy bez aktywności > retencji jest usuwany; świeży zostaje."""
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/stary/a.md", "x")
    writer.create("teams_graph/swiezy/b.md", "y")
    old_time = (datetime.now(tz=UTC) - timedelta(days=40)).timestamp()
    os.utime(tmp_path / "teams_graph" / "stary" / "a.md", (old_time, old_time))

    removed = prune_stale(tmp_path, older_than=timedelta(days=30), now=datetime.now(tz=UTC))

    assert removed == 1
    assert not (tmp_path / "teams_graph" / "stary").exists()
    assert (tmp_path / "teams_graph" / "swiezy").exists()


def test_prune_stale_uses_the_directory_mtime_when_the_conversation_is_empty(tmp_path):
    """Pusty katalog rozmowy nie ma pliku, z którego dałoby się odczytać „aktywność" — bez
    fallbacku na mtime samego katalogu ``max()`` na pustym ciągu wywróciłby sprzątanie, a
    puste katalogi rosłyby bez końca."""
    stary = tmp_path / "teams_graph" / "pusty"
    stary.mkdir(parents=True)
    dawno = (datetime.now(tz=UTC) - timedelta(days=40)).timestamp()
    os.utime(stary, (dawno, dawno))

    removed = prune_stale(tmp_path, older_than=timedelta(days=30), now=datetime.now(tz=UTC))

    assert removed == 1
    assert not stary.exists()


def test_prune_stale_on_a_missing_root_is_a_no_op(tmp_path):
    """Sprzątanie biegnie RAZ NA STARCIE pollera — przed pierwszym zapisem korzenia jeszcze nie
    ma, a wyjątek położyłby start drzwi."""
    assert (
        prune_stale(
            tmp_path / "nie-ma-brudnopisu",
            older_than=timedelta(days=30),
            now=datetime.now(tz=UTC),
        )
        == 0
    )


@posix_only
def test_read_does_not_follow_a_symlink_out_of_the_conversation_directory(tmp_path):
    """Granicą odczytu jest KATALOG ROZMOWY, nie korzeń brudnopisu (ADR 0064 / infra 0012).

    ``resolve()`` rozwija dowiązania, więc symlink ``../<inna rozmowa>/plik`` ląduje wewnątrz
    korzenia i przechodziłby kontrolę ``relative_to(root)`` — czytelnik dostawał CUDZY plik,
    mimo że nazwa jest czysta i strażnik nazw niczego nie widzi. Symlink zakłada się powłoką,
    a ``File`` zostaje na powierzchni właśnie w układzie z powłoką, więc droga jest realna.
    """
    root = tmp_path / "scratchpad"
    moja = root / "teams_graph" / "aaa"
    cudza = root / "teams_graph" / "bbb"
    moja.mkdir(parents=True)
    cudza.mkdir(parents=True)
    (cudza / "tajne.txt").write_text("CUDZA UMOWA", encoding="utf-8")
    (moja / "podglad.txt").symlink_to(cudza / "tajne.txt")

    repo = FilesystemWorkspaceRepository(root)

    assert repo.read("teams_graph/aaa", "podglad.txt") is None
    assert repo.read_bytes("teams_graph/aaa", "podglad.txt") is None


@posix_only
def test_read_refuses_when_the_conversation_directory_itself_is_a_symlink(tmp_path):
    """Dowiązana jest CAŁA rozmowa, nie pojedynczy plik — i wtedy strażnik z sondy wyżej milczy.

    ``_resolve_in_scope`` porównuje rozwiązanego rodzica z rozwiązanym katalogiem rozmowy, ale
    ``resolve()`` przepisuje wtedy RÓWNIEŻ tę drugą stronę na cel dowiązania: obie wychodzą z tego
    samego katalogu i równość zachodzi. Czytelnik dostaje komplet plików cudzej rozmowy, mimo że
    żadna nazwa nie zawiera separatora. Rozpoznanie musi paść PRZED ``resolve`` (wzorzec
    ``filesystem_outbox._outbox``).
    """
    root = tmp_path / "scratchpad"
    cudza = root / "teams_graph" / "bbb"
    cudza.mkdir(parents=True)
    (cudza / "tajne.txt").write_text("CUDZA UMOWA", encoding="utf-8")
    (root / "teams_graph" / "aaa").symlink_to(cudza, target_is_directory=True)

    repo = FilesystemWorkspaceRepository(root)

    assert repo.read("teams_graph/aaa", "tajne.txt") is None
    assert repo.read_bytes("teams_graph/aaa", "tajne.txt") is None


@posix_only
def test_list_skips_symlinked_entries(tmp_path):
    """Wypis pokazuje to, co ``read`` odda — dowiązanie odda ``None``, więc nie ma go w wypisie."""
    root = tmp_path / "scratchpad"
    moja = root / "teams_graph" / "aaa"
    cudza = root / "teams_graph" / "bbb"
    moja.mkdir(parents=True)
    cudza.mkdir(parents=True)
    (cudza / "tajne.txt").write_text("CUDZA UMOWA", encoding="utf-8")
    (moja / "raport.md").write_text("mój", encoding="utf-8")
    (moja / "podglad.txt").symlink_to(cudza / "tajne.txt")

    assert [f.name for f in FilesystemWorkspaceRepository(root).list("teams_graph/aaa")] == [
        "raport.md"
    ]


def test_temp_cleanup_failure_does_not_mask_the_collision_error(tmp_path, monkeypatch):
    """``finally: tmp.unlink()`` bez osłony PODMIENIA właściwy wyjątek na błąd sprzątania.

    Wołający rozpoznaje kolizję po ``WriteError`` z „już istnieje" (odłożenie tego samego
    załącznika drugi raz). Gdy sprzątanie półproduktu padnie (antywirus trzyma uchwyt, katalog
    tylko do odczytu), z ``finally`` wychodzi ``OSError`` i informacja o kolizji ginie.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/raport.md", "pierwsza")

    prawdziwy_unlink = Path.unlink

    def unlink_ktory_pada(self, missing_ok=False):
        if self.name.endswith(".tmp"):
            raise PermissionError("plik tymczasowy trzymany przez inny proces")
        return prawdziwy_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_ktory_pada)

    with pytest.raises(WriteError, match="już istnieje"):
        writer.create("teams_graph/abc/raport.md", "druga")


def test_prune_stale_survives_a_directory_it_cannot_remove_and_does_not_count_it(
    tmp_path, monkeypatch
):
    """Sprzątanie biegnie RAZ na starcie drzwi Teams — ``PermissionError`` na katalogu zapisanym
    przez wykonawcę nie może ich nie podnieść, a nieudane usunięcie nie może iść do licznika.

    ``ignore_errors=True`` dotyczy wyłącznie tego, co ``rmtree`` napotka W ŚRODKU; odmowa na samym
    katalogu (albo błąd spaceru po mtime) leci dalej i wywraca start. Osłona ma obejmować JEDEN
    katalog rozmowy — reszta ma zostać sprzątnięta.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/oporny/a.md", "x")
    writer.create("teams_graph/zwykly/b.md", "y")
    dawno = (datetime.now(tz=UTC) - timedelta(days=40)).timestamp()
    os.utime(tmp_path / "teams_graph" / "oporny" / "a.md", (dawno, dawno))
    os.utime(tmp_path / "teams_graph" / "zwykly" / "b.md", (dawno, dawno))

    prawdziwy_rmtree = shutil.rmtree

    def rmtree_ktory_odmawia(path, *args, **kwargs):
        if Path(path).name == "oporny":
            raise PermissionError("katalog zajęty przez wykonawcę")
        return prawdziwy_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(filesystem_workspace.shutil, "rmtree", rmtree_ktory_odmawia)

    removed = prune_stale(tmp_path, older_than=timedelta(days=30), now=datetime.now(tz=UTC))

    assert removed == 1  # policzony TYLKO ten, który naprawdę zniknął
    assert (tmp_path / "teams_graph" / "oporny").exists()
    assert not (tmp_path / "teams_graph" / "zwykly").exists()


def test_read_still_returns_an_ordinary_file_from_the_conversation_directory(tmp_path):
    """Kontrola symlinków nie może zabrać zwykłego odczytu — inaczej zamiast bramki mamy blokadę."""
    root = tmp_path / "scratchpad"
    scope = root / "teams_graph" / "aaa"
    scope.mkdir(parents=True)
    (scope / "notatka.txt").write_text("treść", encoding="utf-8")

    repo = FilesystemWorkspaceRepository(root)

    assert repo.read("teams_graph/aaa", "notatka.txt") == "treść"
    assert repo.read_bytes("teams_graph/aaa", "notatka.txt") == b"tre\xc5\x9b\xc4\x87"
