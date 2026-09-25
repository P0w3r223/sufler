"""Wspólna dyscyplina ODCZYTU magazynów SQLite dla narzędzi diagnostycznych (ADR 0069 R2).

Trzy powierzchnie obserwowalności (dziennik audytu z ADR 0067 §1, kwarantanna notifiera z ADR 0067
§2 i kwarantanna wejściowa z ADR 0069) były do tej pory ZAPISYWANE i nigdy nie czytane — jedyną
drogą do nich był ``sqlite3`` na wolumenie produkcyjnym. Czytelnik dokłada się do bazy, którą
w tej samej chwili PISZĄ procesy drzwi, więc obowiązuje ta sama dyscyplina co w magazynach
(``check_same_thread=False`` + ``Lock`` po stronie klasy, ``busy_timeout``, WAL) z jedną różnicą:
połączenie otwieramy w trybie ``mode=ro``.

Tryb ``ro`` jest tu granicą PRAKTYCZNĄ, nie bezpieczeństwa (operator ma dostęp do pliku): pilnuje,
by literówka w narzędziu diagnostycznym nie tknęła bazy produkcyjnej i by odczyt nie ZAKŁADAŁ
brakującej bazy ani tabel — inaczej zła ścieżka wracałaby jako „brak wpisów" zamiast jako pomyłka
operatora. Dlatego czytelnik nie tworzy schematu; brak tabeli to osobny, nazwany błąd.

Baza w trybie WAL wymaga od czytelnika pliku ``-shm``; gdy pisarz jest zamknięty, a ``-shm`` nie
istnieje, SQLite musi go założyć — czego połączenie ``ro`` nie może. Taki przypadek schodzi do
zwykłego połączenia do ISTNIEJĄCEGO pliku (ścieżkę i tak weryfikujemy wyżej), bo „nie umiem
otworzyć" byłoby gorszą odpowiedzią niż odczyt bez sprzętowej blokady zapisu.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

_BUSY_TIMEOUT_MS = 5000


class MissingTableError(RuntimeError):
    """Baza jest, ale nie ma w niej tabeli — magazyn nigdy nic nie zapisał (nie: zła ścieżka)."""


def _uri(path: Path) -> str:
    """URI ``file:`` dla ścieżki — z kodowaniem znaków, które SQLite czyta jako składnię URI."""
    return "file:" + quote(str(path).replace("\\", "/"), safe="/:")


def connect_readonly(db_path: Path | str) -> sqlite3.Connection:
    """Otwórz ISTNIEJĄCĄ bazę do odczytu; nie zakłada pliku, katalogu ani schematu."""
    path = Path(db_path).expanduser()
    try:
        conn = sqlite3.connect(f"{_uri(path)}?mode=ro", uri=True, check_same_thread=False)
    except sqlite3.OperationalError:
        # Zwykle: baza w WAL bez pliku ``-shm``, którego połączenie ``ro`` nie może założyć.
        conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    return conn


def as_utc_iso(moment: datetime) -> str:
    """Znacznik czasu w tej samej postaci, w jakiej magazyny go zapisują (ISO 8601, UTC).

    Kolumny czasu są TEKSTEM, więc porównanie zakresu jest leksykograficzne — działa dokładnie
    dlatego, że każdy pisarz odkłada ``datetime.now(tz=UTC).isoformat()``, czyli stałą szerokość
    i stały offset. Granicę filtra sprowadzamy do tej samej postaci; czas naiwny czytamy jako UTC,
    bo strefa maszyny operatora nie ma tu nic do rzeczy.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def select_recent(
    conn: sqlite3.Connection,
    *,
    table: str,
    columns: tuple[str, ...],
    time_column: str,
    source_column: str,
    source: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Ostatnie wiersze tabeli (najnowsze pierwsze) z filtrem po źródle i po okresie.

    Filtry idą do SQL, nie do pętli po wyniku: ``LIMIT`` ma przycinać to, o co operator pytał,
    a nie odcinać wiersze, które filtr dopiero by odrzucił.
    """
    conditions: list[str] = []
    params: list[Any] = []
    if source is not None:
        conditions.append(f"{source_column} = ?")
        params.append(source)
    if since is not None:
        conditions.append(f"{time_column} >= ?")
        params.append(as_utc_iso(since))
    if until is not None:
        conditions.append(f"{time_column} <= ?")
        params.append(as_utc_iso(until))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT {', '.join(columns)} FROM {table}{where} ORDER BY id DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            raise MissingTableError(table) from exc
        raise
    return [{col: row[col] for col in columns} for row in rows]
