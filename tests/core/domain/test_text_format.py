"""Testy wspólnych formaterów jednostronicówek (``core/domain/text_format.py``, ADR 0051/0052).

Te trzy helpery są JEDNYM źródłem formatowania dla ``ProjectBrief.to_text`` i
``ChangeDigest.to_text`` — po to, żeby oba rendery mówiły tym samym głosem. Dotąd nie miały
własnych sond: sprawdzały je pośrednio testy obu renderów, więc wspólny kontrakt („brak → kreska",
„sekundy bez mikrosekund i strefy") nie był nigdzie zapisany wprost i mógł się rozejść przy
pierwszej zmianie jednego z wołających.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from workmate.core.domain.text_format import fmt_counts, fmt_date, fmt_seconds

_MISSING = "—"


def test_date_renders_iso_without_locale():
    """ISO, nie locale — jednostronicówka ma wyglądać tak samo niezależnie od hosta."""
    assert fmt_date(date(2026, 8, 1)) == "2026-08-01"


def test_missing_date_renders_a_dash_not_none():
    """„None" w renderze wyglądałby jak defekt; kreska mówi „nie wiemy" i jest do przeczytania."""
    assert fmt_date(None) == _MISSING


def test_seconds_drop_microseconds():
    """Znacznik do SEKUND: mikrosekundy to szum, którego czytelnik jednostronicówki nie użyje."""
    assert fmt_seconds(datetime(2026, 8, 1, 10, 30, 15, 123456)) == "2026-08-01T10:30:15"


def test_seconds_drop_the_timezone_suffix():
    """Obcięcie do 19 znaków zdejmuje też ``+00:00`` — aware i naiwny renderują się tak samo."""
    aware = datetime(2026, 8, 1, 10, 30, 15, tzinfo=UTC)
    naive = datetime(2026, 8, 1, 10, 30, 15)

    assert fmt_seconds(aware) == fmt_seconds(naive) == "2026-08-01T10:30:15"


def test_seconds_without_microseconds_are_not_truncated():
    """Granica 19 znaków: znacznik KRÓTSZY od niej wraca w całości (nie ucinamy sekund)."""
    assert fmt_seconds(datetime(2026, 8, 1, 10, 30, 15)) == "2026-08-01T10:30:15"


def test_missing_timestamp_renders_a_dash():
    assert fmt_seconds(None) == _MISSING


def test_counts_join_pairs_with_the_shared_separator():
    assert fmt_counts((("commity", 3), ("issue", 1))) == "commity: 3 · issue: 1"


def test_counts_keep_the_given_order():
    """Kolejność jest wołającego (istotność), nie alfabetyczna — formater jej nie zmienia."""
    assert fmt_counts((("b", 1), ("a", 9))) == "b: 1 · a: 9"


def test_empty_counts_render_a_dash_not_an_empty_string():
    """Pusty napis zapadłby się w renderze w pustą komórkę — nieodróżnialną od braku sekcji."""
    assert fmt_counts(()) == _MISSING


@pytest.mark.parametrize("zero_count", [(("commity", 0),), (("a", 0), ("b", 0))])
def test_zero_counts_are_rendered_not_hidden(zero_count):
    """Zero to FAKT („nic się nie zmieniło"), nie brak — kreska kłamałaby o niewiedzy."""
    assert fmt_counts(zero_count) != _MISSING
    assert "0" in fmt_counts(zero_count)
