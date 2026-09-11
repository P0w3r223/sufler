"""Neutralizator kanału markdown — moduł czysty. Druga połowa pary z `richtext.safe`.

Kanał terminalowy i kanał markdown psują się **inaczej**, więc mają osobne neutralizatory,
a skan reguły granic 6 sprawdza pary kanał-neutralizator, nie samą obecność któregoś.
Przepuszczenie napisu przez neutralizator niewłaściwego kanału jest naruszeniem: `richtext.safe`
zdejmie znaczniki `rich` i zostawi `](http://…)`, a to w markdownie jest odnośnikiem.

Co konkretnie ta funkcja gasi w nazwie spółki wpisanej do rejestru przez człowieka:

- `](http://…)` — domknięcie nawiasu robi z fragmentu raportu klikalny odnośnik na obcy adres;
- `|` — rozbija wiersz tabeli, więc jedna nazwa przesuwa całą kolumnę i raport zaczyna kłamać
  cicho, przypisując cudzą podstawę prawną cudzemu podmiotowi;
- `<a href=…>` i `<http://…>` — markdown przepuszcza HTML i autolinki;
- nowa linia i tabulator — w komórce tabeli kończą wiersz w środku zdania;
- `#`, `*`, `_`, `` ` `` — zamieniają fragment nazwy w nagłówek, wyróżnienie albo kod.

Znaki sterujące zdejmuje `safetext.strip_control`, wspólny dla obu kanałów.
"""

from __future__ import annotations

from .safetext import strip_control
from .secrets import mask_tokens

# Znaki o znaczeniu składniowym w markdownie. Odwrotny ukośnik jest pierwszy i musi taki
# zostać: gdyby szedł później, poprzedzałby ukośniki wstawione przez wcześniejsze podmiany
# i zamiast gasić składnię, produkowałby własną.
#
# Czego tu NIE MA i dlaczego: kropki, myślnika i plusa. Znaczą coś wyłącznie na POCZĄTKU
# wiersza (lista, nagłówek setext), a napis z zewnątrz trafia wyłącznie do komórki tabeli,
# czyli nigdy na początek wiersza. Escape'owanie ich zamieniłoby każdą datę i każdą nazwę
# w gąszcz ukośników, co czyta się gorzej i uczy czytelnika ignorować ukośniki.
ZNAKI_SKLADNI = ("\\", "`", "*", "_", "[", "]", "(", ")", "#", "!", "~", "|", "<", ">")

# Białe znaki, które w komórce tabeli kończą wiersz. Zastępujemy spacją, nie usuwamy —
# sklejenie dwóch słów zmienia treść, a to jest raport o konkretnej spółce.
BIALE_ZNAKI = ("\n", "\r", "\t")

# Znaki sterujące kierunkiem pisma. `strip_control` ich nie zdejmuje, bo leżą powyżej U+0020,
# a potrafią sprawić, że nazwa spółki **wyświetla się inaczej, niż jest zapisana** — w
# dokumencie, który ktoś przesyła dalej jako raport o tej spółce. Usuwamy, a nie gasimy
# ukośnikiem: to nie jest składnia markdownu, tylko sterowanie wyświetlaniem.
#
# UWAGA: ten sam rodzaj znaku przechodzi dziś do kanału terminalowego przez wspólny
# `safetext.strip_control`, skopiowany z `ceidg-tool`. Poprawka tam jest decyzją właściciela,
# bo dotyka KOPII (ADR-0023 decyzja 1: naprawa w jednej kopii, a nie w obu, zamienia
# „kopiuj" na „wydziel"). Tutaj naprawiamy własny moduł i zgłaszamy tamto.
ZNAKI_KIERUNKU = (
    "‪",
    "‫",
    "‬",
    "‭",
    "‮",
    "⁦",
    "⁧",
    "⁨",
    "⁩",
    "‎",
    "‏",
)


def safe_md(value: str) -> str:
    """Napis z zewnątrz jako zwykły tekst markdown: bez składni, bez łamania wiersza."""
    tekst = strip_control(mask_tokens(value))
    for znak in ZNAKI_KIERUNKU:
        tekst = tekst.replace(znak, "")
    for znak in BIALE_ZNAKI:
        tekst = tekst.replace(znak, " ")
    for znak in ZNAKI_SKLADNI:
        tekst = tekst.replace(znak, f"\\{znak}")
    return tekst


def safe_md_or_empty(value: str | None) -> str:
    """`safe_md` dla pól opcjonalnych.

    Osobna funkcja, a nie wyrażenie warunkowe w miejscu użycia: skan reguły 6 rozpoznaje
    wywołanie neutralizatora, a nie `safe_md(x) if x else ""`.
    """
    return safe_md(value) if value else ""
