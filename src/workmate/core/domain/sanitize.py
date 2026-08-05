"""Prosty strażnik wstrzyknięć przed zapisem (Bramka 2, obrona w głąb).

Notatki to nasza „baza": zapis idzie WYŁĄCZNIE przez zwalidowany schemat
(``NoteMetadata``), slug tytułu (``[a-z0-9-]``) i rejestr projektów, więc
wstrzyknięcie strukturalne (path traversal, YAML, frontmatter) jest już
neutralizowane u źródła. Ten strażnik dokłada warstwę: odrzuca bajty zerowe i
znaki sterujące — nie mają legalnego zastosowania w treści notatki, a są
klasycznym wektorem wstrzyknięć (terminal, log, nazwa pliku). Czysta funkcja
domenowa, bez I/O — wołana przez ``NotesWriteService`` przed zapisem.
"""

from __future__ import annotations

from workmate.core.errors import WriteError

# Dozwolone „białe" znaki sterujące w treści: nowa linia, tab, powrót karetki.
_ALLOWED_CONTROL = frozenset("\n\t\r")


def reject_dangerous_content(*fields: str) -> None:
    """Podnieś ``WriteError``, gdy które kolwiek pole ma NUL lub znak sterujący.

    ``fields`` to teksty trafiające do notatki (tytuł, treść, elementy list) —
    sprawdzamy je razem, bo każdy z nich ląduje w pliku bazy.
    """
    for field in fields:
        for ch in field:
            if ch in _ALLOWED_CONTROL:
                continue
            code = ord(ch)
            # NUL i C0 (<0x20), DEL (0x7F) oraz C1 (0x80–0x9F): brak legalnego
            # zastosowania w notatce, klasyczny wektor wstrzyknięć (terminal/log/ścieżka).
            if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
                raise WriteError(
                    f"treść zawiera niedozwolony znak sterujący (U+{code:04X}) — zapis odrzucony"
                )


def strip_control_chars(text: str) -> str:
    """Usuń (nie odrzuć) znaki sterujące z tekstu z zewnętrznego API przed pokazaniem go modelowi.

    Lustro klas znaków z ``reject_dangerous_content``, ale dla ścieżki ODCZYTU: treść Jiry
    (opis/komentarz) czy grafiku to DANE, których nie kontrolujemy — nie możemy jej odrzucić jak
    zapisu, więc po prostu wycinamy NUL/C0/DEL/C1 (zostają nowa linia, tab, powrót karetki), żeby
    do promptu/terminala/logu nie trafił klasyczny wektor wstrzyknięć.
    """
    return "".join(
        ch
        for ch in text
        if ch in _ALLOWED_CONTROL
        or not (ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F)
    )
