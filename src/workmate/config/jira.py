"""Ustawienia odczytu Jiry (dual-provider ``server``/``cloud``, ADR 0059)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Warianty wdrożenia Jiry (ADR 0033): ``server`` = Server/Data Center (PAT Bearer, REST v2,
# paginacja startAt); ``cloud`` = Jira Cloud (Basic email+API-token, REST v3, search/jql).
# Domyślnie ``server`` — wstecznie zgodne.
JIRA_DEPLOYMENTS = ("server", "cloud")


@dataclass(frozen=True)
class JiraSettings:
    """Konfiguracja odczytu Jiry (Server/DC lub Cloud, ADR 0030/0033/0054) — WYŁĄCZNIE odczyt.

    ``deployment`` wybiera wariant (ADR 0033): ``server`` (Server/Data Center — PAT Bearer, REST v2)
    lub ``cloud`` (Jira Cloud — Basic ``email``+API-token, REST v3, ``search/jql``). Domyślnie
    ``server`` (wstecznie zgodne). ``email`` jest wymagany tylko na Cloud (konto Basic-auth); na
    Server/DC pozostaje pusty. ``token`` to SEKRET (``repr=False``, env ``WORKMATE_JIRA_TOKEN`` —
    PAT na Server/DC, API token na Cloud) — nigdy w repo/``data/``.

    Most push/ingest (ADR 0030) i zapis/tranzycja (ADR 0031/0032) zostały USUNIĘTE (ADR 0054) —
    jedyna zdolność to "moje zadania": ``my_account`` (login/e-mail/accountId JEDNEGO, z góry
    skonfigurowanego operatora) zasila narzędzie na SERWERZE MCP (stdio, Claude Code/CLI — brak
    tożsamości Teams; patrz ``server._my_jira_tasks_service_if_present``). Na drzwiach Teams
    tożsamość rozwiązuje się inaczej — z mapy AAD→Jira (``teams_graph.app``), niezależnie od tego
    pola.
    """

    base_url: str = ""
    token: str = field(default="", repr=False)
    deployment: str = "server"
    email: str = ""
    my_account: str = ""

    @classmethod
    def from_env(cls) -> JiraSettings:
        return cls(
            base_url=os.environ.get("WORKMATE_JIRA_BASE_URL", "").rstrip("/"),
            token=os.environ.get("WORKMATE_JIRA_TOKEN", ""),
            deployment=os.environ.get("WORKMATE_JIRA_DEPLOYMENT", "server").strip().lower(),
            email=os.environ.get("WORKMATE_JIRA_EMAIL", "").strip(),
            my_account=os.environ.get("WORKMATE_JIRA_MY_ACCOUNT", "").strip(),
        )

    def validate_limits(self) -> None:
        """Kontrola wariantu wdrożenia — NIEZALEŻNA od tego, kto buduje klienta Jiry."""
        deployment = self.deployment.strip().lower()
        if deployment not in JIRA_DEPLOYMENTS:
            raise ValueError(
                "WORKMATE_JIRA_DEPLOYMENT musi być 'server' lub 'cloud', jest: "
                f"{self.deployment!r}."
            )
        # Cloud uwierzytelnia się Basic auth (email + API token); Server/DC — PAT Bearer (bez
        # e-maila). Brak e-maila na Cloud = niedziałające auth — twardy błąd startu (fail-fast).
        if deployment == "cloud" and not self.email:
            raise ValueError(
                "WORKMATE_JIRA_DEPLOYMENT=cloud wymaga WORKMATE_JIRA_EMAIL "
                "(e-mail konta Atlassian do Basic-auth z API tokenem)."
            )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak URL/tokenu albo zły wariant wdrożenia."""
        self.validate_limits()
        missing = [
            name
            for name, value in (
                ("WORKMATE_JIRA_BASE_URL", self.base_url),
                ("WORKMATE_JIRA_TOKEN", self.token),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Odczyt Jiry wymaga URL i tokenu: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )
