"""Testy wiringu drzwi GitHub (``github/app.py``) — atrybucja projektu i bramki pętli pobocznych.

Pętlę pollingu pokrywa ``test_poller``, selekcję ``test_selection``, stan ``test_state``. Zostaje
to, co składa proces: przypisanie zdarzeń do projektu z rejestru (best-effort, ADR 0028/0029),
bramka JEDYNEGO autonomicznego zapisu mostu (auto-komentarz CI, ADR 0024) i pętla utrwalająca jego
kursor. Bramkę po stronie KONFIGURACJI zamyka ``GithubSettings.validate`` (``tests/
test_github_settings.py``) — tu sprawdzamy jej powtórzenie w KODZIE wiringu, bo to ono decyduje,
czy serwis zapisu w ogóle powstanie. Sieci i SQLite nie ma tam, gdzie nie są potrzebne: klient
GitHuba i serwis kursora to atrapy strukturalne.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sufler.adapters.inbound.github import app
from sufler.config import EventsSettings, GithubSettings, TeamsPushSettings

_REJESTR = """projects:
  - key: workmate
    company: biap
    name: Sufler
    description: Asystent wiedzy
    github_repos:
      - BIAP-Inteligentne-Technologie/PIWorkmate
  - key: scada
    company: mpwik
    name: SCADA
    description: Integracja
    github_repos: []
"""


def _rejestr(tmp_path: Path, tresc: str = _REJESTR) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text(tresc, encoding="utf-8")
    return path


def _github(**kw: object) -> GithubSettings:
    base: dict[str, object] = {"token": "gh-pat", "owner": "o", "repo": "r"}
    base.update(kw)
    return GithubSettings(**base)  # type: ignore[arg-type]


# --- atrybucja projektu (``_resolve_project``) ----------------------------------


def test_repo_listed_in_the_registry_resolves_to_its_project_key(tmp_path: Path):
    klucz = app._resolve_project(_rejestr(tmp_path), "BIAP-Inteligentne-Technologie", "PIWorkmate")

    assert klucz == "workmate"


def test_repo_matching_is_case_insensitive_because_github_slugs_are(tmp_path: Path):
    """Ten sam repozytorium pisane inną wielkością liter to WCIĄŻ to repozytorium.

    Rozjazd wielkości liter dawałby zdarzenia bez projektu — czyli cichy brak atrybucji, nie błąd.
    """
    assert app._resolve_project(_rejestr(tmp_path), "biap-inteligentne-technologie", "piworkmate")


def test_repo_absent_from_the_registry_resolves_to_no_project(tmp_path: Path):
    """Brak dopasowania to dozwolony stan (zdarzenia bez projektu), nie awaria startu drzwi."""
    assert app._resolve_project(_rejestr(tmp_path), "obcy", "repo") == ""


def test_unreadable_registry_degrades_to_no_project_instead_of_killing_the_door(
    tmp_path: Path, caplog
):
    """Rejestr to plik konfiguracyjny człowieka. Literówka w YAML nie może zatrzymać mostu —
    zdarzenia mają lecieć dalej, tylko bez atrybucji, i musi to być powiedziane w logu."""
    zepsuty = _rejestr(tmp_path, "projects: [ to nie jest\n  poprawny yaml")

    with caplog.at_level("WARNING"):
        assert app._resolve_project(zepsuty, "o", "r") == ""

    assert any("projektu" in rec.message for rec in caplog.records)


def test_missing_registry_file_degrades_to_no_project(tmp_path: Path):
    assert app._resolve_project(tmp_path / "nie-ma.yaml", "o", "r") == ""


# --- bramka auto-komentarza CI (``_build_ci_autocommenter``, ADR 0024 + Gate 4) --


class _FakeGithubClient:
    """Atrapa klienta — wiring nie wykonuje żadnego wywołania, więc wystarczy sentinel."""


def _ci(state: dict[str, object] | None = None, **kw: object):
    return app._build_ci_autocommenter(
        _FakeGithubClient(), object(), state if state is not None else {}, _github(**kw)
    )


def test_ci_autocomment_is_off_by_default():
    assert _ci() is None


def test_ci_autocomment_stays_off_without_the_general_write_gate():
    """Bramka szczegółowa NIE MOŻE otworzyć zapisu sama. To jedyny autonomiczny zapis mostu,
    więc wymaga OBU flag — gdyby wystarczyła jedna, ogólna bramka zapisu przestałaby cokolwiek
    znaczyć dla ścieżki, która pisze bez człowieka."""
    assert _ci(enable_ci_auto_comment=True, enable_github_write=False) is None


def test_ci_autocomment_stays_off_when_only_the_write_gate_is_open():
    assert _ci(enable_github_write=True) is None


def test_ci_autocomment_builds_the_service_when_both_gates_are_open():
    service = _ci(enable_ci_auto_comment=True, enable_github_write=True, watch_kinds=("ci",))

    assert service is not None


def test_ci_autocomment_resumes_from_its_own_cursor_not_from_the_notifier_one():
    """Kursory są NIEZALEŻNE (osobni konsumenci). Wzięcie cudzego przewinęłoby zdarzenia CI,
    których nikt nie skomentował — po cichu, bo pusty przebieg wygląda jak brak porażek."""
    state = {"ci_autocomment_cursor": 41, "notify_cursor": 7}

    service = _ci(state, enable_ci_auto_comment=True, enable_github_write=True, watch_kinds=("ci",))

    assert service is not None
    assert service.cursor == 41


def test_ci_autocomment_starts_from_zero_on_a_fresh_state():
    service = _ci({}, enable_ci_auto_comment=True, enable_github_write=True, watch_kinds=("ci",))

    assert service is not None
    assert service.cursor == 0


# --- bramka wątkowania kanału (``_build_thread_links``) -------------------------


def test_thread_links_are_absent_when_channel_threading_is_off(tmp_path: Path):
    """OFF (domyślnie) → notifier tworzy nowy root per zdarzenie; magazyn powiązań nie powstaje."""
    events = EventsSettings(db_path=tmp_path / "events.db")

    assert app._build_thread_links(events, TeamsPushSettings()) is None
    assert not (tmp_path / "events.db").exists()  # bramka OFF nie materializuje nawet pliku


def test_thread_links_store_is_built_when_channel_threading_is_on(tmp_path: Path):
    events = EventsSettings(db_path=tmp_path / "events.db")
    push = TeamsPushSettings(enable_channel_threading=True)

    assert app._build_thread_links(events, push) is not None


# --- pętla kursora auto-komentarza (``_pump_ci_autocomment``) -------------------


class _StopPump(BaseException):
    """Wyrwanie z ``while True`` — ``BaseException`` omija ``except Exception`` w pętli."""


def _przerwij_po(monkeypatch: pytest.MonkeyPatch, rundy: int) -> None:
    """Podmień drzemkę pętli tak, by zerwać ją po ``rundy`` rundach."""
    licznik = {"n": 0}

    async def _sleep(_seconds: float) -> None:
        licznik["n"] += 1
        if licznik["n"] >= rundy:
            raise _StopPump

    monkeypatch.setattr(app.asyncio, "sleep", _sleep)


class _FakeCiService:
    """Atrapa serwisu: każda runda przesuwa kursor o ``krok`` (0 = brak nowych zdarzeń)."""

    def __init__(self, *, krok: int = 1, boom: bool = False) -> None:
        self.cursor = 0
        self._krok = krok
        self._boom = boom
        self.rundy = 0

    def process_once(self) -> int:
        self.rundy += 1
        if self._boom:
            raise RuntimeError("GitHub 502")
        self.cursor += self._krok
        return 0


def test_pump_persists_the_cursor_after_a_round_that_moved_it(monkeypatch: pytest.MonkeyPatch):
    zapisane: list[int] = []
    service = _FakeCiService(krok=3)
    _przerwij_po(monkeypatch, rundy=2)

    with pytest.raises(_StopPump):
        asyncio.run(app._pump_ci_autocomment(service, zapisane.append, 0))

    assert zapisane == [3, 6]  # po każdej rundzie, która ruszyła kursor


def test_pump_does_not_rewrite_the_state_when_the_cursor_stood_still(
    monkeypatch: pytest.MonkeyPatch,
):
    """Pusta runda (brak nowych porażek CI) to norma. Zapis stanu za każdym razem byłby
    zbędną rywalizacją o ten sam plik, który równolegle utrwala poller."""
    zapisane: list[int] = []
    service = _FakeCiService(krok=0)
    _przerwij_po(monkeypatch, rundy=3)

    with pytest.raises(_StopPump):
        asyncio.run(app._pump_ci_autocomment(service, zapisane.append, 0))

    assert service.rundy >= 2  # rundy się odbyły…
    assert zapisane == []  # …ale nic nie zapisano


def test_pump_survives_a_failing_round_and_tries_again(monkeypatch: pytest.MonkeyPatch, caplog):
    """Awaria rundy nie kładzie pętli — proces mostu ma przeżyć chwilową niedostępność GitHuba."""
    service = _FakeCiService(boom=True)
    _przerwij_po(monkeypatch, rundy=3)

    with caplog.at_level("ERROR"), pytest.raises(_StopPump):
        asyncio.run(app._pump_ci_autocomment(service, lambda _c: None, 0))

    assert service.rundy >= 2  # kolejna runda mimo wyjątku w poprzedniej
    assert any("auto-komentarza CI" in rec.message for rec in caplog.records)


# --- bramka magazynu zdarzeń jedzie przez main(), nie przez samą konfigurację ---


def test_main_karmi_bramke_zdarzen_katalogiem_danych(tmp_path: Path, monkeypatch):
    """Regresja szwu: te drzwi ZAPISUJĄ zdarzenia, a wołały ``validate()`` bez ``data_dir``.

    Inwariant „treść z drzwi nie trafia do bazy wiedzy" był egzekwowany wyłącznie po stronie
    drzwi Teams, które tego pliku nie zapisują — bramka po jednej stronie wspólnego magazynu
    nie broni niczego. Sonda jedzie przez ``main``, bo to ono podaje argument; sonda na samej
    ``EventsSettings.validate`` zostałaby zielona przy z powrotem pominiętym argumencie.
    """
    monkeypatch.setenv("SUFLER_GITHUB_TOKEN", "t")
    monkeypatch.setenv("SUFLER_GITHUB_OWNER", "o")
    monkeypatch.setenv("SUFLER_GITHUB_REPO", "r")
    monkeypatch.setenv("SUFLER_GITHUB_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("SUFLER_EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("SUFLER_DATA_DIR", str(tmp_path / "data"))

    widziane: dict[str, object] = {}
    prawdziwe = EventsSettings.validate

    def szpieg(self, *, data_dir):  # noqa: ANN001, ANN202
        widziane["data_dir"] = data_dir
        return prawdziwe(self, data_dir=data_dir)

    monkeypatch.setattr(EventsSettings, "validate", szpieg)

    def _nie_uruchamiaj(coro):  # noqa: ANN001, ANN202
        coro.close()  # bez tego pakiet dostaje ostrzeżenie o nieoczekiwanej korutynie

    monkeypatch.setattr(app.asyncio, "run", _nie_uruchamiaj)

    app.main()

    assert widziane["data_dir"] == tmp_path / "data"
