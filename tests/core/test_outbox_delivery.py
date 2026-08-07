"""Sondy dostawy ze skrzynki nadawczej rozmowy (``OutboxDelivery``).

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`.

Ciężar leży na sondach NEGATYWNYCH i na regule sprzątania. Kolektor, który wysyła wszystko,
co znajdzie, i kolektor, który poprawnie odrzuca, dają na szczęśliwej ścieżce identyczny wynik —
różnią się dopiero na pliku spoza białej listy, ponad limitem i na awarii wysyłki.
"""

from __future__ import annotations

import pytest

from workmate.core.application.outbox import DeliveryReport, OutboxDelivery, OutboxLimits
from workmate.core.ports.outbox import Deliverable, OutboxEntry, PermanentDeliveryError

_DIR = "teams-graph/abc123"


class FakeRepo:
    """Atrapa skrzynki: metadane i treść osobno, jak w porcie; notuje odczyty i sprzątanie."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.discarded: list[str] = []
        self.read_names: list[str] = []

    def list_entries(self, dirpath: str) -> list[OutboxEntry]:
        assert dirpath == _DIR
        return [OutboxEntry(name=n, size=len(c)) for n, c in self.files.items()]

    def read(self, dirpath: str, name: str) -> Deliverable | None:
        self.read_names.append(name)
        content = self.files.get(name)
        if content is None:
            return None
        return Deliverable(name=name, content=content, content_type="text/markdown; charset=utf-8")

    def discard(self, dirpath: str, name: str) -> None:
        self.discarded.append(name)
        self.files.pop(name, None)


class FakeClock:
    """Zegar monotoniczny sterowany z testu — budżet mierzymy bez czekania realnego czasu."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self._step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self._step
        return value


def _armed(repo: FakeRepo, **kw) -> OutboxDelivery:
    """Dostawa z ZROBIONĄ migawką — odpowiednik tury, w której skrzynka startuje pusta."""
    delivery = _delivery(repo, **kw)
    snapshot = dict(repo.files)
    repo.files.clear()
    delivery.snapshot(_DIR)
    repo.files.update(snapshot)
    return delivery


def _delivery(
    repo: FakeRepo,
    *,
    max_bytes: int = 1024,
    max_files: int = 5,
    max_seconds: float = 20.0,
    monotonic: FakeClock | None = None,
) -> OutboxDelivery:
    return OutboxDelivery(
        repo,
        OutboxLimits(
            max_file_bytes=max_bytes,
            max_files_per_turn=max_files,
            max_total_seconds=max_seconds,
        ),
        monotonic=monotonic or FakeClock(),
    )


def test_pusta_skrzynka_nie_produkuje_zadnego_komunikatu():
    repo = FakeRepo({})
    report = _armed(repo).deliver(_DIR, lambda item: None)
    assert report.is_empty()
    assert report.notice() == ""
    assert repo.discarded == []


def test_plik_wyslany_znika_ze_skrzynki():
    repo = FakeRepo({"raport.md": b"tresc"})
    sent: list[str] = []

    report = _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["raport.md"]
    assert report.delivered == ("raport.md",)
    assert repo.discarded == ["raport.md"]
    assert "W załączniku: raport.md." in report.notice()


def test_ODRZUCONY_plik_nie_jest_w_ogole_czytany_z_dysku():
    """Treść skrzynki dyktuje model z powłoką, a proces drzwi obsługuje WSZYSTKIE kanały.

    Wczytanie pliku ponad limit do pamięci tylko po to, żeby zaraz go odrzucić, dawałoby
    modelowi sposób na wywrócenie drzwi (`dd if=/dev/zero of=outputs/a.md bs=1M count=8000`).
    """
    repo = FakeRepo({"duzy.md": b"x" * 5000, "maly.md": b"ok"})

    _armed(repo, max_bytes=2048).deliver(_DIR, lambda item: None)

    assert repo.read_names == ["maly.md"], "plik ponad limit nie może trafić do pamięci"


def test_awaria_PRZEJSCIOWA_zostawia_plik_do_ponowienia():
    """Ubytek jest tu realny — treść powstała, a odbiorca jej nie zobaczył."""
    repo = FakeRepo({"raport.md": b"tresc"})

    def send(item: Deliverable) -> None:
        raise RuntimeError("Graph 503")

    report = _armed(repo).deliver(_DIR, send)

    assert report.delivered == ()
    assert report.failed == (("raport.md", "RuntimeError"),)
    assert repo.discarded == [], "plik po przejściowej awarii musi zostać w skrzynce"
    assert "spróbuję ponownie" in report.notice()


def test_awaria_TRWALA_sprzata_plik_zamiast_zapetlac_ponowienia():
    """Bez tego 4xx z Graph doklejałoby „spróbuję ponownie" do KAŻDEJ kolejnej odpowiedzi
    w rozmowie, płacąc dwa żądania za turę, aż ktoś ręcznie wejdzie na wolumen."""
    repo = FakeRepo({"raport.md": b"tresc"})

    def send(item: Deliverable) -> None:
        raise PermanentDeliveryError("Graph odrzucił plik (HTTP 400)")

    report = _armed(repo).deliver(_DIR, send)

    assert report.failed == ()
    assert report.rejected == (("raport.md", "Graph odrzucił plik (HTTP 400)"),)
    assert repo.discarded == ["raport.md"]
    assert "spróbuję ponownie" not in report.notice()


def test_awaria_wysylki_nie_wypuszcza_wyjatku():
    """Dostawa jest dodatkiem do tury, która już się udała — nie może jej zabrać."""
    repo = FakeRepo({"a.md": b"x"})
    _armed(repo).deliver(_DIR, lambda item: (_ for _ in ()).throw(OSError("gniazdo")))


def test_nazwa_jest_normalizowana_przed_wyslaniem():
    """Nazwę nadał MODEL, tworząc plik powłoką; konsument ma dostać slug z białej listy."""
    repo = FakeRepo({"Raport MPWiK.md": b"tresc"})
    sent: list[str] = []

    _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["raport-mpwik.md"]
    assert repo.discarded == ["Raport MPWiK.md"], "sprzątamy po nazwie Z DYSKU, nie po slugu"


def test_rozszerzenie_spoza_bialej_listy_jest_odrzucane_I_sprzatane():
    repo = FakeRepo({"skrypt.sh": b"x"})
    sent: list[str] = []

    report = _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == []
    assert [name for name, _ in report.rejected] == ["skrypt.sh"]
    assert repo.discarded == ["skrypt.sh"], "odrzucenie trwałe sprząta — inaczej zatruta wiadomość"


@pytest.mark.parametrize("nazwa", ["raport.exe", "bezrozszerzenia", "archiwum.tar.gz", "..md"])
def test_niebezpieczne_i_nieobslugiwane_nazwy_nie_ida_do_rozmowcy(nazwa: str):
    repo = FakeRepo({nazwa: b"x"})
    sent: list[str] = []
    _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))
    assert sent == []


def test_plik_ponad_limit_jest_odrzucany_z_podaniem_limitu():
    repo = FakeRepo({"duzy.pdf": b"x" * 5000})

    report = _armed(repo, max_bytes=2048).deliver(_DIR, lambda item: None)

    assert report.delivered == ()
    assert "2 KB" in report.rejected[0][1]
    assert repo.discarded == ["duzy.pdf"]


def test_pusty_plik_nie_jest_wysylany():
    repo = FakeRepo({"pusty.md": b""})
    sent: list[str] = []
    report = _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))
    assert sent == []
    assert report.rejected[0][1] == "jest pusty"


def test_plik_zniknietv_miedzy_wypisem_a_odczytem_jest_pomijany():
    repo = FakeRepo({"znika.md": b"x"})
    repo.files.clear()  # wypis już się odbył, treści już nie ma

    report = _armed(repo).deliver(_DIR, lambda item: None)

    assert report.is_empty()


def test_nadmiar_ponad_limit_liczby_jest_odrzucany_a_reszta_idzie():
    repo = FakeRepo({f"{i}.md": b"x" for i in range(5)})
    sent: list[str] = []

    report = _armed(repo, max_files=2).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["0.md", "1.md"], "wysyłamy deterministycznie — po nazwie, nie po kolejności FS"
    assert len(report.rejected) == 3
    assert set(repo.discarded) == {"0.md", "1.md", "2.md", "3.md", "4.md"}


def test_jedna_zla_pozycja_nie_blokuje_pozostalych():
    repo = FakeRepo({"a.md": b"x", "b.sh": b"x", "c.txt": b"x"})
    sent: list[str] = []

    report = _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["a.md", "c.txt"]
    assert [name for name, _ in report.rejected] == ["b.sh"]


def test_KOMUNIKAT_zwija_sie_przy_wielu_odrzuceniach():
    """Wiadomość Teams ma sufit rozmiaru. Trzysta zdań o odrzuconych plikach mogłoby sprawić,
    że rozmówca nie dostanie NICZEGO — a pliki są już sprzątnięte, więc strata jest trwała."""
    report = DeliveryReport(rejected=tuple((f"{i}.sh", "złe rozszerzenie") for i in range(300)))

    notice = report.notice()

    assert notice.count("Nie wysłałem pliku") == 3
    assert "Pominąłem też 297 innych." in notice


def test_BUDZET_CZASU_odklada_reszte_zamiast_wstrzymywac_ture():
    """Dostawa biegnie w tej samej ścieżce co odpowiedź — rozmówca widzi tekst dopiero po niej.

    Pięć plików po timeoucie klienta HTTP to minuty ciszy po turze, która już się udała.
    Pozycje ponad budżet ZOSTAJĄ w skrzynce (nie są odrzucone) i jadą przy następnej wiadomości.
    """
    repo = FakeRepo({f"{i}.md": b"x" for i in range(4)})
    sent: list[str] = []
    zegar = FakeClock(step=4.0)  # każdy odczyt zegara przesuwa go o 4 s

    report = _armed(repo, max_seconds=10.0, monotonic=zegar).deliver(
        _DIR, lambda item: sent.append(item.name)
    )

    assert sent, "część plików musi pójść — budżet nie może blokować wszystkiego"
    assert report.deferred, "reszta ma zostać odłożona, nie odrzucona"
    assert set(report.deferred) & set(repo.files), "odłożone pliki zostają w skrzynce"
    assert [n for n, _ in report.rejected] == []
    assert "dostarczę je przy następnej wiadomości" in report.notice()


def test_hojny_budzet_nie_odklada_niczego():
    repo = FakeRepo({f"{i}.md": b"x" for i in range(4)})

    report = _armed(repo, monotonic=FakeClock(step=0.1)).deliver(_DIR, lambda item: None)

    assert report.deferred == ()
    assert len(report.delivered) == 4


def test_komunikat_laczy_dostarczone_i_odrzucone():
    report = DeliveryReport(
        delivered=("raport.pdf",), rejected=(("skrypt.sh", "ma złe rozszerzenie"),)
    )
    notice = report.notice()
    assert "W załączniku: raport.pdf." in notice
    assert "Nie wysłałem pliku skrypt.sh — ma złe rozszerzenie." in notice


def test_PODLOZONY_plik_sprzed_tury_NIE_jest_wysylany():
    """Sedno migawki. Wolumen brudnopisu jest WSPÓLNY dla rozmów, a wykonawca ustawia tylko
    `cwd` — powłoka rozmowy A może policzyć katalog rozmowy B (`sha256(team/channel/root)`,
    trójka jawna dla każdego w kanale), założyć w nim `outputs/` zwykłym `mkdir` i podłożyć plik.

    Bez migawki kolektor opublikowałby go w CUDZYM wątku, firmując treść botem. Żaden guard na
    dowiązania by tego nie dotknął — dowiązania tam nie ma.
    """
    repo = FakeRepo({"podszywka.pdf": b"tresc od obcej rozmowy"})
    delivery = _delivery(repo)
    delivery.snapshot(_DIR)  # migawka widzi plik JUŻ w skrzynce
    sent: list[str] = []

    report = delivery.deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == [], "plik sprzed tury nie może pójść do rozmówcy"
    assert report.delivered == ()
    assert repo.discarded == ["podszywka.pdf"]


def test_plik_powstaly_W_TRAKCIE_tury_idzie_normalnie():
    """Druga strona tej samej granicy — migawka nie może zablokować własnej pracy modelu."""
    repo = FakeRepo({})
    delivery = _delivery(repo)
    delivery.snapshot(_DIR)
    repo.files["raport.md"] = b"wynik tury"
    sent: list[str] = []

    delivery.deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["raport.md"]


def test_ponowienie_wlasnej_nieudanej_wysylki_przechodzi_przez_migawke():
    """Plik zostawiony przez NAS po awarii przejściowej jest w migawce następnej tury —
    i musi mimo to pojechać, inaczej mechanizm ponowienia byłby martwy."""
    repo = FakeRepo({})
    delivery = _delivery(repo)

    delivery.snapshot(_DIR)  # tura pierwsza: skrzynka pusta na starcie
    repo.files["raport.md"] = b"tresc"  # model tworzy plik w trakcie tury
    delivery.deliver(_DIR, lambda item: (_ for _ in ()).throw(RuntimeError("503")))
    assert "raport.md" in repo.files, "awaria przejściowa zostawia plik"

    sent: list[str] = []
    delivery.snapshot(_DIR)  # tura druga: plik jest w migawce, ale to NASZ plik
    delivery.deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["raport.md"]


def test_BEZ_migawki_nie_wysylamy_nic():
    """Fail-closed: brak migawki to defekt okablowania, a nie zgoda na wysyłkę wszystkiego."""
    repo = FakeRepo({"raport.md": b"tresc"})
    sent: list[str] = []

    report = _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == []
    assert report.is_empty()
    assert repo.discarded == [], "bez migawki nie kasujemy też niczego"


def _fail_always(item: Deliverable) -> None:
    raise RuntimeError("Graph 503")


def test_plik_niewysylalny_NIE_blokuje_reszty_kolejki():
    """Sortowanie po samej nazwie stawiało trwale zawodzącą pozycję na czele KAŻDEJ tury:
    zjadała budżet i odkładała wszystko za sobą, bez końca."""
    repo = FakeRepo({})
    delivery = _delivery(repo)

    delivery.snapshot(_DIR)  # skrzynka pusta na starcie tury — pliki powstają w jej trakcie
    repo.files.update({"a-zepsuty.md": b"x", "z-zdrowy.md": b"x"})
    delivery.deliver(_DIR, lambda item: None if item.name.startswith("z") else _fail_always(item))

    sent: list[str] = []
    delivery.snapshot(_DIR)  # `a-zepsuty.md` został do ponowienia i jest w migawce jako NASZ
    repo.files["m-nowy.md"] = b"x"  # świeży wynik tury drugiej
    delivery.deliver(_DIR, lambda item: sent.append(item.name))

    assert sent[0] == "m-nowy.md", (
        "pozycja z próbami ma zejść na koniec okna mimo wcześniejszej nazwy alfabetycznie — "
        f"kolejność wysyłki: {sent}"
    )


def test_po_trzeciej_probie_pozycja_jest_sprzatana_i_przestaje_obiecywac_ponowienie():
    repo = FakeRepo({})
    delivery = _delivery(repo)

    for _ in range(3):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        report = delivery.deliver(_DIR, _fail_always)

    assert report.failed == ()
    assert report.rejected == (("raport.md", "nie udało się wysłać po 3 próbach"),)
    assert "raport.md" not in repo.files
    assert "spróbuję ponownie" not in report.notice()


def test_licznik_prob_NIE_rosnie_od_odlozenia_przez_budzet():
    """Pozycja odłożona budżetem nie została nawet spróbowana — wliczanie jej do sufitu
    kasowałoby po trzech turach plik, którego nikt nie wysyłał."""
    repo = FakeRepo({})
    delivery = _delivery(repo, max_seconds=0.0, monotonic=FakeClock(step=1.0))

    for _ in range(4):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        report = delivery.deliver(_DIR, _fail_always)

    assert report.deferred == ("raport.md",)
    assert "raport.md" in repo.files, "plik odłożony budżetem nie może zostać skasowany"


def test_ponowione_pozycje_NIE_wypadaja_przez_limit_liczby_plikow():
    """Pozycja zatrzymana do ponowienia wchodzi do okna PRZED świeżymi plikami tury.

    Nazwa sortuje się tu na SAMYM KOŃCU i to jest cała sonda: dopóki okno brało się z alfabetu,
    taka pozycja wypadała poza limit i była kasowana z powodem o liczbie plików — obietnica
    „spróbuję ponownie" kończyła się cichym usunięciem treści. Poprzednia wersja tego testu
    używała nazwy sortującej się PIERWSZEJ, więc przechodziła nad działającą i nad zepsutą
    implementacją tak samo.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2)

    delivery.snapshot(_DIR)
    repo.files["z-ponawiany.md"] = b"x"
    delivery.deliver(_DIR, _fail_always)

    delivery.snapshot(_DIR)
    repo.files.update({"a-nowy.md": b"x", "b-nowy.md": b"x"})
    report = delivery.deliver(_DIR, lambda item: None)

    powody = {name: reason for name, reason in report.rejected}
    assert "z-ponawiany.md" not in powody, f"ponawiana pozycja odrzucona: {powody}"
    assert "z-ponawiany.md" in report.delivered, f"ponawiana pozycja niewysłana: {report}"


def test_licznik_zeruje_sie_po_udanej_wysylce():
    """Klucz to nazwa, a model użyje jej ponownie — świeży plik nie może dziedziczyć prób."""
    repo = FakeRepo({})
    delivery = _delivery(repo)

    delivery.snapshot(_DIR)
    repo.files["raport.md"] = b"x"
    delivery.deliver(_DIR, _fail_always)

    delivery.snapshot(_DIR)
    delivery.deliver(_DIR, lambda item: None)  # ta sama nazwa, tym razem sukces

    for _ in range(2):
        delivery.snapshot(_DIR)
        repo.files["raport.md"] = b"x"
        report = delivery.deliver(_DIR, _fail_always)

    assert report.failed, "licznik nie wyzerował się — plik zginął przedwcześnie"
