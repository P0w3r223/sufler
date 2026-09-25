"""Testy pól dense w ``RetrievalSettings`` i wiringu rankera (ADR 0039) — bez modelu/extra.

Poprzednia wersja pierwszego testu przepisywała wartości domyślne z ``config/retrieval.py``
(nazwa modelu, ``rrf_k``, ``dense_top_n``) — przechodziła zawsze, także wtedy, gdy zmiana
którejś była błędem, bo porównywała kod z jego kopią. Dziś pilnujemy tego, co z tych liczb
WYNIKA: że warstwa dense jest wyłączona z fabryki i że strojenie fuzji zapisane w DWÓCH
miejscach (ustawienia + sygnatura
``NotesService``) nie rozjeżdża się po cichu — wiring podaje jedno drugiemu, więc rozjazd zmienia
ranking bez śladu w żadnym teście rankingu.
"""

from __future__ import annotations

import inspect

from sufler.adapters.inbound.retrieval_wiring import build_semantic_ranker
from sufler.config import RetrievalSettings
from sufler.core.application.services import NotesService


def test_dense_jest_wylaczony_z_fabryki():
    """Bramka mikro-evalu (ADR 0039): dense wchodzi na produkcję dopiero po przejściu progu."""
    assert RetrievalSettings().enable_dense is False
    assert RetrievalSettings.from_env().enable_dense is False


def test_domyslny_model_dense_jest_wielojezyczny():
    """Korpus jest po polsku — model wyłącznie angielski dawałby ciche pogorszenie trafień."""
    assert "multilingual" in RetrievalSettings().dense_model


def test_strojenie_fuzji_zgadza_sie_z_domyslnymi_notesservice():
    """Te same liczby stoją w ustawieniach i w sygnaturze serwisu; wiring przekazuje jedne drugim.

    Rozjazd nie wywróciłby niczego — po prostu ranking liczyłby się inaczej, niż mówi konfiguracja
    (albo inaczej niż w testach rankingu, które budują ``NotesService`` bez ustawień).
    """
    domyslne_serwisu = {
        name: param.default
        for name, param in inspect.signature(NotesService.__init__).parameters.items()
    }
    ustawienia = RetrievalSettings()

    assert ustawienia.rrf_k == domyslne_serwisu["rrf_k"]
    assert ustawienia.dense_top_n == domyslne_serwisu["dense_top_n"]


def test_dense_env_parsing(monkeypatch):
    monkeypatch.setenv("SUFLER_RETRIEVAL_ENABLE_DENSE", "true")
    monkeypatch.setenv("SUFLER_RETRIEVAL_DENSE_MODEL", "some/model")
    monkeypatch.setenv("SUFLER_RETRIEVAL_RRF_K", "40")
    monkeypatch.setenv("SUFLER_RETRIEVAL_DENSE_TOP_N", "5")
    monkeypatch.setenv("SUFLER_RETRIEVAL_DENSE_MIN_SIM", "0.25")
    s = RetrievalSettings.from_env()
    assert s.enable_dense is True
    assert s.dense_model == "some/model"
    assert s.rrf_k == 40
    assert s.dense_top_n == 5
    assert s.dense_min_similarity == 0.25


def test_build_semantic_ranker_disabled_returns_none():
    # Domyślnie OFF → wiring nie próbuje nawet importować fastembed (brak extra bez znaczenia).
    assert build_semantic_ranker(RetrievalSettings(enable_dense=False)) is None


def test_build_semantic_ranker_nie_importuje_modelu_przy_wylaczonej_bramce(monkeypatch):
    """Bramka ma oszczędzać PAMIĘĆ i CZAS STARTU, nie tylko zmieniać wynik wyszukiwania.

    Import ``fastembed`` ciągnie onnxruntime (~150 MB) — gdyby wiring ładował go „na wszelki
    wypadek", drzwi bez dense płaciłyby pełny koszt warstwy, której nie używają. Podstawiamy
    zaporę w miejsce importu: przy wyłączonej bramce nie wolno jej dotknąć.
    """
    import builtins

    prawdziwy_import = builtins.__import__

    def zapora(name, *args, **kwargs):
        assert "fastembed" not in name, "wiring zaimportował fastembed mimo wyłączonej bramki"
        return prawdziwy_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", zapora)

    assert build_semantic_ranker(RetrievalSettings(enable_dense=False)) is None
