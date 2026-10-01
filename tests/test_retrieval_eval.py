"""Test-strażnik bramki retrievalu (ADR 0023): Faza A (lexical-PL) nie regresuje vs baseline.

Metryki liczone na REALNYM korpusie ``data/notes`` + złotym zbiorze ``eval/golden_queries.yaml`` —
oba zacommitowane, więc deterministyczne. Wymaga extra ``retrieval`` (simplemma) — inaczej pomijany.
Bramka ma DWIE połowy:

1. **Względna** — Faza A nie może być gorsza od baseline'u. Łapie zepsutą lematyzację.
2. **Bezwzględna** — zacommitowane progi. Baseline to dawny ranking podłańcuchowy, więc
   asercja względna przepuszcza każde pogorszenie BM25, które nie zejdzie poniżej niego —
   a zapas jest mały (nDCG@5 0,994 vs 0,972). Progi łapią właśnie ten przypadek.

Korpus jest PRZYPIĘTY listą plików i kopiowany do ``tmp_path``. ``data/notes`` nie jest
w ``.gitignore``, a lokalne ``save_note`` pisze właśnie tam: jedna niezacommitowana notatka zbijała
nDCG@5 do 0,952 i czerwieniła bramkę — także na etapie ``test`` obrazu budowanego z takiego drzewa.
Bramka ma opisywać korpus, który recenzent widzi w diffie, a nie stan maszyny.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import retrieval_eval as ev  # z katalogu eval/ (pythonpath w pyproject)

from sufler.adapters.outbound.markdown_notes_repo import MarkdownNotesRepository
from sufler.core.application.services import NotesService

_DATA_NOTES = Path(__file__).resolve().parents[1] / "data" / "notes"
# Zacommitowany korpus, na którym zmierzono progi. Nowa notatka w repo wchodzi do bramki dopiero
# po dopisaniu tutaj — razem z ponownym pomiarem progów.
_CORPUS = (
    "biap/workmate/2025-05-20-kickoff-biap.md",
    "biap/workmate/2025-06-10-schemat-notatki.md",
    "biap/workmate/2025-06-24-testy-i-wdrozenie.md",
    "enerkom/smart-metering/2025-04-08-spotkanie-sprzedazowe-transkrypt.md",
    "enerkom/smart-metering/2025-05-06-warsztat-wymagan-i-sow.md",
    "enerkom/smart-metering/2025-06-18-przeglad-techniczny-i-ryzyka.md",
    "mpwik/scada-integration/2025-05-14-kickoff-integracji.md",
    "mpwik/scada-integration/2025-06-12-przeglad-api-scada.md",
    "mpwik/scada-integration/2025-06-26-status-i-ryzyka.md",
    "nordmarket/omnichannel/2025-03-25-kickoff-i-transkrypt.md",
    "nordmarket/omnichannel/2025-04-22-negocjacje-umowy-sla.md",
    "nordmarket/omnichannel/2025-06-20-retrospektywa-i-incydent.md",
    "translog/track-and-trace/2025-04-15-spotkanie-ofertowe-i-cennik.md",
    "translog/track-and-trace/2025-05-19-warsztat-integracyjny-i-umowa-powierzenia.md",
    "translog/track-and-trace/2025-06-24-status-eskalacja-ryzyka.md",
)
_GOLDEN_SIZE = 20

# Zmierzone na korpusie `_CORPUS` i 20 zapytaniach złotego zbioru (2026-08-05, potwierdzone
# 2026-10-01: nDCG@5 0,994, recall@3 0,983, MRR 1,0). Podnoszenie po poprawie rankingu: proszę
# bardzo. Zmiana korpusu albo złotego zbioru to NOWY POMIAR — progi ustawia się wtedy od nowa,
# także w dół. ADR-a wymaga dopiero obniżenie progu przy NIEZMIENIONYCH obu zbiorach: wtedy
# spadek mówi o rankingu, a nie o danych.
_FLOORS = {"ndcg@5": 0.99, "recall@3": 0.98, "mrr": 1.0}


@pytest.fixture
def korpus(tmp_path: Path) -> MarkdownNotesRepository:
    """Kopia przypiętego korpusu — względne ścieżki zostają, więc id ze złotego zbioru też."""
    for rel in _CORPUS:
        cel = tmp_path / rel
        cel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_DATA_NOTES / rel, cel)
    return MarkdownNotesRepository(tmp_path)


def test_metric_helpers():
    assert ev.recall_at_k(["a", "b"], {"a", "c"}, 5) == 0.5
    assert ev.mrr(["x", "a"], {"a"}) == 0.5
    # nDCG nagradza wyższą pozycję trafnej notatki.
    assert ev.ndcg_at_k(["a", "b"], {"a"}, 5) > ev.ndcg_at_k(["b", "a"], {"a"}, 5)
    assert ev.ndcg_at_k(["x"], set(), 5) == 0.0


def test_golden_set_ids_exist_in_corpus(korpus: MarkdownNotesRepository):
    corpus = {n.id for n in korpus.all()}
    for item in ev.load_golden():
        missing = item["relevant"] - corpus
        assert not missing, f"złoty zbiór wskazuje notatki spoza przypiętego korpusu: {missing}"


def test_lexical_pl_does_not_regress_vs_baseline(korpus: MarkdownNotesRepository):
    pytest.importorskip("simplemma")
    from sufler.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer

    golden = ev.load_golden()
    base = ev.evaluate(NotesService(korpus), golden)
    lex = ev.evaluate(NotesService(korpus, lemmatizer=SimplemmaLemmatizer()), golden)

    # Faza A ma być CO NAJMNIEJ tak dobra jak baseline (empirycznie: lepsza) — strażnik regresji.
    assert lex["ndcg@5"] >= base["ndcg@5"]
    assert lex["recall@3"] >= base["recall@3"]
    assert lex["mrr"] >= base["mrr"]


def test_lexical_pl_holds_committed_absolute_floors(korpus: MarkdownNotesRepository):
    """Druga połowa bramki: POZIOM, nie tylko delta.

    Asercja wyżej porównuje BM25 z dawnym rankingiem podłańcuchowym. Osłabienie BM25 (np. utrata
    nasycenia TF: ``k1`` bliskie zeru) obniża MRR z 1,0 do 0,975 i nDCG@5 z 0,994 do 0,976 —
    nadal nie gorzej niż baseline, więc tamta asercja przechodzi (sprawdzone mutacją 2026-10-01).
    Zacommitowane progi to jedyne miejsce, w którym „dobrze" ma wartość liczbową.
    """
    pytest.importorskip("simplemma")
    from sufler.adapters.outbound.simplemma_lemmatizer import SimplemmaLemmatizer

    golden = ev.load_golden()
    assert len(golden) == _GOLDEN_SIZE, (
        f"złoty zbiór ma {len(golden)} zapytań, progi zmierzono na {_GOLDEN_SIZE} — "
        "zmierz progi od nowa i zmień obie liczby"
    )
    metrics = ev.evaluate(NotesService(korpus, lemmatizer=SimplemmaLemmatizer()), golden)

    below = {name: metrics[name] for name, floor in _FLOORS.items() if metrics[name] < floor}
    assert not below, f"metryki poniżej zacommitowanych progów {_FLOORS}: {below}"
