"""Testy dla poprawek z końcowego przeglądu (2026-09-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import ceidg_tool.config as config
from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.cli import _criteria_from_options, _parse_formats, app
from ceidg_tool.config import Settings
from ceidg_tool.errors import ConfigError
from ceidg_tool.estimating import effective_spacing, estimate
from ceidg_tool.pipeline import build_deps, run_update
from ceidg_tool.recordid import kanoniczne_id
from ceidg_tool.store import Store
from tests.conftest import FakeClock, detail_record, list_record
from tests.support import FakeApi, load_fixture, registry_id

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"


@pytest.fixture
def runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.chdir(tmp_path)
    # `*_` bo funkcja bierze teraz nazwę pozycji w magazynie (token CEIDG albo klucz
    # asystenta). Atrapa bez tego parametru wywracała **każdy** test CLI naraz.
    monkeypatch.setattr(config, "read_token_from_keyring", lambda *_: None)
    return CliRunner()


def test_wyczysc_wszystko_removes_database_and_open_log(runner: CliRunner, tmp_path: Path) -> None:
    data_dir = tmp_path / "dane"
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(data_dir)}
    # utwórz bazę i log przez zwykłe polecenie offline
    first = runner.invoke(app, ["runy"], env=env)
    assert first.exit_code == 0, first.output
    assert (data_dir / "store-test.sqlite").exists()
    assert (data_dir / "logi" / "ceidg-tool.log").exists()

    result = runner.invoke(
        app, ["wyczysc", "--wszystko", "--tak", "--potwierdzam-usuniecie"], env=env
    )
    assert result.exit_code == 0, result.output
    assert not (data_dir / "store-test.sqlite").exists()
    assert not list((data_dir / "logi").glob("*.log*"))
    assert "Usunięto" in result.output


def test_maks_overrides_yaml_query(tmp_path: Path) -> None:
    query = tmp_path / "q.yaml"
    query.write_text("wojewodztwo: podlaskie\nmax_rekordow: 1000\n", encoding="utf-8")
    criteria = _criteria_from_options(query, szczegoly=False, maks=500)
    assert criteria.max_rekordow == 500 and criteria.wojewodztwo == ("podlaskie",)
    unchanged = _criteria_from_options(query, szczegoly=False)
    assert unchanged.max_rekordow == 1000


def test_unknown_format_and_source_fail_fast(runner: CliRunner, tmp_path: Path) -> None:
    assert _parse_formats("xlsx, csv") == ("xlsx", "csv")
    with pytest.raises(ConfigError, match="Nieznany format"):
        _parse_formats("xls")
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(tmp_path / "dane")}
    result = runner.invoke(
        app, ["pobierz", "-w", "podlaskie", "--zrodlo", "raporty", "--tak"], env=env
    )
    assert result.exit_code == 3 and "Nieznane źródło" in result.output
    result = runner.invoke(app, ["pobierz", "-w", "podlaskie", "--format", "xls", "--tak"], env=env)
    assert result.exit_code == 3 and "Nieznany format" in result.output


def test_effective_spacing_uses_tightest_window() -> None:
    profile = ApiProfile(base_url=BASE)  # okna (48, 180) i (960, 3600) -> 3,75 s
    assert effective_spacing(profile) == pytest.approx(3.75)
    assert estimate(100, profile).spacing_s == pytest.approx(3.75)
    loose = profile.model_copy(
        update={"rate": profile.rate.model_copy(update={"windows": ((100, 100.0),)})}
    )
    # Przy luźnym oknie (1 s) rządzi odstęp minimalny. Oczekiwana wartość idzie z profilu,
    # a nie z liczby w teście: gdy 2026-09-07 domyślna zmieniła się z 3,6 na 3,75 s, wpisana
    # stała robiła z tego czerwony test zamiast potwierdzić, że reguła „max z trzech" działa.
    assert effective_spacing(loose) == pytest.approx(loose.rate.min_spacing_s)
    assert loose.rate.min_spacing_s > 100.0 / 100


def test_update_advances_watermark_per_window_and_skips_fresh_details(tmp_path: Path) -> None:
    # Zegar **za** oknem zmian, nie domyślny z `conftest` (2023-11-14) przy oknie z 2026.
    # Od naprawy A1 próg świeżości szczegółu to koniec okna zmian, więc zegar sprzed okna
    # znaczyłby „szczegół musi pochodzić z przyszłości" i cache nie mógłby trafić ani razu —
    # czyli test mierzyłby coś innego, niż deklaruje w nazwie.
    clock = FakeClock(start_wall=datetime(2026, 9, 11, 12, tzinfo=UTC).timestamp())
    changed = load_fixture("zmiana.json")["body"]
    # Fixture niesie pisownię z `/zmiana` (małe litery); w bazie wpis żyje pod postacią
    # kanoniczną, bo to jedna tożsamość, a nie dwie (ADR-0013).
    ids = kanoniczne_id(changed["identyfikatoryWpisow"])
    calls = {"zmiana": 0, "firma": 0}

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zmiana" in url:
            calls["zmiana"] += 1
            body = {**changed, "links": {**changed["links"], "next": changed["links"]["self"]}}
            return httpx.Response(200, json=body)
        if "/firma" in url:
            calls["firma"] += 1
            wanted = request.url.params.get_list("ids")
            return httpx.Response(
                200,
                json={"firma": [{**detail_record(1), "id": registry_id(rid)} for rid in wanted]},
            )
        return httpx.Response(404)

    api = FakeApi()
    api.fallback = fallback
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    # jeden id ma świeże szczegóły w cache -> nie jest pobierany ponownie
    deps.store.save_details(details=[{**detail_record(2), "id": ids[0]}])
    since = datetime(2026, 9, 1, tzinfo=UTC)
    until = datetime(2026, 9, 11, tzinfo=UTC)  # 10 dni -> dwa okna po 5 dni
    result = run_update(deps, since=since, until=until)

    assert calls["zmiana"] == 2
    # pierwsze okno: 9 id bez świeżego w porcjach po 5 = 2 żądania; drugie okno: te same
    # identyfikatory są już świeże w cache, więc zero żądań o szczegóły
    assert calls["firma"] == (len(ids) - 1 + 4) // 5
    assert result.records == len(ids)
    assert deps.store.get_watermark("zmiana:test") == "2026-09-11T00:00:00Z"
    raw = next(r for r in deps.store.iter_run_records(result.run_id) if r.id == ids[1])
    assert raw.list_json is None and raw.detail_json is not None  # bez podwójnego JSON
    deps.store.close()


def test_watermark_moves_after_first_window_even_if_second_fails(tmp_path: Path) -> None:
    # Zegar za oknem zmian — z tego samego powodu, co w teście wyżej.
    clock = FakeClock(start_wall=datetime(2026, 9, 11, 12, tzinfo=UTC).timestamp())
    changed = load_fixture("zmiana.json")["body"]
    seen: list[str] = []

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/zmiana" in url:
            seen.append(url)
            if len(seen) == 2:
                return httpx.Response(401)
            return httpx.Response(
                200,
                json={**changed, "links": {**changed["links"], "next": changed["links"]["self"]}},
            )
        return httpx.Response(
            200,
            json={
                "firma": [{**detail_record(1), "id": i} for i in request.url.params.get_list("ids")]
            },
        )

    api = FakeApi()
    api.fallback = fallback
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, http=api.client())
    from ceidg_tool.errors import AuthError

    with pytest.raises(AuthError):
        run_update(
            deps, since=datetime(2026, 9, 1, tzinfo=UTC), until=datetime(2026, 9, 11, tzinfo=UTC)
        )
    assert deps.store.get_watermark("zmiana:test") == "2026-09-06T00:00:00Z"
    deps.store.close()


def test_link_ids_and_stale_detail_ids(tmp_path: Path, clock: FakeClock) -> None:
    with Store(tmp_path / "s.sqlite", environment="test", clock=clock) as store:
        run_id = store.start_run(
            run_id="r",
            criteria_json="{}",
            criteria_hash="h",
            profile_hash="p",
            mode="szczegoly",
            tool_version="0",
            cursor_mode="links",
            kind="zmiana",
        )
        store.save_page(run_id, page_index=0, records=[list_record(1)], next_cursor=None)
        store.save_details(details=[detail_record(1)])
        fresh_id = list_record(1)["id"]
        # Próg podaje wołający. Szczegół zapisano „teraz", więc próg sprzed chwili go
        # przepuszcza, a próg z przyszłości uznaje za nieświeży — i to jest cała reguła,
        # na której stoi `aktualizuj` po naprawie A1.
        przed = datetime.fromtimestamp(clock.wall() - 86_400, tz=UTC)
        po = datetime.fromtimestamp(clock.wall() + 86_400, tz=UTC)
        assert store.stale_detail_ids(kanoniczne_id([fresh_id, "NOWY"]), cutoff=przed) == ["NOWY"]
        assert store.stale_detail_ids(kanoniczne_id([fresh_id]), cutoff=po) == [fresh_id]
        assert store.link_ids(run_id, page_index=1, ids=kanoniczne_id([fresh_id, "NOWY"])) == 1
        assert store.count_run_records(run_id) == 2
        rec = next(r for r in store.iter_run_records(run_id) if r.id == fresh_id)
        assert rec.list_json is not None  # link_ids nie nadpisuje cache listy
        assert store.trim_request_log(keep_s=0) == 0


def test_wyczysc_wszystko_refuses_to_delete_unattended(runner: CliRunner, tmp_path: Path) -> None:
    """Decyzja właściciela z 2026-09-06: `--tak` nie kasuje bazy za operatora.

    `--tak` znaczy „przyjmij decyzje domyślne", a domyślną odpowiedzią na „skasować
    wszystko?" jest „nie". Skasowania nie da się cofnąć, więc harmonogram musi powiedzieć
    to drugi raz i wprost — dokładnie jak przy zgodzie na produkcję.
    """
    data_dir = tmp_path / "dane"
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(data_dir)}
    assert runner.invoke(app, ["runy"], env=env).exit_code == 0
    store = data_dir / "store-test.sqlite"
    assert store.exists()

    result = runner.invoke(app, ["wyczysc", "--wszystko", "--tak"], env=env)

    assert result.exit_code == 3
    assert store.exists()  # baza stoi tam, gdzie stała
    assert "--potwierdzam-usuniecie" in result.output


# ------------------------------------------------- `wyczysc --wszystko`: ścieżka interaktywna


def prepared_data_dir(runner: CliRunner, tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Baza, log i pobrany raport ZIP — komplet tego, co `--wszystko` ma usunąć."""
    data_dir = tmp_path / "dane"
    env = {"CEIDG_TOKEN": "tok", "CEIDG_DATA_DIR": str(data_dir)}
    assert runner.invoke(app, ["runy"], env=env).exit_code == 0
    zip_file = data_dir / "raporty" / "podlaskie.zip"
    zip_file.parent.mkdir(parents=True, exist_ok=True)
    zip_file.write_bytes(b"PK\x03\x04udawany raport")
    return data_dir, env


def test_answering_no_at_the_prompt_leaves_everything_in_place(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ścieżka, którą operator przechodzi naprawdę — i której żaden test nie sprawdzał.

    Bez niej odwrócenie `default=False` albo wypadnięcie warunku z `if` przechodziłoby
    przez cały zestaw, mimo że kasowałoby bazę bez pytania.
    """
    monkeypatch.setattr("ceidg_tool.cli.interactive_available", lambda **_: True)
    data_dir, env = prepared_data_dir(runner, tmp_path)

    result = runner.invoke(app, ["wyczysc", "--wszystko"], env=env, input="nie\n")

    assert result.exit_code == 0
    assert (data_dir / "store-test.sqlite").exists()
    assert (data_dir / "raporty" / "podlaskie.zip").exists()


def test_answering_yes_at_the_prompt_removes_the_reports_too(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raporty ZIP to pełne dzienne zrzuty województwa — największy zbiór danych osobowych.

    Pytanie wymienia je od poprawki z przeglądu 2026-09-06; wcześniej operator dowiadywał
    się o nich dopiero z podsumowania, już po usunięciu.
    """
    monkeypatch.setattr("ceidg_tool.cli.interactive_available", lambda **_: True)
    data_dir, env = prepared_data_dir(runner, tmp_path)

    result = runner.invoke(app, ["wyczysc", "--wszystko"], env=env, input="tak\n")

    assert result.exit_code == 0, result.output
    assert "raporty" in result.output.lower()  # pytanie mówi, co znika
    assert not (data_dir / "store-test.sqlite").exists()
    assert not (data_dir / "raporty" / "podlaskie.zip").exists()


def test_a_pipe_is_not_consent_either(runner: CliRunner, tmp_path: Path) -> None:
    """Brak terminala to nie zgoda, nawet bez `--tak`.

    `CliRunner` podaje wejście, ale nie jest terminalem — dokładnie jak harmonogram
    z przekierowanym wejściem. Wcześniej ta ścieżka kończyła się angielskim „Aborted."
    i kodem 1; teraz pada zdanie po polsku i kod 3, ten sam co przy zgodzie na produkcję.
    """
    data_dir, env = prepared_data_dir(runner, tmp_path)

    result = runner.invoke(app, ["wyczysc", "--wszystko"], env=env, input="y\n")

    assert result.exit_code == 3
    assert (data_dir / "store-test.sqlite").exists()
    assert "--potwierdzam-usuniecie" in result.output


def test_the_consent_flag_alone_does_not_quietly_do_something_else(
    runner: CliRunner, tmp_path: Path
) -> None:
    """`--potwierdzam-usuniecie` bez `--wszystko` znaczyło „zrób zwykłą retencję" i milczało.

    Operator, który chciał skasować wszystko i pomylił parę flag, dostawał komunikat
    o sukcesie i nietkniętą bazę.
    """
    data_dir, env = prepared_data_dir(runner, tmp_path)

    result = runner.invoke(app, ["wyczysc", "--potwierdzam-usuniecie"], env=env)

    assert result.exit_code == 3
    assert (data_dir / "store-test.sqlite").exists()


def test_bare_enter_at_the_purge_prompt_keeps_everything(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enter przy pytaniu o skasowanie bazy nie może kasować.

    Wartość domyślna jest tu jedyną barierą między pomyłką a nieodwracalną stratą, a stoi
    w domyślnym argumencie `_confirm` — czyli w miejscu, które łatwo zmienić bez zauważenia.
    """
    monkeypatch.setattr("ceidg_tool.cli.interactive_available", lambda **_: True)
    data_dir, env = prepared_data_dir(runner, tmp_path)

    result = runner.invoke(app, ["wyczysc", "--wszystko"], env=env, input="\n")

    assert result.exit_code == 0
    assert (data_dir / "store-test.sqlite").exists()
    assert (data_dir / "raporty" / "podlaskie.zip").exists()
