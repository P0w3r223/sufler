"""Czysta logika selekcji zdarzeń GitHub (bez I/O — pełna testowalność, jak teams_graph.selection).

Mapuje surowe JSON z GitHub REST na domenowe ``NewEvent`` i egzekwuje dwie decyzje pollera:
strażnik pętli SELF-PING (pomijamy zdarzenia autorstwa konta PAT — inaczej issue utworzone
przez bota wróciłoby jako powiadomienie) oraz wyliczenie nowego watermarku ``since`` (koszt API).
Deduplikację po ``(source, external_id, kind)`` egzekwuje magazyn — tu jej nie powtarzamy.
Treść (tytuł/opis/autor) to DANE, nie polecenia — nie interpretujemy jej.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from workmate.core.domain.events import NewEvent

logger = logging.getLogger(__name__)

_SOURCE = "github"
# Sufit skrótu treści zdarzenia — pełny opis issue bywa długi, a zdarzenie ma być notką.
_MAX_SUMMARY = 500
# Konkluzje CI traktowane jako PORAŻKA (poza nimi „sukces" tylko dla ``success``; reszta —
# cancelled/skipped/neutral/action_required/stale — to szum, pomijamy).
_CI_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
# Stany recenzji PR niosące DECYZJĘ (reszta: komentarz/oczekująca/odrzucona — szum).
_REVIEW_STATES = {"APPROVED": "zatwierdzono", "CHANGES_REQUESTED": "poproszono o zmiany"}
_PULL_NUMBER_RE = re.compile(r"/pull/(\d+)")

# Strażnik pętli self-ping (ADR 0071 decyzja 6). Do 2026-09-07 pytał „czy autorem jest konto PAT" —
# i to była PRZESŁANKA FAŁSZYWA: konto bota założyło w tym repozytorium osiem zgłoszeń, z czego
# tylko dwa przez nasze narzędzie; pozostałe sześć powstało `gh` CLI i przez WWW z sesji Claude
# Code. Filtr po koncie zjadał wszystkie osiem, a echo miały dwa — reszta nie istniała nigdzie.
#
# Pytanie brzmi teraz „czy zapisały to NASZE DRZWI ZAPISU", a odpowiada na nie obecność echa
# w magazynie. Tabela wiąże rodzaj zdarzenia pollera z rodzajem echa; wszystko spoza niej NIE
# podlega self-skipowi wcale, bo drzwi zapisu są create-only na zgłoszeniach i komentarzach
# (reguła 7, ADR 0021) — nie ma drogi, którą moglibyśmy być autorem PR-a czy recenzji.
#
# Kształt kluczy jest KONTRAKTEM MIĘDZY DWOMA MODUŁAMI: echo zapisuje `external_id` z tego samego
# pola payloadu, którego używa mapper. Cicha zmiana po którejkolwiek stronie wyłączyłaby strażnika
# bez jednego czerwonego testu — dlatego pilnuje tego osobna sonda porównująca oba klucze
# WYLICZONE, a nie przepisane (`tests/adapters/inbound/github/test_echo_kontrakt.py`).
_ECHO_KINDS = {
    "issue_opened": "github_issue_created",
    "issue_comment": "github_comment_created",
    "pr_comment": "github_comment_created",
}


def map_issue(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowe issue → ``NewEvent`` (kind ``issue_opened``); ``None`` dla PR-a lub bez daty.

    GitHub zwraca PR-y w tym samym endpointcie co issue (mają klucz ``pull_request``) — tu je
    pomijamy, bo PR-y mapuje ``map_pull`` (ADR 0024). ``external_id`` = numer issue: dzięki dedupowi
    zdarzenie leci raz (przy pierwszym zobaczeniu), a późniejsze aktualizacje issue są pomijane.
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


def map_issue_closed(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowe issue → ``NewEvent`` (kind ``issue_closed``); ``None`` dla otwartych i PR-ów.

    Dane już przychodzą: ``list_issues`` prosi o ``state="all"``, więc każde zamknięte issue wraca
    w każdej rundzie — dotąd wpadało do ``map_issue``, które o stanie nie czyta nic i stemplowało
    ``issue_opened`` niezależnie od tego, czy issue jest otwarte. Warstwa była dziennikiem SAMYCH
    OTWARĆ i strukturalnie nie mogła powiedzieć, co jest otwarte (ADR 0071, incydent 2026-09-04).

    ``actor`` PUSTY — to wybór, nie brak danych (ADR 0071 decyzja 2). ``user`` z payloadu to ten,
    kto issue ZAŁOŻYŁ, więc na zamknięciu nazwałby autora zamykającym: nowy fałsz w miejsce
    starego. ``closed_by`` jest dostępne i świadomie go nie bierzemy, dopóki self-skip filtruje po
    KONCIE: w tym repozytorium zamykającym jest zawsze konto PAT, więc ``actor`` z ``closed_by``
    kazałby strażnikowi zjeść wszystkie zamknięcia — czyli odtworzyć incydent wewnątrz jego
    własnej naprawy. Nazwanie zamykającego wraca po etapie 2, z własnym testem tej interakcji.

    ``external_id`` = ``{numer}#closed@{closed_at}``, a NIE ``{numer}#closed`` — jedyne miejsce,
    w którym ten mapper odchodzi od wzorca ``map_pull_state``, i to odejście jest sednem
    (ADR 0071 decyzja 3). ``{n}#closed`` koduje STAN („zostało kiedyś zamknięte"), a magazyn
    append-only stanu nie unosi: po cyklu zamknięcie → otwarcie → zamknięcie dedup połknąłby
    drugie zamknięcie i nie dałoby się go już odzyskać. Znacznik czasu czyni z tego FAKT
    („zamknięte o T"), nie kosztuje nic (wartość jest w payloadzie) i pozwala dołożyć obsługę
    ponownych otwarć BEZ migracji.

    Znacznik bierzemy z payloadu **dosłownie**, bez parsowania i ponownego formatowania. To nie
    jest ostrożność stylistyczna: klucz dedupu jedzie do magazynu, którego nie da się cofnąć, więc
    każda przyszła zmiana formatowania (strefa, mikrosekundy, ``Z`` kontra ``+00:00``) utworzyłaby
    NOWY klucz dla tego samego faktu i zamknięcie zdublowałoby się na zawsze. Do ``occurred_at``
    ten sam napis wolno sparsować — tam jest wartością, nie kluczem.

    BEZ zapasowego znacznika (ADR 0071 decyzja 4): gdy ``state == "closed"``, a ``closed_at`` nie
    ma, nie emitujemy nic i logujemy. ``map_pull_state`` bierze w tym miejscu ``updated_at`` —
    tutaj byłoby to WYMYŚLONYM znacznikiem w magazynie append-only, a przy decyzji 3 także kluczem
    zbudowanym z wartości, która nie opisuje zamknięcia. GitHub zawsze podaje ``closed_at`` dla
    zamkniętego issue, więc zapas kupowałby odporność na przypadek, który nie występuje.
    """
    if raw.get("pull_request"):
        return None
    number = raw.get("number")
    if number is None:
        return None
    if str(raw.get("state") or "").lower() != "closed":
        return None
    closed_at = raw.get("closed_at")
    if not closed_at:
        logger.warning(
            "Issue #%s ma state=closed bez closed_at — pomijam zamknięcie zamiast zmyślać "
            "znacznik w magazynie append-only (ADR 0071 decyzja 4)",
            number,
        )
        return None
    return NewEvent(
        source=_SOURCE,
        kind="issue_closed",
        external_id=f"{number}#closed@{closed_at}",
        actor="",
        title=f"Issue #{number} zamknięte",
        summary=_clip(str(raw.get("title") or "")),
        url=str(raw.get("html_url") or ""),
        occurred_at=_parse(str(closed_at)),
    )


def map_pull(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowy PR (element ``/issues`` z ``pull_request``) → ``NewEvent`` (``pr_opened``).

    ``None`` dla nie-PR (issue mapuje ``map_issue``) lub bez numeru/daty. ``external_id`` = numer
    PR; numery issue i PR w repo nie kolidują, a ``kind`` i tak różnicuje klucz dedup.
    """
    if not raw.get("pull_request"):
        return None
    number = raw.get("number")
    created = raw.get("created_at")
    if number is None or not created:
        return None
    return NewEvent(
        source=_SOURCE,
        kind="pr_opened",
        external_id=str(number),
        actor=_login(raw.get("user")),
        title=str(raw.get("title") or ""),
        summary=_clip(str(raw.get("body") or "")),
        url=str(raw.get("html_url") or ""),
        occurred_at=_parse(created),
    )


def map_comment(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowy komentarz → ``NewEvent``; ``None`` bez id/daty.

    Kind zależy od ``html_url``: ``/pull/`` → ``pr_comment``, inaczej ``issue_comment``. Komentarze
    issue i PR lecą WSPÓLNYM endpointem ``/issues/comments``. ``external_id`` = id komentarza
    (unikalne), więc każdy komentarz to osobne zdarzenie; numer wyłuskujemy z ``issue_url``.
    """
    comment_id = raw.get("id")
    created = raw.get("created_at")
    if comment_id is None or not created:
        return None
    html_url = str(raw.get("html_url") or "")
    is_pr = "/pull/" in html_url
    kind = "pr_comment" if is_pr else "issue_comment"
    noun = "PR" if is_pr else "issue"
    number = _issue_number(str(raw.get("issue_url") or ""))
    title = f"Komentarz do {noun} #{number}" if number else f"Komentarz do {noun}"
    return NewEvent(
        source=_SOURCE,
        kind=kind,
        external_id=str(comment_id),
        actor=_login(raw.get("user")),
        title=title,
        summary=_clip(str(raw.get("body") or "")),
        url=html_url,
        occurred_at=_parse(created),
    )


def map_review(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surową recenzję PR → ``NewEvent`` (``pr_review``); tylko DECYZJE (approved/changes).

    ``None`` dla recenzji-komentarza/oczekującej/odrzuconej (szum) oraz bez id/daty. Numer PR
    wyłuskujemy z ``html_url`` (``…/pull/{n}#…``) — recenzja pobierana jest per PR, ale niesie url.
    """
    review_id = raw.get("id")
    submitted = raw.get("submitted_at")
    state = str(raw.get("state") or "").upper()
    if review_id is None or not submitted or state not in _REVIEW_STATES:
        return None
    html_url = str(raw.get("html_url") or "")
    pr_no = _pull_number(html_url)
    decision = _REVIEW_STATES[state]
    title = f"Recenzja PR #{pr_no} — {decision}" if pr_no else f"Recenzja PR — {decision}"
    return NewEvent(
        source=_SOURCE,
        kind="pr_review",
        external_id=str(review_id),
        actor=_login(raw.get("user")),
        title=title,
        summary=_clip(str(raw.get("body") or "")),
        url=html_url,
        occurred_at=_parse(str(submitted)),
    )


def map_ci_run(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj surowy workflow run → ``NewEvent`` (``ci_success``/``ci_failure``); ``None`` inaczej.

    BIAŁA LISTA PÓL (bezpieczeństwo, ADR 0024): bierzemy WYŁĄCZNIE nazwę workflow, konkluzję, url
    przebiegu, numer PR i ``repository.html_url`` — NIGDY logów/jobów/commita/env (mogłyby nieść
    sekret). ``external_id`` = ``{run_id}#{run_attempt}``, bo ponowienie (re-run) dzieli ``run_id``
    i inaczej druga porażka zniknęłaby w dedupie. Gdy run dotyczy PR, ``url`` KANONIZUJEMY na stronę
    PR (cel wątku/auto-komentarza z Faz 2-3), a link do przebiegu ląduje w ``summary``. CI nie ma
    autora-człowieka → ``actor`` pusty (brak wektora pętli, brak self-skip).
    """
    run_id = raw.get("id")
    conclusion = str(raw.get("conclusion") or "").lower()
    updated = raw.get("updated_at")
    if run_id is None or not conclusion or not updated:
        return None
    if conclusion == "success":
        kind = "ci_success"
    elif conclusion in _CI_FAILURE_CONCLUSIONS:
        kind = "ci_failure"
    else:
        return None
    attempt = raw.get("run_attempt") or 1
    name = str(raw.get("name") or "workflow")
    run_url = str(raw.get("html_url") or "")
    pr_number = _first_pr_number(raw.get("pull_requests"))
    repo_html = _repo_html_url(raw.get("repository"))
    if pr_number and repo_html:
        url = f"{repo_html}/pull/{pr_number}"
        summary = f"Przebieg: {run_url}" if run_url else ""
        title = f"„{name}” na PR #{pr_number}"
    else:
        url = run_url
        summary = ""
        title = f"„{name}”"
    return NewEvent(
        source=_SOURCE,
        kind=kind,
        external_id=f"{run_id}#{attempt}",
        actor="",
        title=title,
        summary=summary,
        url=url,
        occurred_at=_parse(str(updated)),
    )


def map_pull_state(raw: dict[str, Any]) -> NewEvent | None:
    """Zmapuj PR z ``/pulls`` na TRANZYCJĘ → ``pr_merged``/``pr_closed``; ``None`` dla otwartych.

    Tranzycja to FAKT niezmienny (PR raz zmergowany taki zostaje), więc ``external_id`` =
    ``{numer}#merged``/``{numer}#closed`` — dedup magazynu emituje ją RAZ (ADR 0029). BIAŁA LISTA
    PÓL: numer, stan, ``merged_at``/``closed_at``, tytuł, url — NIGDY diffów/patchy. ``actor`` pusty
    (jak CI): tranzycja to obserwacja read-only, nie wektor pętli — bez self-skip, żeby merge PR-a
    autorstwa konta PAT też był widoczny.
    """
    number = raw.get("number")
    if number is None:
        return None
    if raw.get("merged_at"):
        kind, suffix, when, verb = "pr_merged", "merged", raw.get("merged_at"), "zmergowany"
    elif str(raw.get("state") or "").lower() == "closed":
        when = raw.get("closed_at") or raw.get("updated_at")
        kind, suffix, verb = "pr_closed", "closed", "zamknięty"
    else:
        return None
    if not when:
        return None
    return NewEvent(
        source=_SOURCE,
        kind=kind,
        external_id=f"{number}#{suffix}",
        actor="",
        title=f"PR #{number} {verb}",
        summary=_clip(str(raw.get("title") or "")),
        url=str(raw.get("html_url") or ""),
        occurred_at=_parse(str(when)),
    )


def diff_branches(
    raw_branches: Sequence[dict[str, Any]],
    previous: dict[str, str] | None,
    *,
    repo: str = "",
    project: str = "",
    occurred_at: datetime,
    truncated: bool = False,
) -> tuple[list[NewEvent], dict[str, str]]:
    """Wykryj pushy/usunięcia gałęzi różnicą HEAD SHA między rundami (ADR 0029, zgrubne).

    Pierwsza runda (``previous is None``) SEEDUJE mapę bez zdarzeń (inaczej wszystkie gałęzie
    wyglądałyby jak świeży push). Potem: nowa gałąź lub zmiana SHA → ``branch_pushed``; gałąź
    zniknęła → ``branch_deleted``. ``external_id`` = ``{gałąź}@{sha}`` (dedup emituje raz;
    force-push = nowy SHA = nowe zdarzenie). BIAŁA LISTA PÓL: nazwa gałęzi i SHA — nic więcej.
    ``actor`` pusty (obserwacja, nie wektor pętli). ``occurred_at`` podaje wołający (detekcja jest
    KLIENCKA, bez znacznika GitHuba), więc rdzeń nie woła zegara.

    ``truncated`` mówi, że lista gałęzi mogła być NIEPEŁNA (sufit stron klienta). Wtedy „nie ma
    jej w wyniku" nie znaczy „została usunięta", więc: żadnych ``branch_deleted``, a mapa jest
    ZŁĄCZENIEM poprzedniej z bieżącą — inaczej gałąź wypadła z mapy i przy następnej pełnej
    rundzie wróciłaby jako świeży push. Domyślne ``False`` = zachowanie dotychczasowe.
    """
    current = {
        str(b.get("name") or ""): str((b.get("commit") or {}).get("sha") or "")
        for b in raw_branches
        if b.get("name")
    }
    if previous is None:
        return [], current
    events: list[NewEvent] = []
    for name, sha in current.items():
        if sha and previous.get(name) != sha:
            events.append(_branch_event("branch_pushed", name, sha, occurred_at, repo, project))
    if truncated:
        # Ucięcie jest CICHE po stronie klienta, a ``events.db`` jest append-only — zmyślone
        # „usunięto gałąź" zostaje w nim (i w digestach) na zawsze. Milczenie o usunięciach jest
        # tu jedynym odwracalnym błędem: gałąź naprawdę usunięta zgłosi się w pełnej rundzie.
        return events, {**previous, **current}
    for name, sha in previous.items():
        if name not in current:
            events.append(_branch_event("branch_deleted", name, sha, occurred_at, repo, project))
    return events, current


def _branch_event(
    kind: str, name: str, sha: str, when: datetime, repo: str, project: str
) -> NewEvent:
    pushed = kind == "branch_pushed"
    return NewEvent(
        source=_SOURCE,
        kind=kind,
        external_id=f"{name}@{sha}" + ("" if pushed else "#deleted"),
        actor="",
        title=f"Push do gałęzi {name}" if pushed else f"Usunięto gałąź {name}",
        summary=f"HEAD {sha[:12]}" if sha else "",
        url="",
        repo=repo,
        project=project,
        occurred_at=when,
    )


def select_events(
    raw_issues: Sequence[dict[str, Any]],
    raw_comments: Sequence[dict[str, Any]],
    raw_runs: Sequence[dict[str, Any]] = (),
    raw_reviews: Sequence[dict[str, Any]] = (),
    raw_pulls: Sequence[dict[str, Any]] = (),
    *,
    echo_seen: Callable[[str, str], bool],
    watch_kinds: tuple[str, ...] = ("issues", "comments"),
    runs_since: str = "",
    reviews_since: str = "",
    repo: str = "",
    project: str = "",
) -> list[NewEvent]:
    """Zmapuj pobrane zasoby na zdarzenia, egzekwuj self-skip i watermark, posortuj po czasie.

    ``watch_kinds`` rozstrzyga issue vs PR na WSPÓLNYM endpoincie ``/issues``: ``issue_opened``
    tylko gdy „issues", ``pr_opened`` tylko gdy „pulls". CI i recenzje filtrujemy watermarkiem TU
    (ich endpointy nie mają ``since``), więc przy starcie nie zalewa nas backlog. CI nie podlega
    self-skip (nie ma autora-człowieka); dedup magazynu domyka poprawność w każdym przypadku.
    ``repo`` (``owner/repo``) i ``project`` (klucz z rejestru) STEMPLUJEMY na każdym zdarzeniu
    (ADR 0028/0029) — do atrybucji i filtrowania po projekcie; ``external_id`` zostaje bez zmian
    (drzwi single-repo; złożenie repo w id — ``composite_external_id`` — wejdzie przy multi-repo).

    ``echo_seen(external_id, rodzaj_echa)`` to strażnik pętli self-ping (ADR 0071 decyzja 6):
    odpowiada, czy w magazynie leży echo, które mogły zostawić WYŁĄCZNIE nasze drzwi zapisu.
    Wstrzykiwany, bo ten moduł jest wolny od I/O i ma taki zostać — dokładnie jak zegar, który
    ``diff_branches`` bierze od wołającego. WYMAGANY, bez wartości domyślnej: pominięty przez
    przeoczenie wyłączałby strażnika pętli w ciszy, a to najgorszy możliwy rodzaj wartości
    domyślnej. Wołający bez magazynu podaje jawnie predykat stale fałszywy i widać to w kodzie.
    """
    events: list[NewEvent] = []
    watch_issues = "issues" in watch_kinds
    watch_pulls = "pulls" in watch_kinds
    for raw in raw_issues:
        if raw.get("pull_request"):
            ev = map_pull(raw) if watch_pulls else None
        else:
            ev = map_issue(raw) if watch_issues else None
        if ev is not None and not _echo_naszych_drzwi(ev, echo_seen):
            events.append(ev)
        # Zamknięcie issue z TEJ SAMEJ odpowiedzi (ADR 0071 decyzja 1). Osobne wywołanie, nie
        # gałąź `else`: zamknięte issue ma dawać OBA fakty — otwarcie (dedup połknie powtórki)
        # i zamknięcie. Bez self-skip, jak tranzycje PR: `actor` jest pusty z rozmysłu, więc
        # strażnik po koncie i tak nie miałby czego porównywać, a zamknięcie przez konto PAT
        # ma być widoczne — to obserwacja stanu, nie wektor pętli self-ping.
        if not raw.get("pull_request") and watch_issues:
            zamkniecie = map_issue_closed(raw)
            if zamkniecie is not None:
                events.append(zamkniecie)
    for raw in raw_comments:
        ev = map_comment(raw)
        if ev is not None and not _echo_naszych_drzwi(ev, echo_seen):
            events.append(ev)
    for raw in raw_runs:
        ev = map_ci_run(raw)
        if ev is not None and _after_watermark(raw.get("updated_at"), runs_since):
            events.append(ev)
    for raw in raw_reviews:
        ev = map_review(raw)
        # Recenzje tracą self-skip BEZ ZAMIENNIKA (ADR 0071 decyzja 6): drzwi zapisu są
        # create-only na zgłoszeniach i komentarzach, więc nie ma drogi, którą bylibyśmy autorem
        # recenzji. Filtrowanie ich po koncie było czystą stratą — tą samą, co na zgłoszeniach.
        if ev is not None and _after_watermark(raw.get("submitted_at"), reviews_since):
            events.append(ev)
    for raw in raw_pulls:
        # Tranzycje PR: dedup (external_id ``{n}#merged``/``#closed``) emituje raz; ``actor=""`` →
        # bez self-skip (obserwacja, nie wektor pętli). Watermark zbędny — dedup domyka poprawność.
        ev = map_pull_state(raw)
        if ev is not None:
            events.append(ev)
    if repo or project:
        events = [e.model_copy(update={"repo": repo, "project": project}) for e in events]
    return sorted(events, key=lambda e: e.occurred_at)


def _echo_naszych_drzwi(event: NewEvent, echo_seen: Callable[[str, str], bool]) -> bool:
    """Czy TO zdarzenie zapisały nasze drzwi zapisu — czyli czy w magazynie leży jego echo.

    Rodzaj spoza ``_ECHO_KINDS`` nie podlega self-skipowi wcale i nie kosztuje zapytania do
    magazynu: dla PR-ów, recenzji, CI i zamknięć nie istnieje droga, którą moglibyśmy być
    autorem, więc pytanie byłoby bezprzedmiotowe.
    """
    echo_kind = _ECHO_KINDS.get(event.kind)
    return echo_kind is not None and echo_seen(event.external_id, echo_kind)


def _after_watermark(timestamp: Any, watermark: str) -> bool:
    """Czy znacznik jest NOWSZY niż watermark (ISO ``…Z``, porównanie leksykograficzne, ścisłe).

    UWAGA na model niezawodności: dla issue/komentarzy filtruje SERWER (``since`` inkluzywny), a
    ponowne dostarczenie granicy łapie dedup magazynu. Dla CI/recenzji to filtr KLIENCKI i
    WYKLUCZAJĄCY — co odrzuci, nie trafi do magazynu, więc dedup już tego nie odzyska. Poprawność
    CI/recenzji opiera się zatem na tym watermarku + hurtowym pobraniu strony, NIE na dedupie.
    Pusty watermark przepuszcza wszystko; brak znacznika też przepuszcza (wtedy dedup ochroni).
    """
    if not watermark:
        return True
    value = str(timestamp or "")
    return not value or value > watermark


def next_since(raws: list[dict[str, Any]], current: str, *, field: str = "updated_at") -> str:
    """Nowy watermark = najnowsza wartość ``field`` w partii (albo dotychczasowy watermark).

    GitHub zwraca stały format ISO ``…Z`` (bez zmiennej precyzji ułamków), więc porównanie
    leksykograficzne jest zgodne z chronologicznym. Watermark ogranicza koszt/backlog — poprawność
    (brak dubli) i tak zapewnia dedup magazynu, więc granica inkluzywna jest bezpieczna. ``field``
    to ``updated_at`` dla issue/PR/komentarzy/CI, a ``submitted_at`` dla recenzji.
    """
    newest = current
    for raw in raws:
        value = str(raw.get(field) or "")
        if value and (not newest or value > newest):
            newest = value
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


def _pull_number(url: str) -> str:
    """Numer PR z ``…/pull/{n}…`` (recenzje/komentarze PR); pusty, gdy brak dopasowania."""
    match = _PULL_NUMBER_RE.search(url)
    return match.group(1) if match else ""


def _first_pr_number(pulls: Any) -> str:
    """Numer pierwszego PR z ``workflow_run.pull_requests`` (biała lista); pusty, gdy brak."""
    if isinstance(pulls, list) and pulls:
        first = pulls[0]
        if isinstance(first, dict) and first.get("number") is not None:
            return str(first["number"])
    return ""


def _repo_html_url(repository: Any) -> str:
    """``repository.html_url`` z ``workflow_run`` (do kanonizacji url na PR); pusty, gdy brak."""
    if isinstance(repository, dict):
        return str(repository.get("html_url") or "")
    return ""


def _parse(value: str) -> datetime:
    """ISO-8601 z GitHuba (``…Z``) → aware UTC; niepoprawne → epoka (i tak zdedupowane)."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
