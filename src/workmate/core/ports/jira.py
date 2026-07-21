"""Porty Jira (ADR 0030 read, ADR 0031 write) — kontrakt drzwi na Jira Server/DC REST v2.

Analogicznie do portów GitHub: SYNCHRONICZNE (``httpx.Client``), poller/narzędzia wołają je w puli
wątków. Surowe JSON (``list[dict]``) mapuje czysta ``jira.selection`` w warstwie drzwi; treść Jira
(podsumowania, komentarze) to DANE, nie polecenia. Read i write są ROZDZIELONE jak w GitHub
(``NotesRepository`` vs ``NotesWriter``, ADR 0006): strona odczytu zostaje jawnie read-only, a
zdolność zapisu (CREATE-ONLY, Gate 5) wstrzykiwana jest tylko drzwiom z bramką ``enable_jira_write``
(domyślnie OFF).
"""

from __future__ import annotations

from typing import Any, Protocol


class JiraReadPort(Protocol):
    """Odczyt z Jira (polling PAT Bearer): tożsamość konta + issue z changelogiem wg JQL."""

    def authenticated_account(self) -> str:
        """Login/klucz konta PAT — do strażnika pętli self-skip (pomijamy własne zmiany)."""
        ...

    def search_issues(
        self, jql: str, *, max_results: int = 50, expand: str = "changelog"
    ) -> list[dict[str, Any]]:
        """Surowe issue Jira wg JQL (z ``changelog`` w ``expand``), z paginacją po ``startAt``.

        Selection wybierze utworzenia/tranzycje/komentarze (ADR 0030), białą listą pól.
        """
        ...


class JiraWritePort(Protocol):
    """Zapis do Jira: CREATE-ONLY (Gate 5 / ADR 0031) + tranzycja statusu (ADR 0032, bramkowana)."""

    def create_issue(
        self,
        project: str,
        issue_type: str,
        summary: str,
        description: str,
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Utwórz nowe zgłoszenie w projekcie; zwróć znormalizowane ``{key, url, created}``.

        ``created`` (znacznik ISO serwera Jira) adapter dobiera osobnym GET-em, bo odpowiedź create
        Jiry go nie niesie — potrzebny do ostemplowania echa ``source="teams"`` (strażnik pętli).
        """
        ...

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """Dodaj komentarz do zgłoszenia; zwróć ``{id, url, created}`` (created z odpowiedzi)."""
        ...

    def read_transitions(self, issue_key: str) -> dict[str, Any]:
        """Bieżący status i tranzycje: ``{current_status, transitions: [{id, name, to_status}]}``.

        Jedno ``GET /issue/{key}?fields=status&expand=transitions`` — API pokazuje TYLKO tranzycje
        z bieżącego statusu (sąsiadów, nie cały graf workflow). Serwis (ADR 0032) chodzi po nich
        greedy, hop po hopie; ``to_status`` to nazwa statusu docelowego danej tranzycji.
        """
        ...

    def transition_issue(self, issue_key: str, transition_id: str) -> dict[str, Any]:
        """Wykonaj tranzycję (POST ``transition.id``); zwróć ``{url, status, updated}``.

        ``POST /transitions`` zwraca 204 bez ciała, więc ``status``/``updated`` adapter dobiera
        osobnym GET-em — ``updated`` stempluje echo ``source="teams"`` hopa (strażnik pętli, 0032).
        """
        ...


class JiraWorklogPort(Protocol):
    """Ewidencja czasu w Jirze (ADR 0034, bramkowana) — dopisanie wpisu i odczyt istniejących.

    TRZECI port obok read/write, nie metoda w ``JiraWritePort``: ewidencja czasu ma WŁASNĄ bramkę,
    więc drzwi mogą dostać ją bez zdolności tworzenia zgłoszeń (i odwrotnie). Ten sam rozdział
    zdolności co ``NotesRepository`` vs ``NotesWriter`` (ADR 0006).

    ``on_behalf_of`` (``accountId``) to INTENCJA wołającego, NIE gwarancja. Jira zawsze zapisuje
    autora = konto uwierzytelnione tokenem; adapter nie wysyła pola ``author`` (API i tak je
    ignoruje), tylko oddaje ``on_behalf_of`` z powrotem jako ``requested_author``. Za faktyczną
    atrybucję odpowiada strategia autorstwa (``application/worklog_author.py``).

    ``add_worklog`` jest CREATE-ONLY — brak edycji i usuwania (to byłaby pierwsza nie-create-only
    mutacja Jiry, wymagająca własnego ADR). Dlatego ``read_worklogs`` istnieje: bez cofania wpisu
    jedyną ochroną przed podwójnym zapisem jest sprawdzenie, co już tam jest.
    """

    def add_worklog(
        self,
        issue_key: str,
        *,
        time_spent_seconds: int,
        started: str,
        comment: str = "",
        on_behalf_of: str = "",
    ) -> dict[str, Any]:
        """Dopisz wpis czasu; zwróć ``{id, url, created, author_account_id, requested_author}``.

        ``started`` to znacznik w formacie Jiry (ISO z milisekundami i offsetem bez dwukropka) —
        składa go rdzeń, bo to on zna strefę z polityki. ``created`` stempluje echo ``source=
        "teams"`` (strażnik pętli). ``author_account_id`` to PRAWDZIWY autor z odpowiedzi Jiry.
        """
        ...

    def read_worklogs(self, issue_key: str, *, max_results: int = 100) -> list[dict[str, Any]]:
        """Istniejące wpisy zgłoszenia: ``[{id, author_account_id, started, time_spent_seconds,
        comment}]`` — strażnik duplikatów (bez usuwania wpisów to jedyna ochrona)."""
        ...
