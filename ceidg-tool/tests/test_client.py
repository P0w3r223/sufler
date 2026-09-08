from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.client import CeidgClient, Cursor
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import (
    AuthError,
    BadRequestError,
    RateLimitError,
    ServerError,
    TransportError,
    UntrustedLinkError,
)
from ceidg_tool.ratelimit import InMemoryHistory, RateLimiter
from ceidg_tool.recordid import kanoniczne_id
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria, fixture_response, load_fixture

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"


def make_client(
    api: FakeApi, clock: FakeClock, profile: ApiProfile | None = None
) -> tuple[CeidgClient, RateLimiter]:
    profile = profile or ApiProfile(base_url=BASE, ids_batch_size=5)
    limiter = RateLimiter(
        windows=profile.rate.windows,
        min_spacing_s=profile.rate.min_spacing_s,
        cooldown_s=profile.rate.cooldown_s,
        clock=clock,
        history=InMemoryHistory(),
    )
    client = CeidgClient(
        http=api.client(), profile=profile, limiter=limiter, token="tok", clock=clock
    )
    return client, limiter


def test_count_uses_limit_1_and_reads_total(clock: FakeClock) -> None:
    api = FakeApi()
    api.add_fixture("firmy_limit1.json")
    client, _ = make_client(api, clock)
    assert client.count(Criteria()) == load_fixture("firmy_limit1.json")["body"]["count"]
    assert api.requests[0].endswith("/firmy?limit=1")
    assert client.rate_remaining is not None


def test_pages_follow_links_next_and_stop_when_next_equals_self(clock: FakeClock) -> None:
    api = FakeApi()
    for name in ("firmy_page0_limit5.json", "firmy_page1_limit5.json", "firmy_page2_limit5.json"):
        api.add_fixture(name)
    # ostatnia strona wg sondy: next == self == last
    last = load_fixture("firmy_page2_limit5.json")
    last_body = dict(last["body"])
    last_body["links"] = {**last_body["links"], "next": last_body["links"]["self"]}
    api.add(last["url"], httpx.Response(200, json=last_body))
    api.add(last_body["links"]["self"], httpx.Response(200, json=last_body))
    profile = ApiProfile(base_url=BASE, max_limit_firmy=5)
    client, _ = make_client(api, clock, profile)

    first_url = load_fixture("firmy_page0_limit5.json")["url"]
    pages = list(client.iter_pages(Criteria(), start=Cursor("links", first_url)))
    assert [p.index for p in pages] == [0, 1, 2]
    assert all(len(p.records) == 5 for p in pages)
    assert pages[0].next_cursor is not None and pages[0].next_cursor.mode == "links"
    assert pages[-1].next_cursor is None
    assert len(api.requests) == 3
    assert clock.sleeps and min(clock.sleeps) >= 3.5  # odstęp między stronami


def test_numeric_paging_mode_uses_page_start(clock: FakeClock) -> None:
    api = FakeApi()
    page0 = load_fixture("firmy_page0_limit5.json")["body"]
    page1 = load_fixture("firmy_page1_limit5.json")["body"]
    seen: list[str] = []

    def fallback(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "page=0" in str(request.url):
            return httpx.Response(200, json=page0)
        if "page=1" in str(request.url):
            return httpx.Response(200, json={**page1, "firmy": page1["firmy"][:2]})
        return httpx.Response(204)

    api.fallback = fallback
    profile = ApiProfile(
        base_url=BASE,
        max_limit_firmy=5,
        paging_mode="numeric",
        page_start=0,
        send_page_on_first_request=True,
    )
    client, _ = make_client(api, clock, profile)
    pages = list(client.iter_pages(criteria(wojewodztwo="podlaskie")))
    assert len(pages) == 2 and pages[1].next_cursor is None
    assert "page=0" in seen[0] and "page=1" in seen[1]


def test_204_means_no_results(clock: FakeClock) -> None:
    api = FakeApi()
    api.add_fixture("firmy_204_nazwa.json")
    profile = ApiProfile(base_url=BASE, max_limit_firmy=3)
    client, _ = make_client(api, clock, profile)
    url = load_fixture("firmy_204_nazwa.json")["url"]
    pages = list(client.iter_pages(Criteria(), start=Cursor("links", url)))
    assert len(pages) == 1 and pages[0].records == [] and pages[0].next_cursor is None


def test_400_is_reported_with_api_message_and_not_retried(clock: FakeClock) -> None:
    api = FakeApi()
    api.add_fixture("firmy_400_limit50.json")
    client, _ = make_client(api, clock)
    url = load_fixture("firmy_400_limit50.json")["url"]
    with pytest.raises(BadRequestError, match="NIEPOPRAWNY_ROZMIAR_STRONY"):
        list(client.iter_pages(Criteria(), start=Cursor("links", url)))
    assert len(api.requests) == 1


def test_401_stops_immediately(clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(401)
    client, _ = make_client(api, clock)
    with pytest.raises(AuthError):
        client.count(Criteria())
    assert len(api.requests) == 1


def test_5xx_is_retried_with_backoff_through_limiter_then_fails(clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(503, text="<html>awaria</html>")
    profile = ApiProfile(base_url=BASE)
    client, _ = make_client(api, clock, profile)
    with pytest.raises(ServerError, match="po 4 próbach"):
        client.count(Criteria())
    assert len(api.requests) == 1 + profile.rate.max_retries_5xx
    assert max(clock.sleeps) >= profile.rate.backoff_base_s * 4


def test_truncated_json_is_a_resumable_error(clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(
        200, content=b'{"firmy": [{"id": "x"', headers={"Content-Type": "application/json"}
    )
    client, _ = make_client(api, clock)
    with pytest.raises(ServerError, match="JSON"):
        client.count(Criteria())


def test_429_waits_full_cooldown_and_retries(clock: FakeClock) -> None:
    api = FakeApi()
    calls = {"n": 0}

    def fallback(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429)
        return fixture_response("firmy_limit1.json")

    api.fallback = fallback
    client, _ = make_client(api, clock)
    assert client.count(Criteria()) > 0
    assert calls["n"] == 2
    assert clock.sleeps[-1] == pytest.approx(185.0)


def test_persistent_429_raises_rate_limit_error(clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(429)
    client, _ = make_client(api, clock)
    with pytest.raises(RateLimitError):
        client.count(Criteria())


def test_connection_errors_retry_10_30_60_300_then_give_up(clock: FakeClock) -> None:
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS", request=request)

    api.fallback = fallback
    client, _ = make_client(api, clock)
    with pytest.raises(TransportError, match="30 min"):
        client.count(Criteria())
    delays = [s for s in clock.sleeps if s >= 10]
    assert delays[:4] == [
        pytest.approx(10.0),
        pytest.approx(30.0),
        pytest.approx(60.0),
        pytest.approx(300.0),
    ]
    assert sum(delays) >= 30 * 60 - 300  # poddaje się, gdy kolejny odstęp przekroczy 30 min


def test_next_link_to_foreign_host_is_refused(clock: FakeClock) -> None:
    api = FakeApi()
    page = load_fixture("firmy_page0_limit5.json")
    body = dict(page["body"])
    body["links"] = {**body["links"], "next": "https://evil.example/api/ceidg/v3/firmy?page=1"}
    api.add(page["url"], httpx.Response(200, json=body))
    profile = ApiProfile(base_url=BASE, max_limit_firmy=5)
    client, _ = make_client(api, clock, profile)
    with pytest.raises(UntrustedLinkError):
        list(client.iter_pages(Criteria(), start=Cursor("links", page["url"])))

    # Odmowa musi paść **przed** limiterem, nie dopiero w transporcie. Odkąd `FakeApi.client()`
    # idzie przez `AllowedHostsTransport`, sam wyjątek nie odróżnia już tych dwóch miejsc: obie
    # bramki rzucają ten sam typ z tym samym komunikatem. Rozstrzyga zegar — sprawdzone mutacją
    # (obie kontrole `_checked_host` usunięte): transport odbija żądanie dopiero po odczekaniu
    # `min_spacing_s`, więc `clock.sleeps` rośnie do [3.75] i test czerwienieje.
    assert clock.sleeps == [], "obcy host zajął miejsce w limiterze, zamiast odbić się wcześniej"


def test_client_refuses_profile_outside_allowlist(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        ApiProfile(base_url="https://evil.example/api")


def test_fetch_details_batches_by_profile_and_reports_missing(clock: FakeClock) -> None:
    api = FakeApi()
    sample = load_fixture("firma_by_ids.json")
    ids = [r["id"] for r in sample["body"]["firma"]]
    served: list[int] = []

    def fallback(request: httpx.Request) -> httpx.Response:
        wanted = request.url.params.get_list("ids")
        served.append(len(wanted))
        found = [r for r in sample["body"]["firma"] if r["id"] in wanted]
        if not found:
            return httpx.Response(204)
        return httpx.Response(200, json={"firma": found})

    api.fallback = fallback
    profile = ApiProfile(base_url=BASE, ids_batch_size=2)
    client, _ = make_client(api, clock, profile)
    records, missing = client.fetch_details(ids + ["BRAK-1"])
    assert [r["id"] for r in records] == ids
    assert missing == ["BRAK-1"]
    assert served == [2, 2, 2]


def test_fetch_details_splits_by_url_length(clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(
        200, json={"firma": [{"id": i} for i in request.url.params.get_list("ids")]}
    )
    profile = ApiProfile(base_url=BASE, ids_batch_size=50, max_url_length=200)
    client, _ = make_client(api, clock, profile)
    ids = kanoniczne_id(f"{i:036d}" for i in range(6))
    records, missing = client.fetch_details(ids)
    assert len(records) == 6 and not missing
    assert len(api.requests) > 1


def test_list_reports_and_download(clock: FakeClock, tmp_path: Path) -> None:
    api = FakeApi()
    api.add_fixture("raporty.json")
    api.fallback = lambda request: httpx.Response(
        200, content=b"PK\x03\x04zip", headers={"Content-Type": "application/octet-stream"}
    )
    client, _ = make_client(api, clock)
    reports = client.list_reports()
    assert len(reports) == 12
    assert reports[0].utworzono.startswith("2026-")
    dest = client.download_report(reports[0], tmp_path / "r.zip")
    assert dest.read_bytes().startswith(b"PK")


def test_iter_changes_wraps_ids(clock: FakeClock) -> None:
    api = FakeApi()
    sample = load_fixture("zmiana.json")
    body = dict(sample["body"])
    body["links"] = {**body["links"], "next": body["links"]["self"]}
    api.fallback = lambda request: httpx.Response(200, json=body)
    profile = ApiProfile(base_url=BASE, max_limit_zmiana=500)
    client, _ = make_client(api, clock, profile)
    from datetime import date

    pages = list(client.iter_changes(date(2026, 9, 2), date(2026, 9, 5)))
    assert len(pages) == 1
    assert all("id" in r for r in pages[0].records)
    assert pages[0].count == sample["body"]["count"]


def test_a_low_rate_limit_header_brakes_the_next_request(clock: FakeClock) -> None:
    """Nagłówek budżetu ma sterować limiterem, a nie tylko trafiać do logu.

    Wcześniej `X-Rate-Limit-Remaining` był czytany i zapisywany w polu, którego nikt nie
    pytał — jedyny sygnał o zużyciu tokenu poza tym procesem szedł wyłącznie do logu."""
    api = FakeApi()
    sample = load_fixture("firmy_limit1.json")
    reset_ms = int((clock.wall() + 600.0) * 1000)
    api.add(
        sample["url"],
        httpx.Response(
            200,
            json=sample["body"],
            headers={"X-Rate-Limit-Remaining": "2", "X-Rate-Limit-Reset": str(reset_ms)},
        ),
    )
    client, _ = make_client(api, clock)

    client.count(Criteria())
    assert client.rate_remaining == 2

    started = clock.monotonic()
    client.count(Criteria())
    # 600 s postoju na budżet, a nie 3,75 s zwykłego odstępu
    assert clock.monotonic() - started >= 600.0


# --- postęp pobierania archiwum raportu -------------------------------------------------

# 21 MB archiwum wojewódzkiego to jedno żądanie i minuty transferu. Do 2026-09-07 klient nie
# mówił w tym czasie nic, więc `pipeline` nie miał czym bić serca blokady bazy (600 s) ani
# czym ruszać paskiem — ten sam kształt defektu co strony `/zmiana` w fazie 3e, warstwę niżej.


def zipped_response(size: int, *, with_length: bool = True) -> httpx.Response:
    """Archiwum o zadanym rozmiarze; bez długości odpowiedź jest strumieniowa (chunked).

    Iterator zamiast `bytes` to jedyny sposób, żeby httpx **nie** dopisał `Content-Length` —
    ręczne usunięcie nagłówka udawałoby serwer, który go nie wysłał, a nie testowało tej
    ścieżki naprawdę."""
    body = b"PK\x03\x04" + b"x" * (size - 4)
    headers = {"Content-Type": "application/octet-stream"}
    if with_length:
        return httpx.Response(200, content=body, headers=headers)
    return httpx.Response(200, content=iter([body]), headers=headers)


def download_with_progress(
    clock: FakeClock, tmp_path: Path, response: httpx.Response
) -> tuple[list[tuple[int, int | None]], Path]:
    api = FakeApi()
    api.add_fixture("raporty.json")
    api.fallback = lambda request: response
    client, _ = make_client(api, clock)
    report = client.list_reports()[0]
    seen: list[tuple[int, int | None]] = []
    dest = client.download_report(
        report, tmp_path / "r.zip", progress=lambda done, total: seen.append((done, total))
    )
    return seen, dest


def test_the_download_reports_progress_from_the_first_byte_to_the_last(
    clock: FakeClock, tmp_path: Path
) -> None:
    """Pierwsze zgłoszenie pada zaraz po nagłówkach, ostatnie mówi dokładny rozmiar.

    Zgłoszenie na starcie jest tym, które ma znaczenie dla blokady: przy wolnym łączu pół
    megabajta potrafi iść długo, a bicie serca musi paść, zanim ten pierwszy próg zostanie
    osiągnięty — nie po nim.
    """
    size = 3 * 512 * 1024
    seen, dest = download_with_progress(clock, tmp_path, zipped_response(size))

    assert seen[0] == (0, size)
    assert seen[-1] == (size, size)
    assert [done for done, _ in seen] == sorted(done for done, _ in seen)
    assert dest.read_bytes().startswith(b"PK") and dest.stat().st_size == size


def test_the_download_reports_often_enough_to_look_alive(clock: FakeClock, tmp_path: Path) -> None:
    """Rytm liczy się w bajtach, a nie w porcjach: 512 KiB to ≥1 zdarzenie na 10 s przy 50 kB/s.

    Zdarzenie na porcję (64 KiB) byłoby 336 zdarzeniami na 21 MB — pasek migałby, a każde
    z nich dotykałoby bazy. Zdarzenie na plik to znów cisza. Próg jest kompromisem i test
    pilnuje obu jego stron.
    """
    size = 4 * 512 * 1024
    seen, _ = download_with_progress(clock, tmp_path, zipped_response(size))

    assert 4 <= len(seen) <= 6, f"nietypowa gęstość zdarzeń: {seen}"


def test_a_response_without_content_length_reports_an_unknown_total(
    clock: FakeClock, tmp_path: Path
) -> None:
    """Bez `Content-Length` suma zostaje `None` — kolumna napisze `?`, a nie zmyśloną liczbę."""
    size = 600 * 1024
    seen, _ = download_with_progress(clock, tmp_path, zipped_response(size, with_length=False))

    assert seen and all(total is None for _, total in seen)
    assert seen[-1][0] == size


def test_a_download_without_a_progress_callback_still_works(
    clock: FakeClock, tmp_path: Path
) -> None:
    """`progress` jest opcjonalny: `eksportuj`, testy i biblioteka wołają bez niego."""
    api = FakeApi()
    api.add_fixture("raporty.json")
    api.fallback = lambda request: zipped_response(1024)
    client, _ = make_client(api, clock)

    dest = client.download_report(client.list_reports()[0], tmp_path / "r.zip")

    assert dest.stat().st_size == 1024


def test_a_compressed_response_reports_an_unknown_total(clock: FakeClock, tmp_path: Path) -> None:
    """`Content-Length` opisuje bajty spakowane, a `iter_bytes` oddaje rozpakowane.

    Gdyby brama kiedykolwiek włączyła `Content-Encoding: gzip`, licznik przebiłby sumę i pasek
    poszedłby powyżej 100 %. Nagłówek deklarujemy tu wprost, bo to ustawienie serwera, a nie
    coś, co narzędzie kontroluje — i lepiej powiedzieć „nie wiem" niż pokazać złą liczbę.
    """
    size = 600 * 1024
    body = b"PK\x03\x04" + b"x" * (size - 4)
    response = httpx.Response(
        200,
        content=body,
        headers={"Content-Type": "application/octet-stream", "Content-Encoding": "identity"},
    )
    response.headers["Content-Encoding"] = "gzip"
    seen, _ = download_with_progress(clock, tmp_path, response)

    assert seen and all(total is None for _, total in seen)
