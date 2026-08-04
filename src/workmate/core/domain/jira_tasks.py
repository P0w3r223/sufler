"""Domena "moje zadania" Jira (ADR 0054) — JQL zawężone do JEDNEGO konta + mapowanie wyniku.

CZYSTA logika: budowa JQL i mapowanie surowego issue → ``JiraTask``, bez I/O. ``assignee`` jest
zawsze WSTRZYKIWANY z konfiguracji albo z rozwiązanej tożsamości nadawcy (nigdy z treści prośby
wołającego) — to jedyna gwarancja, że tym narzędziem nie da się podejrzeć cudzych zadań. Treść pól
(``summary``, nazwa statusu/priorytetu) to DANE ze źródła zewnętrznego, nie polecenia.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from workmate.core.domain.sanitize import strip_control_chars
from workmate.core.errors import InvalidRequestError

# Sufit długości podsumowania w wyniku — jak przy zdarzeniach Jiry (jira/selection.py).
_MAX_SUMMARY = 200
# Sufity treści szczegółów zgłoszenia — opis i komentarze to DANE z Jiry karmione modelowi, więc
# przycinamy je, by pojedyncze zgłoszenie nie zjadło całego okna kontekstu (ani nie kosztowało).
_MAX_DESCRIPTION = 2000
_MAX_COMMENT = 500
_MAX_COMMENTS = 5
# Klucz projektu Jira: litera + alfanumeryki/podkreślenia (kanon Atlassian) — wąska biała lista,
# żeby wartość od modelu nie wstrzyknęła składni JQL.
_PROJECT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}$")
# Kategorie statusu Jiry (locale-niezależne stałe) → whitelist wartości JQL ``statusCategory``.
_STATUS_CATEGORIES = {"todo": "To Do", "in_progress": "In Progress", "done": "Done"}
# Data ISO (YYYY-MM-DD) — jedyny dozwolony format dat historii, STEROWANYCH PRZEZ WOŁAJĄCEGO.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JiraTask(BaseModel):
    """Jedno zgłoszenie z listy "moje zadania" — tylko pola potrzebne do pokazania listy.

    ``assignee`` puste znaczy NIEPRZYPISANE (zgłoszenie z klauzuli reporter-unassigned w
    ``build_my_tasks_jql``) — to jedyny sygnał, po którym można rozdzielić „moje przypisane"
    od „zgłoszone przeze mnie i czekające na triage" (``split_by_assignment``).
    """

    key: str
    summary: str
    status: str
    priority: str = ""
    assignee: str = ""
    due_date: str = ""
    resolved: str = ""
    url: str = ""


class JiraComment(BaseModel):
    """Komentarz do zgłoszenia (przycięty) — treść to DANE z Jiry, nie polecenia."""

    author: str = ""
    created: str = ""
    body: str = ""


class JiraTaskDetails(BaseModel):
    """Szczegóły JEDNEGO zgłoszenia — biała lista pól + do 5 ostatnich komentarzy."""

    key: str
    summary: str
    description: str = ""
    status: str = ""
    priority: str = ""
    assignee: str = ""
    reporter: str = ""
    due_date: str = ""
    created: str = ""
    updated: str = ""
    resolved: str = ""
    url: str = ""
    comments: list[JiraComment] = []


def escape_jql_string(value: str) -> str:
    """Zescapuj wartość STEROWANĄ PRZEZ WOŁAJĄCEGO do literału JQL w cudzysłowie.

    Inaczej niż ``build_my_tasks_jql`` (zaufana wartość konfiguracyjna → prosty strip), wyszukiwanie
    bierze tekst od modelu/użytkownika, więc backslash i cudzysłów muszą być poprawnie zescapowane
    (backslash pierwszy), by nie dało się wyjść z literału i dopisać klauzuli JQL.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_my_tasks_jql(assignee: str) -> str:
    """JQL: NIEROZWIĄZANE zgłoszenia MOJE — przypisane do mnie ALBO zgłoszone przeze mnie i wciąż
    nieprzypisane, po priorytecie i terminie.

    ``assignee`` to login/e-mail/accountId z konfiguracji albo z rozwiązanej tożsamości nadawcy —
    NIGDY parametr narzędzia. Cudzysłów w wartości usuwamy (nie escapujemy) — to zaufana wartość
    konfiguracyjna, nie treść od wołającego, więc prosty strip wystarcza za injection guard.

    Klauzula ``reporter = ... AND assignee IS EMPTY`` dołącza zgłoszenia czekające na triage: bez
    niej zadanie, które zgłosiłeś, a nikt jeszcze nie wziął, znikało z „moich zadań" (realny błąd
    z produkcji — 7 zgłoszeń Piotra było niewidocznych).
    """
    if not assignee:
        raise ValueError("assignee nie może być pusty — JQL musiałby wypisać WSZYSTKIE zadania.")
    safe = assignee.replace('"', "")
    return (
        f'(assignee = "{safe}" OR (reporter = "{safe}" AND assignee IS EMPTY)) '
        "AND resolution = EMPTY ORDER BY priority DESC, duedate ASC"
    )


def _validate_iso_date(value: str, label: str) -> str:
    """Zwaliduj datę STEROWANĄ PRZEZ WOŁAJĄCEGO (YYYY-MM-DD) przed wstawieniem do JQL.

    Regex sam nie odrzuca dat nieistniejących (np. 2026-02-30) — dlatego druga faza to
    ``datetime.strptime``, która to robi.
    """
    text = value.strip()
    if not _ISO_DATE_RE.match(text):
        raise InvalidRequestError(f"Niepoprawna data {label} {value!r} — oczekuję YYYY-MM-DD.")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise InvalidRequestError(f"Nieistniejąca data {label} {value!r}.") from None
    return text


def build_history_jql(assignee: str, since: str = "", until: str = "") -> str:
    """JQL: ZAKOŃCZONE zgłoszenia MOJE (albo osoby), opcjonalnie w oknie dat rozwiązania.

    ``assignee`` jak w ``build_my_tasks_jql`` — zaufana tożsamość (konfiguracja/rozwiązany
    nadawca/mapa tożsamości), NIGDY parametr narzędzia; prosty strip cudzysłowu wystarcza.

    ``since``/``until`` są STEROWANE PRZEZ WOŁAJĄCEGO → ścisła walidacja YYYY-MM-DD PRZED
    wstawieniem do JQL (``_validate_iso_date``). ``until`` dostaje dopisek " 23:59", bo goła
    data w JQL oznacza początek doby i wykluczałaby zgłoszenia rozwiązane tego samego dnia.
    """
    if not assignee:
        raise ValueError("assignee nie może być pusty — JQL musiałby wypisać WSZYSTKIE zadania.")
    safe = assignee.replace('"', "")
    clauses = [f'assignee = "{safe}"', 'statusCategory = "Done"']
    since = since.strip()
    until = until.strip()
    if since:
        clauses.append(f'resolved >= "{_validate_iso_date(since, "since")}"')
    if until:
        clauses.append(f'resolved <= "{_validate_iso_date(until, "until")} 23:59"')
    if since and until and since > until:
        raise InvalidRequestError("Data 'since' jest późniejsza niż 'until' — zamień zakres.")
    return " AND ".join(clauses) + " ORDER BY resolved DESC"


def build_search_jql(text: str = "", project: str = "", status_category: str = "") -> str:
    """Złóż JQL wyszukiwania z opcjonalnych filtrów: tekst, projekt, kategoria statusu.

    Wszystkie trzy wartości są STEROWANE PRZEZ WOŁAJĄCEGO, więc każda przechodzi przez białą listę
    albo escaping: ``text`` → escapowany literał ``~`` (kontekst), ``project`` → walidacja klucza
    regexem, ``status_category`` → whitelist (todo/in_progress/done). Domyślnie tylko nierozwiązane;
    ``status_category='done'`` świadomie pokazuje też zamknięte. Pusty zestaw filtrów jest odrzucony
    (nie wypisujemy CAŁEJ Jiry).
    """
    clauses: list[str] = []
    if text.strip():
        clauses.append(f'text ~ "{escape_jql_string(text.strip())}"')
    if project.strip():
        key = project.strip()
        if not _PROJECT_KEY_RE.match(key):
            raise InvalidRequestError(
                f"Niepoprawny klucz projektu {project!r} — oczekuję np. 'WT' albo 'SCRUM'."
            )
        clauses.append(f'project = "{key}"')
    category = status_category.strip().lower()
    if category:
        if category not in _STATUS_CATEGORIES:
            allowed = ", ".join(sorted(_STATUS_CATEGORIES))
            raise InvalidRequestError(
                f"Niepoprawna kategoria statusu {status_category!r}; dozwolone: {allowed}."
            )
        clauses.append(f'statusCategory = "{_STATUS_CATEGORIES[category]}"')
    if not clauses:
        raise InvalidRequestError(
            "Podaj przynajmniej jeden filtr wyszukiwania: tekst, projekt albo kategorię statusu."
        )
    # Nierozwiązane, chyba że jawnie pytamy o zakończone (kategoria 'done').
    if category != "done":
        clauses.append("resolution = EMPTY")
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def map_my_tasks(raw: list[dict[str, Any]], *, base_url: str = "") -> list[JiraTask]:
    """Zmapuj surowe issue Jiry (z ``search_issues``) na listę ``JiraTask`` (biała lista pól)."""
    tasks: list[JiraTask] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        fields = item.get("fields")
        fields = fields if isinstance(fields, dict) else {}
        tasks.append(
            JiraTask(
                key=key,
                summary=_clip(str(fields.get("summary") or "")),
                status=_name(fields.get("status")),
                priority=_name(fields.get("priority")),
                assignee=_person_name(fields.get("assignee")),
                due_date=str(fields.get("duedate") or ""),
                resolved=str(fields.get("resolutiondate") or ""),
                url=_browse_url(base_url, key),
            )
        )
    return tasks


def split_by_assignment(tasks: list[JiraTask]) -> tuple[list[JiraTask], list[JiraTask]]:
    """Podziel wynik "moich zadań" na (przypisane, zgłoszone-nieprzypisane) po pustym ``assignee``.

    JQL "moich zadań" (``build_my_tasks_jql``) zwraca sumę dwóch rozłącznych zbiorów: zgłoszenia
    PRZYPISANE do konta oraz zgłoszenia przez nie ZGŁOSZONE i wciąż NIEPRZYPISANE do nikogo —
    pusty ``assignee`` jednoznacznie wskazuje tę drugą grupę. Bez tego rozróżnienia bot prezentował
    obie grupy jako jedną listę „twoich zadań", myląc użytkownika.
    """
    assigned = [t for t in tasks if t.assignee]
    unassigned = [t for t in tasks if not t.assignee]
    return assigned, unassigned


def _name(value: Any) -> str:
    """Wyłuskaj ``.name`` z obiektu Jiry (status/priorytet) — odporne na brak/None."""
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _browse_url(base_url: str, key: str) -> str:
    if not base_url or not key:
        return ""
    return f"{base_url.rstrip('/')}/browse/{key}"


def map_task_details(
    raw_issue: dict[str, Any],
    raw_comments: list[dict[str, Any]],
    *,
    base_url: str = "",
) -> JiraTaskDetails:
    """Zmapuj surowe issue + komentarze Jiry na ``JiraTaskDetails`` (biała lista pól, przycięcie).

    Opis/komentarze to DANE ze źródła zewnętrznego: wycinamy z nich znaki sterujące i przycinamy do
    limitów (opis ≤2000, komentarz ≤500, najwyżej 5 najnowszych), żeby jedno zgłoszenie nie zjadło
    okna kontekstu ani nie przemyciło wektora wstrzyknięcia do promptu.
    """
    key = str(raw_issue.get("key") or "")
    fields = raw_issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    comments = [
        JiraComment(
            author=_person_name(c.get("author")),
            created=str(c.get("created") or ""),
            body=_clip_text(_body_text(c.get("body")), _MAX_COMMENT),
        )
        for c in raw_comments[:_MAX_COMMENTS]
        if isinstance(c, dict)
    ]
    return JiraTaskDetails(
        key=key,
        summary=_clip(str(fields.get("summary") or "")),
        description=_clip_text(_body_text(fields.get("description")), _MAX_DESCRIPTION),
        status=_name(fields.get("status")),
        priority=_name(fields.get("priority")),
        assignee=_person_name(fields.get("assignee")),
        reporter=_person_name(fields.get("reporter")),
        due_date=str(fields.get("duedate") or ""),
        created=str(fields.get("created") or ""),
        updated=str(fields.get("updated") or ""),
        resolved=str(fields.get("resolutiondate") or ""),
        url=_browse_url(base_url, key),
        comments=comments,
    )


def _person_name(value: Any) -> str:
    """Wyłuskaj nazwę osoby z obiektu Jiry (assignee/reporter/author) — Cloud: ``displayName``."""
    if not isinstance(value, dict):
        return ""
    return str(value.get("displayName") or value.get("name") or value.get("key") or "")


def _body_text(value: Any) -> str:
    """Znormalizuj treść opisu/komentarza do stringu — ADF jest już spłaszczony w adapterze."""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _clip_text(text: str, limit: int) -> str:
    """Wytnij znaki sterujące i przytnij do ``limit`` (treść zewnętrzna karmiona modelowi)."""
    text = strip_control_chars(text).strip()
    return text if len(text) <= limit else text[:limit] + " […]"


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_SUMMARY else text[:_MAX_SUMMARY] + " […]"
