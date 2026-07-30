"""Domena "moje zadania" Jira (ADR 0054) — JQL zawężone do JEDNEGO konta + mapowanie wyniku.

CZYSTA logika: budowa JQL i mapowanie surowego issue → ``JiraTask``, bez I/O. ``assignee`` jest
zawsze WSTRZYKIWANY z konfiguracji albo z rozwiązanej tożsamości nadawcy (nigdy z treści prośby
wołającego) — to jedyna gwarancja, że tym narzędziem nie da się podejrzeć cudzych zadań. Treść pól
(``summary``, nazwa statusu/priorytetu) to DANE ze źródła zewnętrznego, nie polecenia.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# Sufit długości podsumowania w wyniku — jak przy zdarzeniach Jiry (jira/selection.py).
_MAX_SUMMARY = 200


class JiraTask(BaseModel):
    """Jedno zgłoszenie z listy "moje zadania" — tylko pola potrzebne do pokazania listy."""

    key: str
    summary: str
    status: str
    priority: str = ""
    due_date: str = ""
    url: str = ""


def build_my_tasks_jql(assignee: str) -> str:
    """JQL: zgłoszenia NIEROZWIĄZANE przypisane do ``assignee``, po priorytecie i terminie.

    ``assignee`` to login/e-mail/accountId z konfiguracji albo z rozwiązanej tożsamości nadawcy —
    NIGDY parametr narzędzia. Cudzysłów w wartości usuwamy (nie escapujemy) — to zaufana wartość
    konfiguracyjna, nie treść od wołającego, więc prosty strip wystarcza za injection guard.
    """
    if not assignee:
        raise ValueError("assignee nie może być pusty — JQL musiałby wypisać WSZYSTKIE zadania.")
    safe = assignee.replace('"', "")
    return f'assignee = "{safe}" AND resolution = EMPTY ORDER BY priority DESC, duedate ASC'


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
                due_date=str(fields.get("duedate") or ""),
                url=_browse_url(base_url, key),
            )
        )
    return tasks


def _name(value: Any) -> str:
    """Wyłuskaj ``.name`` z obiektu Jiry (status/priorytet) — odporne na brak/None."""
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _browse_url(base_url: str, key: str) -> str:
    if not base_url or not key:
        return ""
    return f"{base_url.rstrip('/')}/browse/{key}"


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_SUMMARY else text[:_MAX_SUMMARY] + " […]"
