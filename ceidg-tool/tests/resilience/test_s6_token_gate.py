"""Scenariusz 6 (uzupelnienie-01.md §D): token wygasły, pusty, z błędnym środowiskiem.

Zaliczenie: zatrzymanie przed pierwszym żądaniem o dane i komunikat co zrobić.
Kluczowa jest tu nie sama treść wyjątku (to sprawdza `test_config.py`), tylko licznik
żądań: przy złym tokenie do API nie może pójść ani jedno zapytanie o dane.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ceidg_tool.config import TOKEN_SERVICE_URL, Settings, load_settings
from ceidg_tool.errors import AuthError, ConfigError
from ceidg_tool.pipeline import build_deps, count_hits, run_fetch
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def make_jwt(**claims: object) -> str:
    def seg(obj: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(claims)}.{'x' * 43}"


def watching_api() -> FakeApi:
    """API, które policzy każde żądanie i o każdym krzyknie — nic nie powinno tu dotrzeć."""
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"żądanie mimo złego tokenu: {request.url}")

    api.fallback = fallback
    return api


@pytest.mark.parametrize(
    ("label", "environ"),
    [
        (
            "token wygasły",
            {"CEIDG_TOKEN": make_jwt(exp=int((NOW - timedelta(days=1)).timestamp()))},
        ),
        ("token pusty", {"CEIDG_TOKEN": ""}),
        ("token z samych spacji", {"CEIDG_TOKEN": "   "}),
        ("brak zmiennej", {}),
    ],
)
def test_bad_token_stops_before_settings_are_even_built(
    label: str, environ: dict[str, str], tmp_path: Path
) -> None:
    with pytest.raises(ConfigError) as caught:
        load_settings(env_file=None, environ=environ, use_keyring=False, data_dir=tmp_path, now=NOW)

    message = str(caught.value)
    assert TOKEN_SERVICE_URL in message, label  # komunikat mówi, skąd wziąć nowy token
    assert caught.value.exit_code == 3, label
    assert not any(tmp_path.iterdir()), label  # żaden plik nie powstał


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("wygasły", make_jwt(exp=int((NOW - timedelta(days=1)).timestamp()))),
        ("pusty", ""),
    ],
)
def test_full_session_with_a_bad_token_sends_nothing(
    label: str, token: str, tmp_path: Path, clock: FakeClock
) -> None:
    """Cała sekwencja jak w CLI: ustawienia → zależności → `count`. Bramka jest w kroku 1.

    `watching_api` wywraca test, gdyby kiedykolwiek doszło do żądania — to jest właściwa
    treść kryterium „zatrzymanie przed pierwszym żądaniem o dane”.
    """
    api = watching_api()

    with pytest.raises(ConfigError) as caught:
        settings = load_settings(
            env_file=None,
            environ={"CEIDG_TOKEN": token},
            use_keyring=False,
            data_dir=tmp_path / "dane",
            now=NOW,
        )
        deps = build_deps(settings, clock=clock, http=api.client())  # nieosiągalne
        count_hits(criteria(wojewodztwo="podlaskie"), deps)

    assert caught.value.exit_code == 3, label
    assert api.requests == [], label
    assert not (tmp_path / "dane").exists(), label  # baza nie powstała


def test_token_valid_but_rejected_by_the_environment_fails_on_the_first_request(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Token produkcyjny użyty wobec środowiska testowego: 401, bez ponawiania, z podpowiedzią."""
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(401, json={"code": "UNAUTHORIZED"})
    settings = Settings(token=make_jwt(iat=1), environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())

    with pytest.raises(AuthError) as caught:
        count_hits(criteria(wojewodztwo="podlaskie"), deps)

    message = str(caught.value)
    assert "test-dane.biznes.gov.pl" in message  # mówi, o które środowisko chodzi
    assert "wygasł" in message
    assert caught.value.exit_code == 3
    assert len(api.requests) == 1  # 401 nie jest ponawiane
    deps.store.close()


def test_rejected_token_does_not_leave_a_half_finished_run(
    tmp_path: Path, clock: FakeClock
) -> None:
    """403 w trakcie pobierania zamyka run jako `blad`, nie zostawia go w stanie `w_toku`."""
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(403)
    settings = Settings(token=make_jwt(iat=1), environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())

    with pytest.raises(AuthError):
        run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=5)

    runs = deps.store.list_runs()
    assert runs and runs[0].status == "blad"
    assert runs[0].error and "Token odrzucony" in runs[0].error
    assert deps.store.count_run_records(runs[0].run_id) == 0
    deps.store.close()


def test_token_never_appears_in_the_error_raised_to_the_user(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Komunikat o odrzuconym tokenie nie może zawierać samego tokenu (§B)."""
    token = make_jwt(iat=1, pesel="00000000000")
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(401)
    settings = Settings(token=token, environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())

    with pytest.raises(AuthError) as caught:
        count_hits(criteria(wojewodztwo="podlaskie"), deps)

    assert token not in str(caught.value)
    assert token not in repr(caught.value)
    deps.store.close()
