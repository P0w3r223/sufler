"""Postęp w trybie `aktualizuj` — czy operator widzi, że program pracuje.

Znalezione przy przechodzeniu bramki 3 na prawdziwym terminalu: pasek skakał na 500/2891
i milkł. Nie był to zawieszony program — po każdej stronie 500 identyfikatorów szła setka
żądań o szczegóły po 3,75 s każde, czyli ponad sześć minut ciszy, a postęp raportował się
raz na stronę. „Wygląda jak zawieszony" jest defektem samo w sobie: operator zabija proces,
który pracuje poprawnie.

Testy pilnują dwóch rzeczy, których zwykłe „czy się nie wywali" nie łapie: jak długo wolno
milczeć i czy liczby na pasku są prawdziwe.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ceidg_tool.config import Settings
from ceidg_tool.pipeline import Deps, build_deps, run_update
from tests.conftest import FakeClock, detail_record
from tests.support import FakeApi, registry_id

IDS_PER_PAGE = 20
BATCH = 5


class Timeline:
    """Zapisuje przeplot żądań o szczegóły i raportów postępu — w kolejności zdarzeń."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, int]] = []

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        if endpoint == "firma":
            self.events.append(("zadanie", 0, 0))

    def on_details(self, done: int, total: int) -> None:
        self.events.append(("postep", done, total))

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        return None

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self.events.append(("strona", records, total or 0))

    def on_export(self, done: int, total: int) -> None:
        self.events.append(("eksport", done, total))

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        self.events.append(("pobieranie", done_bytes, total_bytes or 0))

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self.events.append(("model", tokens, 0))

    def on_message(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def longest_silence(self) -> int:
        """Najdłuższa seria żądań bez ani jednego raportu postępu."""
        best = run = 0
        for kind, _, _ in self.events:
            run = run + 1 if kind == "zadanie" else 0
            best = max(best, run)
        return best

    @property
    def progress(self) -> list[tuple[int, int]]:
        return [(done, total) for kind, done, total in self.events if kind == "postep"]


def make_deps(
    tmp_path: Path,
    clock: FakeClock,
    timeline: Timeline,
    *,
    pages: int = 1,
    last_page: int = IDS_PER_PAGE,
) -> Deps:
    """Klient serwujący `pages` stron zmian; ostatnia może być krótsza — tak wygląda produkcja
    (2891 zmian to pięć stron po 500 i szósta po 391)."""
    served = {"n": 0}
    total_ids = (pages - 1) * IDS_PER_PAGE + last_page

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zmiana" in url:
            served["n"] += 1
            index = served["n"]
            start = (index - 1) * IDS_PER_PAGE
            size = last_page if index >= pages else IDS_PER_PAGE
            ids = [f"id-{i:04d}" for i in range(start, start + size)]
            last = index >= pages
            self_url = f"https://test-dane.biznes.gov.pl/api/ceidg/v3/zmiana?page={index}"
            nxt = self_url if last else f"{self_url}&dalej"
            return httpx.Response(
                200,
                json={
                    "identyfikatoryWpisow": [{"id": i} for i in ids],
                    "count": total_ids,
                    "links": {"self": self_url, "next": nxt},
                },
            )
        if "/firma" in url:
            wanted = request.url.params.get_list("ids")
            return httpx.Response(
                200,
                json={"firma": [{**detail_record(1), "id": registry_id(rid)} for rid in wanted]},
            )
        return httpx.Response(404)

    api = FakeApi()
    api.fallback = fallback
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client(), events=timeline)
    # `max_limit_zmiana` musi zgadzać się z rozmiarem serwowanej strony, inaczej klient
    # słusznie uzna, że stron jest więcej, niż wynika z `count`, i przerwie paginację.
    deps.profile = deps.profile.model_copy(
        update={"ids_batch_size": BATCH, "max_limit_zmiana": IDS_PER_PAGE}
    )
    assert deps.client is not None
    deps.client._profile = deps.profile
    return deps


def run_one_window(deps: Deps) -> None:
    since = datetime(2026, 9, 1, tzinfo=UTC)
    run_update(deps, since=since, until=datetime(2026, 9, 3, tzinfo=UTC))


def test_progress_is_reported_between_detail_batches_not_between_pages(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Sedno defektu: cisza mierzona w żądaniach, nie w stronach.

    Przy stronie 500 identyfikatorów i porcji 5 jedna strona to sto żądań — czyli ponad
    sześć minut bez znaku życia, jeśli raport pada raz na stronę."""
    timeline = Timeline()
    deps = make_deps(tmp_path, clock, timeline)
    run_one_window(deps)
    deps.store.close()

    assert timeline.longest_silence <= 1, (
        f"{timeline.longest_silence} żądań pod rząd bez raportu postępu — "
        f"przy stronie 500 identyfikatorów to minuty ciszy"
    )
    assert len(timeline.progress) >= IDS_PER_PAGE // BATCH


def test_the_numbers_on_the_bar_are_true(tmp_path: Path, clock: FakeClock) -> None:
    """Postęp ma rosnąć i dojść do sumy, a nie skakać.

    Poprzednia arytmetyka mnożyła numer strony przez rozmiar **bieżącej** strony, więc
    przy ostatniej, krótszej stronie licznik cofał się albo nie dobijał do końca."""
    timeline = Timeline()
    deps = make_deps(tmp_path, clock, timeline, pages=3, last_page=7)
    run_one_window(deps)
    deps.store.close()

    dones = [done for done, _ in timeline.progress]
    totals = {total for _, total in timeline.progress}
    assert dones == sorted(dones), f"licznik postępu cofa się: {dones}"
    assert len(totals) == 1, f"suma na pasku zmienia się w trakcie: {totals}"
    assert dones[-1] == totals.pop() == 2 * IDS_PER_PAGE + 7


def test_the_id_listing_phase_is_visible_too(tmp_path: Path, clock: FakeClock) -> None:
    """Zbieranie identyfikatorów zmian to też praca — bez tego pierwsze strony są ciszą."""
    timeline = Timeline()
    deps = make_deps(tmp_path, clock, timeline, pages=2)
    run_one_window(deps)
    deps.store.close()

    strony = [(done, total) for kind, done, total in timeline.events if kind == "strona"]
    assert len(strony) == 2
    assert all(total == 2 * IDS_PER_PAGE for _, total in strony)


def test_the_lock_heartbeat_keeps_up_with_the_detail_batches(
    tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blokada wygasa po 10 minutach, a strona 500 identyfikatorów trwa około 6,25 min.

    Bicie serca raz na stronę mieści się w progu tylko dopóki nic nie idzie źle: jedno 429
    to +185 s, drabinka ponowień po zerwaniu sieci to +400 s. Wtedy blokada gaśnie **pod
    pracującym procesem** i drugi proces może ruszyć na tym samym tokenie. `_fetch_details`
    w zwykłym pobieraniu bije po każdej porcji od początku — tu było inaczej."""
    timeline = Timeline()
    deps = make_deps(tmp_path, clock, timeline)
    bicia: list[int] = []
    prawdziwy = deps.store.touch_lock

    def spy() -> bool:
        bicia.append(len(timeline.events))
        # Wynik wraca: `touch_lock` melduje `False`, gdy blokadę przejął inny proces,
        # a `LockHeartbeat` na tym `False` przerywa run. Atrapa gubiąca tę wartość
        # zamieniałaby podgląd bicia serca w utratę blokady.
        return prawdziwy()

    monkeypatch.setattr(deps.store, "touch_lock", spy)
    run_one_window(deps)
    deps.store.close()

    zadania = [i for i, (kind, _, _) in enumerate(timeline.events) if kind == "zadanie"]
    # między dwoma kolejnymi żądaniami o szczegóły musi paść bicie serca
    for wczesniej, pozniej in zip(zadania, zadania[1:], strict=False):
        assert any(wczesniej < b <= pozniej + 1 for b in bicia), (
            f"między żądaniami {wczesniej} i {pozniej} blokada nie dostała bicia serca"
        )
