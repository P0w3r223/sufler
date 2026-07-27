"""Parsowanie i weryfikacja PRZYSŁANEGO JSON-a ``claude_summary`` (drzwi worklog self-service).

Self-service ODWRACA zbieranie z ADR 0037: zamiast operatora wrzucającego pliki do katalogu
(``ClaudeSummaryStore``), osoba SAMA przysyła swój wynik ``claude_summary`` w czacie 1:1, a bot
odsyła gotowy arkusz. Atrybucja idzie z UWIERZYTELNIONEGO nadawcy (``aad_user_id`` → ``Person``),
NIGDY z pola ``person`` w JSON-ie. Pole ``person`` jest wyłącznie CROSS-CHECKIEM: rozjazd z
``git_email`` nadawcy = odrzucenie (fail-closed, ADR 0037 reg. 3) — tak, żeby przypadkowo wysłany
CUDZY eksport nie wjechał do arkusza pod tożsamością nadawcy.

CZYSTO: bez I/O. Wejściem jest już zdekodowany ``dict`` (adapter drzwi czyta załącznik/tekst),
wyjściem klucze issue per dzień i komentarze per dzień — materiał dla serwisu składającego kartę
czasu (``application/selfservice_worklog``). Grupowanie po dniach bierzemy WPROST z JSON-a
(``claude_summary`` już pogrupował po lokalnej dobie), więc nie ma tu zależności od strefy.

Treść to DANE (już zredagowane po stronie ``claude_summary``): wyłuskujemy wyłącznie stringi,
klucze Jira i komentarz, a arkusz i tak sanityzuje znaki sterujące i blokuje wstrzyknięcie formuły.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel

from workmate.core.domain.day_comment import build_comment
from workmate.core.domain.timesheet import Person
from workmate.core.domain.worklog import extract_issue_keys
from workmate.core.errors import WorkMateError


class SubmissionRejected(WorkMateError):
    """Przysłany dokument nie przechodzi bramki: zły kształt, brak pola ``person`` albo rozjazd
    tożsamości. Fail-closed — drzwi odpowiadają nadawcy komunikatem, nie generują arkusza."""


class SubmittedSummary(BaseModel):
    """Sparsowany wynik ``claude_summary`` jednej osoby: e-mail (do cross-checku) + dane per dzień.

    ``person_git_email`` jest DEKLAROWANY (samozwańczy) — służy tylko weryfikacji wobec nadawcy,
    nigdy jako źródło atrybucji. ``since``/``until`` to okno raportu, oba WŁĄCZNIE (kontrakt
    ``claude_summary``: ``_date_range`` buduje dzień dla każdej daty ``[since, until]``). Serwis
    kart czasu przelicza ``until`` na okno PÓŁOTWARTE, żeby nie zgubić ostatniego dnia (niedzieli).
    ``issue_keys_by_day`` i ``comments_by_day`` zawierają tylko dni, dla których było CO wyłuskać
    (dzień bez kluczy/komentarza po prostu nie ma wpisu).
    """

    person_git_email: str
    since: date
    until: date
    issue_keys_by_day: dict[date, tuple[str, ...]]
    comments_by_day: dict[date, str]


def parse_submitted_summary(payload: Any) -> SubmittedSummary:
    """Zamień zdekodowany JSON ``claude_summary`` na klucze i komentarze per dzień.

    Twardo odrzuca (``SubmissionRejected``) dokument bez rozpoznawalnego kształtu — nie-obiekt,
    brak pola ``person`` (nie ma czego zweryfikować) albo brak listy ``days``. Pojedyncze
    uszkodzone dni pomijamy (degradacja): jeden dziwny wpis nie może zablokować całego tygodnia.
    Pusty wynik (same koszykowe dni bez kluczy) jest POPRAWNY — nie odrzucamy tygodnia bez commitów.
    """
    if not isinstance(payload, dict):
        raise SubmissionRejected("przysłany dokument nie jest obiektem JSON.")
    person = str(payload.get("person") or "").strip().lower()
    if not person:
        raise SubmissionRejected(
            "przysłany JSON nie ma pola 'person' (e-mail git) — nie mam czego zweryfikować."
        )
    days = payload.get("days")
    if not isinstance(days, list):
        raise SubmissionRejected(
            "przysłany JSON nie ma listy 'days' — to nie wynik claude_summary."
        )
    since = _parse_date(payload.get("since"))
    until = _parse_date(payload.get("until"))
    if since is None or until is None:
        raise SubmissionRejected(
            "przysłany JSON nie ma poprawnych pól 'since'/'until' — bez okna nie pobiorę Shifts."
        )
    if since > until:
        raise SubmissionRejected(f"okno jest odwrócone: since={since} > until={until}.")

    keys_by_day: dict[date, tuple[str, ...]] = {}
    comments_by_day: dict[date, str] = {}
    for day_data in days:
        if not isinstance(day_data, dict):
            continue
        day = _parse_date(day_data.get("date"))
        if day is None:
            continue
        keys = _issue_keys_for_day(day_data.get("commits"))
        if keys:
            keys_by_day[day] = keys
        comment = build_comment(
            llm_prose=_as_str_or_none(day_data.get("llm_prose")),
            commit_messages=_strings(day_data.get("commits"), "message"),
            prompt_texts=_strings(day_data.get("prompts"), "text"),
        )
        if comment:
            comments_by_day[day] = comment
    return SubmittedSummary(
        person_git_email=person,
        since=since,
        until=until,
        issue_keys_by_day=keys_by_day,
        comments_by_day=comments_by_day,
    )


def verify_submission_owner(sender: Person, submitted_person_git_email: str) -> None:
    """Cross-check pola ``person`` z tożsamością NADAWCY — rozjazd = odrzucenie (ADR 0037 reg. 3).

    Atrybucja i tak idzie z uwierzytelnionego nadawcy, więc to bramka BEZPIECZEŃSTWA na wypadek,
    gdy ktoś przez pomyłkę wyśle CUDZY eksport (albo podmieni lokalny ``git config user.email``).
    Nadawca bez ``git_email`` w mapie tożsamości = nie da się zweryfikować → odrzucamy fail-closed:
    bez ``git_email`` i tak nie przypisalibyśmy kluczy z commitów tej osobie (por. ``Person``).
    """
    expected = sender.git_email.strip().lower()
    actual = submitted_person_git_email.strip().lower()
    if not expected:
        raise SubmissionRejected(
            f"tożsamość {sender.source_id} nie ma skonfigurowanego git_email — nie mogę "
            "zweryfikować, że przysłany dokument należy do Ciebie (uzupełnij identities.yaml)."
        )
    if actual != expected:
        raise SubmissionRejected(
            f"pole 'person' w przysłanym JSON-ie ({actual!r}) nie zgadza się z Twoim git_email "
            f"({expected!r}) — nie przyjmuję cudzego eksportu (atrybucja idzie z nadawcy)."
        )


def _issue_keys_for_day(commits: Any) -> tuple[str, ...]:
    """Klucze Jira z komunikatów commitów jednego dnia, bez powtórzeń, w kolejności wystąpienia."""
    keys: dict[str, None] = {}
    for message in _strings(commits, "message"):
        for key in extract_issue_keys(message):
            keys.setdefault(key, None)
    return tuple(keys)


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _as_str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _strings(items: Any, key: str) -> list[str]:
    """Wartości ``key`` (jako string) z listy słowników; odporne na dziwny kształt.

    ``item.get(key) or ""`` (nie ``get(key, "")``): domyślna wartość działa tylko przy BRAKU
    klucza, a jawny ``null`` w JSON dałby ``str(None) == "None"`` — literał we wniosku. Ta sama
    dyscyplina co ``ClaudeSummaryStore._strings`` (kontrakt claude_summary jest ten sam).
    """
    if not isinstance(items, list):
        return []
    return [str(item.get(key) or "") for item in items if isinstance(item, dict)]
