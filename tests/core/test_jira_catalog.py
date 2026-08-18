"""Bramki skonsolidowanego narzędzia ``Jira`` (ADR 0009 paczki wdrożeniowej, krok 5.3).

Te same bramki wzorca co przy ``Notes`` i ``GitHub`` — zamrożenie ``input_schema`` z niepustymi
opisami pól — plus sonda, która jest tu WAŻNIEJSZA niż wszystko inne:

**akcje ``my_*`` nie mogą czytać pola ``member``.**

Dotąd ``get_my_jira_tasks`` nie miał ANI JEDNEGO parametru, więc pokazanie cudzych zadań było
strukturalnie niemożliwe (ADR 0054). Po konsolidacji ``member`` stoi w tym samym schemacie co
``my_tasks``, a jedyne, co je rozdziela, to gałąź dispatchera. Regresja typu
``assignee = member or wlasne`` wyglądałaby w schemacie IDENTYCZNIE jak poprawny kod — dlatego
sonda sprawdza nie tylko wynik, ale i to, że serwis odczytu członka NIE ZOSTAŁ ZAWOŁANY.

Zastępuje ``test_jira_read_catalog.py`` (builder ``build_jira_read_catalog`` zniesiony razem
z krokiem). Zachowania czterech dawnych narzędzi odczytu są tu przeniesione co do jednego —
odmowy fail-closed na nieznaną osobę, koperta błędów, flaga ``truncated``.
``build_my_jira_tasks_catalog`` (drzwi MCP, zamrożone) ma dalej własny
``test_my_jira_tasks_catalog.py`` i ten krok go nie dotyka.
"""

from __future__ import annotations

from typing import Any

from workmate.adapters.outbound.anthropic_llm import _to_tool_def
from workmate.core.application.tools import build_jira_catalog
from workmate.core.domain.jira_tasks import JiraTask, JiraTaskDetails
from workmate.core.errors import InvalidRequestError, JiraReadError


class _FakeMyJiraTasks:
    """Atrapa serwisu DOMKNIĘTEGO na koncie nadawcy — zwraca dane rozpoznawalne po kluczu."""

    def __init__(
        self,
        tasks: list[JiraTask] | None = None,
        history: tuple[list[JiraTask], bool] | None = None,
        error: Exception | None = None,
        truncated: bool = False,
    ) -> None:
        self._tasks = (
            tasks
            if tasks is not None
            else [JiraTask(key="MOJE-1", summary="moje", status="To Do", assignee="Ja")]
        )
        self._history = history or ([], False)
        self._error = error
        self._truncated = truncated
        self.history_calls: list[tuple[str, str]] = []

    def my_open_tasks(self) -> tuple[list[JiraTask], bool]:
        if self._error:
            raise self._error
        return self._tasks, self._truncated

    def my_history(self, since: str = "", until: str = "") -> tuple[list[JiraTask], bool]:
        self.history_calls.append((since, until))
        if self._error:
            raise self._error
        return self._history


class _FakeJiraRead:
    def __init__(
        self,
        details: JiraTaskDetails | None = None,
        tasks: list[JiraTask] | None = None,
        history: tuple[list[JiraTask], bool] | None = None,
        error: Exception | None = None,
        truncated: bool = False,
    ) -> None:
        self._details = details or JiraTaskDetails(key="WT-1", summary="x")
        self._tasks = tasks if tasks is not None else []
        self._history = history or ([], False)
        self._error = error
        self._truncated = truncated
        self.member_open_calls: list[str] = []
        self.member_history_calls: list[tuple[str, str, str]] = []
        self.search_calls: list[dict[str, Any]] = []

    def task_details(self, key: str) -> JiraTaskDetails:
        if self._error:
            raise self._error
        return self._details

    def search_tasks(
        self, text: str = "", project: str = "", status_category: str = "", limit: int = 20
    ) -> list[JiraTask]:
        self.search_calls.append(
            {"text": text, "project": project, "status_category": status_category, "limit": limit}
        )
        if self._error:
            raise self._error
        return self._tasks

    def member_open_tasks(self, jira_user: str) -> tuple[list[JiraTask], bool]:
        self.member_open_calls.append(jira_user)
        if self._error:
            raise self._error
        return self._tasks, self._truncated

    def member_history(
        self, jira_user: str, since: str = "", until: str = ""
    ) -> tuple[list[JiraTask], bool]:
        self.member_history_calls.append((jira_user, since, until))
        if self._error:
            raise self._error
        return self._history


def _resolver(known: dict[str, str]):
    def resolve(name: str) -> str | None:
        return known.get(name)

    return resolve


def _zbuduj(mine=None, read=None, known=None):
    mine = mine if mine is not None else _FakeMyJiraTasks()
    read = read if read is not None else _FakeJiraRead()
    spec = build_jira_catalog(mine, read, _resolver(known or {}))[0]  # type: ignore[arg-type]
    return mine, read, spec


def _fn(**kw: Any) -> Any:
    return _zbuduj()[2].fn(**kw)


# ── Zamrożenie schematu ─────────────────────────────────────────────────────────────────


def test_narzedzie_nazywa_sie_jira_i_ma_akcje_wymagana() -> None:
    definicja = _to_tool_def(_zbuduj()[2])
    assert definicja["name"] == "Jira"
    assert definicja["input_schema"]["required"] == ["action"]


def test_kazde_pole_ma_niepusty_opis() -> None:
    """Opisy pól są całym powodem, dla którego wzorzec ``action=…`` jest do przyjęcia."""
    właściwości = _to_tool_def(_zbuduj()[2])["input_schema"]["properties"]
    assert [n for n, pole in właściwości.items() if not pole.get("description")] == []


def test_schemat_ma_dokladnie_te_pola_co_akcje() -> None:
    schema = _to_tool_def(_zbuduj()[2])["input_schema"]
    assert set(schema["properties"]) == {
        "action",
        "member",
        "key",
        "query",
        "project",
        "status",
        "limit",
        "since",
        "until",
    }
    assert set(schema["properties"]["action"]["enum"]) == {
        "my_tasks",
        "my_history",
        "member_tasks",
        "member_history",
        "task",
        "search",
    }


def test_szesc_narzedzi_zeszlo_do_jednego() -> None:
    assert len(build_jira_catalog(_FakeMyJiraTasks(), _FakeJiraRead(), _resolver({}))) == 1  # type: ignore[arg-type]


# ── Sonda negatywna: `my_*` nie czyta `member` (inwariant ADR 0054) ──────────────────────


def test_my_tasks_ignoruje_podane_member_i_nie_wola_serwisu_czlonka() -> None:
    """Gdyby ``my_tasks`` honorowało ``member``, model dostałby cudzą listę zadań.

    Sprawdzamy OBIE strony: że wynik pochodzi z konta domkniętego przy budowie ORAZ że
    ``read_service.member_open_tasks`` nie został tknięty. Sam wynik by nie wystarczył —
    przy pustej atrapie obie ścieżki zwracają listy, które łatwo pomylić.
    """
    mine, read, spec = _zbuduj(
        read=_FakeJiraRead(
            tasks=[JiraTask(key="CUDZE-9", summary="cudze", status="To Do", assignee="Ktoś")]
        ),
        known={"Ktoś Inny": "ktos@example.org"},
    )
    wynik = spec.fn(action="my_tasks", member="Ktoś Inny")

    assert [t["key"] for t in wynik["assigned"]] == ["MOJE-1"]
    assert read.member_open_calls == [], "akcja `my_tasks` sięgnęła po konto z pola `member`"


def test_my_history_ignoruje_podane_member_i_nie_wola_serwisu_czlonka() -> None:
    mine, read, spec = _zbuduj(
        mine=_FakeMyJiraTasks(
            history=([JiraTask(key="MOJE-2", summary="zrobione", status="Done")], False)
        ),
        known={"Ktoś Inny": "ktos@example.org"},
    )
    wynik = spec.fn(action="my_history", member="Ktoś Inny", since="2026-01-01")

    assert [t["key"] for t in wynik["tasks"]] == ["MOJE-2"]
    assert mine.history_calls == [("2026-01-01", "")]
    assert read.member_history_calls == [], "akcja `my_history` sięgnęła po konto z pola `member`"


# ── Akcje własne ────────────────────────────────────────────────────────────────────────


def test_my_tasks_rozdziela_na_dwie_grupy() -> None:
    przypisane = JiraTask(key="WT-1", summary="A", status="To Do", assignee="Ja")
    zgloszone = JiraTask(key="WT-2", summary="B", status="To Do")
    _, _, spec = _zbuduj(mine=_FakeMyJiraTasks(tasks=[przypisane, zgloszone]))
    wynik = spec.fn(action="my_tasks")
    assert [t["key"] for t in wynik["assigned"]] == ["WT-1"]
    assert [t["key"] for t in wynik["reported_unassigned"]] == ["WT-2"]
    assert wynik["count"] == 2


def test_my_history_niesie_flage_truncated() -> None:
    zadanie = JiraTask(key="WT-3", summary="Zrobione", status="Done", resolved="2026-08-01")
    _, _, spec = _zbuduj(mine=_FakeMyJiraTasks(history=([zadanie], True)))
    wynik = spec.fn(action="my_history")
    assert wynik["count"] == 1
    assert wynik["truncated"] is True


def test_blad_serwisu_wlasnego_wraca_koperta_a_nie_wyjatkiem() -> None:
    _, _, spec = _zbuduj(mine=_FakeMyJiraTasks(error=JiraReadError("boom")))
    assert "error" in spec.fn(action="my_tasks")


# ── Akcje członka: fail-closed na nieznaną osobę ────────────────────────────────────────


def test_member_tasks_znana_osoba_rozdziela_grupy_i_uzywa_konta_z_mapy() -> None:
    przypisane = JiraTask(key="WT-1", summary="A", status="To Do", assignee="Ona")
    zgloszone = JiraTask(key="WT-2", summary="B", status="To Do")
    _, read, spec = _zbuduj(
        read=_FakeJiraRead(tasks=[przypisane, zgloszone]),
        known={"Znana Osoba": "znana@example.org"},
    )
    wynik = spec.fn(action="member_tasks", member="Znana Osoba")
    assert [t["key"] for t in wynik["assigned"]] == ["WT-1"]
    assert [t["key"] for t in wynik["reported_unassigned"]] == ["WT-2"]
    assert wynik["member"] == "Znana Osoba"
    assert read.member_open_calls == ["znana@example.org"]


def test_member_tasks_nieznana_osoba_to_czytelna_odmowa_bez_pytania_jiry() -> None:
    """Konta nie zgadujemy — nierozpoznana osoba nie ma prawa dojść do Jiry w ogóle."""
    _, read, spec = _zbuduj(known={})
    wynik = spec.fn(action="member_tasks", member="Ktoś Inny")
    assert "error" in wynik
    assert read.member_open_calls == []


def test_member_history_znana_osoba_przekazuje_daty() -> None:
    zadanie = JiraTask(key="WT-3", summary="Zrobione", status="Done", resolved="2026-08-01")
    _, read, spec = _zbuduj(
        read=_FakeJiraRead(history=([zadanie], False)), known={"Znana Osoba": "znana@example.org"}
    )
    wynik = spec.fn(action="member_history", member="Znana Osoba", since="2026-01-01")
    assert wynik["count"] == 1
    assert wynik["truncated"] is False
    assert read.member_history_calls == [("znana@example.org", "2026-01-01", "")]


def test_member_history_nieznana_osoba_to_czytelna_odmowa() -> None:
    _, read, spec = _zbuduj(known={})
    assert "error" in spec.fn(action="member_history", member="Ktoś Inny")
    assert read.member_history_calls == []


# ── Walidacja per akcja ─────────────────────────────────────────────────────────────────


def test_brak_member_to_blad_strukturalny_a_nie_odmowa() -> None:
    """Brak POLA i nierozpoznana OSOBA to dwa różne braki — model poprawia je inaczej."""
    wynik = _fn(action="member_tasks")
    assert wynik["status"] == "invalid_request"
    assert wynik["tool"] == "Jira"
    assert wynik["action"] == "member_tasks"
    assert wynik["missing"] == ["member"]
    assert wynik["hint"]


def test_task_bez_klucza_wskazuje_brakujace_pole() -> None:
    wynik = _fn(action="task")
    assert wynik["missing"] == ["key"]


def test_task_zwraca_szczegoly_zgloszenia() -> None:
    _, _, spec = _zbuduj(read=_FakeJiraRead(details=JiraTaskDetails(key="WT-9", summary="Coś")))
    assert spec.fn(action="task", key="WT-9")["key"] == "WT-9"


def test_task_blad_odczytu_wraca_koperta() -> None:
    _, _, spec = _zbuduj(read=_FakeJiraRead(error=JiraReadError("boom")))
    assert "error" in spec.fn(action="task", key="WT-9")


# ── Wyszukiwanie ────────────────────────────────────────────────────────────────────────


def test_search_zwraca_liczbe_i_zadania() -> None:
    zadanie = JiraTask(key="WT-1", summary="A", status="To Do")
    _, _, spec = _zbuduj(read=_FakeJiraRead(tasks=[zadanie]))
    wynik = spec.fn(action="search", query="scada")
    assert wynik["count"] == 1
    assert wynik["tasks"] == [zadanie.model_dump(mode="json")]


def test_search_bez_limitu_nie_narzuca_wlasnej_wartosci() -> None:
    """Sufit i wartość domyślna należą do serwisu — druga kopia liczby tutaj by się rozjechała."""
    _, read, spec = _zbuduj()
    spec.fn(action="search", query="scada")
    assert read.search_calls == [
        {"text": "scada", "project": "", "status_category": "", "limit": 20}
    ]


def test_search_przekazuje_podany_limit_do_serwisu() -> None:
    _, read, spec = _zbuduj()
    spec.fn(action="search", query="scada", limit=5)
    assert read.search_calls[0]["limit"] == 5


def test_search_bez_filtrow_wraca_koperta_z_serwisu() -> None:
    """Reguła „co najmniej jeden filtr" żyje w serwisie — narzędzie ma ją tylko przepuścić."""
    _, _, spec = _zbuduj(read=_FakeJiraRead(error=InvalidRequestError("brak filtrów")))
    assert "error" in spec.fn(action="search")


def test_nieznana_akcja_nie_wykonuje_po_cichu_wyszukiwania() -> None:
    """Terminalny ``else`` oddawałby wynik INNEJ zdolności, niż poproszono, bez śladu.

    Model tego nie wywoła (``Literal`` domyka zestaw), ale ``spec.fn`` woła też kod aplikacji —
    z pominięciem koercji argumentów runtime'u. Tak właśnie przeszła regresja `/moje-zadania`.
    """
    wynik = _fn(action="wymyslona")
    assert wynik["status"] == "invalid_request"
    assert "my_tasks" in wynik["allowed"] and "search" in wynik["allowed"]
    assert "wymaga pól" not in wynik["error"]


# ── Instrukcje prezentacji w KOPERCIE, nie w opisie (ADR 0068 §5) ───────────────────────


def test_wynik_zadan_otwartych_niesie_regule_prezentacji_grup() -> None:
    """Reguly formatowania placi sie w KAZDYM zadaniu, gdy stoja w opisie narzedzia.

    W kopercie jada tylko wtedy, gdy model faktycznie patrzy na dane, ktorych dotycza — i stoja
    obok nich, a nie kilka tysiecy tokenow wczesniej.
    """
    wynik = _fn(action="my_tasks")

    assert "OSOBNO" in wynik["note"]
    assert "assigned" in wynik["note"]


def test_opis_narzedzia_juz_nie_niesie_regul_prezentacji() -> None:
    """Dwa miejsca na te sama regule to dwa miejsca do rozjechania — zostaje jedno."""
    opis = _zbuduj()[2].description

    assert "PRZEDSTAW" not in opis
    assert "OSOBNO" not in opis


def test_skrocona_historia_mowi_o_skroceniu_w_wyniku() -> None:
    mine = _FakeMyJiraTasks(history=([], True))

    wynik = _zbuduj(mine=mine)[2].fn(action="my_history")

    assert wynik["truncated"] is True
    assert "wiecej" in wynik["note"].replace("ę", "e")


def test_pelna_historia_nie_dostaje_notki_o_skroceniu() -> None:
    """Notka o skroceniu przy pelnym wyniku kazalaby modelowi ostrzegac bez powodu."""
    wynik = _fn(action="my_history")

    assert wynik["truncated"] is False
    assert "note" not in wynik


# ── Ksztalt "nie znaleziono" (ADR 0068 §9) ─────────────────────────────────────────────


def test_nieznana_osoba_wraca_kopertą_z_podpowiedzia() -> None:
    """Odmowa merytoryczna niesie tyle samo pol co blad wywolania — model poprawia sie tak samo."""
    brak = _fn(action="member_tasks")
    nieznana = _zbuduj(known={"Jerzy Zastepski": "jzastepski"})[2].fn(
        action="member_tasks", member="Ktos Obcy"
    )

    assert nieznana["status"] == "not_found"
    assert nieznana["tool"] == brak["tool"] == "Jira"
    assert nieznana["action"] == brak["action"] == "member_tasks"
    assert nieznana["hint"] == brak["hint"]
