"""Normalizacja i dopasowanie nazwisk — wspólne dla „zadań członka" Jira i grafiku Shifts.

Czyste funkcje, bez I/O. Dopasowanie nazwiska jest tu DOZWOLONE tylko tam, gdzie zbiór kandydatów
jest z góry ZAUFANY (mapa tożsamości albo lista członków zespołu z Graph) — nigdy jako zgadywanie
konta w obcym systemie. Normalizacja składa polskie znaki do ASCII i ujednolica wielkość liter,
żeby „jerzy zastepski", „Jerzy Zastepski" i „ZASTEPSKI  Jerzy" trafiały w tę samą osobę.
"""

from __future__ import annotations

import unicodedata
from typing import TypeVar

_T = TypeVar("_T")


def normalize_name(value: str) -> str:
    """Znormalizuj nazwisko do porównań: bez diakrytyków, casefold, pojedyncze spacje.

    ``ł``/``Ł`` nie mają dekompozycji NFKD, więc podmieniamy je jawnie; resztę diakrytyków usuwa
    odrzucenie znaków łączących po rozkładzie NFKD.
    """
    value = value.replace("ł", "l").replace("Ł", "L")
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.casefold().split())


def match_name(
    candidates: list[tuple[str, _T]], query: str
) -> tuple[_T | None, list[str]]:
    """Dopasuj ``query`` do listy ``(display_name, wartość)``; zwróć ``(wartość | None, niejasne)``.

    Najpierw dokładne dopasowanie znormalizowanego nazwiska; przy braku — dopasowanie po wszystkich
    tokenach zapytania (np. „zastepski" trafia „jerzy zastepski"), ale tylko gdy jest DOKŁADNIE jeden taki
    kandydat. Zero trafień → ``(None, [])``; wiele → ``(None, [oryginalne display_name])``, żeby
    wołający mógł poprosić o doprecyzowanie. Dopasowanie działa na zaufanym zbiorze kandydatów.
    """
    norm_query = normalize_name(query)
    if not norm_query:
        return None, []
    query_tokens = set(norm_query.split())
    exact: list[tuple[str, _T]] = []
    partial: list[tuple[str, _T]] = []
    for display, value in candidates:
        norm = normalize_name(display)
        if norm == norm_query:
            exact.append((display, value))
        elif query_tokens <= set(norm.split()):
            partial.append((display, value))
    # Dokładne trafienie ma pierwszeństwo; częściowe liczą się tylko przy braku dokładnego.
    chosen = exact or partial
    if len(chosen) == 1:
        return chosen[0][1], []
    return None, [display for display, _ in chosen]
