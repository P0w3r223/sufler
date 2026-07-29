"""Preflight drzwi Jira (live-smoke poz. 11, ADR 0030/0033) — READ-ONLY wobec Jiry.

Zbiera w jednym poleceniu weryfikacje, których pakiet ``pytest`` świadomie nie robi
(cała automatyka biegnie na atrapach): łączność + auth do ŻYWEJ instancji, zgodność
``WORKMATE_JIRA_SELF_ACCOUNT`` z realnym kontem tokenu (fail-fast strażnika pętli) oraz pełny
pipeline pollera ``search -> select_events -> atrybucja -> ingest`` puszczony do TYMCZASOWEGO
``events.db``. Nic nie tworzy/zmienia w Jirze i NIE rusza realnego ``~/.workmate/events.db``.

Uruchomienie (w środowisku projektu, z wypełnionym ``.env``)::

    uv run --no-sync python deploy/jira/preflight.py

Kody wyjścia: ``0`` = auth + odczyt OK; ``1`` = konfiguracja/auth odrzucone (szczegóły na stderr).

Uwaga do interpretacji „0 przyjętych": jeśli wszystkie zmiany w nasłuchiwanym projekcie pochodzą
od konta tokenu (``SELF_ACCOUNT``), self-skip poprawnie je pomija. Preflight pokazuje wtedy OBIE
liczby — z self-skip i bez — żeby odróżnić poprawny strażnik pętli od gubienia zdarzeń. Realny,
niepominięty ingest wymaga zmiany w projekcie z INNEGO konta Jira niż token.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from workmate.adapters.inbound import env
from workmate.adapters.inbound.jira import selection
from workmate.adapters.inbound.jira.app import _build_project_map
from workmate.adapters.inbound.jira.poller import JiraPoller
from workmate.adapters.outbound.jira_api import build_jira_client
from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.config import JiraSettings, Settings
from workmate.core.application.events import EventService


def _log(msg: str = "") -> None:
    """Wypis na stderr — stdout zostaje czysty (spójnie z konwencją narzędzi deploy)."""
    print(msg, file=sys.stderr)


def _mask(value: str) -> str:
    """Skróć identyfikator do podglądu (np. accountId) bez ujawniania całości w logu."""
    return value if len(value) <= 12 else f"{value[:8]}…{value[-4:]}"


def _check_auth(client: Any, settings: JiraSettings) -> bool:
    """Sprawdź auth i inwariant self-skip; zwróć czy konfiguracja jest spójna."""
    account = client.authenticated_account()
    _log(f"[auth] konto tokenu = {_mask(account)!r}")
    if not account:
        _log("  !! Brak konta z /myself — auth prawdopodobnie odrzucone.")
        return False
    if settings.self_account and settings.self_account != account:
        _log(
            f"  !! ROZJAZD: WORKMATE_JIRA_SELF_ACCOUNT={_mask(settings.self_account)!r} "
            "!= konto tokenu — strażnik pętli self-skip NIE zadziała (fail-fast pollera)."
        )
        return False
    _log("  OK: SELF_ACCOUNT zgodne z kontem tokenu (strażnik pętli spójny).")
    return True


def _check_read(client: Any, settings: JiraSettings, *, limit: int) -> list[dict[str, Any]]:
    """Wykonaj JQL nasłuchu (bez watermarku) i wypisz kilka najnowszych zgłoszeń."""
    jql = f"project in ({', '.join(settings.watch_projects)}) ORDER BY updated DESC"
    _log(f"\n[read] JQL = {jql!r}")
    issues = client.search_issues(jql, max_results=limit)
    _log(f"  zwrócono {len(issues)} zgłoszeń (max {limit}):")
    for issue in issues:
        fields = issue.get("fields") or {}
        project = fields.get("project") or {}
        status = fields.get("status") or {}
        _log(
            f"    {str(issue.get('key')):10} "
            f"[{project.get('key')}/{project.get('name')}] "
            f"status={status.get('name')!r}"
        )
    return issues


async def _check_pipeline(client: Any, settings: JiraSettings, project_map: dict[str, str]) -> None:
    """Puść pełny pipeline pollera do TYMCZASOWEGO events.db; zaraportuj ingest/dedup/self-skip."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="jira_preflight_"))
    try:
        events = EventService(SqliteEventStore(tmp_dir / "events.db"))
        state: dict[str, Any] = {}  # pusty watermark = pobierz całą historię nasłuchu
        poller = JiraPoller(
            client,
            events,
            base_url=settings.base_url,
            watch_projects=settings.watch_projects,
            state=state,
            persist=lambda _current: None,
            poll_interval=settings.poll_interval_s,
            per_page=settings.per_page,
            self_account=settings.self_account,
            project_map=project_map,
        )
        first = await poller.poll_once()
        state["issues_since"] = ""  # to samo okno — czysty pokaz dedupu
        second = await poller.poll_once()
        _log(f"\n[pipeline] poll #1 przyjęto {first}, poll #2 przyjęto {second} (0 = dedup OK)")

        raw = client.search_issues(
            selection.build_jql(settings.watch_projects, ""), max_results=settings.per_page
        )
        without_skip = selection.select_events(
            raw, self_account="", base_url=settings.base_url, project_map=project_map, since=""
        )
        kinds = Counter(e.kind for e in without_skip)
        projects = Counter(e.project for e in without_skip)
        _log(f"  bez self-skip: {len(without_skip)} zdarzeń — rodzaje={dict(kinds)}")
        _log(f"  atrybucja project (z rejestru): {dict(projects)}")
        if without_skip and first == 0:
            _log(
                "  (0 przyjętych mimo zdarzeń = poprawny self-skip: wszystkie autorstwa konta "
                "tokenu. Niepominięty ingest wymaga zmiany z INNEGO konta Jira.)"
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _run(limit: int) -> int:
    env.load_dotenv()
    settings = JiraSettings.from_env()
    settings.validate()
    _log(f"[cfg] deployment={settings.deployment} base_url={settings.base_url}")
    _log(f"[cfg] watch_projects={settings.watch_projects} email={settings.email}")
    project_map = _build_project_map(Settings.from_env().projects_registry, settings.watch_projects)
    _log(f"[cfg] mapa projektów (Jira->WorkMate) = {project_map or '{} (brak atrybucji!)'}")

    with httpx.Client(timeout=30) as http:
        client = build_jira_client(http, settings)
        try:
            if not _check_auth(client, settings):
                return 1
            _check_read(client, settings, limit=limit)
            await _check_pipeline(client, settings, project_map)
        except httpx.HTTPStatusError as exc:
            _log(f"\n!! Jira odrzuciła żądanie: HTTP {exc.response.status_code}")
            _log(f"   URL: {exc.request.url}")
            _log("   Sprawdź WORKMATE_JIRA_TOKEN/EMAIL (Cloud: Basic) i uprawnienia do projektu.")
            return 1
    _log("\nPreflight OK — read-path poz. 11 gotowy. Push do Teams (poz. 12-15) = osobny krok.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jira-preflight",
        description="Read-only preflight drzwi Jira (live-smoke poz. 11).",
    )
    parser.add_argument(
        "--limit", type=int, default=5, help="Ile najnowszych zgłoszeń pokazać (domyślnie 5)."
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
