"""Katalogi Jiry — ``MyJiraTasks`` i sześcioakcyjne ``Jira`` (odczyt bez zapisu, ADR 0059)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, get_args

from pydantic import Field, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable as _Callable

    from workmate.core.application.jira_read import JiraReadService
    from workmate.core.application.my_jira_tasks import MyJiraTasksService

from workmate.core.application.tools.spec import (
    ToolSpec,
    _brakuje_pol,
    _envelope,
    _nie_znaleziono,
    _puste,
    _zla_akcja,
)
from workmate.core.domain.jira_tasks import split_by_assignment
from workmate.core.errors import WorkMateError


def build_my_jira_tasks_catalog(service: MyJiraTasksService) -> list[ToolSpec]:
    """Zbuduj narzędzia "moje zadania"/"moja historia" Jira (ADR 0054) — czysty ODCZYT, bez
    parametru tożsamości.

    ``service`` jest już ZAWĘŻONY do jednego konta (skonfigurowanego principala albo tożsamości
    nadawcy rozwiązanej PRZED zbudowaniem tego katalogu) — żadne z narzędzi nie przyjmuje
    parametru "czyje zadania", więc nie da się przez nie podejrzeć cudzej listy. Wchodzi bez
    bramki zapisu (nic nie mutuje, ADR 0006) — albo jako ``extra_catalog``/fabryka per nadawca
    (drzwi Teams), albo ADDYTYWNIE na serwerze MCP gdy skonfigurowano stały principal (jak 0040).

    To drugie wejście czyni z TEGO buildera (nie z całego modułu) powierzchnię ZAMROŻONĄ —
    obie nazwy trzyma golden-test ``test_mcp_tool_surface``. ``build_jira_catalog`` niżej
    jest swobodny.
    """

    def get_my_jira_tasks() -> dict[str, Any]:
        """Zwróć TWOJE otwarte zadania z Jiry, ROZDZIELONE na dwie grupy (ODCZYT — nic nie zmienia).

        Bez parametrów: wynik jest zawsze zawężony do konta powiązanego z pytającym. Zwraca DWIE
        osobne listy: ``assigned_to_me`` — zadania PRZYPISANE do Ciebie, oraz
        ``reported_by_me_unassigned`` — zadania ZGŁOSZONE przez Ciebie, ale NIEPRZYPISANE do
        nikogo (czekają na podjęcie). Każde zadanie ma ``key``, ``summary``, ``status``,
        ``priority``, ``assignee``, ``due_date``, ``url``. PRZEDSTAW te grupy OSOBNO (np. "oto
        twoje zadania" i "oto zadania zgłoszone przez ciebie, nieprzypisane do nikogo") — NIE
        mieszaj ich w jedną listę. Obie puste = brak otwartych zadań. ``truncated=true`` znaczy,
        że zadań było więcej — POWIEDZ wtedy, że pokazujesz część. Użyj, gdy użytkownik pyta o
        SWOJE otwarte/bieżące zadania; do zadań ZAKOŃCZONYCH (historia) użyj get_my_jira_history.
        """
        # UWAGA: ta docstringa JEST opisem narzędzia MCP i jest zamrożona bajt w bajt
        # (``tests/adapters/test_mcp_tool_surface.py``). Zdanie o ``truncated`` dopisano
        # ŚWIADOMIE razem z aktualizacją baseline'u (ADR 0068): klucz istniał w wyniku od
        # rundy wcześniej, a opis o nim milczał — model widział ucięty wycinek jako całość.

        def build() -> dict[str, Any]:
            tasks, truncated = service.my_open_tasks()
            assigned, unassigned = split_by_assignment(tasks)
            return {
                "assigned_to_me": [t.model_dump(mode="json") for t in assigned],
                "reported_by_me_unassigned": [t.model_dump(mode="json") for t in unassigned],
                "count": len(assigned) + len(unassigned),
                "truncated": truncated,
            }

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def get_my_jira_history(since: str = "", until: str = "") -> dict[str, Any]:
        """Zwróć TWOJE ZAKOŃCZONE zadania z Jiry — historię pracy (ODCZYT — nic nie zmienia).

        ``since``/``until`` to opcjonalne daty ``YYYY-MM-DD`` zawężające po dacie ROZWIĄZANIA
        zgłoszenia (np. pytanie "moja historia zadań w tym roku" → ``since='RRRR-01-01'``; puste
        pole = bez ograniczenia z tej strony). Zwraca ``tasks`` — każde z ``key``, ``summary``,
        ``status``, ``resolved`` (data zakończenia), ``url`` — najnowsze pierwsze, maks. 50.
        ``truncated=true`` znaczy, że wyników było więcej — POWIEDZ wtedy, że pokazujesz 50
        najnowszych i zaproponuj węższy zakres dat. Użyj, gdy użytkownik pyta o zadania
        ZAKOŃCZONE/zamknięte/historię pracy; do OTWARTYCH służy get_my_jira_tasks.
        """

        def build() -> dict[str, Any]:
            tasks, truncated = service.my_history(since, until)
            return {
                "count": len(tasks),
                "truncated": truncated,
                "tasks": [t.model_dump(mode="json") for t in tasks],
            }

        return _envelope(build, errors=(WorkMateError, ValidationError))

    return [
        ToolSpec("get_my_jira_tasks", get_my_jira_tasks.__doc__ or "", get_my_jira_tasks),
        ToolSpec("get_my_jira_history", get_my_jira_history.__doc__ or "", get_my_jira_history),
    ]


# Opis skrócony (ADR 0068 §4): z 2308 B nad konwencją ~2 KB do wielkości mierzonej bramką
# ``tests/core/test_tool_descriptions.py``. Cięcie objęło instrukcje PREZENTACJI wyniku
# (wróciły polem ``note`` w kopercie, przy wyniku, którego dotyczą) oraz zdanie o granicy
# danych (stoi w prompcie, w sekcji ``Precedence``, wyżej w hierarchii). Rozgraniczenie
# `search` od `member_*` i granica ODCZYTU zostają — to reguły WYBORU narzędzia, więc
# muszą być widoczne PRZED wywołaniem.
_JIRA_DESC = """\
Jira: zadania i zgłoszenia pionu — wyłącznie ODCZYT.

`my_tasks` / `my_history` — TWOJE zadania otwarte / zakończone, bez pól. Zawężone do konta
pytającego, wziętego z zaufanej mapy pionu; pola `member` te akcje nie czytają.

`member_tasks` / `member_history` — to samo dla INNEJ osoby, wymaga `member` (imię i nazwisko,
np. 'Mikołaj Anonimowicz'). Konto rozwiązuje zaufana mapa pionu; osoba nieznana lub
niejednoznaczna daje czytelną odmowę.

`task` — szczegóły JEDNEGO zgłoszenia. Wymaga `key` (np. 'WT-5'). Zwraca podsumowanie, opis,
status, priorytet, osoby, termin, odnośnik i do 5 najnowszych komentarzy.

`search` — wyszukanie zgłoszeń; co najmniej jeden filtr: `query` (tekst w podsumowaniu, opisie
lub komentarzach), `project` (klucz projektu, np. 'WT') albo `status` ('todo', 'in_progress',
'done'). Domyślnie tylko NIEROZWIĄZANE, maks. 20 wyników. Do oglądania CUDZYCH zadań służą
`member_tasks` i `member_history` — `search` jest do szukania zgłoszeń, nie osób.

Historię zawężają `since`/`until` (YYYY-MM-DD, opcjonalne) po dacie ROZWIĄZANIA — np. „co X
zrobił w lipcu" → `since='RRRR-07-01'`, `until='RRRR-07-31'`."""

# Instrukcje PREZENTACJI wyniku (ADR 0068 §5) — w kopercie, nie w opisie. Model dostaje je
# dokładnie wtedy, gdy patrzy na dane, których dotyczą, i płaci za nie tylko przy wywołaniu.
_JIRA_GRUPY_NOTE = (
    "Grupy `assigned` (przypisane) i `reported_unassigned` (zgłoszone, bez wykonawcy) "
    "przedstaw OSOBNO, nie mieszaj w jedną listę. Pytanie o to, czym ktoś zajmuje się TERAZ, "
    "obsłuż wyróżniając spośród `assigned` te ze statusem kategorii w toku."
)
_JIRA_TRUNCATED_NOTE = (
    "Wyników było więcej — powiedz, że pokazujesz najnowszą część, i zaproponuj węższy zakres."
)

# Jedno źródło zestawu akcji: alias typu idzie do sygnatury (schemat), a ``get_args`` daje z niego
# listę do komunikatu odmownego. Dwie ręcznie utrzymywane kopie rozjechałyby się przy pierwszej
# nowej akcji — model dostałby wtedy podpowiedź z wartością, której schemat nie zna.
_JiraAkcja = Literal["my_tasks", "my_history", "member_tasks", "member_history", "task", "search"]
_JIRA_AKCJE: tuple[str, ...] = get_args(_JiraAkcja)

_JIRA_NIEZNANA_OSOBA = "Nie rozpoznaję jednoznacznie osoby {member!r} w mapie pionu."
_JIRA_OSOBA_HINT = "`member` to pełne imię i nazwisko osoby z pionu — sprawdź pisownię"


def build_jira_catalog(
    service: MyJiraTasksService,
    read_service: JiraReadService,
    resolve_member: _Callable[[str], str | None],
) -> list[ToolSpec]:
    """Zbuduj skonsolidowane narzędzie ``Jira`` dla runtime'u agenta (ADR 0009, krok 5.3).

    Wchłania sześć narzędzi: ``get_my_jira_tasks``, ``get_my_jira_history`` (z
    ``build_my_jira_tasks_catalog``) oraz ``get_jira_task``, ``search_jira_tasks``,
    ``get_member_jira_tasks``, ``get_member_jira_history`` (dawny ``build_jira_read_catalog``,
    zniesiony razem z tym krokiem — nie miał innego konsumenta).

    **``build_my_jira_tasks_catalog`` zostaje nietknięty, i to jest istota kroku.** Woła go także
    adapter MCP, a obie jego nazwy stoją w ZAMROŻONYM baseline powierzchni. Konsolidacja
    przeprowadzona na tamtym builderze nie przeniosłaby zdolności na drzwi agenta, tylko
    skasowała ją po stronie MCP — sesja Claude Code nie ma naszej fabryki per nadawca (ta sama
    pułapka co przy ``build_tool_catalog``, ADR 0009 §1).

    Wzorzec wychodzi tu prościej niż przy ``Project``/``Activity``: NIE MA bramki per drzwi,
    więc nie ma dynamicznego ``Literal`` ani przycinania ``__signature__`` — odpada najbardziej
    ryzykowna część maszynerii. Cała zdolność jest fail-closed o poziom wyżej: nadawca bez konta
    Jira w mapie tożsamości nie dostaje tego narzędzia W OGÓLE (fabryka zwraca pustą listę).
    Narzędzie istnieje w całości albo wcale — nie ma stanu „istnieje, ale połowa akcji milczy".

    Inwariant ADR 0054 przeżywa, ale przenosi się z sygnatury do dispatchera i MUSI być
    sondowany. Dotąd ``get_my_jira_tasks`` nie miał ANI JEDNEGO parametru, więc przekierowanie na
    cudze konto było strukturalnie niemożliwe. Teraz pole ``member`` istnieje w tym samym
    schemacie co akcje ``my_*`` — gałęzie ``my_*`` po prostu go NIE CZYTAJĄ (biorą ``service``
    domknięty na koncie nadawcy). Sonda na to jest w ``test_jira_catalog.py``; bez niej regresja
    typu ``assignee = member or wlasne`` przeszłaby niezauważona.

    ``limit`` nie dostaje sufitu w dispatcherze — inaczej niż w ``Activity``, bo
    ``JiraReadService.search_tasks`` domyka go sam (``min(limit, _MAX_SEARCH_RESULTS)``), więc
    drugi sufit tutaj byłby duplikatem reguły, która i tak żyje w serwisie.
    """

    def _grupy(tasks: list[Any], truncated: bool) -> dict[str, Any]:
        """Wspólny kształt odpowiedzi zadań otwartych — jeden dla ``my_tasks`` i ``member_tasks``.

        Dawne narzędzia zwracały ten sam podział pod RÓŻNYMI kluczami
        (``assigned_to_me``/``reported_by_me_unassigned`` kontra ``assigned``/
        ``reported_unassigned``). Pod jednym opisem dwa nazewnictwa byłyby sprzecznością, więc
        zostaje jedno. Builder MCP ma dalej swoje — to osobne, zamrożone drzwi.

        ``truncated`` jak w historii: sufit jest po stronie serwisu, a model ma o nim POWIEDZIEĆ,
        zamiast milcząco przedstawiać wycinek jako całość.
        """
        assigned, unassigned = split_by_assignment(tasks)
        uwagi = [_JIRA_GRUPY_NOTE] + ([_JIRA_TRUNCATED_NOTE] if truncated else [])
        return {
            "assigned": [t.model_dump(mode="json") for t in assigned],
            "reported_unassigned": [t.model_dump(mode="json") for t in unassigned],
            "count": len(assigned) + len(unassigned),
            "truncated": truncated,
            "note": " ".join(uwagi),
        }

    def _historia(tasks: list[Any], truncated: bool) -> dict[str, Any]:
        wynik: dict[str, Any] = {
            "count": len(tasks),
            "truncated": truncated,
            "tasks": [t.model_dump(mode="json") for t in tasks],
        }
        if truncated:
            wynik["note"] = _JIRA_TRUNCATED_NOTE
        return wynik

    def _konto(member: str | None, action: str) -> tuple[str | None, dict[str, Any] | None]:
        """Rozwiąż osobę na konto Jira; zwróć ``(konto, None)`` albo ``(None, odpowiedź_odmowna)``.

        Dwa różne braki dają dwie różne odpowiedzi: brak POLA to błąd wywołania (strukturalny,
        model poprawia sam), a nierozpoznana OSOBA to odmowa merytoryczna — konta nie zgadujemy.
        """
        missing = _puste(member=member)
        if missing:
            return None, _brakuje_pol("Jira", action, missing, _JIRA_OSOBA_HINT)
        jira_user = resolve_member(str(member))
        if not jira_user:
            return None, _nie_znaleziono(
                "Jira", action, _JIRA_NIEZNANA_OSOBA.format(member=member), _JIRA_OSOBA_HINT
            )
        return jira_user, None

    def jira(
        action: Annotated[
            _JiraAkcja,
            Field(
                description=(
                    "Co zrobić: `my_tasks` — twoje otwarte zadania; `my_history` — twoje "
                    "zakończone; `member_tasks` / `member_history` — to samo dla innej osoby "
                    "(wymaga `member`); `task` — szczegóły jednego zgłoszenia (wymaga `key`); "
                    "`search` — wyszukanie zgłoszeń."
                )
            ),
        ],
        member: Annotated[
            str | None,
            Field(
                description=(
                    "Imię i nazwisko osoby z pionu (`member_tasks`, `member_history`). Akcje "
                    "`my_*` tego pola NIE czytają — zawsze dotyczą konta pytającego."
                )
            ),
        ] = None,
        key: Annotated[
            str | None, Field(description="Klucz zgłoszenia, np. 'WT-5' (`task`).")
        ] = None,
        query: Annotated[
            str | None,
            Field(description="Szukany tekst w podsumowaniu/opisie/komentarzu (`search`)."),
        ] = None,
        project: Annotated[
            str | None, Field(description="Klucz projektu Jira, np. 'WT' (`search`).")
        ] = None,
        status: Annotated[
            str | None,
            Field(description="Kategoria statusu: 'todo', 'in_progress' albo 'done' (`search`)."),
        ] = None,
        limit: Annotated[int | None, Field(description="Ile wyników, maks. 20 (`search`).")] = None,
        since: Annotated[
            str | None,
            Field(description="Data od, YYYY-MM-DD, po dacie rozwiązania (akcje historii)."),
        ] = None,
        until: Annotated[
            str | None,
            Field(description="Data do, YYYY-MM-DD, po dacie rozwiązania (akcje historii)."),
        ] = None,
    ) -> dict[str, Any]:
        if action == "my_tasks":
            # Bez odczytu ``member`` — konto siedzi w ``service`` (ADR 0054).
            return _envelope(
                lambda: _grupy(*service.my_open_tasks()),
                errors=(WorkMateError, ValidationError),
            )
        if action == "my_history":
            return _envelope(
                lambda: _historia(*service.my_history(since or "", until or "")),
                errors=(WorkMateError, ValidationError),
            )
        if action == "member_tasks":
            jira_user, odmowa = _konto(member, "member_tasks")
            if odmowa is not None:
                return odmowa
            return _envelope(
                lambda: {
                    "member": member,
                    **_grupy(*read_service.member_open_tasks(str(jira_user))),
                },
                errors=(WorkMateError, ValidationError),
            )
        if action == "member_history":
            jira_user, odmowa = _konto(member, "member_history")
            if odmowa is not None:
                return odmowa
            return _envelope(
                lambda: {
                    "member": member,
                    **_historia(
                        *read_service.member_history(str(jira_user), since or "", until or "")
                    ),
                },
                errors=(WorkMateError, ValidationError),
            )
        if action == "task":
            missing = _puste(key=key)
            if missing:
                return _brakuje_pol(
                    "Jira", "task", missing, "klucz z wyniku `search` albo podany przez człowieka"
                )
            return _envelope(
                lambda: read_service.task_details(str(key)).model_dump(mode="json"),
                errors=(WorkMateError, ValidationError),
            )

        if action != "search":
            # Terminalny ``else`` wykonywałby `search` dla DOWOLNEJ nieznanej akcji — czyli
            # oddawałby wynik innej zdolności, niż poproszono, bez śladu w odpowiedzi. Model tego
            # nie wywoła (``Literal``), ale ``spec.fn`` woła też kod aplikacji, z pominięciem
            # koercji argumentów.
            return _zla_akcja("Jira", action, _JIRA_AKCJE)

        # ``search``: braku filtrów NIE sprawdzamy tutaj. Reguła „co najmniej jeden" żyje
        # w ``JiraReadService.search_tasks`` (razem z walidacją kategorii statusu i escapowaniem
        # JQL) i wraca kopertą jako czytelny błąd. Druga kopia reguły tutaj rozjechałaby się
        # z tamtą przy pierwszej zmianie.
        def szukaj() -> dict[str, Any]:
            # ``limit`` przekazujemy tylko gdy podany — domyślna wartość (i sufit) należy do
            # serwisu, więc powtórzenie liczby tutaj byłoby drugim źródłem tej samej reguły.
            zawezenie = {} if limit is None else {"limit": limit}
            tasks = read_service.search_tasks(
                text=query or "",
                project=project or "",
                status_category=status or "",
                **zawezenie,
            )
            return {"count": len(tasks), "tasks": [t.model_dump(mode="json") for t in tasks]}

        return _envelope(szukaj, errors=(WorkMateError, ValidationError))

    return [ToolSpec("Jira", _JIRA_DESC, jira)]
