"""Porty GitHub (Faza 3 / ADR 0020 read, ADR 0021 write) — kontrakt drzwi na GitHub REST.

``Protocol`` jak pozostałe porty — dowolna implementacja o zgodnych sygnaturach jest
akceptowana bez dziedziczenia. Read i write są ROZDZIELONE (jak ``NotesRepository`` vs
``NotesWriter``, ADR 0006): strona odczytu pozostaje jawnie read-only, a zdolność zapisu
wstrzykiwana jest tylko drzwiom z włączoną bramką (``enable_github_write``).

Metody są SYNCHRONICZNE (GitHub REST przez ``httpx.Client``): poller (async) woła je w puli
wątków — jak teams_graph odświeża sync MSAL — a narzędzia agenta wołają je wprost. Surowe
JSON (``list[dict]``) mapuje czysty ``selection`` w warstwie drzwi; treść z GitHuba to DANE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from datetime import datetime

# Sufit commitów oddawanych przez ``list_commits`` w JEDNYM wywołaniu — CZĘŚĆ KONTRAKTU, nie
# szczegół adaptera. Rdzeń musi go znać, bo pełne wiadro oznacza ucięcie historii (GitHub zwraca
# od najnowszych), a propozycja czasu jest wtedy zaniżona i mówi o tym w ``notes``.
MAX_COMMITS_PER_FETCH = 500


class GithubReadPort(Protocol):
    """Odczyt z GitHub (polling PAT): tożsamość konta + issue/PR, komentarze, recenzje i CI."""

    def authenticated_login(self) -> str:
        """Login konta PAT — do strażnika pętli self-ping (pomijamy własne zdarzenia)."""
        ...

    def list_issues(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        """Surowe issue ORAZ PR od ``since`` (rosnąco), z paginacją; PR mają klucz ``pull_request``
        — rozróżnienie issue vs PR robi selection (ADR 0024)."""
        ...

    def list_issue_comments(
        self, owner: str, repo: str, *, since: datetime | None = None, per_page: int = 50
    ) -> list[dict[str, Any]]:
        """Surowe komentarze do issue ORAZ PR od ``since`` (rosnąco), z paginacją; PR-owe rozpozna
        selection po ``html_url`` (``/pull/``)."""
        ...

    def list_pull_reviews(self, owner: str, repo: str, pull_number: int) -> list[dict[str, Any]]:
        """Surowe recenzje danego PR (endpoint per-PR, bez ``since``); istotne stany wybierze
        selection (ADR 0024)."""
        ...

    def list_pulls(
        self, owner: str, repo: str, *, state: str = "all", per_page: int = 50
    ) -> list[dict[str, Any]]:
        """Surowe PR-y z dedykowanego ``/pulls`` — bogatsze niż z ``/issues``: niosą ``merged_at``,
        ``draft``, ``head``/``base`` i ``state`` (open/closed). Do snapshotu stanu PR i wykrycia
        tranzycji merged/closed/ready (ADR 0029). Biała lista pól — bez diffów/patchy."""
        ...

    def list_branches(self, owner: str, repo: str, *, per_page: int = 50) -> list[dict[str, Any]]:
        """Surowe gałęzie repo — nazwa + HEAD SHA (``commit.sha``). Do snapshotu stanu i wykrycia
        pushy przez różnicę HEAD SHA między rundami (ADR 0029)."""
        ...

    def list_commits(
        self,
        owner: str,
        repo: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        author: str = "",
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        """Surowe commity gałęzi domyślnej w oknie ``since``..``until``, z paginacją (ADR 0034).

        ``author`` zawęża do jednej osoby (login GitHub albo adres e-mail autora commita) —
        bez niego dostalibyśmy pracę całego zespołu. Białą listę pól nakłada dopiero rdzeń;
        NIE pobieramy diffów ani patchy. Wiadomość commita to DANE, nie polecenia.

        OGRANICZENIE: endpoint zwraca commity GAŁĘZI DOMYŚLNEJ, więc praca na
        niezmerge'owanych gałęziach jest niewidoczna (propozycja mówi o tym wprost w ``notes``).

        SUFIT: najwyżej ``MAX_COMMITS_PER_FETCH`` pozycji, liczonych OD NAJNOWSZYCH. Pełne
        wiadro znaczy, że najstarsze dni okna wypadły — wołający MUSI to zgłosić, bo inaczej
        zaniżony wynik wygląda na kompletny.
        """
        ...

    def list_workflow_runs(
        self, owner: str, repo: str, *, per_page: int = 50, status: str = "completed"
    ) -> list[dict[str, Any]]:
        """Surowe zakończone przebiegi CI (workflow runs); sukces/porażkę zmapuje selection
        z BIAŁEJ LISTY pól (ADR 0024) — bez logów/sekretów."""
        ...


class GithubWritePort(Protocol):
    """Zapis do GitHub (bramkowany, Gate 4 / ADR 0021) — CREATE-ONLY: nowe issue i komentarze."""

    def create_issue(
        self, owner: str, repo: str, title: str, body: str, labels: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Utwórz nowe issue; zwróć surową odpowiedź GitHub (m.in. ``number``, ``html_url``)."""
        ...

    def create_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        """Dodaj komentarz do istniejącego issue; zwróć surową odpowiedź GitHub (``html_url``)."""
        ...
