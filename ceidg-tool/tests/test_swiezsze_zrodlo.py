"""Który opis trafia do skoroszytu, gdy lista i szczegóły się nie zgadzają (audyt A5).

`merge_sources` łączy dwa źródła tego samego wpisu: rekord z `/firmy` i rekord z `/firma`.
Do 2026-09-08 szczegół wygrywał **zawsze**, bez patrzenia na to, kiedy każde z nich pobrano.
Zmierzony objaw: wiersz eksportu mówił `status = 'AKTYWNY'`, podczas gdy `list_json` tego samego
wiersza — i kolumna indeksowa `status_api` — mówiły już `WYKRESLONY`.

To nie jest przypadek egzotyczny. Szczegóły siedzą w cache i są odświeżane tylko wtedy, gdy coś
je unieważni, a `pobierz` odświeża listę przy każdym przebiegu. Rozjazd rośnie sam z upływem czasu.
"""

from __future__ import annotations

from ceidg_tool.normalizer import merge_sources, normalize
from ceidg_tool.records import RawRecord, RowContext

WPIS = "18578BAF-BAC7-42B9-AA7E-4C3666132E01"
STARSZY = "2026-09-01T10:00:00Z"
NOWSZY = "2026-09-08T10:00:00Z"


def _rekord(*, list_utc: str | None, detail_utc: str | None) -> RawRecord:
    """Ten sam wpis w dwóch źródłach, z jawnie sprzecznym `status`."""
    return RawRecord(
        id=WPIS,
        list_json={"id": WPIS, "nazwa": "Piekarnia", "status": "WYKRESLONY"},
        list_utc=list_utc,
        detail_json={"id": WPIS, "nazwa": "Piekarnia", "status": "AKTYWNY", "rokPkd": "2025"},
        detail_utc=detail_utc,
        detail_state="pobrany",
        zrodlo="CEIDG_API",
    )


def test_swiezsza_lista_wygrywa_ze_starszym_szczegolem() -> None:
    """Rdzeń A5: skoroszyt nie ma prawa zaprzeczać danym, z których powstał.

    Lista pobrana tydzień po szczegółach mówi `WYKRESLONY`; stara reguła oddawała `AKTYWNY`,
    czyli stan sprzed wykreślenia, i robiła to po cichu."""
    scalony = merge_sources(_rekord(list_utc=NOWSZY, detail_utc=STARSZY))

    assert scalony["status"] == "WYKRESLONY"


def test_swiezszy_szczegol_wygrywa_ze_starsza_lista() -> None:
    """Kontrola pozytywna, i zwykły przypadek: świeże szczegóły biją starą listę.

    Bez tego testu naprawa mogłaby po prostu odwrócić pierwszeństwo, co byłoby tym samym
    defektem obróconym o 180 stopni."""
    scalony = merge_sources(_rekord(list_utc=STARSZY, detail_utc=NOWSZY))

    assert scalony["status"] == "AKTYWNY"


def test_starsze_zrodlo_nadal_uzupelnia_pola_ktorych_swiezsze_nie_ma() -> None:
    """Świeższe źródło wygrywa na **wspólnych** polach, a nie zastępuje drugiego.

    Lista nie niesie `rokPkd`, więc świeższa lista nie ma prawa go skasować — inaczej naprawa
    jednej cichej straty kupowałaby drugą, dokładnie tak jak przy A1."""
    scalony = merge_sources(_rekord(list_utc=NOWSZY, detail_utc=STARSZY))

    assert scalony["rokPkd"] == "2025"


def test_brak_znacznika_czasu_zostawia_dotychczasowa_kolejnosc() -> None:
    """Bez obu dat nie ma czego porównywać — wtedy szczegół wygrywa, jak dotąd."""
    scalony = merge_sources(_rekord(list_utc=None, detail_utc=STARSZY))

    assert scalony["status"] == "AKTYWNY"


def test_wiersz_eksportu_nie_przeczy_kolumnie_indeksowej() -> None:
    """Objaw widziany przez operatora: to wiersz skoroszytu, a nie słownik w pamięci.

    Test idzie przez `normalize`, bo tam powstaje to, co ląduje w arkuszu `Firmy` — i to jest
    jedyny świadek, którego nie da się przekonać licznikiem."""
    wiersz = normalize(
        _rekord(list_utc=NOWSZY, detail_utc=STARSZY),
        RowContext(srodowisko="test", pobrano_utc=NOWSZY),
    )

    assert wiersz.firmy["status"] == "WYKRESLONY"
