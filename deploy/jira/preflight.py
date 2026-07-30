"""Preflight drzwi Jira "moje zadania" (ADR 0054) — READ-ONLY wobec Jiry.

Zastępuje dawny preflight pollera (ADR 0030/0033), usunięty razem z mostem push/ingest (ADR 0054).
Weryfikuje jedyną pozostałą zdolność: auth do żywej instancji (``/myself``), próbne zapytanie
"moje zadania" dla wskazanego konta i — opcjonalnie — rozwiązanie tożsamości nadawcy (AAD → konto
Jira) z tej samej mapy co autoryzacja notatek (ADR 0042). Nic nie tworzy/zmienia w Jirze.

Uruchomienie (w środowisku projektu, z wypełnionym ``.env``)::

    uv run --no-sync python deploy/jira/preflight.py --account mikolaj@example.org
    uv run --no-sync python deploy/jira/preflight.py --account mikolaj@example.org --aad <aad-user-id>

Bez ``--account`` używane jest ``WORKMATE_JIRA_MY_ACCOUNT`` (principal serwera MCP, ADR 0054).
Kody wyjścia: ``0`` = auth + odczyt OK; ``1`` = konfiguracja/auth/odczyt odrzucone (stderr).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from workmate.adapters.inbound import env
from workmate.adapters.outbound.jira_api import build_jira_client
from workmate.config import JiraSettings, TeamsGraphSettings
from workmate.core.application.my_jira_tasks import MyJiraTasksService
from workmate.core.errors import JiraReadError


def _log(msg: str = "") -> None:
    """Wypis na stderr — stdout zostaje czysty (spójnie z konwencją narzędzi deploy)."""
    print(msg, file=sys.stderr)


def _mask(value: str) -> str:
    """Skróć identyfikator do podglądu (np. accountId) bez ujawniania całości w logu."""
    return value if len(value) <= 12 else f"{value[:8]}…{value[-4:]}"


def _check_auth(client: Any) -> bool:
    """Sprawdź, że token/PAT jest ważny i coś odpowiada z ``/myself``."""
    try:
        account = client.authenticated_account()
    except httpx.HTTPStatusError as exc:
        _log(f"  !! Jira odrzuciła auth: HTTP {exc.response.status_code}.")
        return False
    except httpx.HTTPError as exc:
        _log(f"  !! Nie udało się połączyć z Jirą: {exc}.")
        return False
    _log(f"[auth] konto tokenu = {_mask(account)!r}")
    if not account:
        _log("  !! Brak konta z /myself — auth prawdopodobnie odrzucone.")
        return False
    _log("  OK: token/PAT ważny.")
    return True


def _check_my_tasks(client: Any, *, assignee: str, base_url: str, limit: int) -> bool:
    """Wykonaj prawdziwe zapytanie "moje zadania" dla ``assignee`` i pokaż kilka wyników."""
    _log(f"\n[moje zadania] assignee={_mask(assignee)!r}")
    service = MyJiraTasksService(client, assignee=assignee, base_url=base_url)
    try:
        tasks = service.my_open_tasks()
    except JiraReadError as exc:
        _log(f"  !! {exc}")
        return False
    _log(f"  {len(tasks)} otwartych zadań (pokazuję max {limit}):")
    for task in tasks[:limit]:
        due = f" termin={task.due_date}" if task.due_date else ""
        _log(f"    {task.key:10} [{task.priority or '—'}] {task.status!r}{due}  {task.summary}")
    return True


def _check_identity(aad_user_id: str, identities_path: Any) -> bool:
    """Rozwiąż przykładowy ``aad_user_id`` przez tę samą mapę co autoryzacja notatek (ADR 0042)."""
    _log(f"\n[tożsamość] aad_user_id={_mask(aad_user_id)!r} przez mapę {identities_path}")
    if not identities_path.is_file():
        _log(f"  !! Mapa tożsamości nie istnieje: {identities_path}.")
        return False
    from workmate.adapters.outbound.graph_identity_directory import YamlIdentityDirectory

    try:
        directory = YamlIdentityDirectory(identities_path)
    except ValueError as exc:
        _log(f"  !! Mapa tożsamości jest zła: {exc}")
        return False
    person = directory.resolve_by_aad_user_id(aad_user_id)
    if person is None:
        _log("  !! Nie znaleziono (fail-closed) — /moje-zadania odpowie odmową temu AAD id.")
        return False
    _log(f"  OK: rozwiązano na jira_user={_mask(person.jira_user)!r} ({person.display_name!r}).")
    return True


def _run(*, account: str, aad_user_id: str, limit: int) -> int:
    env.load_dotenv()
    settings = JiraSettings.from_env()
    _log(f"[cfg] deployment={settings.deployment} base_url={settings.base_url}")
    if not (settings.base_url and settings.token):
        _log("!! Brak WORKMATE_JIRA_BASE_URL/WORKMATE_JIRA_TOKEN w środowisku/.env.")
        return 1
    assignee = account or settings.my_account
    if not assignee:
        _log("!! Podaj --account albo ustaw WORKMATE_JIRA_MY_ACCOUNT (konto do testu odczytu).")
        return 1

    with httpx.Client(timeout=30) as http:
        client = build_jira_client(http, settings)
        if not _check_auth(client):
            return 1
        if not _check_my_tasks(client, assignee=assignee, base_url=settings.base_url, limit=limit):
            return 1
        if aad_user_id:
            identities = TeamsGraphSettings.from_env().meeting_note_identities
            if not _check_identity(aad_user_id, identities):
                return 1

    _log("\nPreflight OK — odczyt 'moje zadania' (ADR 0054) gotowy.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jira-preflight",
        description="Read-only preflight drzwi Jira 'moje zadania' (ADR 0054).",
    )
    parser.add_argument(
        "--account",
        default="",
        help="Konto Jira (login/e-mail/accountId) do testu; domyślnie WORKMATE_JIRA_MY_ACCOUNT.",
    )
    parser.add_argument(
        "--aad", default="", help="Opcjonalnie: aad_user_id do sprawdzenia mapy tożsamości."
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Ile zadań pokazać (domyślnie 5)."
    )
    args = parser.parse_args(argv)
    return _run(account=args.account, aad_user_id=args.aad, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
