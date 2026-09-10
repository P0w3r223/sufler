"""Granica, na której identyfikator przestaje być napisem od API (ADR-0013).

Kanonizacja ma **jedno** miejsce — `client._records_of` — i przechodzą przez nie wszystkie
rekordy z `/firmy`, `/firma`, `/zmiana` i `/raporty`. Reszta programu (`store._record_id`,
`pipeline.kanoniczne_id`) woła ją powtórnie, bo idempotentnej funkcji nie szkodzi zawołać
dwa razy — ale to znaczy, że sam szew graniczny nie ma własnego objawu: gdyby przestał
kanonizować, defekt wróciłby dopiero tą ścieżką, która akurat nie ma drugiego zabezpieczenia.

Dlatego asercje są tu o tym, co **wychodzi z klienta**, a nie o tym, co potem robi z tym
magazyn. Materiał to prawdziwe fixtures: `zmiana.json` niesie pisownię małą, `firmy_*.json`
wielką, `raporty.json` identyfikatory nieszesnastkowe — czyli obie połowy reguły naraz.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.client import CeidgClient
from ceidg_tool.criteria import Criteria
from ceidg_tool.ratelimit import InMemoryHistory, RateLimiter
from ceidg_tool.recordid import GUID_WPISU, kanoniczne_id, kanoniczny_id
from tests.conftest import FakeClock, detail_record
from tests.support import FakeApi, load_fixture, registry_id

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"


def make_client(api: FakeApi, clock: FakeClock, profile: ApiProfile | None = None) -> CeidgClient:
    profile = profile or ApiProfile(base_url=BASE, ids_batch_size=5)
    limiter = RateLimiter(
        windows=profile.rate.windows,
        min_spacing_s=profile.rate.min_spacing_s,
        cooldown_s=profile.rate.cooldown_s,
        clock=clock,
        history=InMemoryHistory(),
    )
    return CeidgClient(
        http=api.client(), profile=profile, limiter=limiter, token="tok", clock=clock
    )


def _zmiana_jednostronicowa() -> dict[str, Any]:
    """Prawdziwe ciało `/zmiana` z fixture, skrócone do jednej strony."""
    body = dict(load_fixture("zmiana.json")["body"])
    links = dict(body["links"])
    body["links"] = {**links, "next": links["self"]}
    body["count"] = len(body["identyfikatoryWpisow"])
    return body


def test_identyfikatory_zmian_wychodza_z_klienta_kanoniczne(clock: FakeClock) -> None:
    """`/zmiana` odpowiada małymi literami — z klienta ma wyjść tożsamość, nie pisownia.

    To jest ten szew: gdyby kanonizacja tu wypadła, magazyn dostałby drugą tożsamość
    każdego zmienionego wpisu, czyli dokładnie stan bazy z 2026-09-08."""
    body = _zmiana_jednostronicowa()
    surowe = list(body["identyfikatoryWpisow"])
    assert all(rid.islower() for rid in surowe), "fixture przestał nieść pisownię z `/zmiana`"

    api = FakeApi()
    api.fallback = lambda request: httpx.Response(200, json=body)
    client = make_client(api, clock, ApiProfile(base_url=BASE, max_limit_zmiana=len(surowe)))

    strony = list(
        client.iter_changes(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 3, tzinfo=UTC))
    )
    wydane = [str(r["id"]) for p in strony for r in p.records]
    assert wydane == kanoniczne_id(surowe)
    assert wydane != surowe, "fixture i wynik nie mogą być identyczne — nie byłoby czego mierzyć"


def test_identyfikatory_listy_firm_wychodza_kanoniczne(clock: FakeClock) -> None:
    """`/firmy` odpowiada wielkimi — kanonizacja niczego nie psuje na drodze bez różnicy."""
    body = load_fixture("firmy_page0_limit5.json")["body"]
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(200, json=body)
    client = make_client(api, clock, ApiProfile(base_url=BASE, max_limit_firmy=5))

    strona = next(iter(client.iter_pages(Criteria())))
    wydane = [str(r["id"]) for r in strona.records]
    assert wydane == [kanoniczny_id(rid) for rid in wydane]
    assert all(GUID_WPISU.fullmatch(rid) for rid in wydane)


def test_szczegoly_pytane_kanonicznie_nie_gubia_sie_jako_brakujace(clock: FakeClock) -> None:
    """`missing` ma znaczyć „rejestr nie zna tego wpisu", a nie „inaczej to zapisał".

    Porównanie w `fetch_details` jest równością — pobłażliwe `.upper()` zniknęło stamtąd
    razem z defektem — więc trzyma je wyłącznie kanonizacja na granicy. Atrapa odpowiada
    pisownią rejestru (`registry_id`), a nie pisownią z zapytania."""
    ids = kanoniczne_id(
        [str(r["id"]) for r in load_fixture("firmy_page0_limit5.json")["body"]["firmy"]]
    )

    def fallback(request: httpx.Request) -> httpx.Response:
        pytane = request.url.params.get_list("ids")
        return httpx.Response(
            200, json={"firma": [{**detail_record(1), "id": registry_id(rid)} for rid in pytane]}
        )

    api = FakeApi()
    api.fallback = fallback
    client = make_client(api, clock)

    found, missing = client.fetch_details(ids)
    assert missing == []
    assert sorted(str(r["id"]) for r in found) == sorted(ids)


def test_identyfikatory_raportow_wychodza_nietkniete(clock: FakeClock) -> None:
    """Druga połowa reguły: `/raporty` niesie identyfikatory nieszesnastkowe i istotne
    co do wielkości liter — wchodzą wprost do adresu pobrania archiwum."""
    surowe = [str(r["id"]) for r in load_fixture("raporty.json")["body"]["raporty"]]
    assert any(GUID_WPISU.fullmatch(rid) is None for rid in surowe)

    api = FakeApi()
    api.add_fixture("raporty.json")
    client = make_client(api, clock)

    raporty = client.list_reports()
    assert [r.id for r in raporty] == surowe
    for raport in raporty:
        assert raport.url.endswith(raport.id)
