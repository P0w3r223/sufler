"""Preflight drzwi Jira "moje zadania" (ADR 0054) — READ-ONLY wobec Jiry.

Zastępuje dawny preflight pollera (ADR 0030/0033), usunięty razem z mostem push/ingest (ADR 0054).
Weryfikuje jedyną pozostałą zdolność: auth do żywej instancji (``/myself``), próbne zapytanie
"moje zadania" dla wskazanego konta i — opcjonalnie — rozwiązanie tożsamości nadawcy (AAD → konto
Jira) z tej samej mapy co autoryzacja notatek (ADR 0042). Nic nie tworzy/zmienia w Jirze.

Uruchomienie (w środowisku projektu, z wypełnionym ``.env``)::

    uv run --no-sync python deploy/jira/preflight.py --account mikolaj@example.org
    uv run --no-sync python deploy/jira/preflight.py --account mikolaj@example.org \
        --aad <aad-user-id>

Bez ``--account`` używane jest ``SUFLER_JIRA_MY_ACCOUNT`` (principal serwera MCP, ADR 0054).
Kody wyjścia: ``0`` = auth + odczyt OK; ``1`` = konfiguracja/auth/odczyt odrzucone (stderr).

Zakres sprawdzenia ``--aad`` jest węższy, niż bywał opisywany: rozwiązuje AAD id przez plik mapy
i NIE pyta o to konto Jiry. Osoba zmapowana bez ``jira_user`` (wpis „tylko Teams", ADR 0070) daje
kod ``1`` — bo ``--aad`` jest pytaniem „czy ta osoba dostanie Jirę", a odpowiedź brzmi „nie".
Nie jest to wezwanie do uzupełnienia mapy: patrz komunikat w ``_check_identity``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from sufler.adapters.inbound import env
from sufler.adapters.outbound.jira_api import build_jira_client
from sufler.config import JiraSettings, TeamsGraphSettings
from sufler.core.application.my_jira_tasks import MyJiraTasksService
from sufler.core.errors import JiraReadError


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
    from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory

    try:
        directory = YamlIdentityDirectory(identities_path)
    except ValueError as exc:
        _log(f"  !! Mapa tożsamości jest zła: {exc}")
        return False
    person = directory.resolve_by_aad_user_id(aad_user_id)
    if person is None:
        _log("  !! Nie znaleziono (fail-closed) — /moje-zadania odpowie odmową temu AAD id.")
        return False
    if not person.jira_user:
        # Wpis „tylko Teams" (ADR 0070 §1). Do 2026-09-04 ta gałąź była nieosiągalna, bo mapa
        # nie wpuszczała pustego pola — i bez niej ten preflight meldowałby OK z kodem 0 oraz
        # `jira_user=''`, bo `_mask("")` zwraca pusty napis. Fałszywa ZIELEŃ narzędzia
        # weryfikacyjnego jest gorsza od jego braku: `deploy/` jest poza `mypy` (files=["src"])
        # i nie ma tu żadnej innej bramki.
        _log(
            f"  !! {person.display_name!r} JEST rozpoznanym członkiem pionu, ale NIE MA konta "
            "Jira — /moje-zadania odmówi, a narzędzie Jira nie wejdzie do katalogu tej osoby "
            "(ADR 0070 §3)."
        )
        _log(
            "     To stan LEGALNY, nie błąd konfiguracji: NIE dopisuj 'jira_user' do jej wpisu. "
            "Cudze konto pokazałoby jej CUDZE zadania (ADR 0070 §4)."
        )
        return False
    _log(f"  OK: rozwiązano na jira_user={_mask(person.jira_user)!r} ({person.display_name!r}).")
    return True


def _run(*, account: str, aad_user_id: str, limit: int) -> int:
    env.load_dotenv()
    settings = JiraSettings.from_env()
    _log(f"[cfg] deployment={settings.deployment} base_url={settings.base_url}")
    if not (settings.base_url and settings.token):
        _log("!! Brak SUFLER_JIRA_BASE_URL/SUFLER_JIRA_TOKEN w środowisku/.env.")
        return 1
    assignee = account or settings.my_account
    if not assignee:
        _log("!! Podaj --account albo ustaw SUFLER_JIRA_MY_ACCOUNT (konto do testu odczytu).")
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
        help="Konto Jira (login/e-mail/accountId) do testu; domyślnie SUFLER_JIRA_MY_ACCOUNT.",
    )
    parser.add_argument(
        "--aad", default="", help="Opcjonalnie: aad_user_id do sprawdzenia mapy tożsamości."
    )
    parser.add_argument("--limit", type=int, default=5, help="Ile zadań pokazać (domyślnie 5).")
    args = parser.parse_args(argv)
    return _run(account=args.account, aad_user_id=args.aad, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
