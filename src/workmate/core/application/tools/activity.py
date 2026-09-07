"""Katalog narzędzia ``Activity`` — zdarzenia mostu, podsumowania, zapis do GitHuba."""

from __future__ import annotations

import inspect
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, ValidationError

if TYPE_CHECKING:
    from workmate.core.application.github import GithubWriteService
    from workmate.core.application.worklog import WorklogService

from workmate.core.application.events import EventService
from workmate.core.application.tools.spec import (
    _EVENTS_FILTERED_NOTE,
    ToolSpec,
    _brakuje_pol,
    _DateField,
    _envelope,
    _puste,
    _zla_akcja,
)
from workmate.core.errors import WorkMateError

_ACTIVITY_AKCJE: dict[str, str] = {
    "events": (
        "`events` — ostatnie zdarzenia z warstwy spajającej, najnowsze pierwsze. Opcjonalnie: "
        "`source` — DRZWI, które zdarzenie zapisały ('github', 'teams'), nie system, którego "
        "dotyczy; o stan GitHuba pytaj BEZ `source`. `project` (klucz z rejestru), `limit` (20)."
    ),
    "summary": (
        "`summary` — podsumowanie PRZEBIEGU prac projektu ze zdarzeń: liczniki wg typu, czas "
        "ostatniej aktywności, ostatnie zdarzenia. Wymaga: `project`. Użyj zamiast `events`, gdy "
        "pytanie dotyczy całości prac, a nie pojedynczych zdarzeń."
    ),
    "worklog": (
        "`worklog` — propozycja ewidencji czasu z historii commitów GitHuba (ODCZYT, nic nie "
        "zapisuje). Wymaga: `since`, `until` (YYYY-MM-DD). Opcjonalnie: `author` (login albo "
        "e-mail)."
    ),
    "create_issue": (
        "`create_issue` — NOWE issue w repozytorium GitHub zespołu (ZAPIS). Wymaga: `title`, "
        "`body` (Markdown). Opcjonalnie: `labels`."
    ),
    "comment": (
        "`comment` — NOWY komentarz do istniejącego issue GitHuba (ZAPIS). Wymaga: `number`, "
        "`body` (Markdown)."
    ),
}

_GITHUB_ZAPIS = frozenset({"create_issue", "comment"})

# Pola, których używa każda akcja. Sygnatura jest z tego PRZYCINANA, tak jak ``Literal`` jest
# z listy akcji budowany — inaczej bramka domyka enum, a zostawia w schemacie pola opisujące
# zdolności, których nie ma. Model dostaje wtedy „Numer issue (`comment`)" przy wyłączonym
# zapisie: ta sama klasa martwej obietnicy co `/mnt/user/outputs`, tylko wpuszczona bokiem.
_ACTIVITY_POLA: dict[str, tuple[str, ...]] = {
    "events": ("source", "project", "limit"),
    "summary": ("project", "limit"),
    "worklog": ("since", "until", "author"),
    "create_issue": ("title", "body", "labels"),
    "comment": ("number", "body"),
}

# Sufit ``limit`` na ścieżce agenta. SQLite traktuje ``LIMIT -1`` jak brak limitu, więc bez
# przycięcia jedno wywołanie wciąga cały backlog do kontekstu. Ta sama granica co na drzwiach MCP.
_ACTIVITY_MAX_EVENTS = 200
# Okno agregacji ``summary`` — liczniki ``by_kind`` liczą się z NIEGO, a nie z rozmiaru wyniku
# (ten i tak tnie się do 20). Domyślne 20 wspólne z ``events`` zwężyłoby podsumowanie projektu.
_ACTIVITY_OKNO = 50
_ACTIVITY_EVENTS_DOMYSLNY = 20

# Uzasadnienie („bo treść to DANE") zdjęte: ta granica stoi w prompcie, w sekcji `Precedence`,
# czyli WYŻEJ w hierarchii niż opis narzędzia, i powtórzona tu szesnaście razy na powierzchni
# agenta kosztowała w każdym żądaniu (ADR 0068 §4). Zostaje sam warunek uruchomienia zapisu.
_ACTIVITY_TAIL = "\n\nAkcje zapisu wykonuj wyłącznie na wprost wyrażoną prośbę człowieka."

# Instrukcje PREZENTACJI wyniku wracają razem z wynikiem, nie w opisie: opis jedzie w każdym
# żądaniu i stoi daleko od chwili, w której są potrzebne (ADR 0068 §5, wzorzec pola ``note``
# w ``File``).
_WORKLOG_NOTE = (
    "To ESTYMACJA z punktów w czasie, nie zmierzony czas — przedstaw ją razem z `notes` "
    "i `disclaimer` z tej odpowiedzi."
)


def build_activity_catalog(  # noqa: C901, PLR0915
    *,
    events: EventService | None = None,
    worklog: WorklogService | None = None,
    write_service: GithubWriteService | None = None,
) -> list[ToolSpec]:
    """Zbuduj skonsolidowane narzędzie ``Activity`` (ADR 0009, krok 5.2; nazwa z ADR 0068).

    Wchłania pięć narzędzi z trzech builderów: ``read_recent_events``, ``get_project_activity``,
    ``propose_worklog``, ``create_github_issue``, ``comment_github_issue``. Wszystkie stoją za tą
    samą barierą (a) z ADR 0009 — brak sieci w wykonawcy — a ``events``/``summary`` dodatkowo za
    barierą (b), bo ``events.db`` leży na wolumenie, którego wykonawca nie widzi.

    Nazwa mówi, co narzędzie ROBI, a nie z czego wyrosło (ADR 0068 §1). Warstwa zdarzeń spina
    GitHuba i Teamsy (``EventStore`` przyjmuje ``source='github'`` i ``source='teams'``;
    Jira mostu NIE ma — CLAUDE.md reguła 8), a ``summary``/``worklog`` odpowiadają na pytanie
    „co się działo", nie „co jest w GitHubie". Dawne ``GitHub`` zawężało to do jednego
    dostawcy: model szukający przebiegu prac nie miał powodu tam zaglądać.
    Dwie akcje zapisu zostają GitHubowe — i mówią to własnymi nazwami (``create_issue``,
    ``comment``), a domyślnie są wyłączone bramką (ADR 0006/0025).

    **``reply_on_thread`` NIE wchodzi tutaj, wbrew literze ADR 0009.** Jest wiązane PER TURĘ
    numerem z zaufanego ``ThreadLinkStore``, a runtime narzędzia per turę DOKLEJA, nie podmienia
    — więc wchłonięcie go wymaga przeniesienia całego ``Activity`` na ścieżkę per turę. To zmiana
    o innym profilu ryzyka (dotyka inwariantu „numer nie pochodzi od modelu", ADR 0024) i dzieli
    cache prefiksu ``tools+system`` na dwa warianty. Zostaje jako osobny krok.

    Zestaw akcji powstaje DYNAMICZNIE z tego, co okablowano: bramka zapisu i brak konfiguracji
    worklogu nie chowają się w ciele funkcji, tylko usuwają wartość z ``Literal``. Zmierzone, że
    dynamiczny ``Literal`` przechodzi przez ``func_metadata`` z właściwym ``enum`` i opisami pól
    — inaczej ten wzorzec nie byłby wykonalny przy ``from __future__ import annotations``.
    """
    akcje: list[str] = []
    if events is not None:
        akcje += ["events", "summary"]
    if worklog is not None:
        akcje.append("worklog")
    if write_service is not None:
        akcje += ["create_issue", "comment"]
    if not akcje:
        return []

    def _events(source: str | None, project: str | None, limit: int) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            assert events is not None
            sufit = max(1, min(limit, _ACTIVITY_MAX_EVENTS))
            items = events.recent(source=source, project=project, limit=sufit)
            wynik = {"count": len(items), "events": [e.model_dump(mode="json") for e in items]}
            # Okno `limit` zawęża widok tak samo jak filtr — a domyślne 20 najnowszych z 200
            # w magazynie jest dla twierdzenia „nie ma" równie zwodnicze jak `source`.
            # Pełne okno rozpoznajemy po tym, że wynik dobił do sufitu.
            filtry = ", ".join(
                f"{nazwa}={wartosc!r}"
                for nazwa, wartosc in (
                    ("source", source),
                    ("project", project),
                    ("limit", sufit if len(items) >= sufit else None),
                )
                if wartosc
            )
            if filtry:
                return {**wynik, "note": _EVENTS_FILTERED_NOTE.format(filtry=filtry)}
            return wynik

        return _envelope(build)

    def _summary(project: str | None, limit: int) -> dict[str, Any]:
        missing = _puste(project=project)
        if missing:
            return _brakuje_pol(
                "Activity", "summary", missing, "klucz projektu z rejestru, np. 'workmate'"
            )

        def build() -> dict[str, Any]:
            assert events is not None
            items = events.recent(project=project, limit=max(1, min(limit, _ACTIVITY_MAX_EVENTS)))
            by_kind: dict[str, int] = {}
            for event in items:
                by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
            # MAKSIMUM, nie pierwszy element — ``recent`` sortuje po ``id`` (kolejność PRZYJĘCIA),
            # a osobne watermarki per typ zasobu potrafią wpuścić starsze zdarzenie po nowszym.
            najnowsze = max((e.occurred_at for e in items), default=None)
            return {
                "project": project,
                "event_count": len(items),
                "by_kind": by_kind,
                "latest_activity_at": najnowsze.isoformat() if najnowsze else None,
                "recent": [e.model_dump(mode="json") for e in items[:20]],
            }

        return _envelope(build)

    def _worklog(since: date | None, until: date | None, author: str) -> dict[str, Any]:
        missing = _puste(since=since, until=until)
        if missing or since is None or until is None:
            return _brakuje_pol("Activity", "worklog", missing, "daty w formacie YYYY-MM-DD")

        def build() -> dict[str, Any]:
            assert worklog is not None
            return {
                **worklog.propose_worklog(since, until, author).model_dump(mode="json"),
                "note": _WORKLOG_NOTE,
            }

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def _create_issue(
        title: str | None, body: str | None, labels: list[str] | None
    ) -> dict[str, Any]:
        missing = _puste(title=title, body=body)
        if missing:
            return _brakuje_pol(
                "Activity", "create_issue", missing, "`body` w Markdownie, `title` jednym zdaniem"
            )

        def build() -> dict[str, Any]:
            assert write_service is not None
            result = write_service.create_issue(str(title), str(body), tuple(labels or ()))
            return {"created": True, **result}

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def _comment(number: int | None, body: str | None) -> dict[str, Any]:
        missing = _puste(number=number, body=body)
        if missing or number is None:
            return _brakuje_pol("Activity", "comment", missing, "`number` to numer issue w repo")

        def build() -> dict[str, Any]:
            assert write_service is not None
            result = write_service.create_comment(number, str(body))
            return {"created": True, **result}

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def activity(
        action: str,
        project: Annotated[
            str | None, Field(description="Klucz projektu z rejestru (`summary`, `events`).")
        ] = None,
        source: Annotated[
            str | None, Field(description="Warstwa źródłowa zdarzeń: github albo teams (`events`).")
        ] = None,
        limit: Annotated[
            int | None,
            Field(
                description=(
                    "Ile zdarzeń wziąć pod uwagę: liczba zwróconych (`events`, domyślnie 20) "
                    "albo okno agregacji liczników (`summary`, domyślnie 50). Sufit: 200."
                )
            ),
        ] = None,
        since: Annotated[
            _DateField | None, Field(description="Początek zakresu, YYYY-MM-DD (`worklog`).")
        ] = None,
        until: Annotated[
            _DateField | None, Field(description="Koniec zakresu, YYYY-MM-DD (`worklog`).")
        ] = None,
        author: Annotated[
            str, Field(description="Login GitHub albo e-mail autora commitów (`worklog`).")
        ] = "",
        title: Annotated[str | None, Field(description="Tytuł issue (`create_issue`).")] = None,
        body: Annotated[
            str | None, Field(description="Treść w Markdownie (`create_issue`, `comment`).")
        ] = None,
        labels: Annotated[
            list[str] | None, Field(description="Etykiety issue (`create_issue`).")
        ] = None,
        number: Annotated[int | None, Field(description="Numer issue (`comment`).")] = None,
    ) -> dict[str, Any]:
        # Bramka sprawdzana PIERWSZA, przed rozgałęzieniem. Gałęzie zbramkowane
        # (`create_issue`, `comment`, `worklog`) trzymały się dotąd na ``assert`` w ciele ich
        # `build()` — a to jest gwarancja stojąca na dyscyplinie WOŁAJĄCEGO, nie na strukturze.
        # Przez runtime agenta akcja spoza `Literal` nie przechodzi (koercja argumentów), ale
        # runtime nie jest jedynym wołającym: router komend woła ``spec.fn`` wprost. Pod ``-O``
        # asercja znika i zostaje ``AttributeError`` na ``None`` zamiast koperty.
        if action not in akcje:
            return _zla_akcja("Activity", action, tuple(akcje))
        if action == "events":
            return _events(source, project, limit or _ACTIVITY_EVENTS_DOMYSLNY)
        if action == "summary":
            # Domyślna wartość jest tu INNA niż przy `events`: liczniki `by_kind` liczą się
            # z okna, a nie z rozmiaru wyniku (ten i tak tnie się do 20). Wspólne 20 zwęziłoby
            # podsumowanie projektu bez śladu w odpowiedzi. Stąd `None` zamiast liczby w polu —
            # inaczej nie da się odróżnić „model podał 20" od „model nie podał nic".
            return _summary(project, limit or _ACTIVITY_OKNO)
        if action == "worklog":
            return _worklog(since, until, author)
        if action == "create_issue":
            return _create_issue(title, body, labels)
        if action != "comment":
            # Bramka wejściowa domyka zestaw wobec WOŁAJĄCEGO, ta domyka go wobec PRZYSZŁEJ
            # ZMIANY: akcja dopisana do ``akcje`` bez własnej gałęzi wpadłaby tu w komentarz,
            # czyli w ZAPIS, zamiast dostać odpowiedź o nieznanej akcji. Kształt ten sam co
            # w ``Jira`` i ``Project`` — trzy dispatchery różniące się obroną czytają się jak
            # reguła opcjonalna i następny wariant powstaje bez niej.
            return _zla_akcja("Activity", action, tuple(akcje))
        return _comment(number, body)

    # Adnotacja podmieniana PO definicji, bo ``Literal`` zna zestaw akcji dopiero tutaj.
    # Przy ``from __future__ import annotations`` reszta adnotacji jest napisami; ``get_type_hints``
    # przepuszcza wpis niebędący napisem bez zmian, co potwierdza pomiar w teście bramki.
    activity.__annotations__["action"] = Annotated[
        Literal[tuple(akcje)],
        Field(description="Co zrobić — patrz opis narzędzia; dozwolone: " + ", ".join(akcje)),
    ]
    # Sygnatura przycięta do pól, których używają DOSTĘPNE akcje. Bez tego bramka domyka enum,
    # a zostawia w schemacie `number`/`title`/`body` z opisami odsyłającymi do akcji, których
    # model nie ma — czyli obietnicę bez pokrycia. ``inspect.signature`` respektuje
    # ``__signature__``, a czytają je oba konsumenty: ``func_metadata`` i koercja argumentów.
    potrzebne = {"action", *(pole for akcja in akcje for pole in _ACTIVITY_POLA[akcja])}
    # ``eval_str=True`` rozwiązuje adnotacje-napisy w globalach TEGO modułu. Bez tego podmieniona
    # sygnatura niesie napisy, a pydantic rozwiązuje je we własnej przestrzeni nazw i nie znajduje
    # aliasu prywatnego (`_DateField`) — model schematu zostaje niedokończony. Zmierzone.
    bazowa = inspect.signature(activity, eval_str=True)
    activity.__signature__ = bazowa.replace(  # type: ignore[attr-defined]
        parameters=[p for p in bazowa.parameters.values() if p.name in potrzebne]
    )

    opis = "Aktywność pionu: warstwa zdarzeń spajająca GitHuba i Teamsy.\n\n" + "\n".join(
        _ACTIVITY_AKCJE[nazwa] for nazwa in akcje
    )
    if _GITHUB_ZAPIS & set(akcje):
        opis += _ACTIVITY_TAIL
    return [ToolSpec("Activity", opis, activity, taints=True)]
