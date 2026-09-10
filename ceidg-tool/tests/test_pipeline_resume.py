"""Wznowienie i limity w trakcie pobierania — potok + baza + klient, offline na fixtures.

Uzupełnia `test_pipeline_e2e.py` o sytuacje, które ujawniają się dopiero na produkcji:
przerwanie w środku porcji szczegółów, wznowienie po zamkniętym etapie listy
(UZUPELNIENIE_01 §D scenariusz 1) oraz 429 w trakcie stronicowania (§C).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.errors import TransportError
from ceidg_tool.pipeline import build_deps, find_resumable, run_fetch
from ceidg_tool.ratelimit import RateLimiter
from ceidg_tool.store import Store
from tests.conftest import FakeClock
from tests.support import criteria
from tests.test_pipeline_e2e import UNIQUE_IDS, paged_api, settings_for

DETAIL_BATCH = 5
COOLDOWN_S = 185.0


def deps_for(tmp_path: Path, clock: FakeClock, api, *, details: bool = False):  # type: ignore[no-untyped-def]
    """Zależności z profilem dopasowanym do fixtures (5 rekordów na stronę)."""
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    update = {"max_limit_firmy": 5}
    if details:
        update["ids_batch_size"] = DETAIL_BATCH
    deps.profile = deps.profile.model_copy(update=update)
    assert deps.client is not None
    deps.client._profile = deps.profile
    return deps


def _is_detail(url: str) -> bool:
    return "/firma?" in url or "/firma/" in url


# --- przerwanie w środku porcji szczegółów --------------------------------------------


def test_interrupted_detail_batch_resumes_without_refetching_list_or_done_details(
    tmp_path: Path, clock: FakeClock
) -> None:
    api = paged_api()
    healthy = api.fallback
    assert healthy is not None
    detail_calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        if _is_detail(str(request.url)):
            detail_calls["n"] += 1
            if detail_calls["n"] >= 2:  # pierwsza porcja przechodzi, potem sieć znika
                raise httpx.ReadTimeout("timeout", request=request)
        return healthy(request)

    api.fallback = flaky
    deps = deps_for(tmp_path, clock, api, details=True)
    query = criteria(wojewodztwo="podlaskie", szczegoly=True)

    with pytest.raises(TransportError):
        run_fetch(query, deps, known_count=15)

    interrupted = find_resumable(query, deps)
    assert interrupted is not None and interrupted.status == "przerwany"
    # lista jest kompletna, etap przełączony, zapisana dokładnie jedna porcja szczegółów
    checkpoint = deps.store.get_checkpoint(interrupted.run_id)
    assert checkpoint is not None and checkpoint.stage == "szczegoly"
    assert deps.store.count_run_records(interrupted.run_id) == UNIQUE_IDS
    assert deps.store.count_run_details(interrupted.run_id) == DETAIL_BATCH
    pending = deps.store.pending_detail_ids(interrupted.run_id, ttl_days=7)
    assert len(pending) == UNIQUE_IDS - DETAIL_BATCH

    api.fallback = healthy
    before = len(api.requests)
    result = run_fetch(query, deps, resume_run_id=interrupted.run_id)
    resumed = api.requests[before:]

    assert result.status == "zakonczony"
    assert result.records == UNIQUE_IDS and result.details == UNIQUE_IDS
    assert not any("/firmy" in url for url in resumed)  # etap listy nie jest powtarzany
    assert all(_is_detail(url) for url in resumed)
    assert len(resumed) == 2  # 9 brakujących w porcjach po 5
    assert find_resumable(query, deps) is None
    deps.store.close()


def test_resume_after_last_page_but_before_stage_switch_makes_no_list_requests(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`kill -9` w oknie między zapisem ostatniej strony a przełączeniem etapu (§D scen. 1).

    Checkpoint zostaje na etapie `lista` z pustym kursorem — wznowienie musi to rozpoznać
    jako listę zakończoną, a nie pobierać jej od nowa.
    """
    api = paged_api()
    deps = deps_for(tmp_path, clock, api, details=True)
    query = criteria(wojewodztwo="podlaskie", szczegoly=True)
    store = deps.store

    run_id = store.start_run(
        run_id="run-po-liscie",
        criteria_json=query.canonical_json(),
        criteria_hash=query.fingerprint(),
        profile_hash=deps.profile.profile_hash(),
        mode="szczegoly",
        tool_version="0.1.0",
        cursor_mode="links",
    )
    records = [r for i in range(3) for r in _page_records(i)]
    store.save_page(run_id, page_index=0, records=records, next_cursor=None)
    store.update_run_status(run_id, "przerwany", error="kill -9")
    checkpoint = store.get_checkpoint(run_id)
    assert checkpoint is not None and checkpoint.stage == "lista" and checkpoint.cursor is None

    result = run_fetch(query, deps, resume_run_id=run_id)

    assert not any("/firmy" in url for url in api.requests)
    assert result.status == "zakonczony"
    assert result.records == UNIQUE_IDS and result.details == UNIQUE_IDS
    ids = [r.id for r in store.iter_run_records(run_id)]
    assert len(ids) == len(set(ids))  # brak duplikatów po `id`
    store.close()


def _page_records(index: int) -> list[dict]:  # type: ignore[type-arg]
    from tests.support import load_fixture

    return list(load_fixture(f"firmy_page{index}_limit5.json")["body"]["firmy"])


# --- 429 w trakcie stronicowania -------------------------------------------------------


def test_429_between_pages_keeps_saved_page_and_waits_full_cooldown(
    tmp_path: Path, clock: FakeClock
) -> None:
    api = paged_api()
    healthy = api.fallback
    assert healthy is not None
    pages = {"n": 0}

    def with_429(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url and "limit=1" not in url:
            pages["n"] += 1
            if pages["n"] == 2:  # 429 dokładnie po zapisaniu pierwszej strony
                return httpx.Response(429)
        return healthy(request)

    api.fallback = with_429
    deps = deps_for(tmp_path, clock, api)
    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=15)

    assert result.status == "zakonczony"
    assert result.records == UNIQUE_IDS and result.pages == 3
    assert clock.sleeps and max(clock.sleeps) == pytest.approx(COOLDOWN_S)

    # 429 trafia do trwałej historii żądań — zasila limiter po restarcie (§C)
    stamps = deps.store.history(deps.settings.token_fp).recent(0)
    assert [s.status for s in stamps].count(429) == 1
    deps.store.close()


def test_cooldown_from_sqlite_history_binds_a_fresh_limiter_after_restart(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Blokada po 429 przeżywa restart procesu, bo historia żądań jest w bazie."""
    path = tmp_path / "store-test.sqlite"
    with Store(path, environment="test", clock=clock) as first:
        history = first.history("fp")
        history.record(clock.wall(), "firmy")
        history.mark(clock.wall(), 429)

    clock.advance(100.0)  # awaria i ponowny start 100 s po odrzuceniu
    with Store(path, environment="test", clock=clock) as restarted:
        limiter = RateLimiter(
            windows=((48, 180.0),),
            min_spacing_s=0.0,
            cooldown_s=COOLDOWN_S,
            clock=clock,
            history=restarted.history("fp"),
        )
        limiter.acquire("firmy")

    assert clock.sleeps == [pytest.approx(COOLDOWN_S - 100.0)]
