"""Zero, którego nikt nie zmierzył, nie jest zerem (audyt 2026-09-08, pozycja A8).

`liczba_pkd` i `liczba_spolek` liczyły długość listy, której źródło w ogóle nie przysłało.
Ścieżka listy zwraca **siedem pól** — zmierzone na `tests/fixtures/firmy_page0_limit5.json`:
`id`, `nazwa`, `status`, `link`, `wlasciciel`, `adresDzialalnosci`, `dataRozpoczecia` — i nie
ma wśród nich ani `pkd`, ani `spolki`. Mimo to każdy wiersz eksportu z listy twierdził
`liczba_pkd = 0`, czyli „ten wpis nie ma ani jednego kodu PKD". Dla jednoosobowej
działalności to zdanie jest fałszywe z definicji: kod przeważający jest obowiązkowy.

Kształt jest ten sam, co przy `liczba_spolek` dla raportu, i rozumowanie stało w tym samym
pliku, kilkadziesiąt linijek wyżej — brakowało tylko drugiego warunku.

Kontrole pozytywne są tu ważniejsze niż zwykle: naprawa „zawsze puste" byłaby tym samym
defektem obróconym o 180 stopni, a przy `liczba_spolek` zero bywa **prawdziwe** i musi
przeżyć.
"""

from __future__ import annotations

from typing import Any

from ceidg_tool.normalizer import normalize
from ceidg_tool.records import RawRecord, RowContext
from tests.conftest import detail_record, list_record

CTX = RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z")


def _z_listy() -> RawRecord:
    """Rekord tak, jak wraca z `/firmy` bez `--szczegoly`: żadnego `detail_json`."""
    return RawRecord(
        id=list_record(1)["id"],
        list_json=list_record(1),
        detail_json=None,
        list_utc="2026-09-05T09:00:00Z",
        detail_utc=None,
        detail_state="brak",
        zrodlo="CEIDG_API",
    )


def _ze_szczegolami(**nadpisz: Any) -> RawRecord:
    return RawRecord(
        id=list_record(1)["id"],
        list_json=list_record(1),
        detail_json=detail_record(1, **nadpisz),
        list_utc="2026-09-05T09:00:00Z",
        detail_utc="2026-09-05T09:30:00Z",
        detail_state="pobrany",
        zrodlo="CEIDG_API",
    )


def test_lista_bez_szczegolow_nie_podaje_liczby_pkd() -> None:
    """Rdzeń A8. Puste znaczy „nie wiem", zero znaczyłoby „sprawdziłem, nie ma"."""
    wiersz = normalize(_z_listy(), CTX).firmy

    assert wiersz["liczba_pkd"] is None
    assert wiersz["liczba_spolek"] is None


def test_lista_bez_szczegolow_mowi_o_tym_wprost_w_wierszu() -> None:
    """Kolumna `dane_szczegolowe` jest tym, co pozwala operatorowi odróżnić puste od zera.
    Bez niej pusta komórka wygląda jak brak danych w rejestrze, a nie jak nieopłacony fetch."""
    assert normalize(_z_listy(), CTX).firmy["dane_szczegolowe"] is False


def test_szczegoly_licza_kody_pkd() -> None:
    """Kontrola pozytywna: naprawa „zawsze puste" przeszłaby test wyżej i zabrała kolumnę,
    dla której cała ścieżka szczegółów istnieje."""
    assert normalize(_ze_szczegolami(), CTX).firmy["liczba_pkd"] == 4


def test_szczegoly_bez_spolek_podaja_prawdziwe_zero() -> None:
    """Kontrola pozytywna i granica poprawki. Rekord **ze szczegółami** bez klucza `spolki`
    to zmierzone zero: API pomija pola puste zamiast wysyłać `null`, więc tu zero jest
    twierdzeniem, które źródło potrafi wypowiedzieć."""
    wiersz = normalize(_ze_szczegolami(spolki=[]), CTX).firmy

    assert wiersz["liczba_spolek"] == 0
    assert wiersz["liczba_pkd"] == 4


def test_wiersz_z_raportu_liczy_pkd_ale_nie_spolki() -> None:
    """Dzienny raport niesie kody PKD, a spółek cywilnych nie — więc te dwie kolumny
    rozchodzą się dokładnie tam, gdzie rozchodzi się zawartość źródła."""
    surowy = RawRecord(
        id="NIP:1234567890",
        list_json={
            "nip": "1234567890",
            "nazwa": "Zakład testowy",
            "zrodlo": "CEIDG_RAPORT",
            "pkd": [{"kod": "9602Z"}, {"kod": "9621Z"}],
        },
        detail_json=None,
        list_utc="2026-09-05T09:00:00Z",
        detail_utc=None,
        detail_state="brak",
        zrodlo="CEIDG_RAPORT",
    )

    wiersz = normalize(surowy, CTX).firmy

    assert wiersz["liczba_pkd"] == 2, "raport niesie kody, więc liczba jest zmierzona"
    assert wiersz["liczba_spolek"] is None, "spółek raport nie niesie i nigdy nie niósł"
