"""Scenariusz 5 (UZUPELNIENIE_01 §D): ucięty JSON, HTML zamiast JSON, 204 bez treści, 500.

Zaliczenie: czytelny komunikat, checkpoint nietknięty, brak wyjątku nieobsłużonego.
Testy `test_client.py` sprawdzają samą reakcję klienta; tutaj liczy się to, co zostaje
w bazie po awarii — czy wznowienie ruszy z ostatniej dobrej strony, a nie od zera.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ceidg_tool.errors import CeidgError, ResumableError, ServerError
from ceidg_tool.pipeline import build_deps, find_resumable, run_fetch
from tests.conftest import FakeClock
from tests.support import criteria
from tests.test_pipeline_e2e import paged_api, settings_for

PAGE_SIZE = 5
TRUNCATED_JSON = b'{"firmy": [{"id": "9D2531B1-6DED-4538-95EA-22FF2C7D2E01"'
HTML_ERROR_PAGE = b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


def deps_with_broken_second_page(tmp_path: Path, clock: FakeClock, broken: httpx.Response):  # type: ignore[no-untyped-def]
    """Pierwsza strona zdrowa, każda następna zepsuta — awaria w środku stronicowania."""
    api = paged_api()
    healthy = api.fallback
    assert healthy is not None
    pages = {"n": 0}

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url and "limit=1" not in url:
            pages["n"] += 1
            if pages["n"] >= 2:
                return broken
        return healthy(request)

    api.fallback = fallback
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": PAGE_SIZE})
    assert deps.client is not None
    deps.client._profile = deps.profile
    return deps, api


@pytest.mark.parametrize(
    ("label", "response"),
    [
        (
            "ucięty JSON",
            httpx.Response(
                200, content=TRUNCATED_JSON, headers={"Content-Type": "application/json"}
            ),
        ),
        (
            "HTML zamiast JSON",
            httpx.Response(200, content=HTML_ERROR_PAGE, headers={"Content-Type": "text/html"}),
        ),
        ("500 bez treści", httpx.Response(500)),
        ("503 z HTML", httpx.Response(503, content=HTML_ERROR_PAGE)),
    ],
)
def test_broken_response_keeps_checkpoint_on_the_last_good_page(
    tmp_path: Path, clock: FakeClock, label: str, response: httpx.Response
) -> None:
    deps, _api = deps_with_broken_second_page(tmp_path, clock, response)
    query = criteria(wojewodztwo="podlaskie")

    with pytest.raises(ServerError) as caught:
        run_fetch(query, deps, known_count=15)

    # komunikat dla użytkownika: po polsku, z podpowiedzią co dalej, bez śladów wyjątku
    message = str(caught.value)
    assert message and message[0].isupper(), label
    assert "wznów" in message.lower() or "spróbuj" in message.lower(), (label, message)
    assert isinstance(caught.value, ResumableError)  # wznawialny, kod wyjścia 2
    assert caught.value.exit_code == 2

    interrupted = find_resumable(query, deps)
    assert interrupted is not None and interrupted.status == "przerwany", label
    checkpoint = deps.store.get_checkpoint(interrupted.run_id)
    assert checkpoint is not None
    assert checkpoint.stage == "lista"
    assert checkpoint.page_index == 1  # dokładnie jedna zapisana strona
    assert checkpoint.cursor is not None  # jest z czego wznowić
    assert deps.store.count_run_records(interrupted.run_id) == PAGE_SIZE
    deps.store.close()


def test_broken_response_lets_a_later_resume_finish_the_run(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Awaria niczego nie psuje: po powrocie API wznowienie dobiera tylko brakujące strony."""
    deps, api = deps_with_broken_second_page(
        tmp_path, clock, httpx.Response(200, content=HTML_ERROR_PAGE)
    )
    query = criteria(wojewodztwo="podlaskie")
    with pytest.raises(ServerError):
        run_fetch(query, deps, known_count=15)

    interrupted = find_resumable(query, deps)
    assert interrupted is not None
    api.fallback = paged_api().fallback  # API wraca do zdrowia
    before = len(api.requests)
    result = run_fetch(query, deps, resume_run_id=interrupted.run_id)

    assert result.status == "zakonczony"
    assert deps.store.count_run_records(result.run_id) > PAGE_SIZE
    assert len(api.requests) - before <= 2  # dobrane strony 1 i 2, nie całość od nowa
    deps.store.close()


def test_204_without_body_ends_the_run_cleanly(tmp_path: Path, clock: FakeClock) -> None:
    """204 to zero trafień, a nie błąd — run kończy się poprawnie, z pustą listą."""
    from tests.support import FakeApi, criteria

    api = FakeApi()
    api.fallback = lambda request: httpx.Response(204)
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())

    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=0)

    assert result.status == "zakonczony"
    assert result.records == 0
    checkpoint = deps.store.get_checkpoint(result.run_id)
    assert checkpoint is not None and checkpoint.stage == "gotowe"
    deps.store.close()


MALFORMED_BODIES = [
    ("tekst zamiast JSON", httpx.Response(200, content=b"nie-json")),
    ("obiekt zamiast listy", httpx.Response(200, content=b'{"firmy": {"id": "A"}}')),
    ("null jako treść", httpx.Response(200, content=b"null")),
    ("nieznany status HTTP", httpx.Response(418, json={"code": "X", "message": "czajnik"})),
    ("500 z niepoprawnym UTF-8", httpx.Response(500, content=b"\xff\xfe awaria")),
    ("puste 200", httpx.Response(200, content=b"")),
]


@pytest.mark.parametrize(("label", "response"), MALFORMED_BODIES)
def test_malformed_body_ends_as_tool_error_or_clean_run_never_as_crash(
    tmp_path: Path, clock: FakeClock, label: str, response: httpx.Response
) -> None:
    """Brak wyjątku nieobsłużonego: każda odpowiedź kończy się `CeidgError` albo zamkniętym runem.

    Wyjątek spoza taksonomii narzędzia (np. `KeyError`, `TypeError`) wypadnie z `run_fetch`
    i przewróci ten test — o to właśnie chodzi w kryterium „brak wyjątku nieobsłużonego”.
    """
    from tests.support import FakeApi

    api = FakeApi()
    api.fallback = lambda request: response
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    try:
        try:
            result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=1)
        except CeidgError as exc:
            assert str(exc).strip(), label  # komunikat nigdy nie jest pusty
            runs = deps.store.list_runs()
            assert runs and runs[0].status in ("przerwany", "blad"), label
        else:
            # treść dała się zinterpretować (np. pojedynczy obiekt pod kluczem głównym)
            assert result.status == "zakonczony", label
            assert result.records == deps.store.count_run_records(result.run_id), label
            assert all(r.id for r in deps.store.iter_run_records(result.run_id)), label
    finally:
        deps.store.close()
