"""Przebieg drzwi digestu (``teams_digest/app.py``, ADR 0053, F6) — próba i nadrabianie.

Orkiestrację dostawy pokrywa ``test_delivery`` (czysta funkcja), stan — ``test_state``. Tutaj
zostaje szew, którego nie widzi żaden z nich, a którego awaria jest NIEWIDOCZNA aż do skrzynek
ludzi: druga bramka (``dry_run``), nadrabianie pominiętego terminu i przycinanie stanu.

Sieci nie ma: ``_send_chat`` podmieniamy przechwytującym zamknięciem (to jedyne miejsce, w którym
drzwi dotykają Graph), a zdarzenia idą przez PRAWDZIWY ``SqliteEventStore`` w ``tmp_path``, wskazany
``WORKMATE_EVENTS_DB`` — dzięki temu sonda przechodzi też przez ``_events_service`` i realne
składanie digestu, zamiast zakładać jego kształt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from workmate.adapters.inbound.teams_digest import app
from workmate.adapters.inbound.teams_digest import state as state_store
from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.config import TeamsDigestSettings, TeamsPushSettings
from workmate.core.application.events import EventService
from workmate.core.domain.events import NewEvent

_TZ = ZoneInfo("Europe/Warsaw")
# Poniedziałek 2026-07-20, 08:00 lokalnie — dokładnie domyślny termin przebiegu.
_TERMIN = datetime(2026, 7, 20, 8, 0, tzinfo=_TZ)
_LABEL = "2026-W30"


@pytest.fixture
def zdarzenia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wspólny ``events.db`` z jednym świeżym zdarzeniem — digest ma co złożyć (total > 0)."""
    db = tmp_path / "events.db"
    events = EventService(SqliteEventStore(db))
    events.ingest(
        NewEvent(
            source="github",
            kind="issue_opened",
            external_id="7",
            title="Integracja SCADA",
            occurred_at=_TERMIN - timedelta(days=1),
            project="scada-integration",
        )
    )
    monkeypatch.setenv("WORKMATE_EVENTS_DB", str(db))
    return db


def _settings(tmp_path: Path, **overrides: object) -> TeamsDigestSettings:
    base: dict[str, object] = {
        "enabled": True,
        "dry_run": True,
        "recipients": ("EMP-1", "EMP-2"),
        "tz_name": "Europe/Warsaw",
        "state_path": tmp_path / "digest_state.json",
    }
    base.update(overrides)
    return TeamsDigestSettings(**base)  # type: ignore[arg-type]


def _przechwyc_wysylke(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Podmień JEDYNE wyjście sieciowe drzwi; zwróć listę ``(odbiorca, tekst)``."""
    wyslane: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_send_chat", lambda _token, recipient, text: wyslane.append((recipient, text))
    )
    return wyslane


# --- druga bramka: tryb próbny (``dry_run``) ------------------------------------


def test_dry_run_composes_the_digest_but_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    """Tryb próbny ma renderować i logować, nie wysyłać — to DRUGA bramka nad ``enabled``."""
    wyslane = _przechwyc_wysylke(monkeypatch)

    report = app._run_once(_settings(tmp_path), token=object(), as_of=_TERMIN)

    assert report.total_events == 1  # digest naprawdę policzony, nie pominięty
    assert wyslane == []  # …ale nic nie poszło do ludzi


def test_dry_run_does_not_persist_state_so_the_real_run_still_reaches_everyone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    """Gdyby próba oznaczała odbiorców, pierwszy REALNY przebieg tygodnia nie wysłałby nic —
    a wyglądałoby to jak poprawne „już dostali"."""
    _przechwyc_wysylke(monkeypatch)
    settings = _settings(tmp_path)

    app._run_once(settings, token=object(), as_of=_TERMIN)

    assert not settings.state_path.exists()


def test_real_run_sends_to_every_recipient_and_marks_each_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    wyslane = _przechwyc_wysylke(monkeypatch)
    settings = _settings(tmp_path, dry_run=False)

    report = app._run_once(settings, token=object(), as_of=_TERMIN)

    assert [odbiorca for odbiorca, _ in wyslane] == ["EMP-1", "EMP-2"]
    assert report.sent == ("EMP-1", "EMP-2")
    # Stan utrwalony PO KAŻDEJ osobie (at-least-once) — restart w połowie nie cofa wysłanych.
    assert set(state_store.load(settings.state_path)) == {
        state_store.key(_LABEL, "EMP-1"),
        state_store.key(_LABEL, "EMP-2"),
    }


def test_second_real_run_in_the_same_week_is_a_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    """Idempotencja per osoba per tydzień: restart procesu nie może wysłać digestu drugi raz."""
    wyslane = _przechwyc_wysylke(monkeypatch)
    settings = _settings(tmp_path, dry_run=False)

    app._run_once(settings, token=object(), as_of=_TERMIN)
    wyslane.clear()
    report = app._run_once(settings, token=object(), as_of=_TERMIN)

    assert wyslane == []
    assert set(report.already) == {"EMP-1", "EMP-2"}


def test_week_label_comes_from_the_deadline_not_from_the_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    """Nadrabianie liczy tydzień z TERMINU. Z zegara wpis trafiłby pod etykietę BIEŻĄCEGO
    tygodnia, więc nadrobiony digest wyszedłby ponownie w regularnym terminie."""
    _przechwyc_wysylke(monkeypatch)
    settings = _settings(tmp_path, dry_run=False, recipients=("EMP-1",))
    monkeypatch.setattr(app, "_now", lambda: datetime(2026, 7, 28, 9, 0, tzinfo=UTC))

    app._run_once(settings, token=object(), as_of=_TERMIN)

    assert list(state_store.load(settings.state_path)) == [state_store.key(_LABEL, "EMP-1")]


def test_window_is_counted_back_from_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zdarzenia: Path
):
    """``window_days`` odmierza okno WSTECZ od terminu — zdarzenie sprzed okna nie wchodzi."""
    _przechwyc_wysylke(monkeypatch)

    szerokie = app._run_once(_settings(tmp_path, window_days=7), token=object(), as_of=_TERMIN)
    waskie = app._run_once(
        _settings(tmp_path, window_days=0), token=object(), as_of=_TERMIN
    )  # okno od samego dnia terminu — zdarzenie z wczoraj odpada

    assert szerokie.total_events == 1
    assert waskie.total_events == 0


# --- nadrabianie pominiętego terminu (``_missed_deadline``) ---------------------


def _now_at(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    monkeypatch.setattr(app, "_now", lambda: moment)


def test_missed_deadline_returns_the_last_deadline_when_nobody_got_that_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Proces wstał we wtorek po pominiętym poniedziałku → nadrabiamy TEN termin."""
    _now_at(monkeypatch, _TERMIN + timedelta(days=1))

    assert app._missed_deadline(_settings(tmp_path), _TZ) == _TERMIN


def test_missed_deadline_is_none_when_the_week_is_already_in_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Ktokolwiek dostał ten tydzień → przebieg się odbył, nadrabianie byłoby duplikatem."""
    settings = _settings(tmp_path)
    state_store.save(settings.state_path, {state_store.key(_LABEL, "EMP-1"): "t"})
    _now_at(monkeypatch, _TERMIN + timedelta(days=1))

    assert app._missed_deadline(settings, _TZ) is None


def test_missed_deadline_is_none_beyond_the_catchup_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Po dłuższej przerwie nie rozsyłamy nieaktualnego tygodnia — sufit ``max_catchup_days``."""
    _now_at(monkeypatch, _TERMIN + timedelta(days=5))

    assert app._missed_deadline(_settings(tmp_path, max_catchup_days=3), _TZ) is None


def test_catchup_can_be_switched_off_entirely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _now_at(monkeypatch, _TERMIN + timedelta(hours=1))

    assert app._missed_deadline(_settings(tmp_path, max_catchup_days=0), _TZ) is None


# --- przycinanie stanu (``_prune_state``) --------------------------------------


def test_prune_state_drops_old_weeks_and_keeps_the_recent_ones(tmp_path: Path):
    settings = _settings(tmp_path, dry_run=False)
    saved = {
        state_store.key("2025-W01", "EMP-1"): "prehistoria",
        state_store.key(_LABEL, "EMP-1"): "swieze",
    }

    app._prune_state(settings, saved, _TERMIN)

    assert list(state_store.load(settings.state_path)) == [state_store.key(_LABEL, "EMP-1")]


def test_prune_state_keeps_weeks_that_catchup_may_still_need(tmp_path: Path):
    """Trzymamy KILKA tygodni, nie jeden: nadrabianie po przerwie musi widzieć, kto już dostał."""
    settings = _settings(tmp_path, dry_run=False)
    poprzedni = state_store.key("2026-W29", "EMP-1")
    saved = {
        state_store.key("2025-W01", "EMP-1"): "prehistoria",  # wymusza zapis (coś wypada)
        poprzedni: "tydzien-wczesniej",
        state_store.key(_LABEL, "EMP-1"): "swieze",
    }

    app._prune_state(settings, saved, _TERMIN)

    assert poprzedni in state_store.load(settings.state_path)


def test_prune_state_writes_nothing_in_dry_run(tmp_path: Path):
    """Tryb próbny nie dotyka dysku ANI tą drogą — inaczej bramka byłaby dziurawa od tyłu."""
    settings = _settings(tmp_path, dry_run=True)

    app._prune_state(settings, {state_store.key("2025-W01", "EMP-1"): "stare"}, _TERMIN)

    assert not settings.state_path.exists()


def test_prune_state_does_not_rewrite_a_state_that_did_not_change(tmp_path: Path):
    """Brak zmiany = brak zapisu: ten sam plik utrwala równolegle sam przebieg, po każdej osobie."""
    settings = _settings(tmp_path, dry_run=False)

    app._prune_state(settings, {state_store.key(_LABEL, "EMP-1"): "swieze"}, _TERMIN)

    assert not settings.state_path.exists()


# --- fail-fast konfiguracji i odporność pętli -----------------------------------


def test_require_teams_names_every_missing_identity_field():
    with pytest.raises(SystemExit) as exc:
        app._require_teams(TeamsPushSettings())

    komunikat = str(exc.value)
    assert "WORKMATE_TEAMS_PUSH_CLIENT_ID" in komunikat
    assert "WORKMATE_TEAMS_PUSH_TENANT_ID" in komunikat


def test_require_teams_names_only_what_is_missing():
    with pytest.raises(SystemExit) as exc:
        app._require_teams(TeamsPushSettings(client_id="app-1"))

    komunikat = str(exc.value)
    assert "WORKMATE_TEAMS_PUSH_TENANT_ID" in komunikat
    assert "WORKMATE_TEAMS_PUSH_CLIENT_ID" not in komunikat


def test_require_teams_passes_with_full_identity():
    app._require_teams(TeamsPushSettings(client_id="app-1", tenant_id="tenant-1"))  # nie rzuca


def test_a_failed_run_does_not_kill_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Proces ma dożyć następnego poniedziałku: awaria przebiegu wraca logiem, nie wyjątkiem."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Graph 500")

    monkeypatch.setattr(app, "_run_once", _boom)

    with caplog.at_level("ERROR"):
        app._safe_run_once(_settings(tmp_path), token=object(), as_of=_TERMIN)

    assert any("nie powiódł się" in rec.message for rec in caplog.records)


def test_sigterm_during_the_wait_stops_within_the_grace_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regresja: SIGTERM w drzemce był ignorowany do 15 minut, przy ``stop_grace_period`` 45 s.

    Po PEP 475 ``time.sleep`` WZNAWIA się po obsłudze sygnału, więc handler ustawiał flagę,
    a pętla i tak dosypiała do końca ``_MAX_SLEEP_S``. Sonda mierzy nie czas, tylko sposób
    czekania: pętla MUSI czekać na obiekcie zdarzenia, który ``set`` przerywa natychmiast.

    ``time.sleep`` podmieniamy na PUŁAPKĘ, a nie zostawiamy samej asercji na ``czekania``.
    Bez niej regres nie objawia się porażką, tylko ZAWIESZENIEM: zegar jest zatrzymany
    (``_now`` stałe), ``stop`` nigdy nie zostaje zapalone, bo szpieg na ``Event.wait`` w ogóle
    nie zostaje wywołany, więc ``while _now() < target`` kręci się z drzemkami po 900 s bez
    końca. Zawieszony przebieg CI nie mówi, co jest zepsute — pułapka zamienia go w jedną
    czytelną porażkę w pierwszej sekundzie.
    """
    import threading
    import time

    def _pulapka_na_sleep(_sekundy: float) -> None:
        raise AssertionError(
            "pętla czeka przez ``time.sleep`` — po PEP 475 sygnał jej nie przerywa "
            "(regresja SIGTERM: proces dosypia do ``_MAX_SLEEP_S`` i idzie pod SIGKILL)"
        )

    monkeypatch.setattr(time, "sleep", _pulapka_na_sleep)
    stop = threading.Event()
    czekania: list[float] = []
    prawdziwy_wait = stop.wait

    def _spy_wait(timeout: float | None = None) -> bool:
        czekania.append(float(timeout or 0))
        stop.set()  # sygnał przychodzi W TRAKCIE czekania
        return prawdziwy_wait(0)

    monkeypatch.setattr(stop, "wait", _spy_wait)
    monkeypatch.setattr(app, "_missed_deadline", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_now", lambda: _TERMIN - timedelta(days=1))

    app._run_forever(_settings(tmp_path), token=object(), stop=stop)

    assert czekania  # czekaliśmy na Event, a nie na time.sleep…
    assert czekania[0] <= 900  # …z tym samym sufitem długości drzemki co dotąd
