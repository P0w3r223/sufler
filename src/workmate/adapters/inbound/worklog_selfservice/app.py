"""Drzwi worklog self-service (ADR 0038) — entry point ``workmate-worklog-selfservice``.

Interaktywny, jednoosobowy odpowiednik wsadowego ``workmate-worklogi``: bierze PRZYSŁANY wynik
``claude_summary`` jednej osoby, dobiera jej REALNE godziny z Microsoft Shifts, składa arkusz
importu WorklogPRO i (opcjonalnie) odsyła osobie na Teams prywatną wiadomość ze ścieżką pliku —
dostawę pliku ZAŁĄCZNIKIEM projektuje ADR 0026/0027 (M4, wymaga scope admina), tu jest FALLBACK
ścieżką, jak w drzwiach wsadowych (ADR 0035 § how-to).

**Tryb pilotażu = operator (ADR 0037).** Live, uwierzytelnione zbieranie z czatu 1:1 (nadawca =
tożsamość) projektuje ADR 0037/0038 (M5); do tego czasu operator podaje submisję (``--submission``)
i osobę (``--source-id``), a katalog tożsamości vouchuje za mapowanie. Wysyłka rusza dopiero z
``--send`` (domyślnie PRÓBNY: arkusz powstaje, wiadomość nie wychodzi).

Uruchomienie: ``uv run workmate-worklog-selfservice --login`` (raz), potem
``... --submission plik.json --source-id EMP-1 [--send]``. Importy ``httpx``/``msal``/``openpyxl``
są LENIWE — brak extra ``worklogi`` kończy się czytelnym komunikatem.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from workmate.adapters.inbound import env
from workmate.adapters.inbound.worklog_selfservice import state as state_store
from workmate.config import Settings, TeamsPushSettings, WorklogiSettings
from workmate.core.application.selfservice_worklog import SelfServiceWorklog, handle_submission

logger = logging.getLogger(__name__)

_MISSING_EXTRA = (
    "Drzwi self-service wymagają extra 'worklogi'. Zainstaluj: uv sync --extra worklogi"
)


def main() -> None:  # pragma: no cover - kompozycja I/O; logika w handlerze jest testowana
    """Uruchom drzwi self-service: jedna submisja → arkusz + (opcjonalnie) wiadomość na Teams."""
    logging.basicConfig(level=logging.INFO)
    env.load_dotenv()
    args = _parse_args()

    settings = WorklogiSettings.from_env()
    settings.validate(data_dir=Settings.from_env().data_dir)
    push = TeamsPushSettings.from_env()
    _require_teams(push)

    token_provider = _build_token_provider(push)
    if args.login:
        token_provider()
        logger.info("Zalogowano — token w cache %s.", push.token_cache_path)
        return

    if not args.submission or not args.source_id:
        raise SystemExit("Podaj --submission <plik.json> ORAZ --source-id <ID> (albo --login).")
    if args.send and not settings.headers_confirmed:
        raise SystemExit(
            "Wysyłka wymaga WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true — nagłówki WorklogPRO to "
            "HIPOTEZA, dopóki nie potwierdzisz ich z kreatora importu (ADR 0035)."
        )
    _run_once(settings, push, token_provider, args)


def _parse_args() -> argparse.Namespace:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Worklog self-service → Teams (ADR 0038).")
    parser.add_argument("--submission", help="ścieżka do JSON-a z claude_summary")
    parser.add_argument("--source-id", help="source_id osoby z mapy tożsamości (operator vouchuje)")
    parser.add_argument("--send", action="store_true", help="wyślij wiadomość (domyślnie próbny)")
    parser.add_argument("--login", action="store_true", help="jednorazowa zgoda device-code")
    return parser.parse_args()


def _run_once(
    settings: WorklogiSettings, push: TeamsPushSettings, token: Any, args: argparse.Namespace
) -> None:  # pragma: no cover - wiring Graph/openpyxl; testy pokrywają handle_submission
    """Wczytaj submisję, złóż arkusz nadawcy i (z ``--send``) odeślij wiadomość ze ścieżką."""
    import httpx

    from workmate.adapters.outbound.graph_identity_directory import (
        GraphIdentityDirectory,
        fetch_team_members,
    )
    from workmate.adapters.outbound.graph_shift_source import GraphShiftSource
    from workmate.adapters.outbound.openpyxl_sheet_writer import OpenpyxlSheetWriter

    tz = ZoneInfo(settings.tz_name)
    payload = _load_submission(Path(args.submission))

    with httpx.Client(timeout=30) as sync_http:
        members = fetch_team_members(sync_http, settings.team_id, token())
    identities = GraphIdentityDirectory(settings.identities_path, members)
    person = identities.resolve(args.source_id)
    if person is None:
        raise SystemExit(
            f"Nieznany source_id {args.source_id!r} albo osoba spoza zespołu — "
            "uzupełnij identities.yaml (fail-closed)."
        )

    worklog = SelfServiceWorklog(
        GraphShiftSource(token, settings.team_id),
        OpenpyxlSheetWriter(),
        output_dir=str(settings.output_dir),
        tz=tz,
        fallback_issue=settings.fallback_issue,
        start_hour=settings.start_hour,
        max_minutes_per_day=round(settings.max_hours_per_day * 60),
    )
    outcome = handle_submission(worklog, person, payload)
    logger.info(
        "Wynik %s: %s, arkusz=%s, %d min.",
        person.source_id,
        outcome.reason,
        outcome.file_path or "(brak)",
        outcome.total_minutes,
    )

    if not args.send:
        logger.info("PRÓBNY (bez --send): nie wysyłam. Obejrzyj arkusz przed wysyłką.")
        return

    state_path = settings.state_path.with_name("worklog_selfservice_state.json")
    saved = state_store.load(state_path)
    sid = state_store.submission_key(person.source_id, outcome.week_label)
    if outcome.is_success and sid in saved:
        logger.info("Submisja %s już obsłużona (%s) — nie wysyłam ponownie.", sid, saved[sid])
        return

    _send_html(token, person.aad_user_id, outcome.reply_html)
    if outcome.is_success:
        saved[sid] = _now().isoformat()
        state_store.save(state_path, saved)
    logger.info("Wysłano wiadomość do %s.", person.display_name or person.source_id)


def _send_html(token: Any, aad_user_id: str, html: str) -> None:  # pragma: no cover
    """Most sync→async: klient POWSTAJE I GINIE w jednym ``asyncio.run`` (wzorzec z worklogi)."""
    import httpx

    from workmate.adapters.outbound.graph_teams_notifier import HttpxTeamsNotifier

    async def _send() -> None:
        async with httpx.AsyncClient(timeout=30) as async_http:
            await HttpxTeamsNotifier(async_http, token).send_chat_html(aad_user_id, html)

    asyncio.run(_send())


def _load_submission(path: Path) -> Any:  # pragma: no cover
    """Wczytaj i zdekoduj JSON submisji; błąd = czytelny ``SystemExit`` (nie traceback)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Brak pliku submisji: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise SystemExit(f"Nie mogę odczytać JSON-a submisji {path}: {exc}") from exc


def _require_teams(push: TeamsPushSettings) -> None:  # pragma: no cover
    """Bez tożsamości Graph nie ma jak czytać Shifts ani wysłać wiadomości — fail-fast."""
    missing = [
        name
        for name, value in (
            ("WORKMATE_TEAMS_PUSH_CLIENT_ID", push.client_id),
            ("WORKMATE_TEAMS_PUSH_TENANT_ID", push.tenant_id),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Drzwi self-service wymagają konfiguracji Teams: " + ", ".join(missing))


def _build_token_provider(push: TeamsPushSettings) -> Any:  # pragma: no cover
    try:
        from workmate.adapters.inbound.teams_graph.auth import build_token_provider
    except ImportError as exc:
        raise SystemExit(_MISSING_EXTRA) from exc
    return build_token_provider(push)


def _now() -> datetime:  # pragma: no cover
    return datetime.now(tz=timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    main()
