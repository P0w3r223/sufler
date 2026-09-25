"""Testy normalizacji i dopasowania nazwisk (ADR 0059) — czyste funkcje, bez I/O.

Współdzielone przez "zadania członka" Jira (mapa tożsamości) i grafik Shifts (roster Graph) —
dopasowanie jest dozwolone WYŁĄCZNIE na zaufanym zbiorze kandydatów (nigdy zgadywanie konta).
"""

from __future__ import annotations

from sufler.core.domain.names import match_name, normalize_name


def test_normalize_name_folds_polish_diacritics_and_case() -> None:
    assert normalize_name("Mikołaj  ANONIMOWICZ") == "mikolaj anonimowicz"


def test_normalize_name_folds_l_with_stroke() -> None:
    assert normalize_name("Paweł") == "pawel"


def test_normalize_name_collapses_repeated_whitespace() -> None:
    assert normalize_name("  Jerzy   Zastepski  ") == "jerzy zastepski"


_CANDIDATES = [("Jerzy Zastepski", "U1"), ("Jerzy Nowak", "U2")]


def test_match_name_exact_after_diacritics_and_case() -> None:
    assert match_name(_CANDIDATES, "jerzy zastepski") == ("U1", [])


def test_match_name_partial_unambiguous_by_surname() -> None:
    assert match_name(_CANDIDATES, "zastepski") == ("U1", [])


def test_match_name_ambiguous_returns_none_and_candidates() -> None:
    value, ambiguous = match_name(_CANDIDATES, "jerzy")
    assert value is None
    assert set(ambiguous) == {"Jerzy Zastepski", "Jerzy Nowak"}


def test_match_name_no_hits_returns_none_and_empty() -> None:
    assert match_name(_CANDIDATES, "nieistnieje") == (None, [])


def test_match_name_empty_query_returns_none_and_empty() -> None:
    assert match_name(_CANDIDATES, "") == (None, [])


def test_match_name_exact_takes_precedence_over_partial() -> None:
    """Gdyby istniał dokładny i częściowy kandydat, dokładny wygrywa (nie licznik trafień)."""
    candidates = [("Jan Kowalski", "U1"), ("Jan Kowalski Junior", "U2")]
    assert match_name(candidates, "jan kowalski") == ("U1", [])
