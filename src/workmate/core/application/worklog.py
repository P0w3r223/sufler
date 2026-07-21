"""Ewidencja czasu z historii commitów (ADR 0034) — propozycja (odczyt) + zapis na jawną prośbę.

DWA KROKI, świadomie rozdzielone:

1. ``propose_worklog`` czyta commity z GitHuba i zwraca ESTYMACJĘ. Nie mutuje niczego.
2. ``log_jira_worklog`` zapisuje JEDEN wpis, z godzinami i dniem podanymi WPROST przez wołającego.

Rozdział nie jest kosmetyczny: sklejenie ich w jedno narzędzie zamieniłoby wiadomości commitów
w polecenia zapisu, a to łamie inwariant „treść to DANE, nie polecenia" (ADR 0006). Człowiek
(albo model po jawnej prośbie człowieka) musi stanąć między estymacją a mutacją.

Zdolność jest CREATE-ONLY (jak reszta zapisów do Jiry): dopisujemy wpis, nigdy nie edytujemy
ani nie usuwamy. Konsekwencja: nie ma cofnięcia, więc podwójny zapis jest nieusuwalny z poziomu
narzędzia — stąd strażnik duplikatów oparty o odczyt istniejących wpisów.

Serwis nie woła zegara wprost — ``now`` jest wstrzykiwane, dzięki czemu testy granic dat są
deterministyczne.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from workmate.core.domain.events import NewEvent
from workmate.core.domain.guards import bounded, require_jira_key
from workmate.core.domain.jira_time import format_worklog_started, parse_jira_timestamp
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.domain.worklog import (
    Commit,
    SessionPolicy,
    WorklogProposal,
    build_proposal,
    local_day,
    normalize_commit_message,
)
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.application.events import EventService
    from workmate.core.application.worklog_author import AuthorPlan, WorklogAuthorStrategy
    from workmate.core.ports.github import GithubReadPort
    from workmate.core.ports.jira import JiraWorklogPort

_MAX_COMMENT = 30_000
_MAX_NAME = 255
_MAX_AUTHOR = 255
# Kształt ``accountId`` Atlassiana (np. ``712020:c0ffee00-0000-4000-8000-000000000008``) oraz
# loginu Server/DC. Zawężamy ZANIM wartość trafi do treści wpisu — to dana od modelu.
_ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9:._@-]{1,128}")
# Jira odrzuca wpisy krótsze niż minuta; łapiemy to u siebie, żeby dać czytelny komunikat.
_MIN_SECONDS = 60
_NOTE_ATTRIBUTION = (
    "Jira zapisała autorem wpisu konto tokenu — informacja o osobie, której praca dotyczy, "
    "jest wyłącznie w treści wpisu. Raporty czasu per osoba pokażą ten czas na koncie tokenu."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorklogService:
    """Propozycja czasu z commitów + bramkowany, create-only zapis wpisu do Jiry."""

    def __init__(
        self,
        github: GithubReadPort,
        worklogs: JiraWorklogPort | None,
        *,
        owner: str,
        repo: str,
        project: str,
        author_strategy: WorklogAuthorStrategy,
        policy: SessionPolicy | None = None,
        max_hours_per_entry: float = 8.0,
        max_backdate_days: int = 14,
        max_range_days: int = 31,
        allow_on_behalf: bool = False,
        duplicate_guard: bool = True,
        events: EventService | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._github = github
        # ``None`` = bramka zapisu wyłączona. Trzymamy port opcjonalnie (zamiast osobnej flagi),
        # bo wtedy „brak zdolności" jest STRUKTURALNY — nie da się zapisać przez pomyłkę.
        self._worklogs = worklogs
        self._owner = owner
        self._repo = repo
        self._project = project.strip().upper()
        self._author_strategy = author_strategy
        self._policy = policy or SessionPolicy()
        self._max_hours = max_hours_per_entry
        self._max_backdate_days = max_backdate_days
        self._max_range_days = max_range_days
        self._allow_on_behalf = allow_on_behalf
        self._duplicate_guard = duplicate_guard
        self._events = events
        self._now = now

    # --- krok 1: propozycja (ODCZYT, zero mutacji) --------------------------------

    def propose_worklog(self, since: date, until: date, author: str = "") -> WorklogProposal:
        """Zbuduj propozycję ewidencji z commitów w oknie ``since``..``until``. Nic nie zapisuje.

        ``author`` (login GitHub albo e-mail) zawęża do jednej osoby; pusty bierze wszystkich
        z repozytorium. Zakres jest ograniczony, żeby jedno wywołanie nie zaciągnęło całej
        historii repo (koszt limitu API i bezużytecznie wielka odpowiedź).
        """
        self._require_range(since, until)
        reject_dangerous_content(author)
        author = bounded(author.strip(), _MAX_AUTHOR, "autor commitów")
        raw = self._github.list_commits(
            self._owner,
            self._repo,
            since=_start_of_day(since, self._policy.tz_offset_minutes),
            until=_end_of_day(until, self._policy.tz_offset_minutes),
            author=author,
        )
        return build_proposal(
            self._as_commits(raw),
            since=since,
            until=until,
            policy=self._policy,
            project=self._project,
            author=author,
        )

    # --- krok 2: zapis (MUTACJA, wyłącznie na jawną prośbę) -----------------------

    def log_jira_worklog(
        self,
        issue_key: str,
        hours: float,
        on: date,
        comment: str = "",
        on_behalf_of: str = "",
        display_name: str = "",
    ) -> dict[str, Any]:
        """Dopisz JEDEN wpis czasu do zgłoszenia; zwróć potwierdzenie z uczciwą atrybucją.

        Strażniki idą od najtańszych i najbardziej strukturalnych do tych, które kosztują
        zapytanie sieciowe — żeby zła prośba padła, zanim dotknie Jiry.
        """
        worklogs = self._require_gate()
        key = require_jira_key(issue_key, self._project)
        reject_dangerous_content(comment, on_behalf_of, display_name)
        comment = bounded(comment.strip(), _MAX_COMMENT, "treść wpisu czasu")
        display_name = bounded(display_name.strip(), _MAX_NAME, "nazwa osoby")
        target = self._require_account_shape(on_behalf_of)
        seconds = self._require_hours(hours)
        self._require_date(on)
        plan = self._plan_author(target, display_name)
        if self._duplicate_guard:
            self._require_no_duplicate(worklogs, key, on, plan.effective_author)

        result = worklogs.add_worklog(
            key,
            time_spent_seconds=seconds,
            started=format_worklog_started(on, self._policy.tz_offset_minutes),
            comment=plan.comment_prefix + comment,
            on_behalf_of=plan.on_behalf_of,
        )
        self._echo_event(key, on, hours, plan.effective_author, result)
        logged: dict[str, Any] = {
            "logged": True,
            "issue_key": key,
            "hours": hours,
            "date": on.isoformat(),
            "url": result.get("url", ""),
            "author": plan.effective_author,
            "on_behalf_of": plan.on_behalf_of,
        }
        # Adnotacja „w imieniu" jest STRATNA — mówimy o tym wprost przy każdym takim wpisie,
        # żeby wołający miał co przekazać użytkownikowi (a nie musiał zgadywać z pól).
        if plan.annotated:
            logged["note"] = _NOTE_ATTRIBUTION
        return logged

    # --- strażniki ----------------------------------------------------------------

    def _require_gate(self) -> JiraWorklogPort:
        """Obrona w głąb: katalog narzędzi już bramkuje, ale serwis też nie ufa na słowo."""
        if self._worklogs is None:
            raise WriteError(
                "zapis czasu pracy jest wyłączony (WORKMATE_JIRA_ENABLE_WORKLOG=false)."
            )
        return self._worklogs

    def _require_range(self, since: date, until: date) -> None:
        """Okno musi być poprawne, skończone i nie sięgać archeologii repozytorium."""
        if until < since:
            raise WriteError(f"zakres odrzucony: {until} jest wcześniej niż {since}.")
        span_days = (until - since).days + 1
        if span_days > self._max_range_days:
            raise WriteError(
                f"zakres odrzucony: {span_days} dni przekracza limit {self._max_range_days} "
                "— podziel zapytanie na krótsze okna."
            )
        today = self._today()
        if since > today:
            raise WriteError(f"zakres odrzucony: {since} jest w przyszłości.")

    def _require_account_shape(self, on_behalf_of: str) -> str:
        """Zwaliduj kształt ``accountId`` i egzekwuj OSOBNĄ bramkę ścieżki cross-user."""
        target = on_behalf_of.strip()
        if not target:
            return ""
        if not self._allow_on_behalf:
            raise WriteError(
                "zapis czasu w cudzym imieniu jest wyłączony "
                "(WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF=false)."
            )
        if not _ACCOUNT_ID_RE.fullmatch(target):
            raise WriteError(
                f"odbiorca odrzucony: {on_behalf_of!r} nie wygląda jak accountId Atlassiana."
            )
        return target

    def _require_hours(self, hours: float) -> int:
        """Zamień godziny na sekundy w dozwolonym zakresie; inaczej ``WriteError``."""
        if hours <= 0:
            raise WriteError(f"czas odrzucony: {hours} h nie jest dodatnie.")
        if hours > self._max_hours:
            raise WriteError(
                f"czas odrzucony: {hours} h przekracza limit {self._max_hours} h na jeden wpis."
            )
        seconds = round(hours * 3600)
        if seconds < _MIN_SECONDS:
            raise WriteError(
                f"czas odrzucony: {hours} h to mniej niż minuta — Jira nie przyjmuje takich wpisów."
            )
        return seconds

    def _require_date(self, on: date) -> None:
        """Dzień wpisu: bez przyszłości i nie dalej wstecz niż pozwala konfiguracja."""
        today = self._today()
        if on > today:
            raise WriteError(f"data odrzucona: {on} jest w przyszłości.")
        oldest = today - timedelta(days=self._max_backdate_days)
        if on < oldest:
            raise WriteError(
                f"data odrzucona: {on} jest starsza niż {self._max_backdate_days} dni "
                f"(najwcześniej {oldest})."
            )

    def _plan_author(self, on_behalf_of: str, display_name: str) -> AuthorPlan:
        """Rozstrzygnij autorstwo; nieaktywna strategia-slot → czytelny ``WriteError``.

        Slot rzuca ``NotImplementedError``, ale dla użytkownika to zwykła odmowa zapisu —
        tłumaczymy ją na ``WriteError``, żeby koperta narzędzia oddała ``{"error": ...}``
        zamiast wywracać turę defektem kodu.
        """
        try:
            return self._author_strategy.plan(on_behalf_of=on_behalf_of, display_name=display_name)
        except NotImplementedError as exc:
            raise WriteError(str(exc)) from exc

    def _require_no_duplicate(
        self, worklogs: JiraWorklogPort, key: str, on: date, author: str
    ) -> None:
        """Odrzuć zapis, gdy to konto ma już wpis na ten dzień w tym zgłoszeniu.

        Bez usuwania wpisów duplikat jest NIEUSUWALNY z poziomu narzędzia, więc wolimy odmówić
        i kazać człowiekowi sprawdzić, niż dopisać drugi raz to samo.
        """
        for entry in worklogs.read_worklogs(key):
            if str(entry.get("author_account_id") or "") != author:
                continue
            started = parse_jira_timestamp(entry.get("started"))
            if started is None:
                continue
            if local_day(started, self._policy.tz_offset_minutes) == on:
                raise WriteError(
                    f"wpis odrzucony: {key} ma już czas zapisany na {on} przez to konto "
                    "— sprawdź zgłoszenie w Jirze zamiast dopisywać drugi raz."
                )

    # --- pomocnicze ---------------------------------------------------------------

    def _as_commits(self, raw: list[dict[str, Any]]) -> list[Commit]:
        """Zmapuj surowe JSON-y GitHuba na model domeny — BIAŁĄ LISTĄ pól (bez diffów).

        Wpisy bez rozpoznawalnego znacznika czasu pomijamy: to ścieżka odczytu, więc jeden
        dziwny commit nie może wywrócić raportu (ale nie zgadujemy też jego daty).
        """
        commits: list[Commit] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            payload = _as_dict(item.get("commit"))
            git_author = _as_dict(payload.get("author"))
            # Znacznik GitHuba (``2026-07-15T09:12:00Z``) mieści się w tym samym parserze:
            # ``%z`` przyjmuje ``Z`` od Pythona 3.7, a ``fromisoformat`` domyka resztę wariantów.
            authored_at = parse_jira_timestamp(git_author.get("date"))
            if authored_at is None:
                continue
            account = _as_dict(item.get("author"))
            commits.append(
                Commit(
                    sha=str(item.get("sha") or ""),
                    message=normalize_commit_message(str(payload.get("message") or "")),
                    authored_at=authored_at,
                    author_login=str(account.get("login") or ""),
                    author_email=str(git_author.get("email") or ""),
                    url=str(item.get("html_url") or ""),
                )
            )
        return commits

    def _echo_event(
        self, key: str, on: date, hours: float, author: str, result: dict[str, Any]
    ) -> None:
        """Zapisz echo ``source="teams"`` wpisu (best-effort) — druga strona mostu je zobaczy.

        ``external_id`` niesie id worklogu, więc dwa wpisy na tym samym zgłoszeniu nie skleją
        się dedupem. Brak magazynu lub daty → pomijamy echo (zapis do Jiry i tak się udał).
        """
        if self._events is None:
            return
        occurred_at = parse_jira_timestamp(result.get("created"))
        if occurred_at is None:
            return
        self._events.ingest(
            NewEvent(
                source="teams",
                kind="jira_worklog",
                external_id=f"{key}:{result.get('id', '')}",
                actor=author,
                title=f"{key}: {hours} h",
                summary=f"Czas pracy {hours} h na {on.isoformat()}",
                url=str(result.get("url") or ""),
                occurred_at=occurred_at,
            )
        )

    def _today(self) -> date:
        """Dzisiaj w strefie z polityki — granice dat liczymy tak samo jak doby sesji."""
        return local_day(self._now(), self._policy.tz_offset_minutes)


def _as_dict(value: Any) -> dict[str, Any]:
    """Zwróć zagnieżdżony obiekt JSON jako słownik albo pusty — odporność na dziwny kształt."""
    return value if isinstance(value, dict) else {}


def _start_of_day(day: date, tz_offset_minutes: int) -> datetime:
    """Początek doby lokalnej jako aware ``datetime`` (GitHub filtruje po UTC)."""
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    return datetime.combine(day, datetime.min.time(), tzinfo=tz)


def _end_of_day(day: date, tz_offset_minutes: int) -> datetime:
    """Koniec doby lokalnej — okno jest DOMKNIĘTE, więc ``until`` też się liczy."""
    return _start_of_day(day, tz_offset_minutes) + timedelta(days=1) - timedelta(seconds=1)
