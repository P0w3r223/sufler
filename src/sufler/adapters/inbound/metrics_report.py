"""Raport metryk użycia (Tor A) — ``sufler-metrics`` czyta licznik i wypisuje zestawienie.

Najtańszy sposób odczytu: licznik zbiera dane w tle (SqliteMetricsStore, zapis na każdej turze
drzwi), a ten entrypoint zwija je do czytelnej tabeli „drzwi → wywołania · unikalni · powracający".
Ścieżkę bierze z ``--db`` albo ``SUFLER_METRICS_DB`` (ta sama, co włącza zbieranie). Formatowanie
(``format_summary``) jest czyste i testowane jednostkowo; ``main`` to tylko I/O + kod wyjścia.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from sufler.adapters.outbound.sqlite_metrics import SqliteMetricsStore
from sufler.core.domain.metrics import MetricsSummary


def format_summary(summary: MetricsSummary) -> str:
    """Zestawienie metryk jako tekst; pusty licznik → czytelny komunikat zamiast pustej tabeli."""
    if not summary.by_door:
        return "Brak zebranych metryk (licznik pusty)."
    lines = ["Metryki użycia (per drzwi):", "drzwi · wywołania · unikalni · powracający"]
    for d in summary.by_door:
        lines.append(f"• {d.door} · {d.calls} · {d.unique_users} · {d.returning_users}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Wypisz metryki z licznika SQLite; 1, gdy nie wskazano bazy (metryki wyłączone)."""
    parser = argparse.ArgumentParser(
        prog="sufler-metrics", description="Raport metryk użycia bota (Tor A)."
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("SUFLER_METRICS_DB", ""),
        help="ścieżka pliku licznika SQLite (domyślnie SUFLER_METRICS_DB)",
    )
    args = parser.parse_args(argv)
    if not args.db:
        print("Brak ścieżki licznika: ustaw SUFLER_METRICS_DB lub podaj --db.")
        return 1
    # Tryb ODCZYTU: nie materializuj nowego magazynu przy literówce w ścieżce — inaczej zła ścieżka
    # dałaby „licznik pusty" zamiast sygnału o pomyłce operatora (magazyn robi mkdir+connect).
    if not Path(args.db).expanduser().exists():
        print(f"Nie znaleziono pliku licznika: {args.db}")
        return 1
    store = SqliteMetricsStore(args.db)
    print(format_summary(store.summary()))
    return 0
