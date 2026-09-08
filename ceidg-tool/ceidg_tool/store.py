"""SQLite: cache surowych rekordów, checkpoint runu, historia żądań, znacznik zmian (ADR-0004).

Niezmiennik wznowienia: rekordy strony i checkpoint trafiają do bazy w jednej
transakcji — po awarii albo są oba, albo żadne.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .clock import Clock, local_hhmm, utc_iso
from .errors import ProfileMismatchError, StoreError, StoreLockedError
from .ratelimit import RequestStamp
from .recordid import KanonicznyId, kanoniczne_id, kanoniczny_id
from .records import RawRecord

SCHEMA_VERSION = 3

RUN_STATUSES = ("w_toku", "zakonczony", "przerwany", "blad")
RUN_KINDS = ("firmy", "raport", "zmiana")
STAGES = ("lista", "szczegoly", "gotowe")
DEFAULT_LOCK_STALE_S = 600.0
DEFAULT_BUSY_TIMEOUT_S = 30.0
REQUEST_LOG_KEEP_S = 2 * 3600.0  # dłużej niż najdłuższe okno limitera (60 min)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run (
  run_id        TEXT PRIMARY KEY,
  created_utc   TEXT NOT NULL,
  updated_utc   TEXT NOT NULL,
  environment   TEXT NOT NULL CHECK (environment IN ('test','prod')),
  tool_version  TEXT NOT NULL,
  profile_hash  TEXT NOT NULL,
  criteria_json TEXT NOT NULL,
  criteria_hash TEXT NOT NULL,
  mode          TEXT NOT NULL CHECK (mode IN ('lista','szczegoly')),
  status        TEXT NOT NULL CHECK (status IN ('w_toku','zakonczony','przerwany','blad')),
  count_api     INTEGER,
  pages_done    INTEGER NOT NULL DEFAULT 0,
  records_seen  INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  kind          TEXT NOT NULL DEFAULT 'firmy' CHECK (kind IN ('firmy','raport','zmiana'))
);
CREATE INDEX IF NOT EXISTS run_resume_idx ON run(status, criteria_hash, created_utc);

CREATE TABLE IF NOT EXISTS checkpoint (
  run_id      TEXT PRIMARY KEY REFERENCES run(run_id) ON DELETE CASCADE,
  stage       TEXT NOT NULL CHECK (stage IN ('lista','szczegoly','gotowe')),
  cursor_mode TEXT NOT NULL CHECK (cursor_mode IN ('links','numeric')),
  cursor      TEXT,
  page_index  INTEGER NOT NULL DEFAULT 0,
  updated_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS firma (
  id               TEXT PRIMARY KEY,
  environment      TEXT NOT NULL,
  list_json        TEXT,
  list_utc         TEXT,
  detail_json      TEXT,
  detail_utc       TEXT,
  detail_state     TEXT NOT NULL DEFAULT 'brak'
                   CHECK (detail_state IN ('brak','pobrany','nieznaleziony','blad')),
  nip              TEXT,
  regon            TEXT,
  status_api       TEXT,
  wojewodztwo      TEXT,
  data_rozpoczecia TEXT,
  zrodlo           TEXT NOT NULL CHECK (zrodlo IN ('CEIDG_API','CEIDG_RAPORT'))
);
CREATE INDEX IF NOT EXISTS firma_nip_idx ON firma(nip);
CREATE INDEX IF NOT EXISTS firma_detail_idx ON firma(detail_state, detail_utc);

CREATE TABLE IF NOT EXISTS run_firma (
  run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  firma_id   TEXT NOT NULL REFERENCES firma(id) ON DELETE CASCADE,
  page_index INTEGER NOT NULL,
  position   INTEGER NOT NULL,
  PRIMARY KEY (run_id, firma_id)
);
CREATE INDEX IF NOT EXISTS run_firma_order_idx ON run_firma(run_id, page_index, position);
-- Klucz obcy `firma_id` jest po stronie dziecka, a klucz główny prowadzi `run_id` jako pierwszy,
-- więc bez tego indeksu każde usunięcie wiersza `firma` skanuje całą tabelę powiązań w poszukiwaniu
-- kaskady. Zmierzone na bazie operatora: migracja v3 (15 550 wpisów) trwała 64 s zamiast sekundy,
-- a `wyczysc` płacił to samo, tylko nikt tego nie mierzył.
CREATE INDEX IF NOT EXISTS run_firma_firma_idx ON run_firma(firma_id);

CREATE TABLE IF NOT EXISTS request_log (
  ts_epoch    REAL NOT NULL,
  environment TEXT NOT NULL,
  token_fp    TEXT NOT NULL,
  endpoint    TEXT NOT NULL,
  status      INTEGER
);
CREATE INDEX IF NOT EXISTS request_log_ts_idx ON request_log(environment, token_fp, ts_epoch);

CREATE TABLE IF NOT EXISTS watermark (
  scope       TEXT PRIMARY KEY,
  last_utc    TEXT NOT NULL,
  updated_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_lock (
  environment   TEXT PRIMARY KEY,
  pid           INTEGER NOT NULL,
  started_utc   TEXT NOT NULL,
  heartbeat_epoch REAL NOT NULL
);
"""


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    environment: str
    status: str
    mode: str
    criteria_hash: str
    criteria_json: str
    profile_hash: str
    tool_version: str
    count_api: int | None
    pages_done: int
    records_seen: int
    created_utc: str
    updated_utc: str
    error: str | None
    kind: str = "firmy"


@dataclass(frozen=True)
class Checkpoint:
    stage: str
    cursor_mode: str
    cursor: str | None
    page_index: int
    updated_utc: str


def _scalars(
    rec: Mapping[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Skalary indeksowe z rekordu — indeks, nigdy źródło wartości dla eksportu."""
    owner = _mapping(rec.get("wlasciciel"))
    address = _mapping(rec.get("adresDzialalnosci"))
    return (
        _text(owner.get("nip")),
        _text(owner.get("regon")),
        _text(rec.get("status")),
        _text(address.get("wojewodztwo")),
        _text(rec.get("dataRozpoczecia")),
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    """Rejestr bywa niespójny — nie-słownik traktujemy jak brak wartości, nie jak błąd."""
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _record_id(rec: Mapping[str, Any]) -> KanonicznyId:
    """Identyfikator rekordu w postaci kanonicznej — jedyne wejście `id` do bazy.

    Kanonizacja jest tu powtórzona po `client._records_of`, bo `save_details` bywa wołane
    też z rekordami spoza klienta (raporty, testy), a idempotentnej funkcji nie szkodzi
    zawołać dwa razy — w odróżnieniu od pominięcia jej raz (ADR-0013)."""
    rid = rec.get("id")
    if not isinstance(rid, str) or not rid:
        raise StoreError("rekord z API nie ma pola `id` — nie da się go zapisać w cache")
    return kanoniczny_id(rid)


_STAN_WARTOSC = {"pobrany": 3, "nieznaleziony": 2, "blad": 1, "brak": 0}
_SCALANE_KOLUMNY = (
    "list_json",
    "list_utc",
    "nip",
    "regon",
    "status_api",
    "wojewodztwo",
    "data_rozpoczecia",
)


def _ranga_szczegolow(row: Mapping[str, Any]) -> tuple[int, str]:
    """Im wyżej, tym pełniejsza wiedza o szczegółach: 'pobrany' bije 'brak',
    a przy równym stanie wygrywa świeższy `detail_utc`."""
    return (_STAN_WARTOSC.get(str(row["detail_state"]), 0), str(row["detail_utc"] or ""))


class SqliteHistory:
    """`RequestHistory` nad tabelą `request_log`; każdy zapis jest natychmiast trwały."""

    def __init__(self, conn: sqlite3.Connection, environment: str, token_fp: str) -> None:
        self._conn = conn
        self._environment = environment
        self._token_fp = token_fp

    def recent(self, since_epoch: float) -> Sequence[RequestStamp]:
        rows = self._conn.execute(
            "SELECT ts_epoch, endpoint, status FROM request_log "
            "WHERE environment = ? AND token_fp = ? AND ts_epoch >= ? ORDER BY ts_epoch",
            (self._environment, self._token_fp, since_epoch),
        ).fetchall()
        return [RequestStamp(float(r[0]), str(r[1]), r[2]) for r in rows]

    def record(self, ts_epoch: float, endpoint: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO request_log(ts_epoch, environment, token_fp, endpoint, status) "
                "VALUES (?, ?, ?, ?, NULL)",
                (ts_epoch, self._environment, self._token_fp, endpoint),
            )

    def mark(self, ts_epoch: float, status: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE request_log SET status = ? WHERE rowid = ("
                "  SELECT rowid FROM request_log WHERE environment = ? AND token_fp = ? "
                "  AND ts_epoch = ? ORDER BY rowid DESC LIMIT 1)",
                (status, self._environment, self._token_fp, ts_epoch),
            )


class Store:
    """Baza lokalna jednego środowiska."""

    def __init__(
        self,
        path: Path | str,
        *,
        environment: str,
        clock: Clock,
        lock_stale_s: float = DEFAULT_LOCK_STALE_S,
    ) -> None:
        if environment not in ("test", "prod"):
            raise StoreError(f"nieznane środowisko bazy: {environment!r}")
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._environment = environment
        self._clock = clock
        self._lock_stale_s = lock_stale_s
        self.quarantined: Path | None = None
        self._lock_held = False
        self.journal_mode = "memory"
        """Tryb dziennika, jaki baza faktycznie przyjęła. `PRAGMA journal_mode` zwraca wynik,
        a nieprzeczytany zostawiał ciche nieprzejście na WAL bez obserwatora. Baza w pamięci
        nigdy o WAL nie jest pytana, więc startowa wartość opisuje właśnie ten przypadek."""
        self.merged_duplicates = 0
        """Ile wpisów leżało w bazie w **dwóch** wierszach i zostało scalonych (migracja v3).
        Zero po każdym kolejnym otwarciu — `pipeline` melduje to operatorowi raz."""
        self.renamed_identifiers = 0
        """Ile wpisów miało tylko niekanoniczną pisownię i zostało przemianowanych bez scalania.
        Liczone osobno, bo komunikat mówi o wpisach *zapisanych dwukrotnie* — wrzucenie tu
        samych przemianowań zawyżałoby liczbę duplikatów przy przerwanym `aktualizuj`."""
        self._conn = self._connect_checked()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self._path != ":memory:":
            try:
                # `PRAGMA journal_mode` **zwraca** tryb wynikowy. Nieprzeczytany zostawiał
                # ciche nieprzejście na WAL bez obserwatora — ten sam kształt co wyrzucany
                # `rowcount` w `touch_lock`.
                tryb = self._conn.execute("PRAGMA journal_mode = WAL").fetchone()
                self.journal_mode = str(tryb[0]).lower() if tryb else "?"
            except sqlite3.OperationalError as exc:
                # Przełączenie na WAL wymaga blokady wyłącznej i **nie** respektuje
                # `busy_timeout`, więc dwa procesy startujące jednocześnie na bazie spoza WAL
                # dawały operatorowi surowy traceback zamiast zdania. Bazy tworzone przez to
                # narzędzie są już w WAL, więc dotyczy to bazy zrobionej czymś innym.
                raise StoreError(
                    f"Nie udało się otworzyć bazy {self._path}: {exc}. "
                    "Najczęstsze przyczyny: korzysta z niej w tej chwili inny proces, "
                    "plik jest tylko do odczytu albo leży na dysku sieciowym "
                    "(OneDrive, udział sieciowy, kwarantanna antywirusa)."
                ) from exc
        self._migrate()

    # ------------------------------------------------------------------ cykl życia

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def environment(self) -> str:
        return self._environment

    def _connect_checked(self) -> sqlite3.Connection:
        """Łączy z bazą po `PRAGMA integrity_check`; uszkodzoną odkłada z sufiksem `.uszkodzony`."""
        conn = sqlite3.connect(self._path, timeout=DEFAULT_BUSY_TIMEOUT_S)
        if self._path == ":memory:":
            return conn
        try:
            verdict = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            verdict = f"błąd: {exc}"
        if verdict == "ok":
            return conn
        conn.close()
        stamp = utc_iso(self._clock.wall()).replace(":", "").replace("-", "")
        original = Path(self._path)
        target = original.with_name(f"{original.name}.uszkodzony-{stamp}")
        try:
            original.replace(target)
            for suffix in ("-wal", "-shm"):
                side = original.with_name(original.name + suffix)
                if side.exists():
                    side.replace(target.with_name(target.name + suffix))
        except OSError as exc:
            raise StoreError(
                f"Baza {original} jest uszkodzona ({verdict}) i nie da się jej odłożyć: {exc}"
            ) from exc
        self.quarantined = target
        return sqlite3.connect(self._path, timeout=DEFAULT_BUSY_TIMEOUT_S)

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"baza {self._path} ma schemat w wersji {version}, narzędzie zna {SCHEMA_VERSION}"
            )
        # DDL poza transakcją, bo `executescript` i tak commituje otwartą — trzymanie go
        # w środku dawałoby złudzenie jednej transakcji tam, gdzie są dwie.
        self._conn.executescript(_SCHEMA)
        if version == SCHEMA_VERSION:
            return
        with self._conn:
            # `BEGIN IMMEDIATE` **przed** odczytem wersji i zbudowaniem planu. Migracja czyta
            # cały stan tabeli, a potem go przepisuje; gdy blokada zapisu przychodziła dopiero
            # z pierwszym `INSERT`-em, dwa procesy otwierające bazę w tym samym oknie budowały
            # dwa plany z tego samego snapshotu. Drugi wykonywał wtedy `UPDATE ... = (SELECT ...
            # FROM firma WHERE id = ?)` na wiersz źródłowy, którego już nie było — podzapytanie
            # zwracało NULL i kasowało dopiero co scaloną wartość. Oba procesy kończyły się
            # sukcesem, `integrity_check` był czysty: cisza, nie błąd.
            self._conn.execute("BEGIN IMMEDIATE")
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version >= SCHEMA_VERSION:
                return  # inny proces zdążył zmigrować, gdy czekaliśmy na blokadę
            if version < 2:
                self._upgrade_to_2()
            if version < 3:
                self.merged_duplicates, self.renamed_identifiers = self._upgrade_to_3()
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _upgrade_to_2(self) -> None:
        """v1 -> v2: rodzaj runu (`kind`) zamiast wnioskowania z kształtu `criteria_json`."""
        columns = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(run)")}
        if "kind" not in columns:
            self._conn.execute(
                "ALTER TABLE run ADD COLUMN kind TEXT NOT NULL DEFAULT 'firmy' "
                "CHECK (kind IN ('firmy','raport','zmiana'))"
            )
            self._conn.execute(
                "UPDATE run SET kind = 'zmiana' WHERE criteria_json LIKE '{\"zmiana_od%'"
            )

    def _upgrade_to_3(self) -> tuple[int, int]:
        """v2 -> v3: jedna tożsamość wpisu zamiast jednej na pisownię (ADR-0013).

        Do wersji 2 identyfikator z `/zmiana` (małe litery) i ten sam identyfikator
        z `/firma` (wielkie) były dla bazy dwoma różnymi wpisami, więc `aktualizuj`
        zostawiał parę wierszy: zaślepkę bez danych przypiętą do runu i komplet danych
        przypięty do niczego. Migracja scala je po kanonicznym identyfikatorze.

        Scala, a nie kasuje: gdyby obie pisownie niosły dane, `COALESCE` skleja braki,
        a blok szczegółów idzie w całości z wiersza o pełniejszej wiedzy. Bez tego rekordy
        kupione żądaniami znikają przy najbliższym `wyczysc` — `purge_older_than` kasuje
        dokładnie te wiersze, których nie trzyma żaden run. Całość idzie w transakcji
        `_migrate`, więc awaria zostawia stan sprzed migracji.

        Plan powstaje z **metadanych**, nie z pełnych wierszy, i zapisy idą tylko tam, gdzie
        scalenie coś zmienia. Powód jest zmierzony: pierwsza wersja przepisywała `detail_json`
        każdego z 15 550 wpisów na tę samą wartość i zajmowała 66 s, choć sam odczyt bazy
        trwa 0,2 s. Półtorej minuty ciszy przy pierwszym otwarciu bazy po aktualizacji to
        dokładnie ten defekt, którym ten projekt nazywa ciszę."""
        flagi = ", ".join(f"({c} IS NOT NULL) AS ma_{c}" for c in _SCALANE_KOLUMNY)
        meta = self._conn.execute(
            f"SELECT id, detail_state, detail_utc, {flagi} FROM firma"
        ).fetchall()
        grupy: dict[KanonicznyId, list[Any]] = {}
        for row in meta:
            grupy.setdefault(kanoniczny_id(str(row["id"])), []).append(row)
        do_scalenia = {kid: g for kid, g in grupy.items() if len(g) > 1 or str(g[0]["id"]) != kid}
        if not do_scalenia:
            return 0, 0

        materializacja: list[tuple[str, str]] = []
        szczegoly: list[tuple[str, str]] = []
        uzupelnienia: dict[str, list[tuple[str, str]]] = {c: [] for c in _SCALANE_KOLUMNY}
        zrodla: list[tuple[str, str]] = []
        pary: list[tuple[str, str]] = []
        for kid, grupa in do_scalenia.items():
            pary.extend((str(r["id"]), kid) for r in grupa if str(r["id"]) != kid)
            kanoniczny = next((r for r in grupa if str(r["id"]) == kid), None)
            najlepszy = max(grupa, key=_ranga_szczegolow)
            if kanoniczny is None:
                # Wiersz kanoniczny powstaje jako kopia najlepszego, więc od tej chwili to on
                # jest celem scalania. Wcześniejsze `continue` pomijało tu uzupełnienia, czyli
                # jedyna gałąź tej migracji, która **kasowała** zamiast scalać. Dziś nieosiągalna
                # (rejestr emituje tylko UPPER i lower, więc grupa bez pisowni kanonicznej jest
                # jednoelementowa), ale komunikat obiecuje, że nic nie ginie.
                materializacja.append((kid, str(najlepszy["id"])))
                kanoniczny = najlepszy
            elif najlepszy is not kanoniczny:
                szczegoly.append((str(najlepszy["id"]), kid))
            # `zrodlo` idzie za wierszem niosącym **dane**, a nie za blokiem szczegółów.
            # Wiersze z raportu mają `detail_state='brak'` (raport nie niesie szczegółów), więc
            # przypięcie tej kolumny do bloku szczegółów zostawiało scalonemu wpisowi `zrodlo`
            # zaślepki, czyli domyślne 'CEIDG_API'. A ta kolumna steruje ukrywaniem kolumn
            # w eksporcie (`store.record_sources`), więc pomyłka tutaj kłamie w skoroszycie.
            dawca_zrodla = next(
                (r for r in grupa if r["ma_list_json"] or str(r["detail_state"]) != "brak"), None
            )
            if dawca_zrodla is not None and dawca_zrodla is not kanoniczny:
                zrodla.append((str(dawca_zrodla["id"]), kid))
            # Kolumny listy i skalary bierze się per kolumna, w odróżnieniu od bloku
            # szczegółów, który jedzie w całości. Rozjechać się nie mogą, bo `save_page`
            # zapisuje `list_json`, `list_utc` i skalary jednym `INSERT`-em — pochodzą
            # z tej samej odpowiedzi, więc „pierwszy niepusty" nie może ich pomieszać.
            for kolumna in _SCALANE_KOLUMNY:
                if not kanoniczny[f"ma_{kolumna}"]:
                    dawca = next((r for r in grupa if r[f"ma_{kolumna}"]), None)
                    if dawca is not None:
                        uzupelnienia[kolumna].append((str(dawca["id"]), kid))

        kolumny = (
            "environment, list_json, list_utc, detail_json, detail_utc, detail_state, "
            + ", ".join(_SCALANE_KOLUMNY[2:])
            + ", zrodlo"
        )
        self._conn.executemany(
            f"INSERT INTO firma(id, {kolumny}) SELECT ?, {kolumny} FROM firma WHERE id = ?",
            materializacja,
        )
        # Blok szczegółów przenosimy w całości i wewnątrz SQL-a: `detail_json`, `detail_utc`
        # i `detail_state` muszą opisywać to samo pobranie, a blob nie ma powodu wędrować
        # przez Pythona.
        self._conn.executemany(
            "UPDATE firma SET "
            "detail_json = (SELECT s.detail_json FROM firma s WHERE s.id = ?1), "
            "detail_utc = (SELECT s.detail_utc FROM firma s WHERE s.id = ?1), "
            "detail_state = (SELECT s.detail_state FROM firma s WHERE s.id = ?1) "
            "WHERE id = ?2",
            szczegoly,
        )
        self._conn.executemany(
            "UPDATE firma SET zrodlo = (SELECT s.zrodlo FROM firma s WHERE s.id = ?) WHERE id = ?",
            zrodla,
        )
        for kolumna, zadania in uzupelnienia.items():
            self._conn.executemany(
                f"UPDATE firma SET {kolumna} = (SELECT s.{kolumna} FROM firma s WHERE s.id = ?) "
                "WHERE id = ?",
                zadania,
            )
        # Przepięcie hurtem przez tabelę tymczasową, nie wiersz po wierszu: `run_firma` ma
        # klucz główny `(run_id, firma_id)`, więc `WHERE firma_id = ?` nie ma indeksu i skanuje
        # całą tabelę — pętla po 15 550 wpisach kosztowała 93 s. Hurtem to jeden przebieg.
        self._conn.execute(
            "CREATE TEMP TABLE _migracja_v3(stary TEXT PRIMARY KEY, nowy TEXT NOT NULL)"
        )
        try:
            self._conn.executemany("INSERT INTO _migracja_v3(stary, nowy) VALUES (?, ?)", pary)
            # Kolejność jest nośna: `run_firma.firma_id` ma `ON DELETE CASCADE`, więc usunięcie
            # starego wiersza `firma` przed przepięciem zabrałoby ze sobą powiązania z runem.
            # `INSERT OR IGNORE`, bo ten sam run mógł zebrać obie pisownie — wtedy klucz główny
            # `(run_id, firma_id)` zderzyłby je i zwykły `UPDATE` by padł.
            self._conn.execute(
                "INSERT OR IGNORE INTO run_firma(run_id, firma_id, page_index, position) "
                "SELECT rf.run_id, m.nowy, rf.page_index, rf.position "
                "FROM run_firma rf JOIN _migracja_v3 m ON m.stary = rf.firma_id"
            )
            self._conn.execute(
                "DELETE FROM run_firma WHERE firma_id IN (SELECT stary FROM _migracja_v3)"
            )
            self._conn.execute("DELETE FROM firma WHERE id IN (SELECT stary FROM _migracja_v3)")
        finally:
            self._conn.execute("DROP TABLE _migracja_v3")
        scalone = sum(1 for g in do_scalenia.values() if len(g) > 1)
        return scalone, len(do_scalenia) - scalone

    def _now(self) -> str:
        return utc_iso(self._clock.wall())

    # ------------------------------------------------------------------ runy

    def start_run(
        self,
        *,
        run_id: str,
        criteria_json: str,
        criteria_hash: str,
        profile_hash: str,
        mode: str,
        tool_version: str,
        cursor_mode: str,
        kind: str = "firmy",
    ) -> str:
        if mode not in ("lista", "szczegoly"):
            raise StoreError(f"nieznany tryb runu: {mode!r}")
        if kind not in RUN_KINDS:
            raise StoreError(f"nieznany rodzaj runu: {kind!r}")
        now = self._now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO run(run_id, created_utc, updated_utc, environment, tool_version, "
                "profile_hash, criteria_json, criteria_hash, mode, status, kind) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'w_toku', ?)",
                (
                    run_id,
                    now,
                    now,
                    self._environment,
                    tool_version,
                    profile_hash,
                    criteria_json,
                    criteria_hash,
                    mode,
                    kind,
                ),
            )
            self._conn.execute(
                "INSERT INTO checkpoint(run_id, stage, cursor_mode, cursor, page_index, "
                "updated_utc) "
                "VALUES (?, 'lista', ?, NULL, 0, ?)",
                (run_id, cursor_mode, now),
            )
        return run_id

    def get_run(self, run_id: str) -> RunInfo:
        row = self._conn.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(f"brak runu {run_id} w bazie {self._path}")
        return self._run_info(row)

    def find_resumable_run(
        self, criteria_hash: str, *, profile_hash: str, kind: str = "firmy"
    ) -> RunInfo | None:
        """Ostatni niedokończony run danego rodzaju; inny profil API = odmowa wznowienia."""
        row = self._conn.execute(
            "SELECT * FROM run WHERE criteria_hash = ? AND kind = ? "
            "AND status IN ('w_toku','przerwany') ORDER BY created_utc DESC, rowid DESC LIMIT 1",
            (criteria_hash, kind),
        ).fetchone()
        if row is None:
            return None
        info = self._run_info(row)
        if info.profile_hash != profile_hash:
            raise ProfileMismatchError(
                f"Run {info.run_id} pobierano z profilem API {info.profile_hash}, a aktywny "
                f"profil to {profile_hash}. Wznowienie zmieszałoby dwa dialekty API — "
                "przywróć poprzedni profil albo rozpocznij nowe pobranie."
            )
        return info

    def list_runs(self, limit: int = 20) -> list[RunInfo]:
        rows = self._conn.execute(
            "SELECT * FROM run ORDER BY created_utc DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._run_info(r) for r in rows]

    def update_run_status(self, run_id: str, status: str, error: str | None = None) -> None:
        if status not in RUN_STATUSES:
            raise StoreError(f"nieznany status runu: {status!r}")
        with self._conn:
            cur = self._conn.execute(
                "UPDATE run SET status = ?, error = ?, updated_utc = ? WHERE run_id = ?",
                (status, error, self._now(), run_id),
            )
        if cur.rowcount == 0:
            raise StoreError(f"brak runu {run_id}")

    def set_run_count(self, run_id: str, count: int | None) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE run SET count_api = ?, updated_utc = ? WHERE run_id = ?",
                (count, self._now(), run_id),
            )

    def get_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT stage, cursor_mode, cursor, page_index, updated_utc FROM checkpoint "
            "WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return Checkpoint(
            stage=str(row["stage"]),
            cursor_mode=str(row["cursor_mode"]),
            cursor=row["cursor"],
            page_index=int(row["page_index"]),
            updated_utc=str(row["updated_utc"]),
        )

    def set_stage(self, run_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise StoreError(f"nieznany etap: {stage!r}")
        with self._conn:
            self._conn.execute(
                "UPDATE checkpoint SET stage = ?, updated_utc = ? WHERE run_id = ?",
                (stage, self._now(), run_id),
            )

    @staticmethod
    def _run_info(row: sqlite3.Row) -> RunInfo:
        return RunInfo(
            run_id=str(row["run_id"]),
            environment=str(row["environment"]),
            status=str(row["status"]),
            mode=str(row["mode"]),
            criteria_hash=str(row["criteria_hash"]),
            criteria_json=str(row["criteria_json"]),
            profile_hash=str(row["profile_hash"]),
            tool_version=str(row["tool_version"]),
            count_api=row["count_api"],
            pages_done=int(row["pages_done"]),
            records_seen=int(row["records_seen"]),
            created_utc=str(row["created_utc"]),
            updated_utc=str(row["updated_utc"]),
            error=row["error"],
            kind=str(row["kind"]) if "kind" in row.keys() else "firmy",
        )

    # ------------------------------------------------------------------ strony listy

    def save_page(
        self,
        run_id: str,
        *,
        page_index: int,
        records: Sequence[Mapping[str, Any]],
        next_cursor: str | None,
        zrodlo: str = "CEIDG_API",
    ) -> None:
        """Zapisuje rekordy strony i przesuwa checkpoint — jedna transakcja."""
        now = self._now()
        inserted = 0
        with self._conn:
            for position, rec in enumerate(records):
                rid = _record_id(rec)
                nip, regon, status, woj, data_rozp = _scalars(rec)
                self._conn.execute(
                    "INSERT INTO firma(id, environment, list_json, list_utc, nip, regon, "
                    "status_api, wojewodztwo, data_rozpoczecia, zrodlo) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "list_json = excluded.list_json, list_utc = excluded.list_utc, "
                    "nip = COALESCE(excluded.nip, firma.nip), "
                    "regon = COALESCE(excluded.regon, firma.regon), "
                    "status_api = COALESCE(excluded.status_api, firma.status_api), "
                    "wojewodztwo = COALESCE(excluded.wojewodztwo, firma.wojewodztwo), "
                    "data_rozpoczecia = "
                    "COALESCE(excluded.data_rozpoczecia, firma.data_rozpoczecia)",
                    (
                        rid,
                        self._environment,
                        json.dumps(rec, ensure_ascii=False),
                        now,
                        nip,
                        regon,
                        status,
                        woj,
                        data_rozp,
                        zrodlo,
                    ),
                )
                inserted += self._conn.execute(
                    "INSERT OR IGNORE INTO run_firma(run_id, firma_id, page_index, position) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, rid, page_index, position),
                ).rowcount
            self._conn.execute(
                "UPDATE checkpoint SET cursor = ?, page_index = ?, updated_utc = ? "
                "WHERE run_id = ?",
                (next_cursor, page_index + 1, now, run_id),
            )
            self._conn.execute(
                "UPDATE run SET pages_done = pages_done + 1, records_seen = records_seen + ?, "
                "updated_utc = ? WHERE run_id = ?",
                (inserted, now, run_id),
            )

    def link_ids(self, run_id: str, *, page_index: int, ids: Sequence[KanonicznyId]) -> int:
        """Dopina identyfikatory (np. z `/zmiana`) do runu bez nadpisywania cache listy."""
        now = self._now()
        inserted = 0
        with self._conn:
            for position, rid in enumerate(ids):
                self._conn.execute(
                    "INSERT OR IGNORE INTO firma(id, environment, zrodlo) "
                    "VALUES (?, ?, 'CEIDG_API')",
                    (rid, self._environment),
                )
                inserted += self._conn.execute(
                    "INSERT OR IGNORE INTO run_firma(run_id, firma_id, page_index, position) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, rid, page_index, position),
                ).rowcount
            self._conn.execute(
                "UPDATE checkpoint SET page_index = ?, updated_utc = ? WHERE run_id = ?",
                (page_index + 1, now, run_id),
            )
            self._conn.execute(
                "UPDATE run SET pages_done = pages_done + 1, records_seen = records_seen + ?, "
                "updated_utc = ? WHERE run_id = ?",
                (inserted, now, run_id),
            )
        return inserted

    def stale_detail_ids(
        self, ids: Sequence[KanonicznyId], *, cutoff: datetime
    ) -> list[KanonicznyId]:
        """Te z `ids`, których szczegółów nie ma w cache albo pochodzą sprzed `cutoff`.

        Próg **podaje wołający**, i to jest cała treść tej zmiany. Wcześniej metoda liczyła go
        sama z TTL cache'u — regułą sensowną tam, gdzie ponowne użycie cache'u jest celem
        (tak działa `pending_detail_ids` na ścieżce `pobierz`), a **błędną** tutaj: ta metoda
        ma jednego producenta wywołań i jest nim `aktualizuj`, gdzie identyfikatory przychodzą
        z `/zmiana`,
        czyli rejestr właśnie powiedział, że te wpisy się zmieniły. Wpis zmieniony wczoraj,
        a pobrany trzy dni temu, mieścił się w siedmiodniowym TTL i był pomijany — jego stary
        `detail_json` zostawał w bazie, a podsumowanie meldowało go jako odświeżony.

        Defekt był niewidoczny do 2026-09-08: dopóki ADR-0013 nie naprawił pisowni
        identyfikatorów, cache nie trafiał ani razu, więc filtr zwracał wszystko. Naprawa
        jednej cichej straty uruchomiła drugą.

        Sensownym progiem dla `/zmiana` jest **koniec okna zmian**: wpis zgłoszony jako
        zmieniony w `[od, do]` potrzebuje szczegółu pobranego nie wcześniej niż `do`.
        Powtórzenie tego samego okna nadal kosztuje zero żądań, bo wtedy `detail_utc >= do`.
        Że `do` nie leży w przyszłości, pilnują `cli._koniec_zakresu_zmian` i `update_range`.

        Próg wchodzi jako `datetime`, a na tekst zamienia go ta metoda. Porównanie idzie po
        napisach ISO, więc próg zapisany inaczej niż zawartość kolumny (`…Z` kontra `…+00:00`)
        sortowałby się przed **każdym** wierszem i po cichu uznawał wszystko za nieświeże.
        Jedno formatowanie w jednym miejscu czyni to nie do wyrażenia.
        """
        prog = utc_iso(cutoff.timestamp())
        fresh: set[str] = set()
        chunk = 500
        for i in range(0, len(ids), chunk):
            part = list(ids[i : i + chunk])
            marks = ",".join("?" * len(part))
            rows = self._conn.execute(
                f"SELECT id FROM firma WHERE id IN ({marks}) AND detail_state = 'pobrany' "
                "AND detail_utc IS NOT NULL AND detail_utc >= ?",
                (*part, prog),
            ).fetchall()
            fresh.update(str(r[0]) for r in rows)
        return [rid for rid in ids if rid not in fresh]  # `ids` są już kanoniczne

    def outdated_details(
        self, ids: Sequence[KanonicznyId], *, older_than: datetime
    ) -> list[KanonicznyId]:
        """Te z `ids`, które mają szczegół **pobrany**, ale starszy niż `older_than`.

        Zwraca identyfikatory, a nie liczbę, bo wołający sumuje po oknach, a ta sama firma
        może wrócić w dwóch oknach długiego zakresu. Liczba zliczałaby wtedy wystąpienia,
        podczas gdy zdanie dla operatora mówi o **wpisach** — i tylko licznik wpisów da się
        porównać z „Zmienionych wpisów: N", czyli zrobić z alarmu coś więcej niż alarm.

        Obserwator dla `aktualizuj`, liczony **per okno zmian** i sumowany — bo próg
        świeżości też jest per okno. Pierwsza wersja pytała raz na cały run z progiem
        równym początkowi zakresu i była przez to tępsza niż reguła, której pilnuje: wpis
        zgłoszony w drugim oknie, ze szczegółem z pierwszego, mieścił się powyżej progu
        i nie był widziany, choć jest dokładnie tym przypadkiem, o który chodzi.

        Zakres po identyfikatorach, nie po runie, właśnie dlatego: okno zna swoje `ids`
        i swój koniec, a `run_firma` zna tylko numer strony.

        Stan `pobrany` w warunku jest nośny. Wpis, którego rejestr nie zna
        (`nieznaleziony`), nie ma szczegółu i **nie jest** cichą stratą — ma własne
        wyjaśnienie i własny licznik. Bez tego warunku obserwator alarmowałby przy każdym
        poprawnym przebiegu, a alarm, który dzwoni zawsze, nie jest alarmem.

        `count_run_unresolved` tego nie widzi i nie może: tamto liczy wpisy w stanie `brak`,
        a tutaj wpis jest `pobrany`. Jest **rozwiązany, tylko nieprawdziwy** — ten kształt
        defektu projekt nazywa gwarancją bez obserwatora.
        """
        prog = utc_iso(older_than.timestamp())
        znalezione: list[KanonicznyId] = []
        chunk = 500
        for i in range(0, len(ids), chunk):
            part = list(ids[i : i + chunk])
            marks = ",".join("?" * len(part))
            rows = self._conn.execute(
                f"SELECT id FROM firma WHERE id IN ({marks}) "
                "AND detail_state = 'pobrany' AND detail_utc IS NOT NULL AND detail_utc < ?",
                (*part, prog),
            ).fetchall()
            znalezione.extend(KanonicznyId(str(r[0])) for r in rows)
        return znalezione

    def trim_request_log(self, keep_s: float = REQUEST_LOG_KEEP_S) -> int:
        """Przycina historię żądań do okna potrzebnego limiterowi."""
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM request_log WHERE ts_epoch < ?", (self._clock.wall() - keep_s,)
            )
        return int(cur.rowcount)

    # ------------------------------------------------------------------ szczegóły

    def pending_detail_ids(self, run_id: str, *, ttl_days: float) -> list[KanonicznyId]:
        """Lista `id` do pobrania szczegółów — zapytanie, nie zmaterializowana kolejka."""
        cutoff = utc_iso(self._clock.wall() - ttl_days * 86_400)
        rows = self._conn.execute(
            "SELECT f.id FROM run_firma rf JOIN firma f ON f.id = rf.firma_id "
            "WHERE rf.run_id = ? AND ("
            "  f.detail_state IN ('brak','blad') "
            "  OR (f.detail_state = 'pobrany' AND (f.detail_utc IS NULL OR f.detail_utc < ?))"
            ") ORDER BY rf.page_index, rf.position",
            (run_id, cutoff),
        ).fetchall()
        # Kanonizacja także na odczycie: baza jest granicą tak samo jak API, a wiersze
        # zapisane przed migracją do schematu v3 mogą nieść starą pisownię (ADR-0013).
        return kanoniczne_id(str(r[0]) for r in rows)

    def save_details(
        self,
        *,
        details: Sequence[Mapping[str, Any]],
        missing_ids: Sequence[KanonicznyId] = (),
        failed_ids: Sequence[KanonicznyId] = (),
        zrodlo: str = "CEIDG_API",
    ) -> None:
        """Zapisuje porcję szczegółów (i stany 404/błąd) w jednej transakcji."""
        now = self._now()
        with self._conn:
            for rec in details:
                rid = _record_id(rec)
                nip, regon, status, woj, data_rozp = _scalars(rec)
                self._conn.execute(
                    "INSERT INTO firma(id, environment, detail_json, detail_utc, detail_state, "
                    "nip, regon, status_api, wojewodztwo, data_rozpoczecia, zrodlo) "
                    "VALUES (?, ?, ?, ?, 'pobrany', ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "detail_json = excluded.detail_json, detail_utc = excluded.detail_utc, "
                    "detail_state = 'pobrany', "
                    "nip = COALESCE(excluded.nip, firma.nip), "
                    "regon = COALESCE(excluded.regon, firma.regon), "
                    "status_api = COALESCE(excluded.status_api, firma.status_api), "
                    "wojewodztwo = COALESCE(excluded.wojewodztwo, firma.wojewodztwo), "
                    "data_rozpoczecia = "
                    "COALESCE(excluded.data_rozpoczecia, firma.data_rozpoczecia)",
                    (
                        rid,
                        self._environment,
                        json.dumps(rec, ensure_ascii=False),
                        now,
                        nip,
                        regon,
                        status,
                        woj,
                        data_rozp,
                        zrodlo,
                    ),
                )
            for state, ids in (("nieznaleziony", missing_ids), ("blad", failed_ids)):
                for rid in ids:
                    self._conn.execute(
                        "UPDATE firma SET detail_state = ?, detail_utc = ? WHERE id = ?",
                        (state, now, rid),
                    )

    # ------------------------------------------------------------------ eksport

    def count_run_records(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM run_firma WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

    def count_run_details(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM run_firma rf JOIN firma f ON f.id = rf.firma_id "
            "WHERE rf.run_id = ? AND f.detail_state = 'pobrany'",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def count_run_unresolved(self, run_id: str) -> int:
        """Wpisy runu, które nie doczekały się ani szczegółów, ani wyjaśnienia.

        Po `aktualizuj` każdy zmieniony identyfikator powinien skończyć jako `pobrany`,
        `nieznaleziony` albo `blad`. Każdy, który został w stanie `brak`, oznacza pracę
        wykonaną i zgubioną — dokładnie to, co 2026-09-08 stało się z 13 401 wpisami,
        a podsumowanie napisało tylko „szczegółów: 0", bez niczego do porównania."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM run_firma rf JOIN firma f ON f.id = rf.firma_id "
            "WHERE rf.run_id = ? AND f.detail_state = 'brak'",
            (run_id,),
        ).fetchone()
        return int(row[0])

    def iter_run_records(self, run_id: str) -> Iterator[RawRecord]:
        cursor = self._conn.execute(
            "SELECT f.id, f.list_json, f.detail_json, f.list_utc, f.detail_utc, "
            "f.detail_state, f.zrodlo FROM run_firma rf JOIN firma f ON f.id = rf.firma_id "
            "WHERE rf.run_id = ? ORDER BY rf.page_index, rf.position",
            (run_id,),
        )
        for row in cursor:
            yield RawRecord(
                id=str(row["id"]),
                list_json=json.loads(row["list_json"]) if row["list_json"] else None,
                detail_json=json.loads(row["detail_json"]) if row["detail_json"] else None,
                list_utc=row["list_utc"],
                detail_utc=row["detail_utc"],
                detail_state=str(row["detail_state"]),
                zrodlo=str(row["zrodlo"]),
            )

    def iter_records_for_runs(self, run_ids: Sequence[str]) -> Iterator[RawRecord]:
        """Rekordy z kilku runów (pobranie w partiach) bez duplikatów — ta sama firma
        może trafić do dwóch partii, gdy zmieni się jej data rozpoczęcia między partiami."""
        seen: set[str] = set()
        for run_id in run_ids:
            for record in self.iter_run_records(run_id):
                if record.id in seen:
                    continue
                seen.add(record.id)
                yield record

    def record_sources(self, run_ids: Sequence[str]) -> set[str]:
        """Z jakich źródeł pochodzą rekordy tych runów — `zrodlo` zapisane przy każdym wpisie.

        Eksport pyta o to zamiast czytać `run.kind`, bo etykieta runu bywa błędna (starszy
        zapis oznaczył pobranie z raportu jako `firmy`), a `zrodlo` jest wprost danymi i ma
        ograniczenie `CHECK` w schemacie. Pusty zbiór znaczy „run bez rekordów"."""
        if not run_ids:
            return set()
        placeholders = ",".join("?" for _ in run_ids)
        rows = self._conn.execute(
            f"SELECT DISTINCT f.zrodlo FROM run_firma rf JOIN firma f ON f.id = rf.firma_id "
            f"WHERE rf.run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def count_records_for_runs(self, run_ids: Sequence[str]) -> int:
        if not run_ids:
            return 0
        placeholders = ",".join("?" for _ in run_ids)
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT firma_id) FROM run_firma WHERE run_id IN ({placeholders})",
            tuple(run_ids),
        ).fetchone()
        return int(row[0])

    def find_run(
        self,
        criteria_hash: str,
        *,
        statuses: Sequence[str],
        kind: str = "firmy",
        profile_hash: str | None = None,
    ) -> RunInfo | None:
        """Ostatni run o danym odcisku kryteriów w jednym z podanych stanów.

        Planowanie partii jest deterministyczne, więc odcisk kryteriów partii wystarczy,
        by rozpoznać partię już pobraną (pomijaną) i przerwaną (wznawianą). Rodzaj runu
        musi się zgadzać: pobranie z raportu ma ten sam odcisk kryteriów co partia z API,
        a jego rekordy nie mają identyfikatora wpisu ani szczegółów. Inny profil API
        oznacza inny dialekt, więc też nie jest tą samą partią."""
        if not statuses:
            return None
        placeholders = ",".join("?" for _ in statuses)
        # Profil filtrujemy w zapytaniu, nie po wybraniu wiersza: inaczej starszy run pod
        # innym profilem przesłoniłby pasujący i partia byłaby pobierana od nowa.
        profile_clause = "" if profile_hash is None else " AND profile_hash = ?"
        profile_args = () if profile_hash is None else (profile_hash,)
        row = self._conn.execute(
            f"SELECT * FROM run WHERE criteria_hash = ? AND kind = ?{profile_clause} "
            f"AND status IN ({placeholders}) ORDER BY created_utc DESC, rowid DESC LIMIT 1",
            (criteria_hash, kind, *profile_args, *statuses),
        ).fetchone()
        return None if row is None else self._run_info(row)

    # ------------------------------------------------------------------ historia żądań

    def history(self, token_fp: str) -> SqliteHistory:
        return SqliteHistory(self._conn, self._environment, token_fp)

    # ------------------------------------------------------------------ znacznik zmian

    def get_watermark(self, scope: str) -> str | None:
        row = self._conn.execute(
            "SELECT last_utc FROM watermark WHERE scope = ?", (scope,)
        ).fetchone()
        return None if row is None else str(row[0])

    def set_watermark(self, scope: str, last_utc: str) -> None:
        """Znacznik idzie tylko **do przodu**.

        Do wprowadzenia `--do` każda ścieżka produkcyjna kończyła zakres na „teraz", więc
        monotoniczność brała się sama z siebie i nikt jej nie zapisał. Z `--do` da się
        świadomie nadrobić starszy zakres (`--od 09-01 --do 09-05` przy znaczniku na 09-08),
        a bezwarunkowy zapis cofnąłby wtedy znacznik o trzy dni. Danych to nie traci —
        następny przebieg pokryje różnicę — ale każe zapłacić za nią drugi raz, a jednostką
        rachunku są tu czterdziestominutowe przebiegi."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO watermark(scope, last_utc, updated_utc) VALUES (?, ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET "
                "last_utc = MAX(watermark.last_utc, excluded.last_utc), "
                "updated_utc = excluded.updated_utc",
                (scope, last_utc, self._now()),
            )

    # ------------------------------------------------------------------ blokada runu

    def acquire_lock(self, *, force: bool = False) -> bool:
        """Jeden aktywny run na środowisko; warunkowy zapis w jednej transakcji IMMEDIATE.

        Blokada wygasa, gdy heartbeat jest starszy niż `lock_stale_s`. Ten sam PID po
        restarcie systemu nie przejmuje cudzej, żywej blokady.

        Zwraca `True`, gdy `force` przejęło blokadę **wciąż świeżą** — czyli prawdopodobnie
        po procesie, który żyje. Wywołujący ma o tym powiedzieć operatorowi: dwa procesy na
        jednym runie nie zdublują rekordów (klucz główny), ale oba zapisują checkpoint i oba
        oznaczają run jako zakończony, więc eksport z tego okna bywa niepełny.
        """
        now_epoch = self._clock.wall()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            previous = self._conn.execute(
                "SELECT heartbeat_epoch FROM run_lock WHERE environment = ?",
                (self._environment,),
            ).fetchone()
            stolen_alive = bool(
                force
                and previous is not None
                and float(previous["heartbeat_epoch"]) >= now_epoch - self._lock_stale_s
            )
            cur = self._conn.execute(
                "INSERT INTO run_lock(environment, pid, started_utc, heartbeat_epoch) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(environment) DO UPDATE SET "
                "pid = excluded.pid, started_utc = excluded.started_utc, "
                "heartbeat_epoch = excluded.heartbeat_epoch "
                "WHERE ? OR run_lock.heartbeat_epoch < ?",
                (
                    self._environment,
                    os.getpid(),
                    self._now(),
                    now_epoch,
                    force,
                    now_epoch - self._lock_stale_s,
                ),
            )
            if cur.rowcount == 0:
                row = self._conn.execute(
                    "SELECT pid, started_utc, heartbeat_epoch FROM run_lock WHERE environment = ?",
                    (self._environment,),
                ).fetchone()
                # Godzina wygaśnięcia, bo po `kill -9` blokada zostaje po martwym procesie
                # i operator ma prawo wiedzieć, czy czeka minutę, czy dziesięć.
                expires = local_hhmm(float(row["heartbeat_epoch"]) + self._lock_stale_s)
                raise StoreLockedError(
                    f"Inny proces (PID {row['pid']}, start {row['started_utc']}) pobiera dane "
                    f"na środowisku {self._environment}. Blokada wygasa o {expires} — "
                    "poczekaj albo powtórz polecenie w wierszu poleceń z flagą --force "
                    "(np. `ceidg-tool wznow --force`), jeśli tamten proces już nie żyje."
                )
            self._conn.commit()
            self._lock_held = True
            return stolen_alive
        except BaseException:
            self._conn.rollback()
            raise

    def touch_lock(self) -> bool:
        """Odświeża dzierżawę blokady. `False` znaczy, że nie odświeżyliśmy żadnego wiersza.

        Zero zaktualizowanych wierszy ma **dwa** znaczenia i wołający musi je rozróżnić przez
        `holds_lock`: albo `run_lock` istnieje z cudzym PID-em (utrata dzierżawy), albo wiersza
        nie ma wcale, bo ta operacja blokady nigdy nie brała (eksport, `runy`, sonda) — wtedy
        nie ma czego bronić. Sama wygasła dzierżawa, której nikt nie przejął, wciąż jest nasza
        i `UPDATE` przechodzi — słusznie po cichu.

        Dlaczego `rowcount` przestał być wyrzucany: 2026-09-08 maszyna operatora spała
        9 h 50 min w środku `aktualizuj`, a `DEFAULT_LOCK_STALE_S` to 600 s. Zamrożony proces
        nie ma czym bić serca, więc blokada wygasła pod pracującym procesem i po wybudzeniu
        pisał on do bazy bez ważnej dzierżawy. Ostrzeżenie dostawał dotąd tylko ten, kto
        blokadę **przejmuje** (`--force`); ofiara nie dowiadywała się nigdy."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE run_lock SET heartbeat_epoch = ? WHERE environment = ? AND pid = ?",
                (self._clock.wall(), self._environment, os.getpid()),
            )
        return int(cur.rowcount) > 0

    @property
    def holds_lock(self) -> bool:
        """Czy ta instancja wzięła blokadę i jeszcze jej nie oddała.

        Bez tego `touch_lock() is False` znaczyłoby raz „ktoś nam ją zabrał", a raz
        „nigdy jej nie mieliśmy", i bicie serca w eksporcie po cichu **brałoby** blokadę,
        której nie potrzebuje."""
        return self._lock_held

    def release_lock(self) -> None:
        self._lock_held = False
        with self._conn:
            self._conn.execute(
                "DELETE FROM run_lock WHERE environment = ? AND pid = ?",
                (self._environment, os.getpid()),
            )

    # ------------------------------------------------------------------ retencja

    def purge_older_than(self, days: float) -> tuple[int, int]:
        """Usuwa zakończone runy starsze niż `days` i osierocone rekordy. Zwraca (runy, rekordy)."""
        cutoff = utc_iso(self._clock.wall() - days * 86_400)
        with self._conn:
            # Wykluczenie po statusie chroniło run, który właśnie pracuje — ale run porzucony
            # w `w_toku` (ubity proces, nieobsłużony Ctrl+C) nigdy z tego statusu nie wychodził
            # i jego rekordy z danymi osobowymi zostawały bezterminowo. O tym, czy run żyje,
            # rozstrzyga czas ostatniego zapisu: pracujący run zapisuje częściej niż wygasa
            # blokada, więc cokolwiek starszego od jej progu na pewno nie pracuje.
            abandoned = utc_iso(self._clock.wall() - DEFAULT_LOCK_STALE_S)
            runs = self._conn.execute(
                "DELETE FROM run WHERE updated_utc < ? AND (status != 'w_toku' OR updated_utc < ?)",
                (cutoff, abandoned),
            ).rowcount
            records = self._conn.execute(
                "DELETE FROM firma WHERE id NOT IN (SELECT firma_id FROM run_firma) "
                "AND COALESCE(detail_utc, list_utc, '') < ?",
                (cutoff,),
            ).rowcount
            self._conn.execute(
                "DELETE FROM request_log WHERE ts_epoch < ?",
                (self._clock.wall() - REQUEST_LOG_KEEP_S,),
            )
        return int(runs), int(records)
