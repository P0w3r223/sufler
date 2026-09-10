"""Anonimizator ma zachowywać własności API, nie tylko usuwać dane osobowe.

`tests/fixtures/` to kopia prawdziwych odpowiedzi przepuszczona przez
`scripts/anonymize_samples.py`. Kopia jest jedynym materiałem dowodowym całej suity offline,
więc każda własność, której anonimizator nie zachowa, znika bez śladu — a testy dalej
przechodzą, tyle że opisują świat, którego nie ma.

Tak zginęła wielkość liter identyfikatorów. Anonimizator podnosił każdy do wielkich, przez co
fixture `/zmiana` wyglądał jak `/firma`, 768 testów zgadzało się ze sobą, a na produkcji
`aktualizuj` zapisywał każdą zmienioną firmę dwa razy i nie pokazywał żadnej (ADR-0013).
Właściwość została przywrócona; ten plik jest jej asercją, żeby przywrócenie nie było jedyną
rzeczą, która ją trzyma. `tests/test_api_traits.py` pilnuje tego samego od strony wyniku
(czy gotowe fixtures zgadzają się z pomiarem) — tutaj pilnujemy narzędzia, które je robi,
bo fixtures można wygenerować ponownie w każdej chwili.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from ceidg_tool.recordid import GUID_WPISU, kanoniczny_id

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from anonymize_samples import Anonymizer, case_shape  # noqa: E402

# Ten sam wpis w pisowni z `/firmy` i w pisowni z `/zmiana`.
UPPER = "18578BAF-BAC7-42B9-AA7E-4C3666132E69"
LOWER = UPPER.lower()
KSZTALT_GRUP = (8, 4, 4, 4, 12)


def _grupy(rid: str) -> list[int]:
    return [len(g) for g in rid.split("-")]


@pytest.mark.parametrize(
    ("wartosc", "oczekiwane"),
    [
        (UPPER, "upper"),
        (LOWER, "lower"),
        ("18578Baf-BAC7-42B9-AA7E-4C3666132E69", None),  # mieszana — nie do rozstrzygnięcia
        ("1234-5678", None),  # bez liter
        ("", None),
    ],
)
def test_case_shape_rozpoznaje_pisownie(wartosc: str, oczekiwane: str | None) -> None:
    assert case_shape(wartosc) == oczekiwane


def test_podmieniony_identyfikator_zachowuje_pisownie_wystapienia() -> None:
    """Sedno przywróconej własności: wychodzi ta pisownia, która weszła.

    Fixture `/zmiana` ma nieść małe litery, a `/firma` wielkie, bo tak odpowiada rejestr.
    Anonimizator, który to ujednolica, kasuje jedyną różnicę odróżniającą oba endpointy."""
    anon = Anonymizer()
    assert anon.guid(LOWER).islower()
    assert anon.guid(UPPER).isupper()


def test_jeden_wpis_dostaje_jeden_zamiennik_mimo_dwoch_pisowni() -> None:
    """Druga połowa własności: różna pisownia, ten sam wpis — i po podmianie nadal ten sam.

    Bez tego fixtures `/zmiana` i `/firma` opisywałyby dwie różne firmy, więc test przejścia
    całej ścieżki aktualizacji nie mógłby pokazać ani defektu, ani poprawki: nie miałby jak
    stwierdzić, że to ten sam wpis."""
    anon = Anonymizer()
    z_zmiany = anon.guid(LOWER)
    z_firmy = anon.guid(UPPER)
    assert z_zmiany != z_firmy  # pisownia się różni…
    assert kanoniczny_id(z_zmiany) == kanoniczny_id(z_firmy)  # …a tożsamość nie


def test_podmieniony_identyfikator_wpisu_pozostaje_guidem_szesnastkowym() -> None:
    """Zamiennik musi wpadać pod `GUID_WPISU`, inaczej kanonizacja w testach nic nie robi."""
    anon = Anonymizer()
    for wartosc in (anon.guid(UPPER), anon.guid(LOWER)):
        assert GUID_WPISU.fullmatch(wartosc) is not None


def test_identyfikator_raportu_ma_ksztalt_guida_ale_nim_nie_jest() -> None:
    """Podmiana na `RAPORT-000` gubiła kształt, więc kanonizacji nie było jak sprawdzić.

    Identyfikator `/raporty` wchodzi wprost do adresu pobrania archiwum: ma kształt
    8-4-4-4-12, nie jest szesnastkowy i jest istotny co do wielkości liter. Wszystkie trzy
    własności muszą przetrwać anonimizację, bo inaczej fixture nie odróżnia raportu od
    wpisu — a to jest dokładnie ta granica, na której stoi `GUID_WPISU`."""
    anon = Anonymizer()
    identyfikatory = [anon.raport_id() for _ in range(20)]
    for rid in identyfikatory:
        assert _grupy(rid) == list(KSZTALT_GRUP)
        assert GUID_WPISU.fullmatch(rid) is None, f"{rid!r} wygląda jak identyfikator wpisu"
        assert kanoniczny_id(rid) == rid
    # Wielkość liter jest istotna, więc w materiale muszą się w ogóle pojawiać litery obu
    # rodzajów — inaczej „istotna" byłoby zdaniem, którego nic nie sprawdza.
    wszystkie = "".join(identyfikatory)
    assert any(c.islower() for c in wszystkie) and any(c.isupper() for c in wszystkie)


def test_cialo_zmiany_zachowuje_male_litery() -> None:
    """Kontrola przez wejście, którego naprawdę używa generator fixtures."""
    anon = Anonymizer()
    wynik: dict[str, Any] = anon.body({"identyfikatoryWpisow": [LOWER, UPPER], "count": 2})
    assert [rid.islower() for rid in wynik["identyfikatoryWpisow"]] == [True, False]


def test_cialo_listy_firm_zachowuje_wielkie_litery() -> None:
    anon = Anonymizer()
    wynik: dict[str, Any] = anon.body({"firmy": [{"id": UPPER, "nazwa": "X"}]})
    assert wynik["firmy"][0]["id"].isupper()


def test_link_rekordu_wskazuje_na_ten_sam_wpis_co_pole_id() -> None:
    """Fixture musi być spójny wewnętrznie: `link` prowadzi do wpisu, którego niesie `id`.

    Podmiana idzie dwiema drogami (`firm` dla pola, `url` dla adresu), więc rozjazd byłby
    cichy — a testy wznowienia chodzą po `links`."""
    anon = Anonymizer()
    rekord = {"id": UPPER, "link": f"https://dane.biznes.gov.pl/api/ceidg/v3/firma/{UPPER}"}
    wynik: dict[str, Any] = anon.body({"firmy": [rekord]})
    podmieniony = wynik["firmy"][0]
    assert podmieniony["link"].endswith(podmieniony["id"])


def test_raport_i_jego_adres_pobrania_maja_ten_sam_identyfikator() -> None:
    """Adres archiwum niesie identyfikator dosłownie — rozjazd zepsułby pobieranie raportu."""
    anon = Anonymizer()
    zrodlo = {
        "id": "i5KK21pR-HoSX-TmOm-pPg6-lM1pkQwHZ4L1",
        "nazwa": "Złożone wnioski",
        "format": ".xml",
        "raport": "https://dane.biznes.gov.pl/api/ceidg/v3/raport/i5KK21pR-HoSX-TmOm-pPg6-lM1pkQwHZ4L1",
    }
    wynik: dict[str, Any] = anon.body({"raporty": [zrodlo]})
    raport = wynik["raporty"][0]
    assert raport["id"] != zrodlo["id"]
    assert raport["raport"].endswith(f"/{raport['id']}")
    assert GUID_WPISU.fullmatch(raport["id"]) is None


def test_anonimizacja_jest_deterministyczna() -> None:
    """Fixtures są w repozytorium — ponowne wygenerowanie nie może robić szumu w diffie."""
    assert Anonymizer().guid(UPPER) == Anonymizer().guid(UPPER)
    assert Anonymizer().raport_id() == Anonymizer().raport_id()
