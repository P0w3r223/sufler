"""Scenariusz 2 (uzupelnienie-01.md §D): sieć znika na 2 minuty w trakcie pobierania.

Zaliczenie wg §D: kontynuacja bez interwencji i bez utraty danych. „Bez utraty" znaczy tu:
zbiór rekordów jest pełny względem tego, co API podało, i bez duplikatów po `id`. Fixtures
powtarzają jeden `id` między stronami, więc 15 wierszy z API to 14 rekordów w bazie.

Czego ten test **nie** zastępuje: §E chce tego samego z prawdziwym odcięciem karty sieciowej,
z datą i wynikiem w `docs/resilience-report.md`. Tu odcinamy transport, nie kabel — reakcja
systemu operacyjnego na zerwane gniazdo (inny wyjątek niż `httpx.ConnectError`, DNS
odpowiadający z cache) zostaje po stronie przebiegu ręcznego.

Przerwa trwa dwie minuty zegara, a nie ustaloną z góry liczbę nieudanych prób — to sieć
decyduje, kiedy wraca, nie klient. Wychodzi z tego cena, której nie widać w samej liście
odstępów: ponowienia 10 → 30 → 60 s kończą się około 111 sekundy (odstępy limitera też liczą
się do czasu), a więc jeszcze w trakcie przerwy, i czwarta próba przychodzi dopiero po 300 s.
Dwuminutowa awaria kosztuje całą drabinkę i około siedmiu minut czekania — mieści się
w 30-minutowym budżecie §C, ale nie „przeczekuje się sama". Zegar jest atrapą, więc test
trwa milisekundy zamiast siedmiu minut.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.client import CONNECTION_MAX_OUTAGE_S, CONNECTION_RETRY_DELAYS_S
from ceidg_tool.console import ConsoleEvents
from ceidg_tool.pipeline import build_deps, run_fetch
from ceidg_tool.ratelimit import REASON_NO_CONNECTION
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria
from tests.test_pipeline_e2e import UNIQUE_IDS, paged_api, settings_for

OUTAGE_S = 120.0
KNOWN_COUNT = 15


class RecordingEvents:
    """Zdarzenia zapamiętane zamiast wypisane — `on_wait` niesie komunikat wymagany w §A."""

    def __init__(self) -> None:
        self.waits: list[tuple[float, str]] = []
        self.pages: list[int] = []

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        return None

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self.waits.append((seconds, reason))

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self.pages.append(page_index)

    def on_details(self, done: int, total: int) -> None:
        return None

    def on_export(self, done: int, total: int) -> None:
        return None

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        return None

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        return None

    def on_message(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None


def outage_during_the_second_page(api: FakeApi, clock: FakeClock, *, seconds: float) -> None:
    """Po pierwszej stronie sieć znika na `seconds` zegara i wraca sama, bez niczyjej ręki."""
    real = api.fallback
    assert real is not None
    state: dict[str, float] = {"served": 0.0, "began": -1.0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if state["served"] >= 2:
            if state["began"] < 0:
                state["began"] = clock.wall()
            if clock.wall() - state["began"] < seconds:
                raise httpx.ConnectError("sieć zniknęła", request=request)
        state["served"] += 1
        return real(request)

    api.fallback = flaky


def test_the_download_rides_out_a_two_minute_outage_without_intervention(
    tmp_path: Path, clock: FakeClock
) -> None:
    api = paged_api()
    outage_during_the_second_page(api, clock, seconds=OUTAGE_S)
    events = RecordingEvents()
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client(), events=events)
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=KNOWN_COUNT)

    assert result.status == "zakonczony"  # nikt nie musiał nic wznawiać
    ids = [record.id for record in deps.store.iter_run_records(result.run_id)]
    assert deps.store.get_run(result.run_id).count_api == KNOWN_COUNT
    assert len(ids) == UNIQUE_IDS and len(set(ids)) == len(ids)  # bez utraty i bez duplikatów
    waited = [seconds for seconds, reason in events.waits if reason == REASON_NO_CONNECTION]
    assert waited == list(CONNECTION_RETRY_DELAYS_S[: len(waited)])  # drabinka po kolei
    assert sum(waited) > OUTAGE_S  # czekaliśmy dłużej niż trwała przerwa
    assert sum(waited) < CONNECTION_MAX_OUTAGE_S  # i wciąż w budżecie §C
    deps.store.close()


def test_the_operator_is_told_the_progress_is_saved(tmp_path: Path, clock: FakeClock) -> None:
    """§A: przy braku sieci ma paść „brak połączenia, czekam, postęp zapisany".

    Zdanie powstaje w `ConsoleEvents`, więc sprawdzamy je tam, gdzie naprawdę powstaje —
    inaczej test pilnowałby własnej atrapy zamiast programu.
    """
    printed: list[str] = []

    class Spy(ConsoleEvents):
        def on_message(self, text: str) -> None:
            printed.append(text)

    events = Spy(quiet=True)

    events.on_wait(30.0, REASON_NO_CONNECTION, clock.wall() + 30.0)

    assert printed and printed[0].startswith("brak połączenia, czekam do ")
    assert "postęp zapisany" in printed[0]


def test_an_outage_longer_than_the_budget_stops_with_a_resume_instruction(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Po 30 minutach bez sieci program kończy pracę — ale z checkpointem i instrukcją.

    Granica jest w §C; bez niej pobranie wisiałoby w nieskończoność na martwej sieci.
    """
    from ceidg_tool.errors import TransportError

    api = paged_api()
    outage_during_the_second_page(api, clock, seconds=CONNECTION_MAX_OUTAGE_S * 10)
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    with pytest.raises(TransportError) as stopped:
        run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=KNOWN_COUNT)

    assert "wznów poleceniem" in str(stopped.value)
    run = deps.store.list_runs()[0]
    assert run.status == "przerwany"
    assert deps.store.get_checkpoint(run.run_id) is not None  # postęp naprawdę zapisany
    deps.store.close()
