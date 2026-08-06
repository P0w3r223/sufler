"""Sondy dostawy ze skrzynki nadawczej rozmowy (``OutboxDelivery``, ADR 0009 paczki).

Ciężar leży na sondach NEGATYWNYCH i na regule sprzątania. Kolektor, który wysyła wszystko,
co znajdzie, i kolektor, który poprawnie odrzuca, dają na szczęśliwej ścieżce identyczny wynik —
różnią się dopiero na pliku spoza białej listy, ponad limitem i na awarii wysyłki.
"""

from __future__ import annotations

import pytest

from workmate.core.application.outbox import DeliveryReport, OutboxDelivery, OutboxLimits
from workmate.core.ports.outbox import Deliverable

_DIR = "teams-graph/abc123"


class FakeRepo:
    """Atrapa skrzynki: trzyma pliki w pamięci i notuje, co zostało sprzątnięte."""

    def __init__(self, items: list[Deliverable]) -> None:
        self.items = list(items)
        self.discarded: list[str] = []

    def collect(self, dirpath: str) -> list[Deliverable]:
        assert dirpath == _DIR
        return list(self.items)

    def discard(self, dirpath: str, name: str) -> None:
        self.discarded.append(name)


def _doc(name: str, content: bytes = b"tresc") -> Deliverable:
    return Deliverable(name=name, content=content, content_type="text/markdown; charset=utf-8")


def _delivery(repo: FakeRepo, *, max_bytes: int = 1024, max_files: int = 5) -> OutboxDelivery:
    return OutboxDelivery(
        repo, OutboxLimits(max_file_bytes=max_bytes, max_files_per_turn=max_files)
    )


def test_pusta_skrzynka_nie_produkuje_zadnego_komunikatu():
    repo = FakeRepo([])
    report = _delivery(repo).deliver(_DIR, lambda item: None)
    assert report.is_empty()
    assert report.notice() == ""
    assert repo.discarded == []


def test_plik_wyslany_znika_ze_skrzynki():
    repo = FakeRepo([_doc("raport.md")])
    sent: list[str] = []

    report = _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["raport.md"]
    assert report.delivered == ("raport.md",)
    assert repo.discarded == ["raport.md"]
    assert "W załączniku: raport.md." in report.notice()


def test_awaria_wysylki_ZOSTAWIA_plik_do_ponowienia():
    """Ubytek jest tu realny — treść powstała, a odbiorca jej nie zobaczył. Plik musi zostać."""
    repo = FakeRepo([_doc("raport.md")])

    def send(item: Deliverable) -> None:
        raise RuntimeError("Graph 503")

    report = _delivery(repo).deliver(_DIR, send)

    assert report.delivered == ()
    assert report.failed == (("raport.md", "RuntimeError"),)
    assert repo.discarded == [], "plik po nieudanej wysyłce musi zostać w skrzynce"
    assert "spróbuję ponownie" in report.notice()


def test_awaria_wysylki_nie_wypuszcza_wyjatku():
    """Dostawa jest dodatkiem do tury, która już się udała — nie może jej zabrać."""
    repo = FakeRepo([_doc("a.md")])
    _delivery(repo).deliver(_DIR, lambda item: (_ for _ in ()).throw(OSError("gniazdo")))


def test_rozszerzenie_spoza_bialej_listy_jest_odrzucane_I_sprzatane():
    """Zostawienie takiego pliku dokleiłoby ten sam komunikat do KAŻDEJ kolejnej odpowiedzi."""
    repo = FakeRepo([_doc("skrypt.sh")])
    sent: list[str] = []

    report = _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == [], "plik spoza listy nie może pójść do rozmówcy"
    assert report.delivered == ()
    assert [name for name, _ in report.rejected] == ["skrypt.sh"]
    assert repo.discarded == ["skrypt.sh"], "odrzucenie trwałe sprząta — inaczej zatruta wiadomość"


@pytest.mark.parametrize("nazwa", ["raport.exe", "bezrozszerzenia", "archiwum.tar.gz", "..md"])
def test_niebezpieczne_i_nieobslugiwane_nazwy_nie_ida_do_rozmowcy(nazwa: str):
    repo = FakeRepo([_doc(nazwa)])
    sent: list[str] = []
    _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))
    assert sent == []


def test_plik_ponad_limit_jest_odrzucany_z_podaniem_limitu():
    repo = FakeRepo([_doc("duzy.pdf", content=b"x" * 5000)])

    report = _delivery(repo, max_bytes=2048).deliver(_DIR, lambda item: None)

    assert report.delivered == ()
    assert report.rejected[0][0] == "duzy.pdf"
    assert "2 KB" in report.rejected[0][1]
    assert repo.discarded == ["duzy.pdf"]


def test_pusty_plik_nie_jest_wysylany():
    repo = FakeRepo([_doc("pusty.md", content=b"")])
    sent: list[str] = []
    report = _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))
    assert sent == []
    assert report.rejected[0][1] == "jest pusty"


def test_nadmiar_ponad_limit_liczby_jest_odrzucany_a_reszta_idzie():
    repo = FakeRepo([_doc(f"{i}.md") for i in range(5)])
    sent: list[str] = []

    report = _delivery(repo, max_files=2).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["0.md", "1.md"], "wysyłamy deterministycznie — po nazwie, nie po kolejności FS"
    assert len(report.rejected) == 3
    assert set(repo.discarded) == {"0.md", "1.md", "2.md", "3.md", "4.md"}


def test_jedna_zla_pozycja_nie_blokuje_pozostalych():
    repo = FakeRepo([_doc("a.md"), _doc("b.sh"), _doc("c.txt")])
    sent: list[str] = []

    report = _delivery(repo).deliver(_DIR, lambda item: sent.append(item.name))

    assert sent == ["a.md", "c.txt"]
    assert [name for name, _ in report.rejected] == ["b.sh"]


def test_komunikat_laczy_dostarczone_i_odrzucone():
    report = DeliveryReport(
        delivered=("raport.pdf",), rejected=(("skrypt.sh", "ma złe rozszerzenie"),)
    )
    notice = report.notice()
    assert "W załączniku: raport.pdf." in notice
    assert "Nie wysłałem pliku skrypt.sh — ma złe rozszerzenie." in notice
