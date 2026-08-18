"""Przerwana migracja schematu rozmów (``_migrate_messages_add_fk``, ADR 0012).

Migracja PRZEBUDOWUJE tabelę ``messages``: kasuje indeks FTS, usuwa osierocone wiersze, tworzy
tabelę obok, przepisuje dane, kasuje starą i zmienia nazwę nowej. Jej atomowość nie jest nigdzie
zadeklarowana — stoi na NIEJAWNEJ transakcji, którą ``sqlite3`` otwiera przy pierwszym ``DELETE``
i zamyka dopiero ``commit`` w ``_init_schema``. Poprzedni przegląd zostawił to jako pytanie
otwarte, bo żaden test nie ćwiczył przerwania w połowie.

Sondy przerywają migrację w KAŻDYM z sześciu punktów (od usunięcia sierot po przebudowę FTS)
i sprawdzają jedno: po „śmierci procesu" plik ``conversations.db`` daje się otworzyć ponownie,
wiadomości są w komplecie, więz FK zostaje założony, a wyszukiwanie działa. Śmierć procesu
symulujemy ZAMKNIĘCIEM okaleczonego połączenia — dokładnie to robi jądro przy ``docker stop``,
a niezatwierdzona transakcja jest wtedy wycofywana.

Deterministyczne w całości: awaria jest wstrzykiwana po treści SQL, nie po czasie.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from workmate.adapters.outbound import sqlite_conversations
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore

# Punkty przerwania — cała sekwencja przebudowy, po kolei.
_PUNKTY_PRZERWANIA = (
    "DELETE FROM messages WHERE conversation_id NOT IN",
    "CREATE TABLE messages_new",
    "INSERT INTO messages_new",
    "DROP TABLE messages",
    "ALTER TABLE messages_new RENAME TO messages",
    "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')",
)


def _baza_sprzed_0012(path: Path) -> None:
    """Plik rozmów sprzed ADR 0012: ``messages`` BEZ więzu FK, z jednym wierszem osieroconym."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, channel TEXT NOT NULL, external_id TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL, token_estimate INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP)
        );
        INSERT INTO conversations(id, channel, external_id, status)
             VALUES ('c1', 'teams_graph', 'w1', 'active');
        INSERT INTO messages(conversation_id, role, text, token_estimate)
             VALUES ('c1', 'user', 'ustalenia ze spotkania', 0);
        INSERT INTO messages(conversation_id, role, text, token_estimate)
             VALUES ('c1', 'assistant', 'odpowiedź asystenta', 0);
        INSERT INTO messages(conversation_id, role, text, token_estimate)
             VALUES ('znikneła', 'user', 'wiersz osierocony', 0);
        """
    )
    conn.commit()
    conn.close()


class _PolaczenieKtorePada:
    """Połączenie SQLite padające na wskazanym fragmencie SQL — atrapa ubicia procesu.

    Deleguje WSZYSTKO (także ``row_factory``, ustawiane przez magazyn atrybutem) do prawdziwego
    połączenia; różni się jedną rzeczą: na wskazanym zdaniu rzuca ``OperationalError`` zamiast je
    wykonać. Wstrzyknięcie po TREŚCI SQL, nie po liczniku wywołań — odporne na refaktor sąsiednich
    zapytań.
    """

    def __init__(self, prawdziwe: sqlite3.Connection, fragment: str) -> None:
        object.__setattr__(self, "_prawdziwe", prawdziwe)
        object.__setattr__(self, "_fragment", fragment)

    def execute(self, sql: str, *args: object):
        if self._fragment in sql:
            raise sqlite3.OperationalError("symulacja ubicia procesu w trakcie migracji")
        return self._prawdziwe.execute(sql, *args)

    def __getattr__(self, name: str):
        return getattr(object.__getattribute__(self, "_prawdziwe"), name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(object.__getattribute__(self, "_prawdziwe"), name, value)


def _przerwij_migracje(path: Path, fragment: str) -> None:
    """Otwórz magazyn tak, żeby migracja padła na ``fragment``; potem ubij proces (close).

    Podmiana ``sqlite3.connect`` idzie ręcznie, a nie przez ``monkeypatch``: fixture jest
    współdzielona z autouse'owym czyszczeniem środowiska w ``conftest``, więc ``undo`` w połowie
    testu przywróciłby też zmienne ``WORKMATE_*`` maszyny.
    """
    prawdziwy_connect = sqlite3.connect
    okaleczone: list[_PolaczenieKtorePada] = []

    def connect(*args: object, **kwargs: object):
        polaczenie = _PolaczenieKtorePada(prawdziwy_connect(*args, **kwargs), fragment)
        okaleczone.append(polaczenie)
        return polaczenie

    sqlite_conversations.sqlite3.connect = connect  # type: ignore[assignment]
    try:
        with pytest.raises(sqlite3.OperationalError, match="symulacja ubicia"):
            SqliteConversationStore(path)
    finally:
        sqlite_conversations.sqlite3.connect = prawdziwy_connect  # type: ignore[assignment]
    # Ubicie procesu: deskryptor zamyka jądro, a SQLite wycofuje niezatwierdzoną transakcję.
    for polaczenie in okaleczone:
        polaczenie.close()


def _tabele(path: Path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )
    finally:
        conn.close()


@pytest.mark.parametrize("fragment", _PUNKTY_PRZERWANIA)
def test_interrupted_migration_keeps_every_message_and_retries_cleanly(
    tmp_path: Path, fragment: str
):
    """Przerwanie w DOWOLNYM punkcie przebudowy nie może zabrać ani jednej tury rozmowy.

    ``conversations.db`` to pamięć agenta między restartami; migracja biegnie przy KAŻDYM starcie
    drzwi, dopóki nie zejdzie. Utrata połowy wierszy byłaby cicha — wątek po prostu zacząłby
    odpowiadać bez kontekstu.
    """
    path = tmp_path / "conversations.db"
    _baza_sprzed_0012(path)

    _przerwij_migracje(path, fragment)

    store = SqliteConversationStore(path)
    assert [m.text for m in store.messages("c1")] == [
        "ustalenia ze spotkania",
        "odpowiedź asystenta",
    ]


@pytest.mark.parametrize("fragment", _PUNKTY_PRZERWANIA)
def test_interrupted_migration_leaves_no_half_built_table_behind(tmp_path: Path, fragment: str):
    """Po przerwaniu nie może zostać ``messages_new`` — ponowna migracja padłaby na jej istnieniu.

    ``CREATE TABLE messages_new`` nie ma ``IF NOT EXISTS``, więc niedokończona przebudowa
    zamieniłaby się w crash-loop drzwi: każdy start umierałby na „table already exists".
    """
    path = tmp_path / "conversations.db"
    _baza_sprzed_0012(path)

    _przerwij_migracje(path, fragment)

    assert "messages_new" not in _tabele(path)


@pytest.mark.parametrize("fragment", _PUNKTY_PRZERWANIA)
def test_migration_after_an_interruption_applies_the_foreign_key_and_rebuilds_search(
    tmp_path: Path, fragment: str
):
    """Kolejny start ma DOKOŃCZYĆ migrację: więz FK działa, a indeks FTS jest przeliczony.

    Sama nieutrata danych nie wystarcza — gdyby przerwanie zostawiało bazę „prawie zmigrowaną"
    (np. z tabelą bez FK, ale z ``has_fk`` widzianym jako prawda), wiadomość do nieistniejącego
    wątku wchodziłaby po cichu, a wyszukiwanie milczałoby o starych turach.
    """
    path = tmp_path / "conversations.db"
    _baza_sprzed_0012(path)

    _przerwij_migracje(path, fragment)

    store = SqliteConversationStore(path)
    assert [h.text for h in store.search("ustalenia")] == ["ustalenia ze spotkania"]
    with pytest.raises(sqlite3.IntegrityError):
        store.append_message("nie-ma-takiej-rozmowy", "user", "sierota")


@pytest.mark.parametrize("fragment", _PUNKTY_PRZERWANIA)
def test_interrupted_migration_does_not_half_apply_the_orphan_cleanup(
    tmp_path: Path, fragment: str
):
    """Sieroty giną RAZEM z założeniem więzu, nie osobno — inaczej przerwanie kasowałoby dane
    bez dawania niczego w zamian.

    Usunięcie osieroconych wierszy jest nieodwracalne, a jego jedynym uzasadnieniem jest to, że
    zaraz potem powstaje więz FK. Gdyby ``DELETE`` zatwierdzał się osobno od reszty przebudowy,
    seria nieudanych startów zjadałaby dane przy braku jakiegokolwiek postępu migracji.
    """
    path = tmp_path / "conversations.db"
    _baza_sprzed_0012(path)

    _przerwij_migracje(path, fragment)

    conn = sqlite3.connect(str(path))
    try:
        pozostale = [r[0] for r in conn.execute("SELECT text FROM messages ORDER BY id")]
    finally:
        conn.close()
    assert "wiersz osierocony" in pozostale  # nic nie skasowano bez założenia więzu
    assert len(pozostale) == 3
