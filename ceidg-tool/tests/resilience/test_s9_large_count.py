"""Scenariusz 9 (UZUPELNIENIE_01 §D): zapytanie o ~400 000 firm.

Zaliczenie: program **proponuje zawężenie kryteriów albo podział na partie z szacunkiem
czasu** i **nie zaczyna pobierania sam**. Scenariusz był dotąd sprawdzany ręcznie; tutaj
staje się częścią CI, bo to jedyny bezpiecznik przed 16-godzinnym pobraniem ruszonym przez
przypadek (400 000 trafień to 16 000 zapytań listy po 3,75 s).

Miarą zaliczenia jest licznik żądań: po jednym `count` do API nie może pójść nic więcej,
dopóki operator nie podejmie decyzji.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

import ceidg_tool.cli as cli
import ceidg_tool.config as config
from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.config import Settings
from ceidg_tool.errors import ConfigError
from ceidg_tool.estimating import LARGE_COUNT_THRESHOLD, estimate
from ceidg_tool.pipeline import Deps, build_deps
from ceidg_tool.progress import Events
from ceidg_tool.ui import flow, texts
from ceidg_tool.ui.prompts import DefaultsPrompter, ScriptedPrompter
from tests.conftest import FakeClock, list_record
from tests.support import FakeApi, RecordingView, criteria

HUGE = 400_000
TODAY = date(2026, 9, 5)
EXIT_CONFIG = 3
TOKEN = "token-testowy-nie-jwt"

# Zapytanie bez województwa: raport dziennego nie ma, więc jedyną odpowiedzią jest
# zawężenie albo podział — dokładnie sytuacja ze scenariusza 9.
BIG_QUERY = criteria(status="AKTYWNY")


def huge_api() -> FakeApi:
    """`count` = 400 000; każde inne żądanie to porażka scenariusza."""
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url and request.url.params.get("limit") == "1":
            return httpx.Response(200, json={"count": HUGE, "firmy": []})
        raise AssertionError(f"program zaczął pobieranie mimo {HUGE} trafień: {url}")

    api.fallback = fallback
    return api


def deps_for(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token=TOKEN, environment="test", data_dir=tmp_path / "dane")
    return build_deps(settings, clock=clock, http=api.client())


# ----------------------------------------------------------------- propozycja zamiast pobrania


def test_a_huge_count_proposes_a_split_and_starts_no_fetch(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Rdzeń scenariusza: jedno żądanie o `count`, propozycja podziału, zero pobierania."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()

    decision, plan = flow.prepare_fetch(
        BIG_QUERY, deps, ScriptedPrompter({"podzial": "wyjdz"}), view, today=TODAY
    )

    assert decision == "wyjdz"
    assert len(api.requests) == 1  # tylko `count`
    assert deps.store.list_runs() == []  # żaden run nie powstał
    assert plan.batches is not None and len(plan.batches.batches) > 1
    deps.store.close()


def test_the_proposal_states_the_cost_and_the_two_ways_out(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Operator ma zobaczyć, ile to kosztuje i że ma dwa wyjścia: zawęzić albo podzielić."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(BIG_QUERY, deps, ScriptedPrompter({"podzial": "wyjdz"}), view, today=TODAY)

    shown = view.text()
    assert "400 000" in shown  # ile trafień
    assert "nie zacznie" in shown  # że sam nie ruszy
    assert "Zawęź kryteria albo podziel je na partie" in shown  # obie drogi
    deps.store.close()


def test_every_proposed_batch_carries_its_own_time_estimate(
    tmp_path: Path, clock: FakeClock
) -> None:
    """§C mówi wprost o „podziale na partie z szacunkiem czasu dla każdej”."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(BIG_QUERY, deps, ScriptedPrompter({"podzial": "wyjdz"}), view, today=TODAY)

    split = view.block_titled("Propozycja podziału")
    assert len(split.rows) > 1
    assert all(row[4].strip() for row in split.rows)  # kolumna „czas” wypełniona wszędzie
    assert all(row[1].strip() for row in split.rows)  # i zakres dat
    deps.store.close()


def test_each_proposed_batch_fits_under_the_threshold(tmp_path: Path, clock: FakeClock) -> None:
    """Propozycja, w której partia dalej przekracza próg, nie rozwiązywałaby niczego."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)

    _, plan = flow.prepare_fetch(
        BIG_QUERY, deps, ScriptedPrompter({"podzial": "wyjdz"}), RecordingView(), today=TODAY
    )

    assert plan.batches is not None
    assert plan.batches.share <= LARGE_COUNT_THRESHOLD
    assert not plan.batches.exhausted
    deps.store.close()


def test_choosing_to_correct_the_criteria_also_fetches_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Druga dopuszczalna odpowiedź ze scenariusza: zawęzić kryteria."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)

    decision, _ = flow.prepare_fetch(
        BIG_QUERY, deps, ScriptedPrompter({"podzial": "popraw"}), RecordingView(), today=TODAY
    )

    assert decision == "popraw"
    assert len(api.requests) == 1
    deps.store.close()


def test_the_estimate_matches_the_documented_sixteen_hours() -> None:
    """Liczba, która uzasadnia cały bezpiecznik: 400 000 trafień to ok. 16,7 h samej listy."""
    profile_est = estimate(HUGE, _prod_like_profile())

    assert profile_est.requests_list == 16_000
    assert texts.format_duration(profile_est.seconds_list) == "16.7 h"


def _prod_like_profile() -> ApiProfile:
    return ApiProfile(
        base_url="https://dane.biznes.gov.pl/api/ceidg/v3", max_limit_firmy=25, ids_batch_size=5
    )


# ----------------------------------------------------------------- tryb nieinteraktywny


def test_the_noninteractive_mode_refuses_instead_of_guessing(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`--tak` bez `--maks` i bez `--partie` nie ma bezpiecznej odpowiedzi — musi odmówić."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)

    with pytest.raises(ConfigError) as caught:
        flow.prepare_fetch(BIG_QUERY, deps, DefaultsPrompter(), RecordingView(), today=TODAY)

    assert caught.value.exit_code == EXIT_CONFIG
    assert len(api.requests) == 1
    assert deps.store.list_runs() == []
    deps.store.close()


def test_an_explicit_batch_consent_is_the_way_through(tmp_path: Path, clock: FakeClock) -> None:
    """`--partie` to świadoma zgoda operatora — wtedy plan wraca jako decyzja „partie”."""
    api = huge_api()
    deps = deps_for(tmp_path, clock, api)

    decision, plan = flow.prepare_fetch(
        BIG_QUERY, deps, ScriptedPrompter({"podzial": "partie"}), RecordingView(), today=TODAY
    )

    assert decision == "partie"
    assert plan.batches is not None
    assert len(api.requests) == 1  # sama zgoda nadal niczego nie pobrała
    deps.store.close()


# ----------------------------------------------------------------------------- przez CLI


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.chdir(tmp_path)
    # `*_`: funkcja bierze teraz nazwę pozycji w magazynie (token CEIDG albo klucz asystenta).
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    return CliRunner()


def patch_cli_transport(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    """Podstawia atrapę transportu pod `build_deps` używane przez CLI — zero sieci w CI."""

    def fake(settings: Settings, *, events: Events | None = None, **_: object) -> Deps:
        return build_deps(settings, events=events, http=api.client(), clock=FakeClock())

    monkeypatch.setattr("ceidg_tool.cli.build_deps", fake)


def test_the_cli_in_noninteractive_mode_exits_three_and_fetches_nothing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pełna droga operatora: `pobierz --status AKTYWNY --tak` przy 400 000 trafień."""
    api = huge_api()
    patch_cli_transport(monkeypatch, api)
    env = {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(app_args(), ["pobierz", "--status", "AKTYWNY", "--tak"], env=env)

    assert result.exit_code == EXIT_CONFIG, result.output
    assert len([r for r in api.requests if "limit=1" in r]) == 1
    assert not any("limit=25" in r for r in api.requests)  # nie ruszyło pobieranie
    assert not list((tmp_path / "dane" / "wyniki").glob("*.xlsx"))


def test_the_cli_shows_the_split_proposal_before_refusing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Odmowa bez propozycji zostawiłaby operatora bez drogi dalej."""
    api = huge_api()
    patch_cli_transport(monkeypatch, api)
    env = {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(app_args(), ["pobierz", "--status", "AKTYWNY", "--tak"], env=env)

    assert "400 000" in result.output
    assert "Propozycja podziału" in result.output


def test_the_cli_with_explicit_batch_consent_gets_past_the_gate(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--partie` przechodzi bramkę — od tego momentu pobieranie jest świadomą decyzją."""
    api = batched_api(per_batch_records=1)
    patch_cli_transport(monkeypatch, api)
    env = {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(
        app_args(), ["pobierz", "--status", "AKTYWNY", "--partie", "--tak"], env=env
    )

    assert result.exit_code == 0, result.output
    assert "Partie" in result.output
    assert "Podsumowanie" in result.output
    assert len([r for r in api.requests if "limit=1" in r]) > 1  # policzono każdą partię
    assert list((tmp_path / "dane" / "wyniki").glob("*.xlsx"))


def test_batches_that_all_come_back_empty_end_with_a_message_not_a_crash(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pierwotny `count` bywa nieaktualny; gdy każda partia jest pusta, nie powstaje żaden run.

    Bez jawnej obsługi nazwa pliku sięgała po `run_ids[0]` i całość kończyła się `IndexError`
    zamiast zdaniem dla użytkownika.
    """
    api = batched_api(per_batch_records=0)
    patch_cli_transport(monkeypatch, api)
    env = {"CEIDG_TOKEN": TOKEN, "CEIDG_DATA_DIR": str(tmp_path / "dane")}

    result = runner.invoke(
        app_args(), ["pobierz", "--status", "AKTYWNY", "--partie", "--tak"], env=env
    )

    assert result.exit_code == 0, result.output
    assert "nie ma czego eksportować" in result.output
    assert not list((tmp_path / "dane" / "wyniki").glob("*.xlsx"))


def batched_api(*, per_batch_records: int) -> FakeApi:
    """Pierwszy `count` ogromny, każda partia mała — tyle rekordów, ile zamówi test."""
    api = FakeApi()
    counts: list[int] = []

    def fallback(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("limit") == "1":
            counts.append(1)
            first = len(counts) == 1
            return httpx.Response(
                200, json={"count": HUGE if first else per_batch_records, "firmy": []}
            )
        if per_batch_records == 0:
            return httpx.Response(204)
        prefix = request.url.params.get("dataod")
        firmy = [list_record(i, id=f"{prefix}-{i}") for i in range(1, per_batch_records + 1)]
        return httpx.Response(200, json={"count": per_batch_records, "firmy": firmy, "links": {}})

    api.fallback = fallback
    return api


def app_args() -> typer.Typer:
    """Aplikacja typer z `cli` — wydzielone, żeby import był czytelny w asercjach wyżej."""
    return cli.app
