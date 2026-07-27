"""Testy hybrydowego wyszukiwania (NotesService + lematyzacja, ADR 0023 Faza A).

Ścieżkę BM25+lematyzacja testujemy ATRAPĄ lematyzatora (deterministyczną, bez simplemma), a na
końcu jeden test integracyjny na REALNYM simplemma (pominięty, gdy brak extra ``retrieval``).
Sedno: zapytanie w innej formie fleksyjnej niż notatka trafia dopiero z lematyzacją; bez niej
zachowanie podłańcuchowe zostaje bez zmian (zgodność wsteczna).
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from tests.conftest import FakeNotesRepository, make_note
from workmate.core.application.services import NotesService

# Atrapa lematyzatora: mapuje kilka form fleksyjnych na lemat, resztę zostawia (jak OOV simplemma).
_LEMMAS = {
    "integracji": "integracja",
    "integracja": "integracja",
    "kosztów": "koszt",
    "koszty": "koszt",
    "koszt": "koszt",
}


class _StubLemmatizer:
    def lemmatize(self, text: str) -> list[str]:
        return [_LEMMAS.get(w, w) for w in re.findall(r"\w+", text.lower())]


def test_inflected_query_matches_base_form_only_with_lemmatizer():
    notes = [
        make_note(
            "mpwik/scada/2025-05-14-kickoff",
            project="scada",
            title="Kickoff integracji",
            on=date(2025, 5, 14),
            body="Ustalenia startowe.",
        )
    ]
    repo = FakeNotesRepository(notes)

    # Bez lematyzatora: „integracja" NIE jest podłańcuchem „integracji" → brak trafień (stare).
    assert NotesService(repo).search_notes("integracja") == []
    # Z lematyzatorem: oba → lemat „integracja" → trafienie.
    results = NotesService(repo, lemmatizer=_StubLemmatizer()).search_notes("integracja")
    assert [r.id for r in results] == ["mpwik/scada/2025-05-14-kickoff"]


def test_title_match_ranks_higher_than_body_via_weight():
    notes = [
        make_note(
            "x/y/2025-01-01-t",
            project="p",
            title="Raport koszt",
            on=date(2025, 1, 1),
            body="ogólne uwagi",
        ),
        make_note(
            "x/y/2025-01-02-b",
            project="p",
            title="Uwagi ogólne",
            on=date(2025, 1, 2),
            body="analiza koszt i szczegóły",
        ),
    ]
    svc = NotesService(FakeNotesRepository(notes), lemmatizer=_StubLemmatizer())
    results = svc.search_notes("koszty")  # lemat „koszt"
    assert results[0].id == "x/y/2025-01-01-t"  # trafienie w TYTULE wyżej (waga ×3)
    assert results[0].score > results[1].score


def test_without_lemmatizer_keeps_substring_behavior():
    notes = [
        make_note(
            "m/s/2025-01-01-k",
            project="p",
            title="Kickoff integracji",
            on=date(2025, 1, 1),
            body="odczyt danych ze SCADA przez API",
        ),
    ]
    svc = NotesService(FakeNotesRepository(notes))  # brak lematyzatora
    assert [r.id for r in svc.search_notes("SCADA")] == ["m/s/2025-01-01-k"]  # podłańcuch działa
    assert svc.search_notes("integracja") == []  # fleksja NIE łapana (dawne zachowanie)


def test_empty_query_returns_all_with_lemmatizer():
    notes = [
        make_note("a/b/2025-01-01-x", project="p", title="Jeden", on=date(2025, 1, 1)),
        make_note("a/b/2025-01-02-y", project="p", title="Dwa", on=date(2025, 1, 2)),
    ]
    svc = NotesService(FakeNotesRepository(notes), lemmatizer=_StubLemmatizer())
    results = svc.search_notes("")
    assert len(results) == 2
    assert [r.date for r in results] == sorted([r.date for r in results], reverse=True)


def test_score_is_float():
    notes = [make_note("a/b/2025-01-01-x", project="p", title="Raport koszt", on=date(2025, 1, 1))]
    svc = NotesService(FakeNotesRepository(notes), lemmatizer=_StubLemmatizer())
    (result,) = svc.search_notes("koszty")
    assert isinstance(result.score, float)


def test_real_simplemma_matches_polish_inflection():
    pytest.importorskip("simplemma")
    from workmate.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer

    notes = [
        make_note(
            "mpwik/scada/2025-06-12-w",
            project="scada",
            title="Wdrożenie systemu",
            on=date(2025, 6, 12),
            body="Plan wdrożenia oraz koszty integracji z SCADA.",
        ),
    ]
    svc = NotesService(FakeNotesRepository(notes), lemmatizer=SimplemmaLemmatizer())
    # Realna fleksja: zapytanie w innej formie niż w notatce, mimo to trafia.
    assert [r.id for r in svc.search_notes("integracja")] == ["mpwik/scada/2025-06-12-w"]
    assert [r.id for r in svc.search_notes("koszt")] == ["mpwik/scada/2025-06-12-w"]


# ── Faza B: fuzja warstwy dense (ADR 0039) ──────────────────────────────────────


class _StubSemanticRanker:
    """Atrapa rankera dense: zwraca skonfigurowaną kolejność id (reszta kandydatów na końcu)."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    def rank(self, query, candidates) -> list[str]:
        ids = [n.id for n in candidates]
        ranked = [i for i in self._order if i in ids]
        return ranked + [i for i in ids if i not in ranked]


def _corpus() -> FakeNotesRepository:
    return FakeNotesRepository(
        [
            make_note("x/y/2025-01-01-a", project="p", title="Raport koszt", on=date(2025, 1, 1)),
            make_note("x/y/2025-01-02-b", project="p", title="Notatka ogólna", on=date(2025, 1, 2)),
            make_note("x/y/2025-01-03-c", project="p", title="Inna sprawa", on=date(2025, 1, 3)),
        ]
    )


def test_dense_surfaces_note_that_bm25_misses():
    repo = _corpus()
    # „koszty"→lemat „koszt" trafia leksykalnie tylko notatkę A; B nie ma pokrycia słów.
    lexical = NotesService(repo, lemmatizer=_StubLemmatizer())
    assert {r.id for r in lexical.search_notes("koszty")} == {"x/y/2025-01-01-a"}
    # Dense (atrapa) stawia B wysoko → fuzja RRF wypływa B mimo braku pokrycia leksykalnego.
    hybrid = NotesService(
        repo, lemmatizer=_StubLemmatizer(), semantic=_StubSemanticRanker(["x/y/2025-01-02-b"])
    )
    ids = {r.id for r in hybrid.search_notes("koszty")}
    assert "x/y/2025-01-02-b" in ids  # dense dołożył notatkę, której BM25 nie znalazł
    assert "x/y/2025-01-01-a" in ids  # trafienie leksykalne dalej obecne


def test_dense_top_n_caps_dense_contribution():
    repo = _corpus()
    order = ["x/y/2025-01-03-c", "x/y/2025-01-02-b", "x/y/2025-01-01-a"]
    # Bez limitu: dense wnosi całą listę → C i B obecne mimo braku pokrycia leksykalnego.
    full = NotesService(repo, lemmatizer=_StubLemmatizer(), semantic=_StubSemanticRanker(order))
    assert {"x/y/2025-01-03-c", "x/y/2025-01-02-b"} <= {r.id for r in full.search_notes("koszty")}
    # dense_top_n=1: dense wnosi tylko czoło (C) → B już NIE wypływa przez dense.
    capped = NotesService(
        repo, lemmatizer=_StubLemmatizer(), semantic=_StubSemanticRanker(order), dense_top_n=1
    )
    ids = {r.id for r in capped.search_notes("koszty")}
    assert "x/y/2025-01-03-c" in ids
    assert "x/y/2025-01-02-b" not in ids


def test_dense_ignored_without_lemmatizer():
    repo = _corpus()
    # Bez lematyzatora serwis jest na ścieżce podłańcuchowej — dense NIE jest konsultowany.
    svc = NotesService(repo, semantic=_StubSemanticRanker(["x/y/2025-01-02-b"]))
    # „koszt" to podłańcuch tytułu „Raport koszt" (A); B/C bez trafienia i bez dense → nieobecne.
    assert [r.id for r in svc.search_notes("koszt")] == ["x/y/2025-01-01-a"]
