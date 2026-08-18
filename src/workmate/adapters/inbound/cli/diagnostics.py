"""Czytelnik powierzchni diagnostycznych: ``workmate-diagnostics {audit|dead-letters|inbound}``.

Trzy magazyny obserwowalności — dziennik audytu (ADR 0067 §1), kwarantanna notifiera (ADR 0067 §2)
i kwarantanna wiadomości przychodzących (ADR 0069) — były PISANE i nigdy czytane: ``recent()``
nie miało wołającego w ``src/``, a jedyną drogą do wpisów był ``sqlite3`` na wolumenie
produkcyjnym. To jest ten czytelnik (ADR 0069, R2): jedno polecenie na trzy powierzchnie, bo
operator pyta o nie w jednym przebiegu i z tego samego powodu — ktoś nie dostał odpowiedzi.

Ścieżka bazy nigdy nie jest zaszyta: ``--db`` albo zmienna środowiskowa (``WORKMATE_EVENTS_DB``
dla obu kwarantann, ``WORKMATE_AUDIT_DB`` dla audytu — ta sama, która WŁĄCZA zapis). Bez żadnej
z nich polecenie kończy się błędem zamiast zgadywać ``~/.workmate/events.db``: domyślna ścieżka
wdrożenia jest własnością konfiguracji, a nie narzędzia diagnostycznego.

Wyjście niesie IDENTYFIKATORY, powód i czas — nigdy treści. Kwarantanna wejściowa świadomie nie
przechowuje tekstu wiadomości (ADR 0069 §5), więc to zestawienie ma pomóc ODNALEŹĆ wiadomość
w Teams (kanał, wątek, id, nadawca po AAD id), a nie ją odtworzyć. ``--json`` daje te same pola
skryptom.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from workmate.adapters.inbound import env
from workmate.adapters.outbound.sqlite_audit import SqliteAuditReader
from workmate.adapters.outbound.sqlite_dead_letters import SqliteDeadLetterReader
from workmate.adapters.outbound.sqlite_readonly import MissingTableError

if TYPE_CHECKING:
    from collections.abc import Sequence

# Tyle wpisów, ile operator przeczyta w jednym rzucie; ``--limit`` podnosi świadomie.
_DEFAULT_LIMIT = 50
# Powód bywa wieloliniowy (ślad wyjątku) — w wydruku tekstowym spłaszczamy go do wiersza.
_REASON_CHARS = 300
_RELATIVE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_RELATIVE_UNITS = {"m": "minutes", "h": "hours", "d": "days"}

_ENV_BY_SURFACE = {
    "audit": "WORKMATE_AUDIT_DB",
    "dead-letters": "WORKMATE_EVENTS_DB",
    "inbound": "WORKMATE_EVENTS_DB",
}
_TITLES = {
    "audit": "Dziennik audytu wywołań narzędzi",
    "dead-letters": "Kwarantanna wyjściowa (zdarzenia niewysłane)",
    "inbound": "Kwarantanna wejściowa (wiadomości porzucone)",
}


class TimeSpecError(ValueError):
    """Nieczytelna granica okresu — osobny typ, żeby ``main`` odróżnił ją od awarii odczytu."""


def parse_moment(text: str, *, now: datetime) -> datetime:
    """Zamień granicę okresu na moment: ``24h``/``7d``/``30m`` wstecz albo znacznik ISO 8601.

    Postać względna jest tu domyślnym narzędziem operatora („co się działo od wczoraj"), bo
    wpisanie pełnego znacznika po incydencie to praca dla samej pracy. Czas naiwny czytamy jako
    UTC — magazyny zapisują UTC, a domyślanie się strefy maszyny przesuwałoby okno bez ostrzeżenia.
    """
    value = text.strip()
    relative = _RELATIVE.match(value)
    if relative is not None:
        amount, unit = int(relative.group(1)), relative.group(2).lower()
        return now - timedelta(**{_RELATIVE_UNITS[unit]: amount})
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TimeSpecError(
            f"Nie rozumiem granicy okresu {text!r}. Podaj 24h / 7d / 30m albo znacznik ISO "
            "(2026-08-17 lub 2026-08-17T09:30)."
        ) from exc
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _one_line(text: str) -> str:
    """Spłaszcz powód do jednego wiersza i przytnij — wpis ma być czytelny, nie kompletny."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _REASON_CHARS else flat[:_REASON_CHARS].rstrip() + "…"


def _wpisy(count: int) -> str:
    """Polska odmiana liczebnika — nagłówek czyta człowiek, nie parser."""
    if count == 1:
        return "1 wpis"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} wpisy"
    return f"{count} wpisów"


def _header(surface: str, rows: Sequence[dict[str, Any]], *, db: Path) -> str:
    return f"{_TITLES[surface]} · {_wpisy(len(rows))} · {db}"


def _empty(surface: str, *, db: Path) -> str:
    return f"{_TITLES[surface]}: brak wpisów w podanym zakresie ({db})."


def format_outbound(rows: Sequence[dict[str, Any]], *, db: Path) -> str:
    """Zdarzenia, których nie udało się wysłać: czas · źródło · id zdarzenia · próby · powód."""
    if not rows:
        return _empty("dead-letters", db=db)
    lines = [_header("dead-letters", rows, db=db)]
    for row in rows:
        lines.append(
            f"{row['failed_at']} · {row['source']} · zdarzenie {row['event_id']} "
            f"· prób {row['attempts']}"
        )
        lines.append(f"    powód: {_one_line(row['reason'])}")
    return "\n".join(lines)


def format_inbound(rows: Sequence[dict[str, Any]], *, db: Path) -> str:
    """Porzucone wiadomości: tyle identyfikatorów, ile trzeba, by otworzyć wątek w Teams."""
    if not rows:
        return _empty("inbound", db=db)
    lines = [_header("inbound", rows, db=db)]
    for row in rows:
        lines.append(
            f"{row['failed_at']} · {row['door']} · wiadomość {row['message_id']} "
            f"· prób {row['attempts']}"
        )
        lines.append(
            f"    kanał {row['channel']} · wątek {row['thread_root_id']} · nadawca {row['sender']}"
        )
        lines.append(f"    powód: {_one_line(row['reason'])}")
    return "\n".join(lines)


def format_audit(rows: Sequence[dict[str, Any]], *, db: Path) -> str:
    """Wpisy dziennika: kto (pseudonim), skąd, co wywołał, z jakim skutkiem i zaufaniem."""
    if not rows:
        return _empty("audit", db=db)
    lines = [_header("audit", rows, db=db)]
    for row in rows:
        verdict = f" · werdykt {row['judge_verdict']}" if row["judge_verdict"] else ""
        lines.append(
            f"{row['occurred_at']} · {row['door']} · {row['tool_name']} "
            f"· {row['status']} · zaufanie {row['trust_class']}{verdict}"
        )
        lines.append(f"    nadawca {row['actor_key']} · rozmowa {row['conversation_key']}")
        lines.append(f"    argumenty: {_one_line(row['arg_summary'])}")
    return "\n".join(lines)


_FORMATTERS = {
    "audit": format_audit,
    "dead-letters": format_outbound,
    "inbound": format_inbound,
}


def format_json(surface: str, rows: Sequence[dict[str, Any]], *, db: Path) -> str:
    """Te same pola co wydruk tekstowy, bez skracania powodu — wejście dla dalszej obróbki."""
    payload = {"surface": surface, "db": str(db), "count": len(rows), "entries": list(rows)}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="workmate-diagnostics",
        description=(
            "Odczyt powierzchni diagnostycznych WorkMate: dziennika audytu i obu kwarantann. "
            "Przykład: workmate-diagnostics inbound --since 24h --source teams_graph"
        ),
    )
    subparsers = parser.add_subparsers(dest="surface", required=True)
    for surface, title in _TITLES.items():
        sub = subparsers.add_parser(surface, help=title)
        sub.add_argument(
            "--db",
            default=None,
            help=f"ścieżka pliku SQLite (domyślnie {_ENV_BY_SURFACE[surface]})",
        )
        sub.add_argument(
            "--source",
            default=None,
            help="zawęź do źródła: drzwi (audyt, kwarantanna wejściowa) lub źródła zdarzenia",
        )
        sub.add_argument("--since", default=None, help="od kiedy: 24h / 7d / 30m albo znacznik ISO")
        sub.add_argument("--until", default=None, help="do kiedy: jak --since (domyślnie: teraz)")
        sub.add_argument(
            "--limit",
            type=int,
            default=_DEFAULT_LIMIT,
            help=f"ile wpisów (domyślnie {_DEFAULT_LIMIT}, najnowsze pierwsze)",
        )
        sub.add_argument("--json", action="store_true", help="wynik jako JSON (dla skryptów)")
    return parser.parse_args(list(argv))


def _resolve_db(surface: str, raw: str | None) -> Path:
    """Ścieżka z ``--db`` albo ze zmiennej środowiskowej; nigdy zaszyta i nigdy nietrafiona."""
    variable = _ENV_BY_SURFACE[surface]
    value = raw or os.environ.get(variable, "")
    if not value:
        raise ValueError(f"Brak ścieżki bazy: ustaw {variable} albo podaj --db.")
    path = Path(value).expanduser()
    # Tryb ODCZYTU nie materializuje bazy: literówka w ścieżce ma wrócić jako pomyłka operatora,
    # a nie jako „brak wpisów" nad świeżo założonym, pustym plikiem.
    if not path.exists():
        raise ValueError(f"Nie znaleziono bazy: {path}")
    return path


def _fetch(surface: str, db: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Wywołaj czytelnika właściwego dla powierzchni — filtry są dla wszystkich trzech te same."""
    now = datetime.now(tz=UTC)
    source: str | None = args.source
    since = parse_moment(args.since, now=now) if args.since else None
    until = parse_moment(args.until, now=now) if args.until else None
    limit = max(0, args.limit)
    if surface == "audit":
        return SqliteAuditReader(db).entries(source=source, since=since, until=until, limit=limit)
    reader = SqliteDeadLetterReader(db)
    if surface == "inbound":
        return reader.inbound(source=source, since=since, until=until, limit=limit)
    return reader.outbound(source=source, since=since, until=until, limit=limit)


def main(argv: Sequence[str] | None = None) -> int:
    """Wypisz wpisy wskazanej powierzchni; 1 przy braku bazy lub nieczytelnym zakresie.

    Pusty wynik to SUKCES (kod 0) z jawnym komunikatem — „nic nie wpadło do kwarantanny" jest
    odpowiedzią, a nie awarią; kod 1 zostaje dla pomyłki operatora i niedostępnej bazy.
    """
    env.force_utf8_io()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    surface: str = args.surface
    try:
        db = _resolve_db(surface, args.db)
        rows = _fetch(surface, db, args)
    except MissingTableError as exc:
        print(
            f"Baza nie ma tabeli {exc} — ten magazyn nic jeszcze nie zapisał.",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:  # brak/zła ścieżka, nieczytelna granica okresu
        print(str(exc), file=sys.stderr)
        return 1
    except sqlite3.Error as exc:  # baza zajęta, uszkodzona, nie do otwarcia
        print(f"Nie udało się odczytać bazy: {exc}", file=sys.stderr)
        return 1
    print(format_json(surface, rows, db=db) if args.json else _FORMATTERS[surface](rows, db=db))
    return 0
