"""Bramki skonsolidowanego narzędzia ``Activity`` (ADR 0009 paczki wdrożeniowej, krok 5.2).

Te same dwie bramki co w ``test_notes_catalog.py`` — zamrożenie ``input_schema`` z niepustymi
opisami pól oraz sonda negatywna na bramkę zapisu — plus trzecia, którą wymusiło znalezisko
z przeglądu: **pola muszą znikać razem z akcjami**. Bramka domykająca sam ``enum`` zostawiała
w schemacie `number`, `title`, `body` z opisami odsyłającymi do akcji, których model nie ma.

Asercje idą przez WYRENDEROWANY schemat (``_to_tool_def``), nie przez ``__annotations__``:
podmiana adnotacji i sygnatury po definicji funkcji jest najbardziej ryzykownym elementem tego
wzorca, więc sprawdzać trzeba to, co faktycznie zobaczy model.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from workmate.adapters.outbound.anthropic_llm import _to_tool_def
from workmate.core.application.tools import build_activity_catalog
from workmate.core.domain.events import Event


class _FakeEvents:
    """Atrapa ``EventService`` — zapamiętuje ``limit``, bo to on jest tu przedmiotem sondy."""

    def __init__(self) -> None:
        self.limits: list[int] = []

    def recent(self, *, source: str | None = None, project: str | None = None, limit: int = 20):
        self.limits.append(limit)
        return [
            Event(
                id=1,
                source="github",
                kind="issue_opened",
                external_id="repo#1",
                occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
                ingested_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        ]


class _FakeWrite:
    def __init__(self) -> None:
        self.comments: list[tuple[int, str]] = []

    def create_issue(self, title: str, body: str, labels: tuple[str, ...]) -> dict[str, Any]:
        return {"number": 7, "url": "https://example/7"}

    def create_comment(self, number: int, body: str) -> dict[str, Any]:
        self.comments.append((number, body))
        return {"url": "https://example/7#c1"}


def _schema(**kw: Any) -> dict[str, Any]:
    return _to_tool_def(build_activity_catalog(**kw)[0])["input_schema"]


def _akcje(schema: dict[str, Any]) -> set[str]:
    action = schema["properties"]["action"]
    return set(action["enum"]) if "enum" in action else {action["const"]}


# ── Zamrożenie schematu ─────────────────────────────────────────────────────────────────


def test_narzedzie_nazywa_sie_github_i_ma_akcje_wymagana() -> None:
    definicja = _to_tool_def(build_activity_catalog(events=_FakeEvents())[0])
    assert definicja["name"] == "Activity"
    assert definicja["input_schema"]["required"] == ["action"]


def test_kazde_pole_ma_niepusty_opis() -> None:
    właściwości = _schema(events=_FakeEvents(), worklog=object(), write_service=_FakeWrite())[
        "properties"
    ]
    assert [n for n, pole in właściwości.items() if not pole.get("description")] == []


def test_brak_okablowania_nie_tworzy_narzedzia() -> None:
    """Narzędzie bez ani jednej czynnej akcji byłoby pozycją w budżecie wyboru za darmo."""
    assert build_activity_catalog() == []


# ── Sonda negatywna: pola znikają razem z akcjami ───────────────────────────────────────


def test_pola_akcji_niedostepnych_nie_istnieja_w_schemacie() -> None:
    """Regresja z przeglądu: bramka domykała `enum`, ale zostawiała pola po akcjach zapisu.

    Model dostawał wtedy pole „Numer issue (`comment`)" przy wyłączonym zapisie — obietnicę
    bez pokrycia, czyli tę samą klasę defektu, którą konsolidacja miała usunąć.
    """
    właściwości = set(_schema(events=_FakeEvents())["properties"])
    assert właściwości == {"action", "project", "source", "limit"}
    assert not właściwości & {"title", "body", "labels", "number", "since", "until", "author"}


def test_pola_worklogu_wchodza_dopiero_z_worklogiem() -> None:
    bez = set(_schema(events=_FakeEvents())["properties"])
    z_nim = set(_schema(events=_FakeEvents(), worklog=object())["properties"])
    assert z_nim - bez == {"since", "until", "author"}


def test_bez_write_service_akcje_zapisu_nie_istnieja() -> None:
    schema = _schema(events=_FakeEvents(), worklog=object())
    assert _akcje(schema) == {"events", "summary", "worklog"}
    assert "create_issue" not in str(schema) and "comment" not in str(schema)


def test_bez_write_service_opis_nie_obiecuje_zapisu() -> None:
    opis = build_activity_catalog(events=_FakeEvents())[0].description
    assert "ZAPIS" not in opis
    assert "create_issue" not in opis and "`comment`" not in opis


def test_z_write_service_akcje_zapisu_sa_dostepne() -> None:
    assert _akcje(_schema(events=_FakeEvents(), write_service=_FakeWrite())) == {
        "events",
        "summary",
        "create_issue",
        "comment",
    }


# ── Walidacja per akcja i zachowanie ────────────────────────────────────────────────────


def test_brak_pola_wymaganego_przez_akcje_daje_blad_strukturalny() -> None:
    spec = build_activity_catalog(events=_FakeEvents(), write_service=_FakeWrite())[0]
    wynik = spec.fn(action="comment", body="treść")
    assert wynik["status"] == "invalid_request"
    assert wynik["tool"] == "Activity"
    assert wynik["missing"] == ["number"]


def test_activity_bez_projektu_wskazuje_brakujace_pole() -> None:
    spec = build_activity_catalog(events=_FakeEvents())[0]
    assert spec.fn(action="summary")["missing"] == ["project"]


def test_limit_ma_sufit() -> None:
    """``limit`` idzie od modelu prosto do SQL — bez sufitu jedna tura wciąga cały backlog."""
    events = _FakeEvents()
    build_activity_catalog(events=events)[0].fn(action="events", limit=10_000)
    assert events.limits == [200]


def test_activity_agreguje_z_szerszego_okna_niz_events() -> None:
    """Liczniki `by_kind` liczą się z okna, nie z rozmiaru wyniku — wspólne 20 je zwęziło."""
    events = _FakeEvents()
    katalog = build_activity_catalog(events=events)[0]
    katalog.fn(action="events")
    katalog.fn(action="summary", project="workmate")
    assert events.limits == [20, 50]


def test_comment_trafia_do_serwisu_z_numerem_od_modelu() -> None:
    write = _FakeWrite()
    spec = build_activity_catalog(events=_FakeEvents(), write_service=write)[0]
    assert spec.fn(action="comment", number=12, body="ok")["created"] is True
    assert write.comments == [(12, "ok")]


def test_zadna_akcja_poza_comment_nie_dochodzi_do_zapisu_komentarza() -> None:
    """Sonda na PRZYSZŁĄ akcję, nie na dzisiejszego wołającego.

    Ostatnia gałąź dispatchera przez jeden krok była domyślna: akcja dopisana do zestawu bez
    własnej gałęzi wpadłaby w komentarz, czyli w ZAPIS. Bramka wejściowa tego nie łapie, bo taka
    akcja jest w zestawie legalna. Sonda bierze akcje z WYRENDEROWANEGO enumu, więc dopisanie
    akcji bez gałęzi wywraca ją samo — bez pamiętania o tym pliku.
    """
    write = _FakeWrite()

    class _FakeWorklog:
        def propose_worklog(self, since: date, until: date, author: str):  # pragma: no cover
            raise AssertionError("worklog wołany bez kompletu dat")

    katalog = build_activity_catalog(
        events=_FakeEvents(), worklog=_FakeWorklog(), write_service=write
    )[0]
    akcje = _akcje(_to_tool_def(katalog)["input_schema"])
    assert "comment" in akcje, "sonda straciła przedmiot — akcji `comment` nie ma w zestawie"

    for akcja in sorted(akcje - {"comment"}):
        # Z KOMPLETEM pól komentarza: bez nich każda akcja wpadająca w gałąź `comment` wróciłaby
        # na braku `number` i sonda przepuściłaby mutację, mimo że zapis był o krok.
        katalog.fn(action=akcja, number=999, body="treść, która nie ma prawa nigdzie pojechać")
        assert write.comments == [], f"akcja `{akcja}` doszła do zapisu komentarza"


def test_worklog_wymaga_obu_dat() -> None:
    class _FakeWorklog:
        def propose_worklog(self, since: date, until: date, author: str):  # pragma: no cover
            raise AssertionError("nie powinno dojść do serwisu bez kompletu dat")

    spec = build_activity_catalog(events=_FakeEvents(), worklog=_FakeWorklog())[0]
    assert spec.fn(action="worklog", since=date(2026, 1, 1))["missing"] == ["until"]


def test_akcja_spoza_bramki_wraca_koperta_a_nie_assertem() -> None:
    """Bramka nie może stać na ``assert`` w ciele — to gwarancja dyscypliny wołającego.

    Runtime agenta odsiewa akcję spoza ``Literal`` przy koercji argumentów, ale nie jest jedynym
    wołającym ``spec.fn``: router komend woła narzędzie wprost. Pod ``python -O`` asercja znika
    i zamiast koperty przychodzi ``AttributeError`` na ``None``.
    """
    spec = build_activity_catalog(events=_FakeEvents())[0]
    wynik = spec.fn(action="create_issue", title="t", body="b")
    assert wynik["status"] == "invalid_request"
    assert wynik["allowed"] == ["events", "summary"]
    assert "wymaga pól" not in wynik["error"], "komunikat każe dosłać `action`, którą podano"


# --- Opis nie obiecuje zrodla, ktorego w warstwie zdarzen nie ma (ADR 0068, amendment) ----


def test_opis_nie_obiecuje_jiry_w_warstwie_zdarzen() -> None:
    """Do `EventStore` trafia `source='github'` i `source='teams'` — Jira NIE ma mostu.

    Pierwsze zdanie opisu obiecywalo „GitHuba, Jire i Teamsy", a filtr `source` wymienial
    'jira' jako wartosc. To ta sama klasa defektu, ktora ADR 0068 zamyka gdzie indziej —
    tyle ze w linii, ktora model czyta jako pierwsza, i przy filtrze, ktory zwrocilby pustke.
    Odczyt Jiry jest osobnym narzedziem (`Jira`), nie zrodlem tej warstwy (CLAUDE.md regula 8).
    """
    opis = build_activity_catalog(events=_FakeEvents())[0].description  # type: ignore[arg-type]

    assert "Jir" not in opis.splitlines()[0]
    assert "'jira'" not in opis
