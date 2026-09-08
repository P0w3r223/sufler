"""Sprawdzenie jednej firmy po NIP (`pipeline.lookup_nip`) — ADR-0008, decyzja 4.

Trzy rzeczy przesądzają o tym, czy ta ścieżka jest tania i uczciwa: literówka w NIP-ie
kosztuje **zero** żądań (suma kontrolna jest liczona lokalnie w `Criteria`), trafienie
kosztuje **dwa** (lista + szczegóły), a wynik zostaje w bazie jako zwykły run, więc
`eksportuj` zrobi z niego skoroszyt bez ponownego pobierania.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.config import Settings
from ceidg_tool.errors import ConfigError
from ceidg_tool.pipeline import Deps, build_deps, lookup_nip, run_export
from tests.conftest import FakeClock, detail_record, list_record
from tests.support import FakeApi

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
NIP = "3563457932"
OTHER_NIP = "8567773578"


def deps_for(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    return build_deps(settings, clock=clock, http=api.client())


def nip_api(*, found: bool = True) -> FakeApi:
    """Jedna strona listy i jedna odpowiedź ze szczegółami — tyle, ile kosztuje sprawdzenie."""
    api = FakeApi()
    record = list_record(1)
    record["wlasciciel"] = {**record["wlasciciel"], "nip": NIP}

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url:
            if not found:
                return httpx.Response(204)
            return httpx.Response(200, json={"count": 1, "firmy": [record], "links": {}})
        if "/firma" in url:
            wanted = request.url.params.get_list("ids")
            detail = detail_record(1)
            detail["wlasciciel"] = {**detail["wlasciciel"], "nip": NIP}
            return httpx.Response(200, json={"firma": [{**detail, "id": i} for i in wanted]})
        raise AssertionError(f"nieoczekiwane żądanie: {url}")

    api.fallback = fallback
    return api


# ----------------------------------------------------------------------------- zła suma kontrolna


@pytest.mark.parametrize(
    ("bad_nip", "reason"),
    [
        ("3563457931", "błędna suma kontrolna"),
        ("356345793", "za krótki"),
        ("35634579321", "za długi"),
        ("abcdefghij", "litery"),
        ("", "pusty"),
        ("   ", "same spacje"),
    ],
    ids=["suma_kontrolna", "za_krotki", "za_dlugi", "litery", "pusty", "spacje"],
)
def test_a_bad_nip_costs_zero_requests(
    bad_nip: str, reason: str, tmp_path: Path, clock: FakeClock
) -> None:
    """Literówka jest wyłapywana lokalnie — API nie może zobaczyć ani jednego żądania.

    Przypadki „pusty” i „same spacje” pilnują konkretnej pułapki: walidator list odsiewa
    puste napisy, więc `Criteria(nip=("",))` nie jest błędem — jest kryterium **bez filtra**,
    czyli zapytaniem o cały rejestr. Bez jawnej odmowy `sprawdz-nip ""` (albo Enter na
    pytaniu o NIP w kreatorze) ruszyłby pobieranie całego CEIDG.
    """
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    with pytest.raises(ConfigError) as caught:
        lookup_nip(bad_nip, deps)

    assert caught.value.exit_code == 3, reason
    assert "Niepoprawny NIP" in str(caught.value)
    assert api.requests == []
    assert deps.store.list_runs() == []  # nie powstał nawet pusty run
    deps.store.close()


def test_a_nip_with_dashes_and_spaces_is_accepted_and_normalised(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Operator przeklei NIP z faktury razem z myślnikami — to nie powód do odmowy."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(" 356-345-79-32 ", deps)

    assert lookup.nip == NIP  # w wyniku jest postać kanoniczna, nie to, co wpisano
    assert lookup.record is not None
    deps.store.close()


# ----------------------------------------------------------------------------- trafienie


def test_a_found_company_costs_exactly_two_requests(tmp_path: Path, clock: FakeClock) -> None:
    """Decyzja 4: jedna lista + jedne szczegóły. Trzecie żądanie znaczyłoby regres kosztu."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(NIP, deps)

    assert lookup.record is not None
    assert lookup.requests == 2
    assert len(api.requests) == 2
    assert sum("/firmy" in r for r in api.requests) == 1
    assert sum("/firma?" in r or "/firma/" in r for r in api.requests) == 1
    deps.store.close()


def test_the_found_record_carries_the_public_ceidg_link(tmp_path: Path, clock: FakeClock) -> None:
    """Rekord z API ma GUID, więc kolumna `link_ceidg` musi być wypełniona (decyzja 5)."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(NIP, deps)

    assert lookup.record is not None
    assert lookup.record.firmy["nip"] == NIP
    assert lookup.record.firmy["link_ceidg"]
    deps.store.close()


def test_the_lookup_asks_the_api_for_that_one_nip(tmp_path: Path, clock: FakeClock) -> None:
    """Filtr musi trafić do zapytania — inaczej dostalibyśmy pierwszą lepszą firmę."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup_nip(NIP, deps)

    listing = next(r for r in api.requests if "/firmy" in r)
    assert f"nip={NIP}" in listing


def test_a_lookup_without_details_costs_one_request(tmp_path: Path, clock: FakeClock) -> None:
    """`szczegoly=False` to świadome oszczędzanie — wtedy szczegóły nie mogą się doliczyć."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(NIP, deps, szczegoly=False)

    assert lookup.requests == 1
    assert not any("/firma?" in r for r in api.requests)
    deps.store.close()


# ----------------------------------------------------------------------------- brak trafienia


def test_a_nip_absent_from_the_registry_is_a_result_not_an_error(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Poprawny NIP bez wpisu to normalny wynik — `record is None`, bez wyjątku."""
    api = nip_api(found=False)
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(NIP, deps)

    assert lookup.record is None
    assert lookup.nip == NIP
    assert lookup.run_id  # run i tak powstał, więc widać go w `runy`
    deps.store.close()


def test_a_missing_company_does_not_pay_for_a_details_request(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Nie ma czego uszczegóławiać — drugie żądanie byłoby zmarnowane."""
    api = nip_api(found=False)
    deps = deps_for(tmp_path, clock, api)

    lookup_nip(NIP, deps)

    assert not any("/firma?" in r for r in api.requests)
    deps.store.close()


# ----------------------------------------------------------------------------- ślad w bazie


def test_the_lookup_leaves_a_normal_run_that_export_can_use(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Decyzja 4: wynik zostaje w bazie, więc `eksportuj` robi skoroszyt bez nowych żądań."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)
    lookup = lookup_nip(NIP, deps)
    requests_after_lookup = len(api.requests)

    summary = run_export(lookup.run_id, tmp_path / "jedna_firma.xlsx", deps)

    assert summary.records == 1
    assert summary.kind == "firmy"
    assert summary.run_ids == (lookup.run_id,)
    assert len(api.requests) == requests_after_lookup  # eksport nie wysłał nic
    deps.store.close()


def test_the_run_is_marked_finished(tmp_path: Path, clock: FakeClock) -> None:
    """Gdyby run został „w toku”, kreator zaproponowałby wznowienie sprawdzenia jednej firmy."""
    api = nip_api()
    deps = deps_for(tmp_path, clock, api)

    lookup = lookup_nip(NIP, deps)

    assert deps.store.get_run(lookup.run_id).status == "zakonczony"
    deps.store.close()
