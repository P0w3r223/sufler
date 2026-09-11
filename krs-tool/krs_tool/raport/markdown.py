"""Raport w markdownie — drugi kanał wyjścia i jedyne miejsce, które zna `marktext`.

Kanał ma **własny** neutralizator, bo psuje się inaczej niż terminal: `richtext.safe` zdejmie
znaczniki `rich` i przepuści `](http://…)`, a to w markdownie jest odnośnikiem prowadzącym na
obcy adres, w dokumencie, który ktoś komuś przesyła dalej jako raport o spółce. Dlatego skan
reguły granic 6 sprawdza **pary** kanał-neutralizator: ten moduł ma wołać `safe_md` i nie ma
prawa zawołać `safe`, a `render.py` odwrotnie.

Każda komórka, także pochodząca z `texts.py`, przechodzi przez neutralizator — tak samo jak
w kanale terminalowym i z tego samego powodu: gdy jutro do komórki trafi nazwa spółki
z odpisu, neutralizator ma już tu stać, a nie zostać dopisany w tej samej zmianie, w której
pojawia się wrogie wejście.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..marktext import safe_md
from ..texts import Block
from .texts import Raport

ROZDZIELACZ = "---"


def raport_markdown(raport: Raport) -> str:
    """Cały raport jako jeden dokument markdown, zakończony znakiem nowej linii."""
    return "\n\n".join([f"# {safe_md(raport.tytul)}", *(_sekcja(s) for s in raport.sekcje)]) + "\n"


def _sekcja(blok: Block) -> str:
    linie = [f"## {safe_md(blok.title)}"]
    if blok.headers:
        linie.append(_wiersz(blok.headers))
        linie.append(_wiersz(tuple(ROZDZIELACZ for _ in blok.headers)))
        linie.extend(_wiersz(wiersz) for wiersz in blok.rows)
    # Punkty listy, nie kolejne linie cytatu: markdown skleja sąsiadujące `>` w jeden akapit,
    # więc sekcja „czego to narzędzie nie twierdzi" zlałaby się w ścianę tekstu — czyli w coś,
    # czego czytelnik nie przeczyta, a to akurat jest sekcja napisana po to, żeby ją przeczytał.
    linie.extend(f"- {safe_md(uwaga)}" for uwaga in blok.notes)
    return "\n".join(linie)


def _wiersz(komorki: Sequence[str]) -> str:
    """Wiersz tabeli. Pionowe kreski wokół komórek są NASZE — te z komórek są zgaszone."""
    return "| " + " | ".join(safe_md(komorka) for komorka in komorki) + " |"
