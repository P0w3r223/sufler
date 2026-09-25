"""Awarie I/O pisarzy plikowych: zapis urwany w połowie i plik znikający pod ręką.

Pakiet ćwiczył dotąd JEDNĄ awarię zapisu — nieudany ``os.link``. Reszta drogi (urwany zapis
pliku tymczasowego, ``os.replace`` w połowie, znikający plik, katalog bez prawa zapisu) była
nietknięta, a to właśnie ona decyduje, czy przy ``docker stop``/ENOSPC zostaje spójny stan,
czy notatka ucięta w pół zdania.

Każda sonda asertuje niezmiennik, który kod DOTRZYMUJE dziś: urwany zapis zostawia oryginał
nietknięty, podnosi ``WriteError`` (a nie surowy ``OSError``) i nie zostawia półproduktu ``.tmp``;
plik znikający pod ręką daje „nie ma pliku", a nie wywróconą turę. Docstringi opisują, JAK ten
niezmiennik był łamany, zanim sonda powstała — to opis powodu istnienia sondy, nie opis stanu
bieżącego. Znaczników ``xfail`` nie ma tu żadnych i nie powinno być: sonda oczekująca porażki
przestaje pilnować naprawionego kodu, a niezmienniki poniżej są egzekwowane.

Wszystko deterministyczne: awarie wstrzykujemy podmianą metod ``Path``, bez zależności od czasu,
kolejności nici ani od tego, co akurat robi system plików.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from sufler.adapters.inbound.github import state
from sufler.adapters.outbound.filesystem_outbox import FilesystemOutboxRepository
from sufler.adapters.outbound.filesystem_snapshots import FilesystemNoteSnapshots
from sufler.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
    prune_stale,
)
from sufler.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from sufler.adapters.outbound.markdown_notes_writer import MarkdownNotesWriter
from sufler.core.domain.models import Note, NoteMetadata
from sufler.core.errors import RepositoryError, WriteError

_KATALOG_ROZMOWY = "teams-graph/abc123"

# Uprawnienia POSIX: na Windows ``chmod`` jest no-opem, a root je ignoruje — w obu wypadkach
# sonda padłaby na PRZYGOTOWANIU, nie na asercji. Asercja zostaje nietknięta i biega na CI (Linux).
posix_only = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="uprawnienia katalogu działają tylko na POSIX i nie dotyczą roota",
)


def _note(note_id: str, body: str = "treść notatki") -> Note:
    return Note(
        id=note_id,
        metadata=NoteMetadata(
            title="Przegląd", project="scada-integration", date=date(2026, 8, 17)
        ),
        body=body,
    )


def _urwij_zapis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zapis pliku tymczasowego urywa się w POŁOWIE (ENOSPC / ubicie procesu).

    Do pliku trafia połowa treści, dopiero potem leci ``OSError`` — czyli dokładnie ten stan,
    przed którym broni plik tymczasowy: półprodukt istnieje, ale nie ma prawa się opublikować.
    Warunek po ``.tmp`` zostawia nietknięte przygotowanie testu (zapis „przed awarią").
    """
    prawdziwy_text = Path.write_text
    prawdziwy_bytes = Path.write_bytes

    def write_text(self: Path, data: str, *args: object, **kwargs: object):
        if self.name.endswith(".tmp"):
            prawdziwy_text(self, data[: len(data) // 2], *args, **kwargs)
            raise OSError("brak miejsca na urządzeniu")
        return prawdziwy_text(self, data, *args, **kwargs)

    def write_bytes(self: Path, data: bytes):
        if self.name.endswith(".tmp"):
            prawdziwy_bytes(self, data[: len(data) // 2])
            raise OSError("brak miejsca na urządzeniu")
        return prawdziwy_bytes(self, data)

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "write_bytes", write_bytes)


def _znika_po_sprawdzeniu(monkeypatch: pytest.MonkeyPatch, nazwa: str) -> None:
    """Plik istnieje w chwili ``is_file()``, a znika, zanim ktoś go dotknie.

    To jest realne okno, nie egzotyka: skrzynkę i brudnopis zapełnia POWŁOKA modelu, która biegnie
    obok drzwi, a sprzątanie TTL chodzi po tym samym drzewie. Symulacja jest flagowa, nie licznikowa
    (``is_file`` woła ``stat`` wewnętrznie, więc liczenie wywołań byłoby wróżeniem z implementacji
    ``pathlib``).
    """
    odpowiedziano: set[str] = set()
    prawdziwy_is_file = Path.is_file
    prawdziwy_stat = Path.stat
    prawdziwy_open = Path.open
    prawdziwy_read_bytes = Path.read_bytes
    prawdziwy_read_text = Path.read_text

    def znikniete(self: Path) -> bool:
        return self.name == nazwa and nazwa in odpowiedziano

    def is_file(self: Path, *args: object, **kwargs: object):
        wynik = prawdziwy_is_file(self, *args, **kwargs)
        if self.name == nazwa:
            odpowiedziano.add(nazwa)
        return wynik

    def stat(self: Path, *args: object, **kwargs: object):
        if znikniete(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return prawdziwy_stat(self, *args, **kwargs)

    def opener(self: Path, *args: object, **kwargs: object):
        if znikniete(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return prawdziwy_open(self, *args, **kwargs)

    def read_bytes(self: Path):
        if znikniete(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return prawdziwy_read_bytes(self)

    def read_text(self: Path, *args: object, **kwargs: object):
        if znikniete(self):
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return prawdziwy_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


def _nieczytelny(monkeypatch: pytest.MonkeyPatch, nazwa: str) -> None:
    """Plik JEST i ``is_file()`` to potwierdza, ale każdy odczyt treści dostaje odmowę.

    Stan przeciwny do ``_znika_po_sprawdzeniu`` i to przeciwieństwo jest tu całą treścią:
    wolumen zamontowany z innym uid, ``chmod 000`` z powłoki modelu, błąd I/O. Osłona, która
    myli te dwa stany, melduje „nie ma pliku" o pliku, który leży na dysku.
    """
    prawdziwy_open = Path.open
    prawdziwy_read_bytes = Path.read_bytes
    prawdziwy_read_text = Path.read_text

    def odmowa(self: Path) -> bool:
        return self.name == nazwa

    def opener(self: Path, *args: object, **kwargs: object):
        if odmowa(self):
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_open(self, *args, **kwargs)

    def read_bytes(self: Path):
        if odmowa(self):
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_read_bytes(self)

    def read_text(self: Path, *args: object, **kwargs: object):
        if odmowa(self):
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opener)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


# --- zapis urwany w połowie: oryginał i katalog mają zostać czyste ----------------


def test_interrupted_write_of_a_note_leaves_neither_the_note_nor_a_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Notatka publikuje się dopiero ``os.link`` — urwany zapis nie ma prawa nic po sobie zostawić.

    Czytelnik bazy wiedzy jest ZACHŁANNY (``MarkdownNotesRepository.all`` czyta cały katalog), więc
    półprodukt pod docelową nazwą wróciłby jako notatka ucięta w pół zdania. Sonda pilnuje obu
    stron: braku pliku docelowego i braku śmiecia ``.tmp`` w drzewie.
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    _urwij_zapis(monkeypatch)

    with pytest.raises(WriteError, match="nie udało się zapisać"):
        writer.write(note)

    assert not (tmp_path / f"{note.id}.md").exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_interrupted_overwrite_keeps_the_previous_version_of_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Podmiana treści (ADR 0065) idzie przez ``os.replace`` — urwany zapis zostawia STARĄ wersję.

    Zapis „w miejscu" ucinałby notatkę przy awarii w połowie, czyli po cichu tracił wiedzę pod
    pozorem udanej edycji. Sonda sprawdza to, czego nie widać po samym wyjątku: bajty na dysku.
    """
    writer = MarkdownNotesWriter(tmp_path)
    note_id = "mpwik/scada-integration/2026-08-17-przeglad"
    writer.write(_note(note_id, body="pierwsza wersja"))
    przed = (tmp_path / f"{note_id}.md").read_bytes()
    skrot = writer.digest(note_id)
    _urwij_zapis(monkeypatch)

    with pytest.raises(WriteError, match="nie udało się podmienić"):
        writer.overwrite_body(note_id, "druga wersja", expected_sha256=skrot)

    assert (tmp_path / f"{note_id}.md").read_bytes() == przed
    assert list(tmp_path.rglob("*.tmp")) == []


def test_interrupted_write_of_a_workspace_file_leaves_no_half_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Brudnopis dzieli wzorzec z bazą wiedzy: ``mkstemp`` + ``os.link``, więc urwany zapis znika.

    Wypis brudnopisu pomija ``.tmp`` (jest na to test), ale półprodukt zostawiony na dysku rósłby
    z każdą awarią zapisu na wolumenie, którego nikt nie ogląda.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    _urwij_zapis(monkeypatch)

    with pytest.raises(WriteError, match="nie udało się zapisać"):
        writer.create("teams_graph/abc/raport.md", "treść robocza")

    assert not (tmp_path / "teams_graph" / "abc" / "raport.md").exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_interrupted_snapshot_is_loud_and_leaves_no_leftover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Migawka przed mutacją (ADR 0065) MUSI być głośna — na niej stoi odwracalność.

    Bramka mutacji zamienia ``WriteError`` na odmowę operacji. Migawka, która „czasem się nie
    uda", nie jest zabezpieczeniem; a półprodukt ``.tmp`` obok migawek udawałby kopię, której
    nie ma.
    """
    snapshots = FilesystemNoteSnapshots(tmp_path / "snapshots")
    _urwij_zapis(monkeypatch)

    with pytest.raises(WriteError, match="nie udało się zapisać migawki"):
        snapshots.save("mpwik/scada-integration/2026-08-17-przeglad", "treść pliku")

    assert list((tmp_path / "snapshots").rglob("*.tmp")) == []
    assert list((tmp_path / "snapshots").rglob("*.md")) == []


def test_interrupted_write_of_the_bridge_cursor_keeps_the_previous_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Watermark mostu przeżywa ubicie W TRAKCIE zapisu, nie tylko w chwili podmiany.

    Pakiet ćwiczył dotąd awarię ``os.replace`` (po pełnym zapisie). Urwanie SAMEGO zapisu to
    inne okno: gdyby stan szedł prosto do pliku docelowego, poller wstawałby z uciętym JSON-em
    i — po tolerancyjnym ``load`` — repollował historię od zera przy każdym starcie.
    """
    path = tmp_path / "github_state.json"
    state.save(path, {"since": {"issues": "2026-08-16T10:00:00Z"}, "notify_cursor": 41})
    _urwij_zapis(monkeypatch)

    with pytest.raises(OSError, match="brak miejsca"):
        state.save(path, {"since": {"issues": "2026-08-17T10:00:00Z"}, "notify_cursor": 42})

    assert state.load(path) == {
        "since": {"issues": "2026-08-16T10:00:00Z"},
        "notify_cursor": 41,
    }


# --- uprawnienia katalogu (POSIX) -------------------------------------------------


@posix_only
def test_write_into_a_read_only_project_directory_reports_a_write_error(tmp_path: Path):
    """Wolumen bazy wiedzy zamontowany read-only ma dać ``WriteError``, nie surowy ``OSError``.

    Granica narzędzi łapie ``WriteError`` i zwraca ``{"error": ...}``; wyjątek nieznany wypływa
    jako defekt kodu, czyli tura agenta wywraca się zamiast odpowiedzieć „nie udało się zapisać".
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    katalog_projektu = tmp_path / "mpwik" / "scada-integration"
    katalog_projektu.mkdir(parents=True)
    katalog_projektu.chmod(0o555)
    try:
        with pytest.raises(WriteError):
            writer.write(note)
    finally:
        katalog_projektu.chmod(0o755)

    assert not (tmp_path / f"{note.id}.md").exists()


def test_write_that_must_create_a_directory_reports_a_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ta sama awaria, inny moment: katalog firmy/projektu dopiero POWSTAJE.

    ``_atomic_create`` opakowuje ``OSError`` w ``WriteError``, ale ``path.parent.mkdir`` stoi
    linijkę WYŻEJ, poza ``try``. Read-only wolumen bazy wiedzy (albo katalog bez prawa zapisu)
    przechodzi więc do wołającego surowym ``PermissionError`` — czyli jako defekt kodu, mimo że
    to zwykły, oczekiwany stan infrastruktury.
    """
    prawdziwy_mkdir = Path.mkdir

    def mkdir(self: Path, *args: object, **kwargs: object):
        if self.name == "scada-integration":
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)

    with pytest.raises(WriteError):
        MarkdownNotesWriter(tmp_path).write(_note("mpwik/scada-integration/2026-08-17-przeglad"))


def test_prune_stale_survives_a_channel_directory_it_cannot_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sprzątanie TTL biegnie RAZ na starcie drzwi — żaden katalog nie może ich położyć.

    Docstring ``prune_stale`` obiecuje połykanie błędów I/O, ale osłona (``_prune_one``) obejmuje
    wyłącznie pojedynczy katalog rozmowy. Odmowa na poziomie KANAŁU (``channel_dir.iterdir()``)
    albo na korzeniu leci wprost do ``teams_graph.app``, gdzie nikt jej nie łapie: drzwi nie
    wstają, choć jedyną szkodą jest niesprzątnięty brudnopis.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/zwykly/a.md", "x")
    (tmp_path / "oporny_kanal").mkdir()
    dawno = (datetime.now(tz=UTC) - timedelta(days=40)).timestamp()
    os.utime(tmp_path / "teams_graph" / "zwykly" / "a.md", (dawno, dawno))
    prawdziwy_iterdir = Path.iterdir

    def iterdir(self: Path):
        if self.name == "oporny_kanal":
            raise PermissionError(13, "Permission denied", str(self))
        return prawdziwy_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)

    usuniete = prune_stale(tmp_path, older_than=timedelta(days=30), now=datetime.now(tz=UTC))

    assert usuniete == 1  # reszta drzewa sprzątnięta mimo jednego katalogu bez dostępu


# --- plik znikający pod ręką (powłoka modelu biegnie obok drzwi) ------------------


def test_reading_an_entry_that_vanished_after_the_scan_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rdzeń dostawy MA gotową ścieżkę na zniknięcie pliku — adapter musi ją umieć wywołać.

    ``OutboxDelivery.deliver`` sprawdza ``if item is None`` z komentarzem „plik zniknął między
    wypisem a odczytem". Adapter zwraca ``None`` tylko wtedy, gdy plik zniknął PRZED ``is_file``;
    zniknięcie tuż po nim wypuszcza ``FileNotFoundError``, a ten — zgodnie z docstringiem
    ``deliver`` („błąd odczytu skrzynki propaguje") — przewraca całą turę zamiast pominąć
    jedną pozycję.
    """
    outbox = tmp_path / "teams-graph" / "abc123" / "outputs"
    outbox.mkdir(parents=True)
    (outbox / "raport.md").write_bytes(b"# Raport")
    _znika_po_sprawdzeniu(monkeypatch, "raport.md")

    assert FilesystemOutboxRepository(tmp_path).read(_KATALOG_ROZMOWY, "raport.md") is None


def test_listing_skips_an_entry_that_vanished_mid_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Wypis skrzynki ma pominąć pozycję, której już nie ma — nie zabrać reszty dostawy.

    Skrzynkę zapełnia powłoka modelu, a wypis leci na starcie tury (``snapshot``) i przy dostawie.
    Plik usunięty w tym oknie (własny ``rm`` modelu, sprzątanie TTL) wywraca cały skan, więc
    ginie także sąsiedni plik, który dało się wysłać.
    """
    outbox = tmp_path / "teams-graph" / "abc123" / "outputs"
    outbox.mkdir(parents=True)
    (outbox / "a.md").write_bytes(b"aaa")
    (outbox / "znika.md").write_bytes(b"bbbbb")
    _znika_po_sprawdzeniu(monkeypatch, "znika.md")

    wpisy = FilesystemOutboxRepository(tmp_path).list_entries(_KATALOG_ROZMOWY)

    assert [w.name for w in wpisy] == ["a.md"]


def test_workspace_listing_skips_an_entry_that_vanished_mid_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ta sama luka na wypisie brudnopisu — a tu wypis obsługuje narzędzie ``File`` modelu.

    Model kasuje własne pliki robocze tą samą powłoką, w której je tworzy, więc okno jest
    codzienne. Wywrócony wypis zabiera całą listę, mimo że brakuje jednej pozycji.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/a.md", "aaa")
    writer.create("teams_graph/abc/znika.md", "bbb")
    _znika_po_sprawdzeniu(monkeypatch, "znika.md")

    pliki = FilesystemWorkspaceRepository(tmp_path).list("teams_graph/abc")

    assert [p.name for p in pliki] == ["a.md"]


def test_digest_of_a_note_that_vanished_returns_an_empty_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Port mówi wprost: brak notatki → pusty skrót. Wyścig nie może z tego zrobić wyjątku.

    ``digest`` jest krokiem bramki mutacji (ADR 0065): najpierw skrót, potem migawka, potem
    zmiana. Notatka skasowana równolegle ma dać odmowę „notatka zmieniła się od odczytu",
    a nie surowy ``FileNotFoundError`` w środku bramki.
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    writer.write(note)
    _znika_po_sprawdzeniu(monkeypatch, "2026-08-17-przeglad.md")

    assert writer.digest(note.id) == ""


# --- plik NIECZYTELNY: jest, ale odczyt odmawia ----------------------------------


def test_baza_wiedzy_nie_polyka_notatki_ktorej_nie_da_sie_odczytac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Notatka wypchnięta z korpusu po cichu zmniejsza wynik wyszukiwania o nią samą.

    Osłona w ``all()`` obejmowała ``stat`` i ``read_text``, a komentarz mówił wyłącznie
    o wyścigu z kasowaniem. ``PermissionError`` na notatce albo na katalogu firmy dawał ten
    sam skutek co zniknięcie — tyle że plik zostawał na dysku, a ``search_notes`` i ranking
    milcząco zwracały mniej. Odpowiedź „nie mam takiej wiedzy" jest wtedy nieprawdziwa,
    a w dzienniku nie ma ani jednego wiersza, od którego dałoby się zacząć.
    """
    writer = MarkdownNotesWriter(tmp_path)
    writer.write(_note("mpwik/scada-integration/2026-08-16-czytelna"))
    writer.write(_note("mpwik/scada-integration/2026-08-17-oporna"))
    _nieczytelny(monkeypatch, "2026-08-17-oporna.md")

    with caplog.at_level("WARNING"):
        notatki = MarkdownNotesRepository(tmp_path).all()

    assert [n.id for n in notatki] == ["mpwik/scada-integration/2026-08-16-czytelna"]
    assert "2026-08-17-oporna.md" in caplog.text, "notatka wypadła z korpusu bez śladu"


def test_get_notatki_znikajacej_pod_reka_daje_brak_notatki(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Szósta osłona nie została założona po stronie CZYTELNIKA, choć wyścig jest ten sam.

    ``get`` robi ``is_file()``, a potem ``_load`` — i biegnie przez środek bramki mutacji
    (``note_mutation._require_mutable``), w tej samej turze, w której pisarz liczy ``digest``.
    Tam osłona jest. Notatka skasowana w tym oknie wychodziła stąd surowym ``FileNotFoundError``,
    czyli jako defekt kodu, mimo że kontrakt ``get`` ma na to gotowe „notatki nie ma".
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    writer.write(note)
    _znika_po_sprawdzeniu(monkeypatch, "2026-08-17-przeglad.md")

    assert MarkdownNotesRepository(tmp_path).get(note.id) is None


def test_get_notatki_nieczytelnej_nie_udaje_ze_notatki_nie_ma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """„Nie da się przeczytać" nie może wyjść jako „nie istnieje" — bramka mutacji tak odmawia.

    Odpowiedź ``None`` z ``get`` zamienia się w bramce w komunikat „notatka nie istnieje",
    czyli w zdanie nieprawdziwe o stanie bazy wiedzy, wypowiedziane dokładnie do tego, kto
    o tę notatkę pyta.
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    writer.write(note)
    _nieczytelny(monkeypatch, "2026-08-17-przeglad.md")

    with pytest.raises(RepositoryError, match="nie udało się odczytać notatki"):
        MarkdownNotesRepository(tmp_path).get(note.id)


def test_odczyt_pliku_roboczego_znikajacego_pod_reka_daje_brak_pliku(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``File(read)`` idzie nago po ``is_file()`` z ``_resolve_in_scope`` — a to DWA dyski.

    Katalog roboczy kasuje ta sama powłoka tego samego modelu, który tym narzędziem czyta.
    Zniknięcie w tym oknie wywracało turę surowym ``FileNotFoundError`` zamiast oddać
    „nie ma takiego pliku", które port ``WorkspaceRepository`` ma w kontrakcie.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/raport.md", "treść robocza")
    _znika_po_sprawdzeniu(monkeypatch, "raport.md")
    repo = FilesystemWorkspaceRepository(tmp_path)

    assert repo.read("teams_graph/abc", "raport.md") is None
    assert repo.read_bytes("teams_graph/abc", "raport.md") is None


def test_odczyt_nieczytelnego_pliku_roboczego_nie_udaje_ze_pliku_nie_ma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Odmowa dostępu ma dojść do modelu z prawdziwym powodem, nie jako „plik nie istnieje".

    Koperta narzędzia zamienia ``RepositoryError`` na ``{"error": ...}``, więc tura się nie
    wywraca; ``None`` kazałoby modelowi szukać literówki w nazwie pliku, który widzi na wypisie.
    """
    writer = FilesystemWorkspaceWriter(tmp_path)
    writer.create("teams_graph/abc/raport.md", "treść robocza")
    _nieczytelny(monkeypatch, "raport.md")
    repo = FilesystemWorkspaceRepository(tmp_path)

    with pytest.raises(RepositoryError, match="nie udało się odczytać pliku raport.md"):
        repo.read_bytes("teams_graph/abc", "raport.md")


def test_podmiana_nieczytelnej_notatki_nie_melduje_ze_jej_nie_ma(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fail-closed zostaje, ale komunikat ma być prawdziwy — inaczej człowiek szuka nie tam.

    ``_read_bytes_or_none`` łapał każdy ``OSError``, więc odmowa dostępu do istniejącej notatki
    wychodziła z bramki mutacji jako „notatka nie istnieje, nie ma czego podmienić" i nie
    zostawiała ani wiersza w dzienniku.
    """
    writer = MarkdownNotesWriter(tmp_path)
    note = _note("mpwik/scada-integration/2026-08-17-przeglad")
    writer.write(note)
    skrot = writer.digest(note.id)
    _nieczytelny(monkeypatch, "2026-08-17-przeglad.md")

    with pytest.raises(WriteError, match="nie udało się odczytać notatki"):
        writer.overwrite_body(note.id, "druga wersja", expected_sha256=skrot)

    with pytest.raises(WriteError, match="nie udało się odczytać notatki"):
        writer.delete(note.id, expected_sha256=skrot)
