"""Współbieżność magazynów SQLite — DWA połączenia do JEDNEGO pliku i wiele wątków na jednym.

Drzwi (MCP, CLI, teams_graph, GitHub) to OSOBNE PROCESY nad tymi samymi plikami stanu, a każdy
magazyn deklaruje w docstringu ten sam wzorzec: jedno połączenie ``check_same_thread=False``
+ ``Lock``, ``WAL`` + ``busy_timeout``. Dotąd żaden test tego nie ćwiczył — pakiet sprawdzał
każdy magazyn W POJEDYNKĘ, więc usunięcie pragmy albo zamka przechodziło na zielono, a płacił
za to dopiero operator (``database is locked`` w drugich drzwiach).

Sondy tutaj są DETERMINISTYCZNE: przeplot wymuszają bariera i zdarzenie (``threading``), nie
uśpienie. Asercje nie zależą od tego, która nić dobiegnie pierwsza — sprawdzamy niezmienniki
(dokładnie jeden wiersz, licznik zsumowany, kursor bez luki), nie kolejność.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.conftest import make_note
from workmate.adapters.outbound.sqlite_audit import SqliteAuditStore
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.adapters.outbound.sqlite_dead_letters import SqliteDeadLetterStore
from workmate.adapters.outbound.sqlite_events import SqliteEventStore
from workmate.adapters.outbound.sqlite_metrics import SqliteMetricsStore
from workmate.adapters.outbound.sqlite_thread_links import SqliteThreadLinkStore
from workmate.core.domain.events import NewEvent

_WHEN = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)


# Rejestr magazynów dzielonych przez PROCESY drzwi. Każdy wpis niesie JEDEN zapis i sprawdzenie
# jego skutku, bo wspólny „wzorzec konstruktora" jest własnością ZACHOWANIA (zapis z innej nici,
# zapis pod cudzym zamkiem, ścieżka z ``~``), a nie zestawem pragm do odczytania z połączenia.
def _zapisz_events(store) -> None:  # noqa: ANN001
    store.append(_event("z-innej-nici"))


def _ile_events(store) -> int:  # noqa: ANN001
    return len(store.recent(limit=10))


def _zapisz_audit(store) -> None:  # noqa: ANN001
    store.record_tool_call(
        occurred_at=_WHEN,
        actor_key="a1",
        conversation_key="k1",
        door="teams_graph",
        tool_name="Project",
        arg_summary="action=status",
        status="ok",
        trust_class="T1",
    )


def _ile_audit(store) -> int:  # noqa: ANN001
    return len(store.recent())


def _zapisz_metrics(store) -> None:  # noqa: ANN001
    store.record_call("agent", "user-hash", "2026-W33", _WHEN)


def _ile_metrics(store) -> int:  # noqa: ANN001
    return len(store.summary().by_door)


def _zapisz_dead_letters(store) -> None:  # noqa: ANN001
    store.record(source="github", event_id=11, reason="503", attempts=1)


def _ile_dead_letters(store) -> int:  # noqa: ANN001
    return len(store.recent())


def _zapisz_thread_links(store) -> None:  # noqa: ANN001
    store.link("t1", "c1", "issue", "7", "root-1")


def _ile_thread_links(store) -> int:  # noqa: ANN001
    return 1 if store.get_root("t1", "c1", "issue", "7") == "root-1" else 0


def _zapisz_conversations(store) -> None:  # noqa: ANN001
    store.open_conversation("teams_graph", "w1")


def _ile_conversations(store) -> int:  # noqa: ANN001
    return len(store.list_conversations())


_MAGAZYNY = (
    ("events", SqliteEventStore, _zapisz_events, _ile_events),
    ("audit", SqliteAuditStore, _zapisz_audit, _ile_audit),
    ("metrics", SqliteMetricsStore, _zapisz_metrics, _ile_metrics),
    ("dead_letters", SqliteDeadLetterStore, _zapisz_dead_letters, _ile_dead_letters),
    ("thread_links", SqliteThreadLinkStore, _zapisz_thread_links, _ile_thread_links),
    ("conversations", SqliteConversationStore, _zapisz_conversations, _ile_conversations),
)
_IDS = [nazwa for nazwa, *_reszta in _MAGAZYNY]
_KLASY = tuple((nazwa, klasa) for nazwa, klasa, _z, _l in _MAGAZYNY)


def _event(external_id: str, **kw) -> NewEvent:
    base = {
        "source": "github",
        "kind": "issue_opened",
        "external_id": external_id,
        "occurred_at": _WHEN,
    }
    base.update(kw)
    return NewEvent(**base)


class _AtrapaEmbeddera:
    """Embedder bez modelu: wektor dobierany po podłańcuchu, z listą osadzonych tekstów."""

    _WEKTORY = {"query": [1.0, 0.0], "aaa": [1.0, 0.0], "bbb": [0.0, 1.0]}

    def __init__(self) -> None:
        self.osadzone: list[str] = []

    def embed(self, teksty):
        for tekst in teksty:
            self.osadzone.append(tekst)
            klucz = next((k for k in self._WEKTORY if k in tekst), None)
            yield list(self._WEKTORY.get(klucz, [0.0, 1.0]))


# --- konfiguracja połączenia: jedyne, co dzieli drzwi od ``database is locked`` ----


@pytest.mark.parametrize(("nazwa", "magazyn"), _KLASY, ids=_IDS)
def test_every_shared_store_opens_the_file_in_wal(tmp_path, nazwa, magazyn):
    """WAL na pliku to warunek, żeby drugie drzwi czytały w trakcie cudzego zapisu.

    Domyślny tryb SQLite to ``delete``, więc asercja jest ostra: usunięcie jednej linii
    z konstruktora nie psuje ŻADNEGO testu jednoprocesowego, a operatorowi daje blokady między
    procesami.

    ``busy_timeout`` sondy tu NIE MA i to jest decyzja, nie przeoczenie. ``sqlite3.connect``
    ustawia go SAM na 5000 ms (parametr ``timeout`` domyślnie 5.0 s), więc ``PRAGMA busy_timeout
    = 5000`` z konstruktora jest wobec tej wartości nierozróżnialny: dawna asercja ``>= 5000``
    przechodziła identycznie po wykreśleniu pragmy, czyli nie pilnowała niczego, co obiecywała
    jej nazwa. Zamiast mierzyć linię, mierzymy WŁASNOŚĆ, po którą ta linia sięga — sonda
    „czeka na cudzy zamek zamiast paść" niżej.
    """
    magazyn(tmp_path / f"{nazwa}.db")

    # ``journal_mode`` jest własnością PLIKU — czyta go świeże, niezależne połączenie.
    inne_drzwi = sqlite3.connect(str(tmp_path / f"{nazwa}.db"))
    try:
        assert inne_drzwi.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        inne_drzwi.close()


@pytest.mark.parametrize(("nazwa", "magazyn", "zapisz", "policz"), _MAGAZYNY, ids=_IDS)
def test_every_shared_store_writes_from_a_pool_thread_while_another_connection_holds_the_lock(
    tmp_path, nazwa, magazyn, zapisz, policz
):
    """Sonda ``busy_timeout`` ORAZ ``check_same_thread=False`` — po zachowaniu, nie po pragmach.

    Obie własności wzorca psują się dopiero pod obciążeniem produkcyjnym i obie są tu naraz,
    bo w produkcji występują razem: drzwi async wołają magazyn z PULI WĄTKÓW (to wymaga
    ``check_same_thread=False`` i zamka wokół operacji), a plik w tej samej chwili bywa zajęty
    przez DRUGI PROCES drzwi (to wymaga czekania zamiast ``database is locked``).

    Najpierw dowód, że zamek jest REALNY — nagie połączenie z ``timeout=0`` odbija się od niego.
    Potem zapis magazynem z innej nici; zamek zwalniamy dopiero, gdy nić ruszyła. Bez uśpień:
    sygnalizuje ``Event``, a asercja mówi o SKUTKU zapisu, nie o kolejności nici.
    """
    path = tmp_path / f"{nazwa}.db"
    store = magazyn(path)  # schemat zakłada GŁÓWNA nić — zapis poleci z puli
    trzymajacy = sqlite3.connect(str(path), timeout=0)
    trzymajacy.execute("BEGIN IMMEDIATE")
    try:
        nagie = sqlite3.connect(str(path), timeout=0)
        with nagie, pytest.raises(sqlite3.OperationalError, match="locked"):
            nagie.execute("BEGIN IMMEDIATE")
        nagie.close()

        ruszyl = threading.Event()

        def pisz_z_puli() -> None:
            ruszyl.set()
            zapisz(store)

        with ThreadPoolExecutor(max_workers=1) as executor:
            zadanie = executor.submit(pisz_z_puli)
            assert ruszyl.wait(timeout=5)
            trzymajacy.commit()  # zwolnienie zamka — nić z ``busy_timeout`` dopisuje
            zadanie.result(timeout=10)
    finally:
        trzymajacy.close()

    assert policz(store) == 1


@pytest.mark.parametrize(("nazwa", "magazyn", "zapisz", "policz"), _MAGAZYNY, ids=_IDS)
def test_every_shared_store_expands_a_home_relative_path_before_it_connects(
    tmp_path, monkeypatch, nazwa, magazyn, zapisz, policz
):
    """``~`` w ścieżce bazy rozwijał się WYŁĄCZNIE na potrzeby ``mkdir``.

    Katalog powstawał pod rozwiniętą ścieżką (``~/.workmate``), a plik bazy — pod literalnym
    ``~`` w katalogu roboczym procesu: dwa różne pliki pod jedną nazwą z konfiguracji. Domyślne
    ścieżki WSZYSTKICH tych magazynów mówią ``~/.workmate/…``, więc to droga produkcyjna.

    Naprawa poszła do sześciu konstruktorów, a sonda powstała dla JEDNEGO (``SqliteEventStore``,
    ``test_sqlite_events.py``). Pozostałe pięć wracało do defektu bez zrywania czegokolwiek —
    ta sama klasa dziury, co bramka mierząca podzbiór powierzchni.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    store = magazyn(f"~/.workmate/{nazwa}.db")
    zapisz(store)

    assert (tmp_path / ".workmate" / f"{nazwa}.db").is_file()
    assert not (tmp_path / "~").exists()  # żadnego katalogu o nazwie "~" obok
    assert policz(store) == 1


def test_a_second_door_waits_for_the_write_lock_instead_of_failing(tmp_path):
    """Zapis w oknie cudzej transakcji: nagie połączenie pada, magazyn czeka i zapisuje.

    Najpierw pokazujemy, że blokada jest REALNA (połączenie z ``busy_timeout=0`` dostaje
    ``database is locked``), a potem że drugie drzwi przez ten sam plik zapisują mimo trwającej
    transakcji pierwszych. Bez uśpień: nić sygnalizuje wejście, główny wątek zwalnia blokadę.
    """
    path = tmp_path / "events.db"
    trzymajacy = SqliteEventStore(path)
    drugie_drzwi = SqliteEventStore(path)
    trzymajacy._conn.execute("BEGIN IMMEDIATE")
    trzymajacy._conn.execute(
        "INSERT INTO events(source, kind, external_id, occurred_at) VALUES (?, ?, ?, ?)",
        ("github", "issue_opened", "pierwszy", _WHEN.isoformat()),
    )

    goly = sqlite3.connect(str(path), timeout=0)
    with goly, pytest.raises(sqlite3.OperationalError, match="locked"):
        goly.execute(
            "INSERT INTO events(source, kind, external_id, occurred_at) VALUES (?, ?, ?, ?)",
            ("github", "issue_opened", "nagi", _WHEN.isoformat()),
        )
    goly.close()

    ruszyl = threading.Event()

    def pisz_z_drugich_drzwi() -> int:
        ruszyl.set()
        return drugie_drzwi.append(_event("drugi")).id

    with ThreadPoolExecutor(max_workers=1) as executor:
        zadanie = executor.submit(pisz_z_drugich_drzwi)
        assert ruszyl.wait(timeout=5)
        trzymajacy._conn.commit()  # zwolnienie blokady — nić z ``busy_timeout`` dopisuje
        assert zadanie.result(timeout=10) > 0

    assert sorted(e.external_id for e in drugie_drzwi.recent(limit=10)) == ["drugi", "pierwszy"]


# --- deduplikacja i liczniki egzekwowane przez BAZĘ, nie przez logikę procesu ------


def test_two_doors_appending_the_same_event_produce_exactly_one_row(tmp_path):
    """Dedup ma pochodzić z ``UNIQUE`` + ``ON CONFLICT``, nie z ``exists`` sprzed zapisu.

    Poller GitHub i drzwi zapisu chodzą na wspólnym ``events.db`` (reguła 7) — gdyby dedup stał
    na sprawdzeniu w Pythonie, echo własnego zapisu wracałoby do Teams jako drugie zdarzenie.
    """
    path = tmp_path / "events.db"
    poller = SqliteEventStore(path)
    drzwi_zapisu = SqliteEventStore(path)

    pierwszy = poller.append(_event("7", title="z pollera"))
    echo = drzwi_zapisu.append(_event("7", title="z drzwi zapisu"))

    assert echo.id == pierwszy.id
    assert echo.title == "z pollera"  # pierwszy wpis zostaje, drugi to no-op
    assert len(drzwi_zapisu.recent(limit=10)) == 1


def test_quarantine_written_by_a_second_connection_keeps_the_first_reason(tmp_path):
    """``INSERT OR IGNORE`` po ``(source, event_id)``: powtórna kwarantanna nie nadpisuje powodu.

    Po restarcie notifiera licznik prób rusza od zera, więc TO SAMO zdarzenie trafia do
    kwarantanny drugi raz — z innej instancji i innego połączenia. Pierwszy powód i czas mają
    zostać, inaczej ślad po pierwotnej awarii ginie.
    """
    path = tmp_path / "events.db"
    przed_restartem = SqliteDeadLetterStore(path)
    po_restarcie = SqliteDeadLetterStore(path)

    przed_restartem.record(source="github", event_id=11, reason="404 z Teams", attempts=5)
    po_restarcie.record(source="github", event_id=11, reason="timeout", attempts=1)

    wpisy = po_restarcie.recent()
    assert len(wpisy) == 1
    assert wpisy[0]["reason"] == "404 z Teams"
    assert wpisy[0]["attempts"] == 5


def test_metrics_counter_sums_across_two_connections_instead_of_duplicating_rows(tmp_path):
    """UPSERT po ``(door, user_key, week)`` — dwa procesy drzwi liczą do JEDNEGO wiersza.

    Metryki zbierają CLI i teams_graph naraz. Gdyby klucz albo ``DO UPDATE`` znikły, raport
    pokazywałby dwóch „unikalnych użytkowników" tam, gdzie jest jeden — cicho i wiarygodnie.
    """
    path = tmp_path / "metrics.db"
    cli = SqliteMetricsStore(path)
    teams = SqliteMetricsStore(path)

    cli.record_call("agent", "user-hash", "2026-W33", _WHEN)
    teams.record_call("agent", "user-hash", "2026-W33", _WHEN)

    (drzwi,) = teams.summary().by_door
    assert (drzwi.calls, drzwi.unique_users) == (2, 1)


def test_audit_entries_from_two_doors_land_in_one_log_with_distinct_ids(tmp_path):
    """Dziennik audytu jest wspólny dla drzwi — dwa połączenia nie mogą zjeść sobie wierszy."""
    path = tmp_path / "audit.db"
    mcp = SqliteAuditStore(path)
    agent = SqliteAuditStore(path)

    mcp.record_tool_call(
        occurred_at=_WHEN,
        actor_key="a1",
        conversation_key="k1",
        door="mcp",
        tool_name="Project",
        arg_summary="action=save",
        status="ok",
        trust_class="T1",
    )
    agent.record_tool_call(
        occurred_at=_WHEN,
        actor_key="a2",
        conversation_key="k2",
        door="teams_graph",
        tool_name="Activity",
        arg_summary="action=events",
        status="ok",
        trust_class="T2",
    )

    assert sorted(w["door"] for w in mcp.recent()) == ["mcp", "teams_graph"]


def test_thread_link_rewritten_by_the_notifier_is_seen_by_the_reactive_door(tmp_path):
    """Przełączenie roota (degradacja po 404) musi być widoczne w DRUGICH drzwiach od razu.

    ``link`` jest upsertem właśnie po to; gdyby zapis trafiał do pamięci procesu albo do drugiego
    wiersza, drzwi reaktywne dalej odpowiadałyby w usuniętym wątku.
    """
    path = tmp_path / "events.db"
    notifier = SqliteThreadLinkStore(path)
    drzwi_reaktywne = SqliteThreadLinkStore(path)

    notifier.link("t1", "c1", "issue", "7", "root-stary")
    assert drzwi_reaktywne.get_root("t1", "c1", "issue", "7") == "root-stary"

    notifier.link("t1", "c1", "issue", "7", "root-nowy")

    assert drzwi_reaktywne.get_root("t1", "c1", "issue", "7") == "root-nowy"
    assert drzwi_reaktywne.get_target("t1", "c1", "root-nowy") == ("issue", "7")
    assert drzwi_reaktywne.get_target("t1", "c1", "root-stary") is None


def test_three_adapters_share_one_events_db_without_locking_each_other_out(tmp_path):
    """Układ produkcyjny: ``events``, ``dead_letters`` i ``thread_links`` to TRZY połączenia
    do jednego pliku, każde z własnego adaptera. Wspólny plik jest decyzją (tabele siostry),
    więc test pilnuje, że trzy równoległe połączenia się nie wykluczają.
    """
    path = tmp_path / "events.db"
    zdarzenia = SqliteEventStore(path)
    kwarantanna = SqliteDeadLetterStore(path)
    linki = SqliteThreadLinkStore(path)

    zdarzenie = zdarzenia.append(_event("7"))
    kwarantanna.record(source="github", event_id=zdarzenie.id, reason="503", attempts=5)
    linki.link("t1", "c1", "issue", "7", "root-1")

    assert [e.external_id for e in zdarzenia.read_since(0)] == ["7"]
    assert kwarantanna.recent()[0]["event_id"] == zdarzenie.id
    assert linki.get_root("t1", "c1", "issue", "7") == "root-1"


# --- rozmowy: FK, skaza i widoczność między drzwiami ------------------------------


def test_message_written_by_one_door_is_readable_by_the_other_with_fk_enforced(tmp_path):
    """Rozmowę otwierają jedne drzwi, wiadomość dopisują drugie — FK ma działać na OBU."""
    path = tmp_path / "conversations.db"
    cli = SqliteConversationStore(path)
    teams = SqliteConversationStore(path)

    rozmowa = cli.open_conversation("cli", "u1")
    teams.append_message(rozmowa.id, "user", "z drugich drzwi")

    assert [m.text for m in cli.messages(rozmowa.id)] == ["z drugich drzwi"]
    with pytest.raises(sqlite3.IntegrityError):
        teams.append_message("nie-ma-takiej-rozmowy", "user", "sierota")


def test_taint_lit_by_two_doors_keeps_the_source_of_the_first_one(tmp_path):
    """Skaza (ADR 0066) siedzi NA DYSKU i zapala ją ``UPDATE ... WHERE tainted=0``.

    Dwoje drzwi nad jedną rozmową to osobne procesy: „sprawdź, potem zapisz" w Pythonie
    przestawiłoby źródło pierwszego zapłonu. Ten test ćwiczy to przez DWA połączenia — dotąd
    sprawdzała to wyłącznie sonda jednoprocesowa.
    """
    path = tmp_path / "conversations.db"
    pierwsze = SqliteConversationStore(path)
    drugie = SqliteConversationStore(path)
    rozmowa = pierwsze.open_conversation("teams_graph", "w1")

    pierwsze.mark_tainted(rozmowa.id, "zalacznik")
    drugie.mark_tainted(rozmowa.id, "notatka")

    stan = drugie.get(rozmowa.id)
    assert stan is not None
    assert (stan.tainted, stan.taint_source) == (True, "zalacznik")


# --- wiele wątków na JEDNYM magazynie (pula drzwi async) --------------------------


def test_parallel_threads_on_one_store_append_every_event_exactly_once(tmp_path):
    """Drzwi async wołają magazyn z puli wątków — jedno połączenie, wiele nici.

    Bariera wpuszcza wszystkie nici do ``append`` naraz (przeplot wymuszony, bez uśpienia).
    Niezmiennik: tyle wierszy, ile wywołań, i identyfikatory bez powtórzeń — gubienie wierszy
    albo wspólny kursor ujawniłyby się dopiero pod obciążeniem produkcyjnym.
    """
    store = SqliteEventStore(tmp_path / "events.db")
    ile = 8
    bariera = threading.Barrier(ile)

    def dopisz(numer: int) -> int:
        bariera.wait(timeout=10)
        return store.append(_event(f"issue-{numer}")).id

    with ThreadPoolExecutor(max_workers=ile) as executor:
        identyfikatory = [
            f.result(timeout=20) for f in [executor.submit(dopisz, n) for n in range(ile)]
        ]

    assert len(set(identyfikatory)) == ile
    assert len(store.recent(limit=ile * 2)) == ile


def test_parallel_threads_appending_the_same_key_still_produce_one_row(tmp_path):
    """Ten sam klucz zdarzenia z kilku nici: dedup bazy ma zadziałać także wewnątrz procesu.

    To jest wyścig, którego ``exists``-przed-``append`` nie zamyka — a poller ma prawo zobaczyć
    to samo zdarzenie w dwóch rundach naraz (ponowienie po timeoucie sieci).
    """
    store = SqliteEventStore(tmp_path / "events.db")
    ile = 6
    bariera = threading.Barrier(ile)

    def dopisz() -> int:
        bariera.wait(timeout=10)
        return store.append(_event("7")).id

    with ThreadPoolExecutor(max_workers=ile) as executor:
        identyfikatory = [
            f.result(timeout=20) for f in [executor.submit(dopisz) for _ in range(ile)]
        ]

    assert set(identyfikatory) == {1}
    assert len(store.recent(limit=10)) == 1


def test_parallel_threads_on_one_conversation_do_not_lose_messages(tmp_path):
    """Jedna rozmowa, wiele nici (narzędzia agenta piszą turę z puli): żadna tura nie ginie.

    ``append_message`` robi INSERT, czyta ``lastrowid`` i dopiero potem SELECT — bez zamka
    dwie nici mogłyby odczytać cudzy wiersz. Sprawdzamy tożsamość zwróconych wierszy z tym,
    co naprawdę leży w bazie.
    """
    store = SqliteConversationStore(tmp_path / "conversations.db")
    rozmowa = store.open_conversation("teams_graph", "w1")
    ile = 8
    bariera = threading.Barrier(ile)

    def dopisz(numer: int) -> tuple[int, str]:
        bariera.wait(timeout=10)
        wiadomosc = store.append_message(rozmowa.id, "user", f"tura {numer}")
        return wiadomosc.id, wiadomosc.text

    with ThreadPoolExecutor(max_workers=ile) as executor:
        zwrocone = [f.result(timeout=20) for f in [executor.submit(dopisz, n) for n in range(ile)]]

    zapisane = {m.id: m.text for m in store.messages(rozmowa.id)}
    assert len(zapisane) == ile
    assert dict(zwrocone) == zapisane  # każdy zwrócił SWÓJ wiersz, nie cudzy


# --- cache osadzeń dense: plik indeksu też bywa dzielony przez drzwi ---------------


@pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None,
    reason="wymaga numpy (extra retrieval-dense)",
)
def test_embedding_cache_written_by_one_process_is_reused_by_another(tmp_path):
    """Indeks wektorów leży w pliku dzielonym przez drzwi — drugi proces ma NIE liczyć od nowa.

    Osadzanie to sekundy pracy modelu; gdyby cache nie przechodził przez plik (albo transakcja
    zostawała otwarta i blokowała drugie połączenie), każde drzwi płaciłyby pełny koszt.
    """
    from workmate.adapters.outbound.onnx_semantic_ranker import OnnxSemanticRanker

    index: Path = tmp_path / "dense" / "vectors.db"
    notatki = [
        make_note("x/y/2025-01-01-aaa", project="p", title="aaa", on=date(2025, 1, 1)),
        make_note("x/y/2025-01-02-bbb", project="p", title="bbb", on=date(2025, 1, 2)),
    ]
    pierwsze_drzwi = _AtrapaEmbeddera()
    OnnxSemanticRanker(model="fake", index_path=index, embedder=pierwsze_drzwi).rank(
        "query", notatki
    )
    assert len(pierwsze_drzwi.osadzone) == 3  # dwa pasaże + zapytanie

    drugie_drzwi = _AtrapaEmbeddera()
    kolejnosc = OnnxSemanticRanker(model="fake", index_path=index, embedder=drugie_drzwi).rank(
        "query", notatki
    )

    assert kolejnosc[0] == notatki[0].id
    # Drugie drzwi osadzają WYŁĄCZNIE zapytanie — treści notatek biorą z cache w pliku.
    assert drugie_drzwi.osadzone == ["query"]
