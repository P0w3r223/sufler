"""Przebieg end-to-end offline: fixtures → SQLite → xlsx, zero sieci (bramka 2 bez API)."""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.config import Settings
from ceidg_tool.errors import ServerError
from ceidg_tool.estimating import estimate
from ceidg_tool.pipeline import (
    build_deps,
    find_resumable,
    output_name,
    run_export,
    run_fetch,
    run_report_fetch,
)
from ceidg_tool.records import Report
from ceidg_tool.ui import flow, texts
from ceidg_tool.ui.texts import estimate_text
from tests.conftest import FakeClock
from tests.support import FakeApi, RecordingView, criteria, load_fixture
from tests.test_reports import HEADER, row

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
UNIQUE_IDS = len(
    {r["id"] for i in range(3) for r in load_fixture(f"firmy_page{i}_limit5.json")["body"]["firmy"]}
)


def settings_for(tmp_path: Path) -> Settings:
    return Settings(token="tok", environment="test", data_dir=tmp_path / "dane")


def paged_api() -> FakeApi:
    """Trzy strony po 5 rekordów; ostatnia ma next == self (jak na produkcji)."""
    api = FakeApi()
    pages = [load_fixture(f"firmy_page{i}_limit5.json") for i in range(3)]
    bodies = [dict(p["body"]) for p in pages]
    bodies[2]["links"] = {**bodies[2]["links"], "next": bodies[2]["links"]["self"]}
    detail_sample = load_fixture("firma_by_ids.json")["body"]["firma"]

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url:
            if "limit=1" in url and "page" not in url:
                return httpx.Response(200, json={**bodies[0], "firmy": bodies[0]["firmy"][:1]})
            page = request.url.params.get("page")
            if page is None:
                return httpx.Response(200, json=bodies[0])
            return httpx.Response(200, json=bodies[int(page)])
        if "/firma" in url:
            wanted = request.url.params.get_list("ids")
            found = [r for r in detail_sample if r["id"] in wanted]
            fake = [
                {**detail_sample[0], "id": i} for i in wanted if i not in {r["id"] for r in found}
            ]
            return httpx.Response(200, json={"firma": found + fake})
        return httpx.Response(404, json={"code": "X", "message": "brak"})

    api.fallback = fallback
    return api


def test_end_to_end_list_and_details_to_workbook(tmp_path: Path, clock: FakeClock) -> None:
    api = paged_api()
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5, "ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile  # profil testowy z limitem 5 jak w fixtures
    query = criteria(wojewodztwo="podlaskie", szczegoly=True)

    result = run_fetch(query, deps, known_count=15)
    assert result.status == "zakonczony"
    assert result.records == UNIQUE_IDS and result.details == UNIQUE_IDS
    assert result.pages == 3

    dest = tmp_path / "wynik.xlsx"
    summary = run_export(result.run_id, dest, deps, cel_pobrania="test e2e")
    assert summary.paths == (dest,)
    wb = load_workbook(dest)
    assert wb["Firmy"].max_row == UNIQUE_IDS + 1
    assert "PKD" in wb.sheetnames
    meta = {r[0].value: r[1].value for r in wb["Metadane"].iter_rows(min_row=2)}
    assert meta["cel_pobrania"] == "test e2e"
    assert meta["liczba_pobranych_rekordow"] == UNIQUE_IDS
    assert meta["srodowisko"] == "test"
    assert summary.records == UNIQUE_IDS and summary.by_status
    deps.store.close()


def test_the_workbook_records_which_pkd_population_was_fetched(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Skoroszyt ma powiedzieć, którą populację pobrano — inaczej wyniku nie da się odtworzyć.

    Wybór rocznika PKD zmienia zawartość pobrania o całe branże (ADR-0012). Skoroszyt, który
    tego nie zapisuje, wygląda po miesiącu identycznie jak węższy i nie ma po czym poznać
    różnicy — a `Metadane` istnieją właśnie po to, żeby dało się wrócić do tego, co zrobiono.
    Sprawdzane na **prawdziwym pliku** czytanym przez `openpyxl`, bo model kryteriów mógłby
    nieść pole, którego eksport i tak nie zapisuje.
    """
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5, "ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    query = criteria(wojewodztwo="podlaskie", pkd="9621Z", pkd_2007="9602Z")

    result = run_fetch(query, deps, known_count=15)
    dest = tmp_path / "rocznik.xlsx"
    run_export(result.run_id, dest, deps, cel_pobrania="kontrola rocznika")

    meta = {r[0].value: r[1].value for r in load_workbook(dest)["Metadane"].iter_rows(min_row=2)}
    # Zdanie dla człowieka: rozszerzenie ma być opisane jako **dodatek**, a nie wtopione
    # w listę kodów, o które operator poprosił.
    assert "dodatkowo kody PKD 2007: 9602Z" in meta["kryteria"]
    # I postać maszynowa, żeby przebieg dało się powtórzyć co do kodu.
    assert '"pkd_2007":["9602Z"]' in meta["kryteria_json"].replace(", ", ",")
    deps.store.close()


def test_resume_does_not_refetch_saved_pages(tmp_path: Path, clock: FakeClock) -> None:
    api = paged_api()
    real_fallback = api.fallback
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] >= 3:  # od trzeciego żądania: sieć znika na dobre
            raise httpx.ReadTimeout("timeout", request=request)
        assert real_fallback is not None
        return real_fallback(request)

    api.fallback = flaky
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    query = criteria(wojewodztwo="podlaskie")

    from ceidg_tool.errors import TransportError

    with pytest.raises(TransportError):
        run_fetch(query, deps, known_count=15)
    interrupted = find_resumable(query, deps)
    assert interrupted is not None and interrupted.status == "przerwany"
    assert deps.store.count_run_records(interrupted.run_id) == 10  # dwie strony zapisane

    requests_before = len(api.requests)
    api.fallback = real_fallback
    result = run_fetch(query, deps, resume_run_id=interrupted.run_id)
    assert result.status == "zakonczony" and result.records == UNIQUE_IDS
    new_requests = api.requests[requests_before:]
    assert len(new_requests) == 1 and "page=2" in new_requests[0]
    assert find_resumable(query, deps) is None
    deps.store.close()


def test_max_rekordow_limits_pages(tmp_path: Path, clock: FakeClock) -> None:
    api = paged_api()
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    result = run_fetch(criteria(wojewodztwo="podlaskie", max_rekordow=7), deps, known_count=15)
    assert result.records == 7
    assert sum("/firmy" in r for r in api.requests) == 2
    deps.store.close()


def test_report_path_downloads_once_and_filters_locally(tmp_path: Path, clock: FakeClock) -> None:
    rows = [
        row(Nip="3563457932", DataRozpoczeciaDzialalnosci="2014-07-29"),
        row(
            Nip="8567773578",
            Regon="113110043",
            DataRozpoczeciaDzialalnosci="2020-01-01",
            StatusDzialalnosci="Zawieszony",
        ),
        row(
            Nip="", Regon="", NazwaPodmiotu="=CMD() FIRMA", DataRozpoczeciaDzialalnosci="2014-01-15"
        ),
    ]
    csv_text = ";".join(HEADER) + "\n" + "\n".join(";".join(r.values()) for r in rows) + "\n"
    zip_bytes = tmp_path / "src.zip"
    with zipfile.ZipFile(zip_bytes, "w") as z:
        z.writestr("Zarejestrowane działalności.csv", "﻿" + csv_text)

    api = FakeApi()
    api.fallback = lambda request: httpx.Response(200, content=zip_bytes.read_bytes())
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    report = Report(
        "r1",
        "Zarejestrowane działalności - województwo podlaskie",
        ".csv",
        f"{BASE}/raport/r1",
        "2026-09-04 06:40:59",
    )
    query = criteria(wojewodztwo="podlaskie", data_od="2014-01-01", data_do="2014-12-31")
    result = run_report_fetch(query, deps, report)
    assert result.records == 2 and result.requests == 1
    dest = tmp_path / "raport.xlsx"
    summary = run_export(result.run_id, dest, deps)
    wb = load_workbook(dest)
    ws = wb["Firmy"]
    header = {c.value: i + 1 for i, c in enumerate(ws[1])}
    sources = {ws.cell(row=r, column=header["zrodlo"]).value for r in (2, 3)}
    assert sources == {"CEIDG_RAPORT"}
    names = {ws.cell(row=r, column=header["nazwa"]).value for r in (2, 3)}
    assert "'=CMD() FIRMA" in names
    assert summary.with_email == 2
    deps.store.close()


def report_zip(tmp_path: Path, rows: list[dict[str, str]], name: str = "src.zip") -> Path:
    csv_text = ";".join(HEADER) + "\n" + "\n".join(";".join(r.values()) for r in rows) + "\n"
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Zarejestrowane działalności.csv", "﻿" + csv_text)
    return path


def report_deps(tmp_path: Path, clock: FakeClock, archive: Path):  # type: ignore[no-untyped-def]
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(200, content=archive.read_bytes())
    return build_deps(settings_for(tmp_path), clock=clock, http=api.client())


REPORT = Report(
    "r1",
    "Zarejestrowane działalności - województwo podlaskie",
    ".csv",
    f"{BASE}/raport/r1",
    "2026-09-04 06:40:59",
)


def test_report_rows_sharing_an_identity_collapse_into_one_record(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Raport ma jeden wiersz na wpis, ale `id` powstaje z NIP-u — powtórzony NIP skleja wiersze.

    Wygrywa wiersz ostatni (`ON CONFLICT DO UPDATE` w `save_page`), a wiersze bez NIP-u
    i REGON-u są rozróżniane wyłącznie skrótem treści.
    """
    rows = [
        row(Nip="3563457932", NazwaPodmiotu="FIRMA PIERWSZA"),
        row(Nip="3563457932", NazwaPodmiotu="FIRMA DRUGA"),  # ten sam NIP
        row(Nip="", Regon="", NazwaPodmiotu="BEZ IDENTYFIKATOROW", Miejscowosc="Białystok"),
        row(Nip="", Regon="", NazwaPodmiotu="BEZ IDENTYFIKATOROW", Miejscowosc="Białystok"),
        row(Nip="", Regon="", NazwaPodmiotu="INNA BEZ IDENTYFIKATOROW", Miejscowosc="Łomża"),
    ]
    deps = report_deps(tmp_path, clock, report_zip(tmp_path, rows))
    result = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)

    stored = list(deps.store.iter_run_records(result.run_id))
    assert len(stored) == 3
    assert len({r.id for r in stored}) == 3  # brak duplikatów po `id`
    names = {(r.list_json or {}).get("nazwa") for r in stored}
    assert names == {"FIRMA DRUGA", "BEZ IDENTYFIKATOROW", "INNA BEZ IDENTYFIKATOROW"}

    # `records_seen` liczy faktycznie wstawione rekordy, więc zgadza się z bazą…
    run = deps.store.get_run(result.run_id)
    assert run.records_seen == 3
    assert result.records == 3
    # …a `count_api` liczy unikalne identyfikatory, nie wiersze CSV przed sklejeniem.
    # Rozbieżność jest widoczna dla użytkownika w arkuszu Metadane (patrz asercje niżej).
    assert run.count_api == 3

    dest = tmp_path / "raport.xlsx"
    summary = run_export(result.run_id, dest, deps)
    assert summary.records == 3
    assert load_workbook(dest)["Firmy"].max_row - 1 == 3
    meta = {r[0].value: r[1].value for r in load_workbook(dest)["Metadane"].iter_rows(min_row=2)}
    assert meta["liczba_trafien_count"] == 3
    assert meta["liczba_pobranych_rekordow"] == 3
    deps.store.close()


def test_report_record_without_nip_exports_with_empty_identifier_columns(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Pusty NIP nie może wywrócić eksportu ani udawać wartości `"None"` w komórce."""
    rows = [row(Nip="", Regon="", NazwaPodmiotu="FIRMA BEZ IDENTYFIKATOROW")]
    deps = report_deps(tmp_path, clock, report_zip(tmp_path, rows))
    result = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, REPORT)
    assert result.records == 1

    dest = tmp_path / "bez_nip.xlsx"
    run_export(result.run_id, dest, deps)
    ws = load_workbook(dest)["Firmy"]
    header = {c.value: i + 1 for i, c in enumerate(ws[1])}
    assert ws.cell(row=2, column=header["nip"]).value is None
    assert ws.cell(row=2, column=header["regon"]).value is None
    assert ws.cell(row=2, column=header["nazwa"]).value == "FIRMA BEZ IDENTYFIKATOROW"
    assert ws.cell(row=2, column=header["zrodlo"]).value == "CEIDG_RAPORT"
    # `id` z raportu nie jest GUID-em, więc nie ma linku do publicznej wyszukiwarki
    assert ws.cell(row=2, column=header["link_ceidg"]).value is None
    deps.store.close()


def test_estimate_and_output_name() -> None:
    profile = ApiProfile(base_url=BASE, max_limit_firmy=25, ids_batch_size=5)
    est = estimate(1240, profile)
    assert est.requests_list == 50 and est.requests_details == 248
    text = estimate_text(est)
    assert "1 240" in text and "~50 zapytań" in text and "~298 zapytań" in text
    capped = estimate(1240, profile, max_rekordow=100)
    assert capped.requests_list == 4
    name = output_name(
        criteria(wojewodztwo="podlaskie", data_od="2014-01-01"),
        "test",
        datetime(2026, 9, 5, 12, 30, tzinfo=UTC),
    )
    assert name == "ceidg_podlaskie_2014-01-01_x_test_20260905_1230.xlsx"


def test_server_error_marks_run_and_keeps_checkpoint(tmp_path: Path, clock: FakeClock) -> None:
    api = FakeApi()
    api.fallback = lambda request: httpx.Response(500, json={"code": "E", "message": "awaria"})
    settings = settings_for(tmp_path)
    deps = build_deps(settings, clock=clock, http=api.client())
    with pytest.raises(ServerError):
        run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=5)
    runs = deps.store.list_runs()
    assert runs and runs[0].status == "przerwany"
    deps.store.close()


def test_licznik_niewyjasnionych_milczy_przy_pobraniu_samej_listy(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Bez szczegółów każdy wpis zostaje w stanie `brak` **z definicji** — to nie jest strata.

    Licznik ostrzega przed pracą wykonaną i zgubioną (ADR-0013). Gdyby liczył też pobranie
    listy, każde takie pobranie kończyłoby się ostrzeżeniem o wszystkich rekordach naraz —
    czyli alarm zamieniłby się w szum i przestałby cokolwiek znaczyć."""
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5, "ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=15)

    assert result.details == 0
    assert result.unresolved == 0, "lista bez szczegółów nie jest stratą"
    stany = {str(r[0]) for r in deps.store._conn.execute("SELECT DISTINCT detail_state FROM firma")}
    assert stany == {"brak"}, "kontrola: wpisy naprawdę stoją w stanie `brak`"
    deps.store.close()


def test_licznik_niewyjasnionych_liczy_przy_pobraniu_ze_szczegolami(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Przy `szczegoly=True` każdy wpis ma skończyć wyjaśniony — a jeśli nie, licznik mówi ile.

    Druga gałąź tego samego warunku: tu stan `brak` znaczy dokładnie to, przed czym licznik
    ostrzega, bo żądanie o szczegóły poszło i wynik nie miał gdzie usiąść."""
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5, "ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile

    result = run_fetch(criteria(wojewodztwo="podlaskie", szczegoly=True), deps, known_count=15)

    assert result.details == result.records
    assert result.unresolved == 0
    deps.store.close()


def test_uwaga_o_gubionych_wpisach_dociera_na_ekran_podsumowania(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Ostatnie ogniwo łańcucha: `notes` z `ExecuteResult` ma się pojawić w bloku końcowym.

    `_from_run` produkuje zdanie, `export_and_report` przekłada je na wiersz „uwaga",
    a `summary_table` rysuje. Bez tego testu środkowe ogniwo byłoby jedynym miejscem, gdzie
    zdanie o cichej stracie mogłoby po cichu zniknąć — i nikt by tego nie zauważył, bo obie
    strony łańcucha mają własne testy."""
    api = paged_api()
    deps = build_deps(settings_for(tmp_path), clock=clock, http=api.client())
    deps.profile = deps.profile.model_copy(update={"max_limit_firmy": 5, "ids_batch_size": 5})
    assert deps.client is not None
    deps.client._profile = deps.profile
    result = run_fetch(criteria(wojewodztwo="podlaskie"), deps, known_count=15)
    view = RecordingView()
    uwaga = texts.unresolved_note(4)

    flow.export_and_report([result.run_id], deps, view, out=tmp_path / "wynik.xlsx", notes=(uwaga,))

    assert uwaga in view.text()
    blok = view.block_titled("Podsumowanie")
    assert any("uwaga" in wiersz for wiersz in blok.as_text().splitlines())
    deps.store.close()
