"""Domena ewidencji czasu (ADR 0034) — historia commitów → propozycja godzin.

CZYSTA agregacja: bez I/O, bez SDK, bez zegara (okno dat podaje wołający). Wejściem są już
zmapowane commity (białą listą pól, w adapterze/serwisie), wyjściem PROPOZYCJA — nigdy zapis.
Ścieżki zapisu NIE MA i nie było to uproszczenie implementacji: zapis czasu do Jiry wycięto
wraz z resztą ADR 0034 (godziny wchodzą dziś arkuszem WorklogPRO, importowanym przez człowieka
— ADR 0035). Wiadomość commita zostaje DANĄ, nigdy poleceniem (inwariant z ADR 0006).

Model czasu: commity są PUNKTAMI, nie odcinkami — praca między nimi jest niewidoczna. Sesję
tniemy, gdy przerwa przekroczy próg ALBO zmieni się doba kalendarzowa (wpis worklogu dotyczy
JEDNEGO dnia, więc sesja nie może przekraczać północy). Do rozpiętości sesji doliczamy
„rozbieg" — pracę przed pierwszym commitem, której żaden commit nie widzi. To ESTYMACJA
i tak jest oznaczona (``confidence``, ``disclaimer``): jeden commit na koniec dnia da rozbieg,
a nie osiem godzin.

Rachunki prowadzimy w MINUTACH (``int``), a godziny wyprowadzamy — dzięki temu sumy dzienne
i per zgłoszenie sumują się DOKŁADNIE do całości, bez dryfu zaokrągleń float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from workmate.core.domain.guards import JIRA_KEY_RE
from workmate.core.domain.jira_time import parse_jira_timestamp

# Strefa, w której domyślnie liczymy dobę kalendarzową. Nazwa IANA, nie offset — zmiana czasu
# przesuwałaby granicę doby o godzinę przez pół roku (patrz ``SessionPolicy.tz``).
DEFAULT_TZ = ZoneInfo("Europe/Warsaw")

# Ile SHA-ów sesji pokazujemy. Pełna lista przy oknie 500 commitów to kilkanaście kilobajtów
# w kontekście modelu za każdym wywołaniem — próbka wystarcza do rozpoznania pracy, a licznik
# (``commit_count``) i tak niesie pełną liczbę.
_SHA_SAMPLE = 5

# Skan klucza Jira W TEKŚCIE wiadomości commita. Kształt bierzemy z jednoźródłowego
# ``JIRA_KEY_RE`` (``domain/guards.py``), dokładając granice — bez nich ``ABC-12`` wpadłoby
# ze środka ``XABC-123`` albo ``WT-1`` z ``WT-12``. Wyłącznie WIELKIE litery: małe (``wt-1``)
# to zwykły tekst, nie klucz.
_KEY_IN_TEXT_RE = re.compile(rf"(?<![A-Za-z0-9_])(?:{JIRA_KEY_RE.pattern})(?![A-Za-z0-9_])")

# Białe znaki sterujące w wiadomości commita — resztę WYCINAMY (nie rzucamy). To ścieżka
# ODCZYTU: jeden dziwny commit sprzed roku nie może wywrócić raportu. Zapis ma własny,
# ostrzejszy strażnik (``reject_dangerous_content``), który rzuca.
_ALLOWED_CONTROL = frozenset("\t")

_DISCLAIMER = (
    "ESTYMACJA z historii commitów — commity to punkty w czasie, nie odcinki pracy. "
    "Zweryfikuj godziny przed zapisem do Jiry."
)
_NOTE_NO_COMMITS = (
    "Brak commitów w tym oknie. Sprawdź, czy 'author' pasuje do loginu GitHub albo adresu "
    "e-mail z 'git config user.email' — inny adres da cichy zerowy wynik."
)
_NOTE_UNATTRIBUTED = (
    "Część sesji nie ma klucza Jira w wiadomości commita — te godziny są poza podziałem "
    "na zgłoszenia (pole 'unattributed_hours'). Przypisz je ręcznie."
)
_NOTE_DEFAULT_BRANCH = (
    "Widoczne są tylko commity gałęzi domyślnej — praca na niezmerge'owanych gałęziach "
    "nie wchodzi do tego zestawienia."
)
_NOTE_TRUNCATED = (
    "Historia commitów została UCIĘTA na limicie pobrania — GitHub zwraca od najnowszych, więc "
    "brakuje NAJSTARSZYCH dni okna, a godziny są zaniżone. Zawęź zakres dat albo podaj 'author'."
)


class Commit(BaseModel):
    """Pojedynczy commit sprowadzony do pól potrzebnych do estymacji (biała lista).

    ``message`` jest już znormalizowana (pierwsza linia, bez znaków sterujących) — to DANE
    ze źródła niezaufanego, używane wyłącznie do wyciągnięcia kluczy Jira i podglądu.
    """

    sha: str
    message: str = ""
    authored_at: datetime
    author_login: str = ""
    author_email: str = ""
    url: str = ""


class WorkSession(BaseModel):
    """Ciąg commitów bez przerwy dłuższej niż próg, w obrębie JEDNEJ doby kalendarzowej."""

    day: date
    started_at: datetime
    ended_at: datetime
    commit_count: int
    minutes: int
    hours: float
    # PRÓBKA (najstarsze ``_SHA_SAMPLE``), nie pełna lista — pełną liczbę niesie ``commit_count``.
    shas: tuple[str, ...] = ()
    issue_keys: tuple[str, ...] = ()
    confidence: str = "low"


class DayTotal(BaseModel):
    """Suma estymowanego czasu w jednej dobie kalendarzowej."""

    day: date
    minutes: int
    hours: float
    commit_count: int
    issue_keys: tuple[str, ...] = ()


class IssueTotal(BaseModel):
    """Suma estymowanego czasu przypisana do jednego zgłoszenia Jira."""

    issue_key: str
    minutes: int
    hours: float
    commit_count: int
    days: tuple[date, ...] = ()


class WorklogProposal(BaseModel):
    """PROPOZYCJA ewidencji — wynik odczytu, nic jeszcze nie trafiło do Jiry.

    ``unattributed_*`` to czas sesji bez klucza Jira w wiadomości commita. ``notes`` niesie
    jawne ostrzeżenia dla wołającego (pusty wynik, praca bez przypisania, ograniczenie gałęzi
    domyślnej) — model ma je PRZEKAZAĆ użytkownikowi, nie połykać.
    """

    since: date
    until: date
    author: str = ""
    sessions: tuple[WorkSession, ...] = ()
    by_day: tuple[DayTotal, ...] = ()
    by_issue: tuple[IssueTotal, ...] = ()
    commit_count: int = 0
    total_minutes: int = 0
    total_hours: float = 0.0
    unattributed_minutes: int = 0
    unattributed_hours: float = 0.0
    notes: tuple[str, ...] = ()
    disclaimer: str = _DISCLAIMER


@dataclass(frozen=True)
class SessionPolicy:
    """Nastawy grupowania i estymacji — jeden komplet pokręteł dla serwisu i testów.

    ``idle_gap_minutes`` — przerwa kończąca sesję. ``ramp_up_minutes`` — praca doliczana PRZED
    pierwszym commitem sesji (commit jest efektem, nie początkiem pracy). ``round_minutes`` —
    zaokrąglenie W GÓRĘ (ewidencja czasu jest kwantowana). ``max_session_hours`` — sufit jednej
    sesji, backstop przed absurdem z rzadkiego commitowania.

    ``tz`` — strefa, w której liczymy dobę kalendarzową. Nazwana strefa IANA, NIE stały offset:
    pierwotny kompromis ADR 0034 („czystość domeny") upadł, gdy ADR 0035 wprowadził ``week.py``
    liczący granice przez ``ZoneInfo`` i dodał ``tzdata`` do zależności RDZENIA. Ze stałym
    offsetem granica doby przez pół roku wypadała o godzinę obok, więc commity z okolic północy
    lądowały w sąsiednim dniu.
    """

    idle_gap_minutes: int = 90
    ramp_up_minutes: int = 30
    round_minutes: int = 15
    max_session_hours: float = 8.0
    tz: ZoneInfo = field(default=DEFAULT_TZ)


def normalize_commit_message(raw: str, *, max_len: int = 200) -> str:
    """Sprowadź wiadomość commita do jednej bezpiecznej linii (pierwsza linia, bez sterujących).

    Znaki sterujące WYCINAMY zamiast rzucać — to ścieżka odczytu, a historia repo bywa dziwna
    i nie może wywrócić raportu. Nadmiar długości ucinamy z wielokropkiem (opis, nie treść).
    """
    first_line = str(raw or "").splitlines()[0] if raw else ""
    kept = [
        ch
        for ch in first_line
        if ch in _ALLOWED_CONTROL
        or not (ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F)
    ]
    text = " ".join("".join(kept).split())
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def extract_issue_keys(message: str) -> tuple[str, ...]:
    """Wyciągnij klucze Jira z wiadomości commita, bez powtórzeń, w kolejności wystąpienia.

    BEZ zawężania do projektu. Zawężanie miało sens, dopóki propozycja karmiła zapis do
    konkretnego projektu Jiry (ADR 0034); dziś to czysty raport, a wspomniany mimochodem
    ``OPS-9`` opisuje pracę, która naprawdę się odbyła. Wyciągamy WYŁĄCZNIE klucze; żadna
    inna treść commita nie ma wpływu na wynik (treść to DANE, nie polecenia).
    """
    seen: dict[str, None] = {}
    for match in _KEY_IN_TEXT_RE.finditer(message or ""):
        seen.setdefault(match.group(0), None)
    return tuple(seen)


def map_github_commits(raw: list[dict[str, Any]]) -> list[Commit]:
    """Zmapuj surowe JSON-y GitHuba (``/commits``) na model domeny — BIAŁĄ LISTĄ pól (bez diffów).

    Wpisy bez rozpoznawalnego znacznika czasu pomijamy: ścieżka ODCZYTU, więc jeden dziwny commit
    nie może wywrócić raportu (daty nie zgadujemy). Wiadomość normalizujemy (pierwsza linia, bez
    znaków sterujących) — to DANE, użyte wyłącznie do wyłuskania kluczy Jira i podglądu.

    Wspólny mapper dla propozycji czasu (ADR 0034) i źródła commitów kart czasu (ADR 0036) —
    jedna, testowana biała lista zamiast dwóch, które mogłyby się rozjechać.
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


def _as_dict(value: Any) -> dict[str, Any]:
    """Zagnieżdżony obiekt JSON jako słownik albo pusty — odporność na dziwny kształt."""
    return value if isinstance(value, dict) else {}


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    """Doba kalendarzowa ``moment`` w strefie ``tz`` (naiwny czas traktujemy jako UTC).

    Konwersja przez ``astimezone`` uwzględnia zmianę czasu — w odróżnieniu od stałego offsetu,
    który przez pół roku przesuwałby granicę doby o godzinę.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz).date()


def estimate_minutes(span_minutes: float, policy: SessionPolicy) -> int:
    """Oszacuj minuty sesji: rozpiętość + rozbieg, przycięte sufitem, zaokrąglone W GÓRĘ.

    Sesja jednocommitowa ma zerową rozpiętość — zostaje sam rozbieg (świadomie skromnie:
    wolimy zaniżyć i dać człowiekowi podnieść, niż zawyżyć czyjąś ewidencję).
    """
    raw = max(0.0, span_minutes) + max(0, policy.ramp_up_minutes)
    capped = min(raw, policy.max_session_hours * 60)
    step = max(1, policy.round_minutes)
    return int(-(-capped // step) * step)


def session_confidence(span_minutes: float, commit_count: int, policy: SessionPolicy) -> str:
    """Oceń wiarygodność estymacji sesji: ``high`` / ``medium`` / ``low``.

    ``low`` gdy sesja ma jeden commit (rozpiętość zerowa — czysty domysł) albo gdy sufit
    przyciął rozpiętość (wiemy, że wynik jest ZŁY, tylko nie wiemy o ile). ``high`` gdy
    commitów jest co najmniej trzy i rozpiętość przekracza rozbieg (praca widoczna w danych).
    """
    if commit_count <= 1:
        return "low"
    if span_minutes + policy.ramp_up_minutes > policy.max_session_hours * 60:
        return "low"
    if commit_count >= 3 and span_minutes >= policy.ramp_up_minutes:
        return "high"
    return "medium"


def group_sessions(commits: list[Commit], policy: SessionPolicy) -> tuple[WorkSession, ...]:
    """Pogrupuj commity w sesje: przerwa > progu ALBO zmiana doby zaczyna nową sesję.

    Cięcie po dobie jest TWARDE — wpis worklogu dotyczy jednego dnia, więc sesja przez północ
    byłaby niezapisywalna. Wejście sortujemy sami (kolejność z API bywa malejąca).
    """
    if not commits:
        return ()
    ordered = sorted(commits, key=lambda c: c.authored_at)
    groups: list[list[Commit]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        gap_minutes = (current.authored_at - previous.authored_at).total_seconds() / 60
        same_day = local_day(current.authored_at, policy.tz) == local_day(
            previous.authored_at, policy.tz
        )
        if gap_minutes > policy.idle_gap_minutes or not same_day:
            groups.append([current])
        else:
            groups[-1].append(current)
    return tuple(_as_session(group, policy) for group in groups)


def build_proposal(
    commits: list[Commit],
    *,
    since: date,
    until: date,
    policy: SessionPolicy,
    author: str = "",
    truncated: bool = False,
) -> WorklogProposal:
    """Złóż pełną propozycję: sesje, sumy dzienne, sumy per zgłoszenie i ostrzeżenia.

    ``truncated`` mówi, że źródło oddało tylko część commitów okna (sufit pobrania). Wynik jest
    wtedy ZANIŻONY i musi to powiedzieć wprost — inaczej propozycja wygląda na kompletną.
    """
    sessions = group_sessions(commits, policy)
    by_day = _totals_by_day(sessions)
    by_issue, unattributed = _totals_by_issue(sessions)
    total_minutes = sum(session.minutes for session in sessions)
    notes: list[str] = []
    if not commits:
        notes.append(_NOTE_NO_COMMITS)
    else:
        notes.append(_NOTE_DEFAULT_BRANCH)
    if truncated:
        notes.append(_NOTE_TRUNCATED)
    if unattributed > 0:
        notes.append(_NOTE_UNATTRIBUTED)
    return WorklogProposal(
        since=since,
        until=until,
        author=author,
        sessions=sessions,
        by_day=by_day,
        by_issue=by_issue,
        commit_count=len(commits),
        total_minutes=total_minutes,
        total_hours=_hours(total_minutes),
        unattributed_minutes=unattributed,
        unattributed_hours=_hours(unattributed),
        notes=tuple(notes),
    )


def _as_session(group: list[Commit], policy: SessionPolicy) -> WorkSession:
    """Zamień ciąg commitów jednej sesji w podsumowanie z estymacją i pewnością."""
    started, ended = group[0].authored_at, group[-1].authored_at
    span_minutes = (ended - started).total_seconds() / 60
    minutes = estimate_minutes(span_minutes, policy)
    keys: dict[str, None] = {}
    for commit in group:
        for key in extract_issue_keys(commit.message):
            keys.setdefault(key, None)
    return WorkSession(
        day=local_day(started, policy.tz),
        started_at=started,
        ended_at=ended,
        commit_count=len(group),
        minutes=minutes,
        hours=_hours(minutes),
        shas=tuple(commit.sha for commit in group[:_SHA_SAMPLE]),
        issue_keys=tuple(keys),
        confidence=session_confidence(span_minutes, len(group), policy),
    )


def _totals_by_day(sessions: tuple[WorkSession, ...]) -> tuple[DayTotal, ...]:
    """Zsumuj sesje w doby kalendarzowe (rosnąco po dacie)."""
    buckets: dict[date, list[WorkSession]] = {}
    for session in sessions:
        buckets.setdefault(session.day, []).append(session)
    totals = []
    for day in sorted(buckets):
        same_day = buckets[day]
        minutes = sum(session.minutes for session in same_day)
        keys: dict[str, None] = {}
        for session in same_day:
            for key in session.issue_keys:
                keys.setdefault(key, None)
        totals.append(
            DayTotal(
                day=day,
                minutes=minutes,
                hours=_hours(minutes),
                commit_count=sum(session.commit_count for session in same_day),
                issue_keys=tuple(keys),
            )
        )
    return tuple(totals)


def _totals_by_issue(sessions: tuple[WorkSession, ...]) -> tuple[tuple[IssueTotal, ...], int]:
    """Rozdziel czas sesji na jej zgłoszenia; zwróć sumy i minuty BEZ przypisania.

    Sesja wspominająca kilka kluczy dzieli czas RÓWNO między nie — nie mamy danych, żeby
    ważyć inaczej, a przypisanie całości pierwszemu kluczowi zawyżałoby go systematycznie.
    Podział robimy w minutach z resztą do pierwszego klucza, więc sumy zgadzają się CO DO
    MINUTY z całością (bez dryfu float). Sesja bez klucza idzie w całości do „bez przypisania".
    """
    minutes_by_key: dict[str, int] = {}
    commits_by_key: dict[str, int] = {}
    days_by_key: dict[str, dict[date, None]] = {}
    unattributed = 0
    for session in sessions:
        keys = session.issue_keys
        if not keys:
            unattributed += session.minutes
            continue
        for key, share in _split_minutes(session.minutes, keys).items():
            minutes_by_key[key] = minutes_by_key.get(key, 0) + share
            commits_by_key[key] = commits_by_key.get(key, 0) + session.commit_count
            days_by_key.setdefault(key, {}).setdefault(session.day, None)
    totals = tuple(
        IssueTotal(
            issue_key=key,
            minutes=minutes_by_key[key],
            hours=_hours(minutes_by_key[key]),
            commit_count=commits_by_key[key],
            days=tuple(sorted(days_by_key[key])),
        )
        # Malejąco po czasie — najpierw to, na czym praca faktycznie stała.
        for key in sorted(minutes_by_key, key=lambda k: (-minutes_by_key[k], k))
    )
    return totals, unattributed


def _split_minutes(minutes: int, keys: tuple[str, ...]) -> dict[str, int]:
    """Podziel minuty równo między klucze; resztę z dzielenia oddaj pierwszym kluczom."""
    base, remainder = divmod(minutes, len(keys))
    return {key: base + (1 if index < remainder else 0) for index, key in enumerate(keys)}


def _hours(minutes: int) -> float:
    """Minuty → godziny dziesiętne zaokrąglone do 2 miejsc (prezentacja; źródłem są minuty)."""
    return round(minutes / 60, 2)
