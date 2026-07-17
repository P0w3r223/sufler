"""Testy wyznaczania miejsca notatki: slug tytułu i identyfikator (czyste, bez I/O).

Slugify jest jednocześnie granicą bezpieczeństwa (Bramka 2, ADR 0006): tytuł
pochodzi od wołającego i trafia do ścieżki pliku, więc wynik jest zawężony do
białej listy ``[a-z0-9-]``. Testy pilnują tej reguły oraz transliteracji polskich
znaków, przypadków pustych i przycięcia długości.
"""

from __future__ import annotations

from datetime import date

import pytest

from workmate.core.domain.paths import note_id, slugify

# Musi być spójne z _SLUG_MAX_LENGTH w paths.py (górny limit długości sluga).
_SLUG_MAX_LENGTH = 80


def test_slugify_lowercases_and_hyphenates_words():
    assert slugify("Przeglad integracji SCADA") == "przeglad-integracji-scada"


def test_slugify_transliterates_polish_diacritics_to_ascii():
    # Zażółć gęślą jaźń + Ł, którego NFKD nie rozkłada (osobna translacja).
    assert slugify("Zażółć gęślą jaźń Ł") == "zazolc-gesla-jazn-l"


def test_slugify_collapses_and_strips_separators():
    assert slugify("  Hello -- World!!  ") == "hello-world"


@pytest.mark.parametrize(
    "traversal",
    [
        "../../etc/passwd",
        "/absolute/path",
        "a/b\\c",
        "..",
    ],
)
def test_slugify_neutralizes_path_traversal(traversal: str):
    slug = slugify(traversal)

    # Ochrona przed path traversal: brak '/', '\\', '.' i '..' w wyniku.
    assert "/" not in slug
    assert "\\" not in slug
    assert ".." not in slug
    assert set(slug) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")


@pytest.mark.parametrize(
    "text",
    ["", "   ", "...", "日本語", "!!!", "@#$%"],
)
def test_slugify_returns_empty_for_untranslatable_titles(text: str):
    # Pusty slug to sygnał dla wołającego, że tytuł nie ma znaków ASCII [a-z0-9].
    assert slugify(text) == ""


def test_slugify_truncates_to_max_length():
    slug = slugify("a" * 200)

    assert len(slug) == _SLUG_MAX_LENGTH


def test_slugify_strips_trailing_hyphen_left_by_truncation():
    # 79 znaków, potem separator: przycięcie trafia w '-' na pozycji 80, które
    # musi zostać usunięte (nazwa pliku nie kończy się myślnikiem).
    slug = slugify("a" * 79 + " " + "b" * 20)

    assert not slug.endswith("-")
    assert len(slug) == 79


def test_note_id_has_company_project_date_slug_shape():
    result = note_id("mpwik", "scada-integration", date(2025, 6, 12), "Przeglad API")

    assert result == "mpwik/scada-integration/2025-06-12-przeglad-api"


def test_note_id_raises_when_title_has_no_usable_slug():
    with pytest.raises(ValueError):
        note_id("mpwik", "scada-integration", date(2025, 6, 12), "日本語")
