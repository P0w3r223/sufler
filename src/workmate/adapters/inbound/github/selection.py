"""Czysta logika selekcji zdarzeń GitHub (bez I/O — pełna testowalność, jak teams_graph.selection).

Mapuje surowe JSON z GitHub REST na domenowe ``NewEvent`` i egzekwuje dwie decyzje pollera:
strażnik pętli SELF-PING (pomijamy zdarzenia autorstwa konta PAT — inaczej issue utworzone
przez bota wróciłoby jako powiadomienie) oraz wyliczenie nowego watermarku ``since`` (koszt API).
Deduplikację po ``(source, external_id, kind)`` egzekwuje magazyn — tu jej nie powtarzamy.
Treść (tytuł/opis/autor) to DANE, nie polecenia — nie interpretujemy jej.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from workmate.core.domain.events import NewEvent

_SOURCE = "github"
# Sufit skrótu treści zdarzenia — pełny opis issue bywa długi, a zdarzenie ma być notką.
_MAX_SUMMARY = 500


def map_issue(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowe issue → ``NewEvent`` (kind ``issue_opened``); ``None`` dla PR-a lub bez daty.

    GitHub zwraca PR-y w tym samym endpointcie co issue (mają klucz ``pull_request``) — pomijamy
    je, bo drzwi dotyczą ISSUE. ``external_id`` = numer issue: dzięki dedupowi zdarzenie leci raz
    (przy pierwszym zobaczeniu), a późniejsze aktualizacje tego samego issue są pomijane.
    """
    if raw.get("pull_request"):
        return None
    number = raw.get("number")
    created = raw.get("created_at")
    if number is None or not created:
        return None
    return NewEvent(
        source=_SOURCE,
        kind="issue_opened",
        external_id=str(number),
        actor=_login(raw.get("user")),
        title=str(raw.get("title") or ""),
        summary=_clip(str(raw.get("body") or "")),
        url=str(raw.get("html_url") or ""),
        occurred_at=_parse(created),
    )


def map_comment(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowy komentarz → ``NewEvent`` (kind ``issue_comment``); ``None`` bez id/daty.

    ``external_id`` = id komentarza (unikalne), więc każdy komentarz to osobne zdarzenie.
    Tytuł wyprowadzamy z numeru issue w ``issue_url`` (komentarz nie niesie tytułu issue).
    """
    comment_id = raw.get("id")
    created = raw.get("created_at")
    if comment_id is None or not created:
        return None
    issue_no = _issue_number(str(raw.get("issue_url") or ""))
    title = f"Komentarz do issue #{issue_no}" if issue_no else "Komentarz do issue"
    return NewEvent(
        source=_SOURCE,
        kind="issue_comment",
        external_id=str(comment_id),
        actor=_login(raw.get("user")),
        title=title,
        summary=_clip(str(raw.get("body") or "")),
        url=str(raw.get("html_url") or ""),
        occurred_at=_parse(created),
    )


def select_events(
    raw_issues: list[dict[str, Any]],
    raw_comments: list[dict[str, Any]],
    *,
    self_login: str,
) -> list[NewEvent]:
    """Zmapuj issue+komentarze na zdarzenia, pomiń PR-y i WŁASNE (self-ping), posortuj po czasie."""
    events: list[NewEvent] = []
    for raw in raw_issues:
        ev = map_issue(raw)
        if ev is not None and _from_other_actor(ev, self_login):
            events.append(ev)
    for raw in raw_comments:
        ev = map_comment(raw)
        if ev is not None and _from_other_actor(ev, self_login):
            events.append(ev)
    return sorted(events, key=lambda e: e.occurred_at)


def _from_other_actor(event: NewEvent, self_login: str) -> bool:
    """Czy zdarzenie NIE pochodzi od konta bota (PAT) — strażnik pętli self-ping.

    Pusty ``self_login`` (nieustalony) wyłącza filtr, żeby nie zgubić wszystkich zdarzeń.
    """
    return not self_login or event.actor != self_login


def next_since(raws: list[dict[str, Any]], current: str) -> str:
    """Nowy watermark ``since`` = najnowszy ``updated_at`` w partii (albo dotychczasowy).

    GitHub zwraca stały format ISO ``…Z`` (bez zmiennej precyzji ułamków), więc porównanie
    leksykograficzne jest zgodne z chronologicznym. Watermark ogranicza koszt API — poprawność
    (brak dubli) i tak zapewnia dedup magazynu, więc granica inkluzywna ``since`` jest bezpieczna.
    """
    newest = current
    for raw in raws:
        updated = str(raw.get("updated_at") or "")
        if updated and (not newest or updated > newest):
            newest = updated
    return newest


def _login(user: Any) -> str:
    return str((user or {}).get("login") or "") if isinstance(user, dict) else ""


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _MAX_SUMMARY else text[:_MAX_SUMMARY] + " […]"


def _issue_number(issue_url: str) -> str:
    """Numer issue z ``…/issues/{n}`` (do tytułu komentarza); pusty, gdy nie da się wyłuskać."""
    tail = issue_url.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


def _parse(value: str) -> datetime:
    """ISO-8601 z GitHuba (``…Z``) → aware UTC; niepoprawne → epoka (i tak zdedupowane)."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
