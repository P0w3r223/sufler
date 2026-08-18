"""Sondy dostawy ze skrzynki nadawczej rozmowy (``OutboxDelivery``).

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`.

Ciężar leży na sondach NEGATYWNYCH i na regule sprzątania. Kolektor, który wysyła wszystko,
co znajdzie, i kolektor, który poprawnie odrzuca, dają na szczęśliwej ścieżce identyczny wynik —
różnią się dopiero na pliku spoza białej listy, ponad limitem i na awarii wysyłki.
"""

from __future__ import annotations

import re

import pytest

from workmate.core.application.outbox import DeliveryReport, OutboxDelivery, OutboxLimits
from workmate.core.ports.outbox import (
    Deliverable,
    OutboxEntry,
    OutboxReadError,
    PermanentDeliveryError,
)

_DIR = "teams-graph/abc123"

# Nazwa na drucie niesie skrót TREŚCI (patrz `test_dwa_watki_...`). Sondy o INNYCH własnościach
# — kolejność, sprzątanie, limity — nie mają powtarzać tej reguły ani zaszytego skrótu, więc
# zdejmują sufiks i mówią o tym, o czym są.
_SUFIKS_TRESCI = re.compile(r"-[0-9a-f]{8}(?=\.[^.]+$)")


def _bez_skrotu(nazwa: str) -> str:
    return _SUFIKS_TRESCI.sub("", nazwa)


def _bez_skrotow(nazwy: tuple[str, ...]) -> list[str]:
    return [_bez_skrotu(n) for n in nazwy]


class FakeRepo:
    """Atrapa skrzynki: metadane i treść osobno, jak w porcie; notuje odczyty i sprzątanie."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.discarded: list[str] = []
        self.read_names: list[str] = []
        # Pozycje, których NIE DA SIĘ przeczytać, choć leżą w skrzynce (odmowa dostępu, błąd
        # I/O, urośnięcie ponad sufit odczytu). Port oddaje to wyjątkiem, nie ``None``.
        self.read_errors: dict[str, OutboxReadError] = {}

    def list_entries(self, dirpath: str) -> list[OutboxEntry]:
        assert dirpath == _DIR
        return [OutboxEntry(name=n, size=len(c)) for n, c in self.files.items()]

    def read(self, dirpath: str, name: str) -> Deliverable | None:
        self.read_names.append(name)
        blad = self.read_errors.get(name)
        if blad is not None:
            raise blad
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

    assert [_bez_skrotu(n) for n in sent] == ["raport.md"]
    assert repo.discarded == ["raport.md"]
    # Raport ma niesć nazwę WYSŁANĄ, nie źródłową: odbiorcą komunikatu jest człowiek, a pod
    # wiadomością wisi załącznik ze skrótem treści w nazwie. „W załączniku: raport.md" przy
    # pliku ``raport-1a2b3c4d.md`` kazałoby mu szukać czegoś, czego tam nie ma.
    assert report.delivered == tuple(sent)
    assert f"W załączniku: {sent[0]}." in report.notice()


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
    """Dostawa jest dodatkiem do tury, która już się udała — nie może jej zabrać.

    Samo „nie rzuciło" nie odróżnia jednak połknięcia awarii od jej PRZEMILCZENIA: sonda
    sprawdza więc też, że awaria wraca w raporcie i że plik ZOSTAJE w skrzynce. Przejściowy
    błąd gniazda, po którym plik znika, to cicha utrata wyniku pracy modelu.
    """
    repo = FakeRepo({"a.md": b"x"})

    raport = _armed(repo).deliver(_DIR, lambda item: (_ for _ in ()).throw(OSError("gniazdo")))

    assert [name for name, _ in raport.failed] == ["a.md"]
    assert not raport.delivered
    assert repo.discarded == []  # plik czeka na ponowienie, nie wyparował


def test_nazwa_jest_normalizowana_przed_wyslaniem():
    """Nazwę nadał MODEL, tworząc plik powłoką; konsument ma dostać slug z białej listy."""
    repo = FakeRepo({"Raport MPWiK.md": b"tresc"})
    sent: list[str] = []

    _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert [_bez_skrotu(n) for n in sent] == ["raport-mpwik.md"]
    assert repo.discarded == ["Raport MPWiK.md"], "sprzątamy po nazwie Z DYSKU, nie po slugu"


def test_TA_SAMA_nazwa_z_DWOCH_watkow_nie_daje_jednej_sciezki_na_dysku_kanalu():
    """Sedno defektu: dostawa wgrywa plik na dysk KANAŁU, wspólny dla wszystkich wątków.

    Sam slug znaczył, że `outputs/raport.md` z wątku B nadpisuje plik wątku A pod tą samą
    ścieżką — załącznik wiszący pod wiadomością A zaczynał serwować dokument z rozmowy B.
    Naraz utrata treści wątku A i ujawnienie treści wątku B osobom, które jej nie widziały.
    """
    watek_a = FakeRepo({"raport.md": b"ustalenia z MPWiK"})
    watek_b = FakeRepo({"raport.md": "wynagrodzenia zespołu".encode()})
    sent: list[str] = []

    _armed(watek_a).deliver(_DIR, lambda item: sent.append(item.name))
    _armed(watek_b).deliver(_DIR, lambda item: sent.append(item.name))

    assert len(set(sent)) == 2, f"ta sama ścieżka dla różnych treści: {sent}"
    assert all(_bez_skrotu(n) == "raport.md" for n in sent), sent


def test_PONOWIENIE_tej_samej_tresci_trafia_w_TE_SAMA_nazwe():
    """Idempotencja ponowienia. Gdyby sufiks brał się z czasu albo losowości, każda nieudana
    próba zostawiałaby na dysku kanału kolejną kopię tego samego dokumentu."""
    repo = FakeRepo({})
    delivery = _delivery(repo)
    sent: list[str] = []

    delivery.snapshot(_DIR)
    repo.files["raport.md"] = b"ta sama tresc"
    delivery.deliver(_DIR, lambda item: (_ for _ in ()).throw(RuntimeError("503")))

    delivery.snapshot(_DIR)
    delivery.deliver(_DIR, lambda item: sent.append(item.name))

    repo2 = FakeRepo({"raport.md": b"ta sama tresc"})
    _armed(repo2).deliver(_DIR, lambda item: sent.append(item.name))

    assert len(sent) == 2 and sent[0] == sent[1], f"ta sama treść dała dwie nazwy: {sent}"


def test_NADMIAR_PONAWIANYCH_ponad_limit_liczby_jest_ODKLADANY_a_nie_kasowany():
    """Docstring modułu mówi wprost: przy awarii przejściowej pliku nie wolno usuwać.

    Limit liczby plików jest granicą JEDNEJ tury, a nie werdyktem o treści — kasował jednak
    także pozycje zatrzymane do ponowienia, którym system obiecał „spróbuję ponownie".
    Ubytek jest realny: treść powstała, odbiorca jej nie zobaczył, a plik zniknął z wolumenu.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2)

    delivery.snapshot(_DIR)
    repo.files.update({"x-stary.md": b"1", "y-stary.md": b"2", "z-stary.md": b"3"})
    delivery.deliver(_DIR, _fail_always)  # trzy pozycje ponawiane, okno mieści dwie

    delivery.snapshot(_DIR)
    repo.files["a-nowy.md"] = b"4"  # świeży plik rezerwuje sobie miejsce w oknie
    report = delivery.deliver(_DIR, _fail_always)

    poza_oknem = set(report.deferred) - set(report.delivered)
    assert poza_oknem, "trzecia pozycja ponawiana musi zostać odłożona"
    assert not [n for n, _ in report.rejected], f"ponawiane odrzucone limitem liczby: {report}"
    assert poza_oknem <= set(repo.files), "odłożona pozycja musi ZOSTAĆ na dysku"


def test_pozycja_GLODZONA_przez_rezerwacje_okna_nie_zostaje_na_zawsze():
    """Odłożenie nie zużywa próby — więc bez sufitu WIEKU zbiór rósłby bez końca.

    ``_okno_tury`` rezerwuje ponowieniom najwyżej ``limit - 1`` miejsc, dopóki jest co świeżego
    wysłać, więc przy ``|ours| >= limit`` co najmniej jedna ponawiana za każdym razem wypada za
    okno i nie zużywa NICZEGO: ani próby (bo nikt jej nie wysyłał), ani miejsca w kolejce.
    Przy utrzymującej się awarii wysyłki napływ przewyższał wtedy drenaż, a rośnie to na
    wolumenie brudnopisu WSPÓLNYM dla wszystkich rozmów.
    """
    from workmate.core.application.outbox import _MAX_CARRIED_TURNS

    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2)
    szczyt = 0

    for tura in range(_MAX_CARRIED_TURNS * 3):
        delivery.snapshot(_DIR)
        repo.files[f"swiezy-{tura:02d}.md"] = b"x"  # napływ: jeden nowy plik na turę
        delivery.deliver(_DIR, _fail_always)
        szczyt = max(szczyt, len(repo.files))

    # Sufit jest z zapasem: chodzi o OGRANICZONOŚĆ, nie o dokładną liczbę. Przed naprawą zbiór
    # rósł liniowo z liczbą tur, więc przy trzydziestu turach byłby wielokrotnie większy.
    assert szczyt <= _MAX_CARRIED_TURNS, (
        f"skrzynka rośnie z liczbą tur — szczyt {szczyt} przy {_MAX_CARRIED_TURNS * 3} turach"
    )


def test_wiek_nie_wyprzedza_sufitu_prob_w_zwyklej_sciezce():
    """Pozycja, która NORMALNIE wchodzi do okna, ma dalej odpaść na próbach — z ich powodem.

    Sufit wieku jest ostatnią zaporą dla pozycji głodzonej, nie zamiennikiem sufitu prób:
    ten drugi mówi dokładniej, co się stało („nie udało się wysłać po 3 próbach").
    """
    repo = FakeRepo({})
    delivery = _delivery(repo)

    for _ in range(3):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        report = delivery.deliver(_DIR, _fail_always)

    assert report.rejected == (("raport.md", "nie udało się wysłać po 3 próbach"),)


def test_pozycja_przeterminowana_NIE_obiecuje_juz_ponowienia():
    """Komunikat nie może obiecywać ponowienia pliku, którego przed chwilą nie stało."""
    from workmate.core.application.outbox import _MAX_CARRIED_TURNS

    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2, max_seconds=0.0, monotonic=FakeClock(step=1.0))

    for _ in range(_MAX_CARRIED_TURNS):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")  # odkładany budżetem: ani jednej próby
        report = delivery.deliver(_DIR, _fail_always)

    assert "raport.md" not in repo.files, "pozycja bez ani jednej próby została na zawsze"
    assert "raport.md" not in report.deferred
    assert "spróbuję ponownie" not in report.notice()
    assert [powod for name, powod in report.rejected if name == "raport.md"] == [
        f"nie udało się dostarczyć przez {_MAX_CARRIED_TURNS} tur"
    ]


def test_swiezy_nadmiar_ponad_limit_liczby_dalej_jest_odrzucany():
    """Druga strona tej samej granicy — świeże pliki ponad limit dalej znikają z powodem.

    Bez tego skrzynka rosłaby bez końca: model potrafi wygenerować dwadzieścia plików w turze,
    a materiał źródłowy zostaje piętro wyżej.
    """
    repo = FakeRepo({f"{i}.md": b"x" for i in range(5)})

    report = _armed(repo, max_files=2).deliver(_DIR, lambda item: None)

    assert report.deferred == ()
    assert len(report.rejected) == 3
    assert repo.files == {}


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

    assert [_bez_skrotu(n) for n in sent] == ["0.md", "1.md"], (
        "wysyłamy deterministycznie — po nazwie, nie po kolejności FS"
    )
    assert len(report.rejected) == 3
    assert set(repo.discarded) == {"0.md", "1.md", "2.md", "3.md", "4.md"}


def test_jedna_zla_pozycja_nie_blokuje_pozostalych():
    repo = FakeRepo({"a.md": b"x", "b.sh": b"x", "c.txt": b"x"})
    sent: list[str] = []

    report = _armed(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert [_bez_skrotu(n) for n in sent] == ["a.md", "c.txt"]
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

    assert [_bez_skrotu(n) for n in sent] == ["raport.md"]


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

    assert [_bez_skrotu(n) for n in sent] == ["raport.md"]


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

    assert _bez_skrotu(sent[0]) == "m-nowy.md", (
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
    assert "z-ponawiany.md" in _bez_skrotow(report.delivered), (
        f"ponawiana pozycja niewysłana: {report}"
    )


def test_swieza_tresc_dociera_mimo_zaleglych_ponowien():
    """Ponowienia biorą najwyżej `limit - 1` miejsc, dopóki jest co świeżego wysłać.

    Bezwzględny priorytet ponowień zamieniał jedną stratę na drugą: przy trwającej awarii
    wysyłki świeże pliki były kasowane, żeby zrobić miejsce pozycjom, które i tak zginą na
    suficie prób. Rozmówca nie dostawał wtedy nowej treści ANI RAZU.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2)

    delivery.snapshot(_DIR)
    repo.files.update({"a-stary.md": b"x", "b-stary.md": b"x"})
    delivery.deliver(_DIR, _fail_always)  # oba zostają jako ponawiane

    delivery.snapshot(_DIR)
    repo.files["c-nowy.md"] = b"x"
    report = delivery.deliver(_DIR, lambda item: None)

    assert "c-nowy.md" in _bez_skrotow(report.delivered), f"świeży plik nie dojechał: {report}"


def test_bez_swiezych_plikow_ponowienia_biora_cale_okno():
    """Rezerwacja miejsca dla nikogo byłaby trzecią klasą straty — okno ma być pełne."""
    repo = FakeRepo({})
    delivery = _delivery(repo, max_files=2)

    delivery.snapshot(_DIR)
    repo.files.update({"a-stary.md": b"x", "b-stary.md": b"x"})
    delivery.deliver(_DIR, _fail_always)

    delivery.snapshot(_DIR)
    report = delivery.deliver(_DIR, lambda item: None)

    assert sorted(_bez_skrotow(report.delivered)) == ["a-stary.md", "b-stary.md"], report


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


# --- pozycja, której NIE DA SIĘ przeczytać (odmowa dostępu, sufit odczytu) --------


def test_pozycja_NIECZYTELNA_zostaje_w_raporcie_i_doczekuje_nastepnej_tury():
    """„Nie ma czego czytać" wolno przemilczeć; „nie da się przeczytać" — nie.

    Adapter oddawał KAŻDĄ awarię odczytu jako ``None``, a rdzeń ma na ``None`` jedną gałąź:
    „plik zniknął — nie ma czego wysyłać ani sprzątać". Pozycja, która nie zniknęła, nie
    trafiała więc ani do ``failed``, ani do ``deferred``, ani do ``rejected``: wypadała ze
    zbioru zatrzymanych, a NASTĘPNA tura widziała ją jako podłożoną z innej rozmowy i kasowała.
    Praca modelu znikała, rozmówca dostawał ciszę. Sonda pilnuje obu tur.
    """
    repo = FakeRepo({"raport.md": b"tresc"})
    repo.read_errors["raport.md"] = OutboxReadError("nie udało się odczytać pliku: EACCES")
    delivery = _armed(repo)

    report = delivery.deliver(_DIR, lambda item: None)

    assert [n for n, _ in report.failed] == ["raport.md"]
    assert repo.discarded == [], "nieczytelny plik ma zostać na wolumenie"
    assert "spróbuję ponownie" in report.notice()

    repo.read_errors.clear()  # uprawnienia wróciły (albo skończył się błąd I/O)
    sent: list[str] = []
    delivery.snapshot(_DIR)
    delivery.deliver(_DIR, lambda item: sent.append(item.name))

    assert [_bez_skrotu(n) for n in sent] == ["raport.md"], "plik skasowany jako obcy"


def test_pozycja_ponad_sufit_odczytu_dostaje_WLASNY_wpis_w_raporcie():
    """Sufit odczytu jest werdyktem o pliku, więc ma być zdaniem do rozmówcy, nie linią w logu.

    Ostrzeżenie w dzienniku procesu widzi administrator, a nie ten, kto na plik czekał —
    i dopiero raport odróżnia „za duży, nie wyślę" od cichego zniknięcia.
    """
    repo = FakeRepo({"raport.md": b"x"})
    repo.read_errors["raport.md"] = OutboxReadError(
        "przekracza sufit odczytu 24 MB", permanent=True
    )

    report = _armed(repo).deliver(_DIR, lambda item: None)

    assert report.rejected == (("raport.md", "przekracza sufit odczytu 24 MB"),)
    assert report.failed == ()
    assert repo.discarded == ["raport.md"], "trwały werdykt sprząta, inaczej zatruwa każdą turę"
    assert "Nie wysłałem pliku raport.md — przekracza sufit odczytu 24 MB." in report.notice()


# --- liczniki rozmowy a skrzynka opróżniona POZA dostawą -------------------------


def test_WIEK_pozycji_nie_przezywa_wyczyszczenia_skrzynki():
    """Wcześniejszy powrót przy pustej skrzynce czyścił ``_ours``, ale nie wiek pozycji.

    Zatrzymane pliki znikają z wolumenu także POZA dostawą (sprzątanie TTL, ``rm`` z powłoki
    modelu). Wiek zostawał wtedy na zawsze, a klucz to NAZWA OD MODELU — więc przeterminowany
    ``carried=9`` zabijał świeży plik przy pierwszym odłożeniu, komunikatem o dziesięciu turach
    dla pliku, który istnieje jedną.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo, max_seconds=0.0, monotonic=FakeClock(step=1.0))

    for _ in range(9):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        delivery.deliver(_DIR, _fail_always)

    repo.files.clear()  # plik znika z wolumenu poza dostawą
    delivery.snapshot(_DIR)
    assert delivery.deliver(_DIR, _fail_always).is_empty()

    delivery.snapshot(_DIR)
    repo.files["raport.md"] = b"swieza tresc"  # ta sama nazwa, inna zawartość
    report = delivery.deliver(_DIR, _fail_always)

    assert report.deferred == ("raport.md",)
    assert not report.rejected, f"świeży plik odziedziczył wiek po poprzedniku: {report}"
    assert "raport.md" in repo.files


def test_LICZNIK_PROB_nie_przezywa_wyczyszczenia_skrzynki():
    """Ta sama dziura, drugi licznik: próby wysyłki też stały za wcześniejszym powrotem.

    ``test_licznik_zeruje_sie_po_udanej_wysylce`` pilnuje zerowania po SUKCESIE — a plik
    równie dobrze znika z wolumenu bez żadnej wysyłki i wtedy nikt licznika nie zdejmował.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo)

    for _ in range(2):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        delivery.deliver(_DIR, _fail_always)

    repo.files.clear()
    delivery.snapshot(_DIR)
    assert delivery.deliver(_DIR, _fail_always).is_empty()

    delivery.snapshot(_DIR)
    repo.files["raport.md"] = b"swieza tresc"
    report = delivery.deliver(_DIR, _fail_always)

    assert report.failed, f"świeży plik odziedziczył próby po poprzedniku: {report}"
    assert not report.rejected
    assert "raport.md" in repo.files


def test_sufit_prob_nazywa_KROK_ktory_zawiodl_a_nie_zawsze_wysylke():
    """Pozycja nieprzeczytana i pozycja niewysłana schodzą tą samą gałęzią — słusznie.

    Reguła sprzątania jest dla nich wspólna, myli tylko zdanie: „nie udało się wysłać po
    3 próbach" o pliku, którego ani razu nie dało się OTWORZYĆ, każe szukać awarii w kanale,
    a nie na wolumenie. To jedyna informacja, jaką o tej pozycji dostaje człowiek — plik
    właśnie został skasowany.
    """
    repo = FakeRepo({})
    delivery = _delivery(repo)

    for _ in range(3):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        repo.read_errors["raport.md"] = OutboxReadError("błąd odczytu: Permission denied")
        report = delivery.deliver(_DIR, lambda item: None)

    assert report.rejected == (("raport.md", "nie udało się odczytać po 3 próbach"),)
    assert "Nie wysłałem pliku raport.md — nie udało się odczytać po 3 próbach." in report.notice()


def test_sufit_prob_dla_awarii_WYSYLKI_mowi_dalej_o_wysylce():
    """Druga strona tej samej sondy — rozróżnienie kroku nie może przepisać zdania o wysyłce."""
    repo = FakeRepo({})
    delivery = _delivery(repo)

    for _ in range(3):
        delivery.snapshot(_DIR)
        repo.files.setdefault("raport.md", b"x")
        report = delivery.deliver(_DIR, _fail_always)

    assert report.rejected == (("raport.md", "nie udało się wysłać po 3 próbach"),)
