"""Drzwi proaktywnego cotygodniowego digestu zmian (ADR 0053, F6) — ``workmate-teams-digest``.

W poniedziałek o wyznaczonej godzinie: digest zmian z ostatnich ``window_days`` (reuse
``ChangeDigestService``, F5) → prywatna wiadomość Teams do każdego odbiorcy z jawnej listy.
Struktura schedulera jest LUSTREM ``worklogi`` (ADR 0035): tryby ``--once``/``--login``/pętla,
``next_run`` + puls żywotności + single-instance lock, nadrabianie pominiętego terminu z sufitem,
``_safe_run_once`` (awaria nie kładzie pętli) i dedup „kto już dostał" w atomowym pliku stanu.

Dwustopniowa bramka: domyślnie WYŁĄCZONY (``enabled``) i PRÓBNY (``dry_run``) — przebieg renderuje i
loguje digest, nie wysyła i nie zapisuje stanu. Realna wysyłka wymaga ``enabled=true`` I
``dry_run=false``. Importy ``httpx``/``msal`` są LENIWE — brak extra kończy czytelnym komunikatem.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from workmate.adapters.inbound import env
from workmate.adapters.inbound.heartbeat import heartbeat_path, write_heartbeat
from workmate.adapters.inbound.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)
from workmate.adapters.inbound.teams_digest import state as state_store
from workmate.adapters.inbound.teams_digest.delivery import deliver_weekly_digest
from workmate.config import TeamsDigestSettings, TeamsPushSettings, require_writable
from workmate.core.domain.week import next_run, previous_run, week_label

if TYPE_CHECKING:
    from workmate.adapters.inbound.teams_digest.delivery import DigestRunReport
    from workmate.core.application.events import EventService

logger = logging.getLogger(__name__)

_MISSING_EXTRA = (
    "Drzwi digestu wymagają extra 'teams-graph'. Zainstaluj: uv sync --extra teams-graph"
)
# Ile ostatnich tygodni trzymamy w stanie — nadrabianie musi widzieć, kto już dostał.
_KEEP_WEEKS = 6
# Sufit jednej drzemki pętli — budzi się regularnie, reaguje na sygnał i przelicza termin.
_MAX_SLEEP_S = 900


def main() -> None:
    """Uruchom drzwi digestu: jeden przebieg, pętla albo jednorazowe logowanie."""
    env.load_dotenv()
    env.configure_logging()
    args = _parse_args()

    settings = TeamsDigestSettings.from_env()
    settings.validate()
    if not settings.enabled:
        raise SystemExit(
            "WORKMATE_TEAMS_DIGEST_ENABLED=false — drzwi digestu są wyłączone (ADR 0053)."
        )
    push = TeamsPushSettings.from_env()
    _require_teams(push)

    token_provider = _build_token_provider(push)
    if args.login:
        token_provider()  # jednorazowa zgoda device-code (cache tokenu)
        logger.info("Zalogowano — token w cache %s.", push.token_cache_path)
        return

    # R/L1: stan tygodniowy (dedup „kto już dostał") MUSI być zapisywalny — inaczej po restarcie
    # ktoś dostaje digest drugi raz. Fail-fast, nim weźmiemy lock i ruszymy przebieg.
    require_writable(settings.state_path, "WORKMATE_TEAMS_DIGEST_STATE")
    try:
        with acquire_single_instance_lock(settings.state_path):
            if args.once:
                _safe_run_once(settings, token_provider, as_of=None)
            else:
                _run_forever(settings, token_provider)
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cotygodniowy digest zmian → Teams DM (ADR 0053).")
    parser.add_argument("--once", action="store_true", help="jeden przebieg i wyjście")
    parser.add_argument("--login", action="store_true", help="jednorazowa zgoda device-code")
    return parser.parse_args()


def _install_stop_flag() -> threading.Event:
    """Handler SIGTERM/SIGINT ustawiający flagę zatrzymania (graceful shutdown, jak worklogi)."""
    stop = threading.Event()

    def _request_stop(_signum: int, _frame: Any) -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError):  # poza wątkiem głównym → domyślne zamknięcie
            signal.signal(sig, _request_stop)
    return stop


def _run_forever(
    settings: TeamsDigestSettings, token: Any, *, stop: threading.Event | None = None
) -> None:
    """Pętla: nadrób pominięty termin, potem czekaj na kolejne poniedziałki (lustro worklogi)."""
    if stop is None:
        stop = _install_stop_flag()
    tz = ZoneInfo(settings.tz_name)
    hb = heartbeat_path(settings.state_path)
    write_heartbeat(hb)
    missed = _missed_deadline(settings, tz)
    if missed is not None and not stop.is_set():
        logger.info("Wykryto pominięty termin (%s) — nadrabiam digest za jego tydzień.", missed)
        _safe_run_once(settings, token, as_of=missed)
    while not stop.is_set():
        target = next_run(
            _now(),
            tz=tz,
            weekday=settings.run_weekday,
            hour=settings.run_hour,
            minute=settings.run_minute,
        )
        logger.info("Następny digest: %s.", target)
        while _now() < target and not stop.is_set():
            write_heartbeat(hb)  # luka między pulsami ≤ _MAX_SLEEP_S
            # ``stop.wait`` zamiast ``time.sleep``: po PEP 475 ``sleep`` WZNAWIA się po obsłudze
            # sygnału, więc handler SIGTERM ustawiał flagę, a pętla i tak dospała do końca
            # drzemki — do 15 minut, przy ``stop_grace_period`` 45 s. Log mówił „zatrzymanie na
            # sygnał", a proces szedł pod SIGKILL. ``Event.wait`` wraca NATYCHMIAST po ``set``.
            stop.wait(min(_MAX_SLEEP_S, max(1.0, (target - _now()).total_seconds())))
        if stop.is_set():
            break
        _safe_run_once(settings, token, as_of=target)
        write_heartbeat(hb)
    logger.info("Drzwi digestu: zatrzymanie na sygnał, stan utrwalony.")


def _safe_run_once(settings: TeamsDigestSettings, token: Any, *, as_of: datetime | None) -> None:
    """Przebieg, którego awaria NIE kładzie pętli — proces ma dożyć następnego poniedziałku."""
    try:
        _run_once(settings, token, as_of=as_of)
    except Exception:
        logger.exception("Przebieg digestu (%s) nie powiódł się — czekam na kolejny termin.", as_of)


def _missed_deadline(settings: TeamsDigestSettings, tz: ZoneInfo) -> datetime | None:
    """Pominięty termin do nadrobienia (albo ``None``); tydzień już w stanie → nie nadrabiamy."""
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
        return None
    saved = state_store.load(settings.state_path)
    label = week_label(last.astimezone(tz))
    return None if any(k.startswith(f"{label}:") for k in saved) else last


def _run_once(
    settings: TeamsDigestSettings, token: Any, *, as_of: datetime | None = None
) -> DigestRunReport:
    """Wykonaj JEDEN przebieg: złóż digest okna i wyślij odbiorcom nieobsłużonym w tym tygodniu.

    ``as_of`` to CHWILA terminu (nadrabianie liczy tydzień z terminu, nie z zegara). Okno to
    ostatnie ``window_days`` dni przed terminem; etykieta tygodnia = klucz dedupu w stanie.
    """
    from workmate.core.application.change_digest import ChangeDigestService

    tz = ZoneInfo(settings.tz_name)
    moment = as_of if as_of is not None else _now()
    local = moment.astimezone(tz)
    label = week_label(local)
    since = local.date() - timedelta(days=settings.window_days)
    change_service = ChangeDigestService(_events_service())
    saved = state_store.load(settings.state_path)
    already = {k.split(":", 1)[1] for k in saved if k.startswith(f"{label}:")}

    def send(recipient: str, text: str) -> None:
        if settings.dry_run:
            logger.info(
                "[dry-run] digest %s do %s… (%d znaków) — nie wysyłam.",
                label,
                recipient[:8],
                len(text),
            )
            return
        _send_chat(token, recipient, text)

    def mark(recipient: str) -> None:
        if settings.dry_run:
            return  # tryb próbny nie utrwala stanu — artefakty można oglądać wielokrotnie
        saved[state_store.key(label, recipient)] = _now().isoformat()
        state_store.save(settings.state_path, saved)  # po KAŻDEJ osobie (at-least-once)

    report = deliver_weekly_digest(
        change_service,
        since=since,
        week_label=label,
        recipients=settings.recipients,
        already=already,
        send=send,
        mark=mark,
    )
    _prune_state(settings, saved, local)
    logger.info(
        "Digest %s: wysłano %d, pominięto %d (już), nieudanych %d%s%s.",
        report.week_label,
        len(report.sent),
        len(report.already),
        len(report.failed),
        " (pusty tydzień)" if report.skipped_empty else "",
        " [dry-run — nic nie wysłano]" if settings.dry_run else "",
    )
    return report


def _send_chat(token: Any, recipient: str, text: str) -> None:
    """Most sync→async: przebieg jest wsadowy, adapter Teams asynchroniczny.

    Klient POWSTAJE I GINIE wewnątrz jednego ``asyncio.run`` (wzorzec worklogi): współdzielony
    trzymałby połączenie przypięte do już zamkniętej pętli, przez co co drugi DM by nie wyszedł.
    Kilku odbiorców raz w tygodniu — koszt zestawienia połączenia bez znaczenia.
    """
    try:
        import httpx

        from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc

    async def _send() -> None:
        async with httpx.AsyncClient(timeout=30) as async_http:
            await HttpxTeamsNotifier(async_http, token).send_chat(recipient, text)

    asyncio.run(_send())


def _events_service() -> EventService | None:
    """``EventService`` nad wspólnym ``events.db`` TYLKO gdy plik istnieje (most zdarzeń w użyciu).

    Bez pliku ``None`` — ``ChangeDigestService`` zwróci pusty digest (dozwolona degradacja). Drzwi
    nie tworzą pustego ``events.db`` tylko pod odczyt.
    """
    from pathlib import Path

    from workmate.adapters.outbound.sqlite_events import SqliteEventStore
    from workmate.config import EventsSettings
    from workmate.core.application.events import EventService

    db_path = EventsSettings.from_env().db_path
    if not Path(str(db_path)).expanduser().exists():
        return None
    return EventService(SqliteEventStore(db_path))


def _prune_state(settings: TeamsDigestSettings, saved: dict[str, str], local: datetime) -> None:
    """Przytnij stan do kilku ostatnich tygodni — plik nie ma rosnąć bez końca (jak worklogi)."""
    keep = tuple(week_label(local - timedelta(days=7 * i)) for i in range(_KEEP_WEEKS))
    pruned = state_store.prune(saved, keep_weeks=keep)
    if pruned != saved and not settings.dry_run:
        state_store.save(settings.state_path, pruned)


def _require_teams(push: TeamsPushSettings) -> None:
    """Bez tożsamości Graph nie ma jak wysłać DM-a — fail-fast z listą braków."""
    missing = [
        name
        for name, value in (
            ("WORKMATE_TEAMS_PUSH_CLIENT_ID", push.client_id),
            ("WORKMATE_TEAMS_PUSH_TENANT_ID", push.tenant_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Drzwi digestu wymagają konfiguracji Teams: " + ", ".join(missing) + ".")


def _build_token_provider(push: TeamsPushSettings) -> Any:
    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc
    return build_token_provider(push)


def _now() -> datetime:
    return datetime.now(tz=UTC)


if __name__ == "__main__":  # pragma: no cover
    main()
