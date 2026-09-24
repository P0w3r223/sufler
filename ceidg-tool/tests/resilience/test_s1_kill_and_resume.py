"""Scenariusz 1 (uzupelnienie-01.md §D): `kill -9` w połowie strony, potem `wznow`.

Zaliczenie wg §D: po wznowieniu liczba rekordów równa się `count`, bez duplikatów po `id`.

Uwaga o liczbach: fixtures powtarzają jeden `id` między stronami, więc API serwuje 15 wierszy,
które w bazie schodzą się do 14 rekordów. Sprawdzamy jedno i drugie: `count_api` ma pozostać
tym, co podało API, a zbiór rekordów ma być pełny i bez powtórzeń. Gdyby test porównywał
tylko z `count`, byłby fałszywie czerwony na danych, które rejestr naprawdę zwraca.

Czego ten test **nie** zastępuje. §E osobno wymaga wykonania scenariusza ręcznie i wpisania
daty oraz wyniku do `docs/resilience-report.md`. Test jest potwierdzeniem, nie odhaczeniem:
modeluje to, co ubity proces zostawia na dysku (zatwierdzone transakcje stron, run w stanie
`w_toku`, trzymaną blokadę, żaden `finally` niewykonany), a nie samo zabijanie procesu.
Spójność pliku po nagłym zamknięciu pilnuje SQLite w trybie WAL i `PRAGMA integrity_check`
przy otwarciu bazy (`tests/test_uzupelnienie.py`).

Bez tego testu przeoczyliśmy defekt, który blokował całą tę ścieżkę: komunikat blokady
odsyłał do flagi `--force`, której żadne polecenie nie miało, więc udokumentowane
`ceidg-tool wznow` po awarii odbijało się o blokadę martwego procesu przez 10 minut.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.errors import StoreLockedError
from ceidg_tool.pipeline import Deps, build_deps, run_fetch
from ceidg_tool.progress import NullEvents
from ceidg_tool.records import RawRecord
from ceidg_tool.store import Store
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria
from tests.test_pipeline_e2e import UNIQUE_IDS, paged_api, settings_for

KNOWN_COUNT = 15
PAGES_BEFORE_KILL = 2
RECORDS_BEFORE_KILL = 10


class ProcessKilled(BaseException):
    """Zabicie procesu to nie wyjątek, który program obsługuje.

    Dlatego `BaseException` spoza taksonomii `CeidgError`: `run_fetch` go nie łapie, więc
    nie oznaczy runu jako `przerwany` — a właśnie brak tego oznaczenia odróżnia awarię
    od czystego zakończenia i jest tym, co musi umieć rozpoznać wznowienie.
    """


def deps_for(tmp_path: Path, api: FakeApi, clock: FakeClock) -> Deps:
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    return deps


def kill_after_two_pages(api: FakeApi) -> None:
    """Trzecie żądanie nie wraca — proces przestaje istnieć w połowie pobierania."""
    real = api.fallback
    assert real is not None
    calls = {"n": 0}

    def killed(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > PAGES_BEFORE_KILL:
            raise ProcessKilled("kill -9")
        return real(request)

    api.fallback = killed


def test_a_killed_run_resumes_to_a_complete_set_of_records(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = paged_api()
    kill_after_two_pages(api)
    query = criteria(wojewodztwo="podlaskie")
    # Zabity proces nie wykonuje żadnego `finally`, więc blokada zostaje po nim na dysku.
    monkeypatch.setattr(Store, "release_lock", lambda self: None)
    killed = deps_for(tmp_path, api, clock)

    with pytest.raises(ProcessKilled):
        run_fetch(query, killed, known_count=KNOWN_COUNT)

    run_id = killed.store.list_runs()[0].run_id
    assert killed.store.get_run(run_id).status == "w_toku"  # nie „przerwany”: nikt nie sprzątał
    assert killed.store.count_run_records(run_id) == RECORDS_BEFORE_KILL
    killed.store.close()

    # Nowy proces na tej samej bazie: dokładnie to, co robi operator po awarii.
    monkeypatch.undo()
    api.fallback = paged_api().fallback
    fresh = deps_for(tmp_path, api, FakeClock())
    requests_before = len(api.requests)

    with pytest.raises(StoreLockedError) as blocked:
        run_fetch(query, fresh, resume_run_id=run_id)
    assert "--force" in str(blocked.value)
    assert "Blokada wygasa o" in str(blocked.value)

    result = run_fetch(query, fresh, resume_run_id=run_id, force_lock=True)

    assert result.status == "zakonczony"
    ids = [record.id for record in fresh.store.iter_run_records(run_id)]
    assert fresh.store.get_run(run_id).count_api == KNOWN_COUNT  # ścieżka `count` nietknięta
    assert len(ids) == UNIQUE_IDS  # kryterium §D: nic nie zginęło z tego, co API podało
    assert len(set(ids)) == len(ids)  # kryterium §D: bez duplikatów po `id`
    new_requests = api.requests[requests_before:]
    assert len(new_requests) == 1 and "page=2" in new_requests[0]  # tylko brakująca strona
    fresh.store.close()


def test_the_lock_expires_on_its_own_so_waiting_is_a_real_option(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Przejęcie blokady flagą to wyjście awaryjne, nie jedyne — po `lock_stale_s` mija sama.

    Gdyby było odwrotnie, `--force` stałoby się nawykiem i dwa procesy pisałyby do jednego
    runu. Dlatego komunikat podaje godzinę wygaśnięcia: operator ma wybór, a nie ślepy zaułek.
    """
    api = paged_api()
    kill_after_two_pages(api)
    monkeypatch.setattr(Store, "release_lock", lambda self: None)
    killed = deps_for(tmp_path, api, clock)
    with pytest.raises(ProcessKilled):
        run_fetch(criteria(wojewodztwo="podlaskie"), killed, known_count=KNOWN_COUNT)
    run_id = killed.store.list_runs()[0].run_id
    killed.store.close()

    monkeypatch.undo()
    api.fallback = paged_api().fallback
    late = FakeClock()
    late.advance(3600)  # godzina później: heartbeat martwego procesu jest dawno nieświeży
    fresh = deps_for(tmp_path, api, late)

    result = run_fetch(criteria(wojewodztwo="podlaskie"), fresh, resume_run_id=run_id)

    assert result.status == "zakonczony"
    assert result.records == UNIQUE_IDS
    fresh.store.close()


def test_records_saved_before_the_kill_are_not_fetched_again(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checkpoint i rekordy strony idą jedną transakcją, więc po awarii są albo oba, albo żadne."""
    api = paged_api()
    kill_after_two_pages(api)
    monkeypatch.setattr(Store, "release_lock", lambda self: None)
    killed = deps_for(tmp_path, api, clock)
    with pytest.raises(ProcessKilled):
        run_fetch(criteria(wojewodztwo="podlaskie"), killed, known_count=KNOWN_COUNT)
    run_id = killed.store.list_runs()[0].run_id

    # `page_index` w checkpoincie to strona, od której ma ruszyć wznowienie — czyli
    # pierwsza niezapisana, a nie ostatnia zapisana.
    checkpoint = killed.store.get_checkpoint(run_id)
    saved: list[RawRecord] = list(killed.store.iter_run_records(run_id))

    assert checkpoint is not None and checkpoint.page_index == PAGES_BEFORE_KILL
    assert len(saved) == RECORDS_BEFORE_KILL
    assert len({record.id for record in saved}) == RECORDS_BEFORE_KILL
    killed.store.close()


def test_forcing_a_live_lock_tells_the_operator_what_they_just_did(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` na blokadzie martwej jest ratunkiem, na żywej — kolizją, i ma to powiedzieć.

    Rekordy się nie zdublują (klucz główny), ale oba procesy piszą checkpoint i oba oznaczą
    run jako zakończony, więc eksport z tego okna bywa niepełny. Ostrzeżenie jest jedyną
    rzeczą, po której operator pozna różnicę między „przejąłem po trupie" a „wszedłem
    komuś w drogę" — flaga wygląda tak samo w obu przypadkach.
    """
    api = paged_api()
    kill_after_two_pages(api)
    monkeypatch.setattr(Store, "release_lock", lambda self: None)
    killed = deps_for(tmp_path, api, clock)
    with pytest.raises(ProcessKilled):
        run_fetch(criteria(wojewodztwo="podlaskie"), killed, known_count=KNOWN_COUNT)
    run_id = killed.store.list_runs()[0].run_id
    killed.store.close()

    monkeypatch.undo()
    api.fallback = paged_api().fallback
    messages: list[str] = []
    fresh = deps_for(tmp_path, api, FakeClock())  # zegar od nowa: heartbeat jest świeży
    fresh.events = _Recorder(messages)

    run_fetch(criteria(wojewodztwo="podlaskie"), fresh, resume_run_id=run_id, force_lock=True)

    assert any("--force przejęła blokadę" in message for message in messages)
    fresh.store.close()


class _Recorder(NullEvents):
    """Zdarzenia zapisane zamiast wypisanych — interesuje nas tylko `on_message`."""

    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def on_message(self, text: str) -> None:
        self._sink.append(text)
