"""Drzwi cotygodniowych kart czasu (ADR 0035) — entry point ``workmate-worklogi``.

W piątek o wyznaczonej godzinie: godziny za tydzień ZAMKNIĘTY (poprzedni pon.–ndz.) → arkusz
importu WorklogPRO per osoba → prywatna wiadomość na Teams z zestawieniem i ścieżką pliku.
Wiadomość dostają tylko te osoby, które faktycznie pracowały. Okno liczy ``reported_week``:
tydzień poprzedni, bo bieżący gubił weekend i piątkowe popołudnie (ADR 0035 § Consequences).

Uruchomienie: ``uv run workmate-worklogi`` (wymaga ``uv sync --extra worklogi``).
Tryby: ``--once`` (jeden przebieg i koniec), ``--login`` (jednorazowa zgoda device-code),
bez flag — pętla czekająca na kolejne terminy.

**Domyślnie PRÓBNY.** Arkusze powstają, wiadomości nie wychodzą, stan się nie zapisuje —
artefakty można obejrzeć przed pierwszym uruchomieniem bojowym
(``WORKMATE_WORKLOGI_DRY_RUN=false``).

Importy ``httpx``/``msal``/``openpyxl`` są LENIWE: brak extra kończy się czytelnym komunikatem,
nie surowym ``ImportError``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from workmate.adapters.inbound import env
from workmate.adapters.inbound.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)
from workmate.adapters.inbound.worklogi import state as state_store
from workmate.config import Settings, TeamsPushSettings, WorklogiSettings
from workmate.core.application.weekly_timesheets import RunReport, WeeklyTimesheetService
from workmate.core.domain.week import next_run, previous_run, reported_week, week_label

logger = logging.getLogger(__name__)

_MISSING_EXTRA = "Drzwi kart czasu wymagają extra 'worklogi'. Zainstaluj: uv sync --extra worklogi"
# Ile ostatnich tygodni trzymamy w stanie — nadrabianie musi widzieć, kto już dostał.
_KEEP_WEEKS = 6
# Sufit jednej drzemki pętli. Bez niego proces spałby do piątku jednym ``sleep`` i nie zareagował
# na sygnał zatrzymania ani na zmianę czasu; z nim budzi się regularnie i przelicza termin.
_MAX_SLEEP_S = 900


def main() -> None:
    """Uruchom drzwi kart czasu: jeden przebieg, pętla albo jednorazowe logowanie."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()
    args = _parse_args()

    settings = WorklogiSettings.from_env()
    settings.validate(data_dir=Settings.from_env().data_dir)
    if not settings.enabled:
        raise SystemExit(
            "WORKMATE_WORKLOGI_ENABLED=false — drzwi kart czasu są wyłączone (ADR 0035)."
        )
    push = TeamsPushSettings.from_env()
    _require_teams(push)

    token_provider = _build_token_provider(push)
    if args.login:
        # Jednorazowa zgoda device-code (także po dodaniu TeamMember.Read.All do zakresów).
        token_provider()
        logger.info("Zalogowano — token w cache %s.", push.token_cache_path)
        return

    try:
        with acquire_single_instance_lock(settings.state_path):
            if args.once:
                _run_once(settings, push, token_provider)
            else:
                _run_forever(settings, push, token_provider)
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cotygodniowe karty czasu → Teams (ADR 0035).")
    parser.add_argument("--once", action="store_true", help="jeden przebieg i wyjście")
    parser.add_argument("--login", action="store_true", help="jednorazowa zgoda device-code")
    return parser.parse_args()


def _run_forever(settings: WorklogiSettings, push: TeamsPushSettings, token: Any) -> None:
    """Pętla: nadrób pominięty termin, potem czekaj na kolejne piątki.

    Nadrabianie ma SUFIT (``max_catchup_days``): po dłuższej przerwie lepiej nie rozsyłać
    nieaktualnych godzin, tylko poczekać na najbliższy normalny termin.
    """
    tz = ZoneInfo(settings.tz_name)
    missed = _missed_deadline(settings, tz)
    if missed is not None:
        logger.info("Wykryto pominięty termin (%s) — nadrabiam za jego tydzień.", missed)
        _safe_run_once(settings, push, token, as_of=missed)
    while True:
        target = next_run(
            _now(),
            tz=tz,
            weekday=settings.run_weekday,
            hour=settings.run_hour,
            minute=settings.run_minute,
        )
        logger.info("Następny przebieg: %s.", target)
        while _now() < target:
            time.sleep(min(_MAX_SLEEP_S, max(1.0, (target - _now()).total_seconds())))
        _safe_run_once(settings, push, token, as_of=target)


def _safe_run_once(
    settings: WorklogiSettings, push: TeamsPushSettings, token: Any, as_of: datetime
) -> None:
    """Przebieg, którego awaria NIE kładzie pętli — proces ma dożyć następnego piątku.

    Bez tego jedna niedostępność (plik godzin w trakcie zapisu przez system źródłowy, 403
    z Graph, chwilowy brak sieci) wywracała cały proces. Pod systemd z ``Restart=always``
    wracał, nadrabianie znów było należne, znów padał — pętla restartów, w której nikt nie
    dostaje nic. Wzorzec przeniesiony z ``Powiadomienia_teams`` razem z resztą modułu.
    """
    try:
        _run_once(settings, push, token, as_of=as_of)
    except Exception:
        logger.exception(
            "Przebieg kart czasu (%s) nie powiódł się — czekam na kolejny termin.", as_of
        )


def _missed_deadline(settings: WorklogiSettings, tz: ZoneInfo) -> datetime | None:
    """Pominięty termin do nadrobienia (albo ``None``) — ZWRACAMY GO, nie samo „tak/nie".

    Wołający liczy tydzień raportu z TERMINU, nie z „teraz". Termin (piątek) i podniesienie
    (poniedziałek) leżą po DWÓCH stronach granicy tygodnia, więc raport przeskakiwał o siedem
    dni: tydzień, który przepadł, nie trafiał do nikogo NIGDY, a osoby zapisane w stanie pod
    etykietą tygodnia następnego były pomijane w jego prawdziwym przebiegu — traciły oba.
    Kontrakt przeniesiony z ``Powiadomienia_teams`` (tam ta sama pułapka jest opisana wprost).
    """
    if settings.max_catchup_days <= 0:
        return None
    last = previous_run(
        _now(),
        tz=tz,
        weekday=settings.run_weekday,
        hour=settings.run_hour,
        minute=settings.run_minute,
    )
    if _now() - last > timedelta(days=settings.max_catchup_days):
        logger.info("Pominięty termin (%s) jest za stary — pomijam nadrabianie.", last)
        return None
    week_start, _ = reported_week(last, tz)
    saved = state_store.load(settings.state_path)
    label = week_label(week_start)
    return None if any(k.startswith(f"{label}:") for k in saved) else last


def _run_once(
    settings: WorklogiSettings,
    push: TeamsPushSettings,
    token: Any,
    as_of: datetime | None = None,
) -> RunReport:
    """Wykonaj JEDEN przebieg tygodniowy i zwróć raport.

    ``as_of`` to CHWILA, dla której liczymy tydzień raportu — termin przebiegu, nie zegar.
    Dzięki temu nadrobienie po awarii raportuje tydzień, który przepadł, a nie bieżący
    (patrz ``_missed_deadline``). Brak wartości = tryb ``--once``, gdzie „teraz" jest
    intencją użytkownika.
    """
    import httpx

    from workmate.adapters.outbound.graph_identity_directory import (
        GraphIdentityDirectory,
        fetch_team_members,
    )
    from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier
    from workmate.adapters.outbound.json_hours_source import JsonHoursSource
    from workmate.adapters.outbound.openpyxl_sheet_writer import OpenpyxlSheetWriter

    tz = ZoneInfo(settings.tz_name)
    moment = as_of if as_of is not None else _now()
    saved = state_store.load(settings.state_path)

    with httpx.Client(timeout=30) as sync_http:
        members = fetch_team_members(sync_http, settings.team_id, token())
    identities = GraphIdentityDirectory(settings.identities_path, members)
    hours = JsonHoursSource(settings.hours_path)

    def send_html(aad_user_id: str, html: str) -> None:
        """Most sync→async: przebieg jest wsadowy, a adapter Teams asynchroniczny.

        Klient POWSTAJE I GINIE wewnątrz jednego ``asyncio.run``. Współdzielony między
        wywołaniami trzymałby w puli połączenie keep-alive przypięte do pętli, którą
        poprzednie ``asyncio.run`` już ZAMKNĘŁO — kolejna osoba dostawała
        ``RuntimeError: Event loop is closed``, czyli wiadomość szła do co drugiej osoby.
        Kilkanaście osób raz w tygodniu, więc koszt zestawienia połączenia jest bez
        znaczenia wobec ceny pomyłki.
        """

        async def _send() -> None:
            async with httpx.AsyncClient(timeout=30) as async_http:
                await HttpxTeamsNotifier(async_http, token).send_chat_html(aad_user_id, html)

        asyncio.run(_send())

    def mark_done(week: str, source_id: str, _outcome: Any) -> None:
        saved[state_store.key(week, source_id)] = _now().isoformat()
        state_store.save(settings.state_path, saved)  # po KAŻDEJ osobie

    service = WeeklyTimesheetService(
        hours,
        identities,
        OpenpyxlSheetWriter(),
        send_html,
        output_dir=str(settings.output_dir),
        tz=tz,
        start_hour=settings.start_hour,
        max_minutes_per_day=round(settings.max_hours_per_day * 60),
        dry_run=settings.dry_run,
        only_source_ids=settings.only_source_ids,
        already_done=lambda week, sid: state_store.key(week, sid) in saved,
        mark_done=mark_done,
        now=lambda: moment,
    )
    report = service.run()
    _prune_state(settings, saved, tz, moment)
    for failure in report.failed:
        logger.warning("NIEUDANE %s (%s): %s", failure.source_id, failure.reason, failure.detail)
    return report


def _prune_state(
    settings: WorklogiSettings, saved: dict[str, str], tz: ZoneInfo, moment: datetime
) -> None:
    """Przytnij stan do kilku ostatnich tygodni — plik nie ma rosnąć bez końca.

    ``moment`` to ta sama chwila, dla której liczono raport — inaczej nadrabianie mogłoby
    wyciąć z okna zachowywanych tygodni ten, który właśnie zapisało.
    """
    start, _ = reported_week(moment, tz)
    keep = tuple(week_label(start - timedelta(days=7 * i)) for i in range(_KEEP_WEEKS))
    pruned = state_store.prune(saved, keep_weeks=keep)
    if pruned != saved and not settings.dry_run:
        state_store.save(settings.state_path, pruned)


def _require_teams(push: TeamsPushSettings) -> None:
    """Bez tożsamości Graph nie ma jak wysłać wiadomości — fail-fast z listą braków."""
    missing = [
        name
        for name, value in (
            ("WORKMATE_TEAMS_PUSH_CLIENT_ID", push.client_id),
            ("WORKMATE_TEAMS_PUSH_TENANT_ID", push.tenant_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Drzwi kart czasu wymagają konfiguracji Teams: " + ", ".join(missing) + "."
        )


def _build_token_provider(push: TeamsPushSettings) -> Any:
    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc
    return build_token_provider(push)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    main()
