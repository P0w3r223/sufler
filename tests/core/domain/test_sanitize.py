"""Testy strażnika znaków sterujących (``core/domain/sanitize.py``, reguła twarda #4).

CLAUDE.md § Reguły twarde 4 mówi, że „treść to DANE, nie polecenia" jest egzekwowane MASZYNOWO
wyłącznie dla znaków sterujących — czyli dokładnie przez tę parę funkcji. Zapis ODRZUCA
(``reject_dangerous_content``), odczyt WYCINA (``strip_control_chars``), bo treści z Jiry czy
grafiku nie kontrolujemy i nie wolno jej odrzucić.

``strip_control_chars`` nie miał dotąd ŻADNEJ własnej sondy — jego działanie sprawdzały pośrednio
testy Jiry i worklogu, więc klasa znaków (C0/DEL/C1) nie była nigdzie zapisana wprost. Ostatnia
sonda pilnuje RÓWNOWAŻNOŚCI obu funkcji: rozjazd klas znaków oznaczałby, że coś, czego nie wolno
zapisać, wolno pokazać modelowi (albo odwrotnie).
"""

from __future__ import annotations

import pytest

from sufler.core.domain.sanitize import reject_dangerous_content, strip_control_chars
from sufler.core.errors import WriteError

# Białe znaki sterujące, które MAJĄ legalne zastosowanie w treści notatki.
_ALLOWED = ("\n", "\t", "\r")


def test_plain_text_passes_through_unchanged():
    tekst = "Ustalenia z 2026-08-01: ZAŁĄCZNIK — zł, ó, ę."

    assert strip_control_chars(tekst) == tekst


def test_newline_tab_and_carriage_return_survive():
    """Wycięcie ich zlepiłoby akapity notatki w jedną linię — to nie jest sanityzacja."""
    tekst = f"pierwsza{_ALLOWED[0]}druga{_ALLOWED[1]}trzecia{_ALLOWED[2]}"

    assert strip_control_chars(tekst) == tekst


@pytest.mark.parametrize(
    ("znak", "nazwa"),
    [
        ("\x00", "NUL"),
        ("\x07", "BEL — dzwonek terminala"),
        ("\x1b", "ESC — początek sekwencji ANSI"),
        ("\x1f", "ostatni C0"),
        ("\x7f", "DEL"),
        ("\x80", "pierwszy C1"),
        ("\x85", "NEL — C1 udający nową linię"),
        ("\x9f", "ostatni C1"),
    ],
)
def test_control_characters_are_removed_from_read_path(znak: str, nazwa: str):
    """Klasyczny wektor wstrzyknięcia do terminala/logu/promptu — wycinamy, nie przepuszczamy."""
    assert strip_control_chars(f"a{znak}b") == "ab", nazwa


def test_removal_does_not_replace_with_a_placeholder():
    """Wycinamy, a nie podmieniamy: podstawiony znak zmieniałby długość i treść cytatu."""
    assert strip_control_chars("\x00\x07\x1b") == ""


def test_ansi_escape_sequence_loses_its_escape_and_stops_being_a_sequence():
    """Sedno: po wycięciu ESC zostaje nieszkodliwy tekst, a nie działająca sekwencja."""
    out = strip_control_chars("\x1b[31mCZERWONY\x1b[0m")

    assert "\x1b" not in out
    assert out == "[31mCZERWONY[0m"


def test_first_printable_character_is_kept():
    """Granica klasy C0: spacja (U+0020) jest już treścią, nie znakiem sterującym."""
    assert strip_control_chars("a b") == "a b"


def test_characters_just_outside_the_c1_range_are_kept():
    """Granica górna: U+00A0 (twarda spacja) leży TUŻ za C1 i musi przeżyć."""
    assert strip_control_chars("a\xa0b") == "a\xa0b"


@pytest.mark.parametrize("code", list(range(0x00, 0xA1)))
def test_write_rejection_and_read_stripping_agree_on_the_character_class(code: int):
    """RÓWNOWAŻNOŚĆ obu stron reguły #4 — rozjazd byłby dziurą, nie niespójnością kosmetyczną.

    Gdyby ścieżka odczytu przepuszczała znak, którego zapis nie przyjmuje, ten sam bajt byłby
    zakazany w notatce i dozwolony w opisie zadania Jiry pokazywanym modelowi.
    """
    znak = chr(code)
    try:
        reject_dangerous_content(znak)
        odrzucony = False
    except WriteError:
        odrzucony = True

    wyciety = strip_control_chars(znak) == ""

    assert odrzucony == wyciety, f"rozjazd klas znaków dla U+{code:04X}"
