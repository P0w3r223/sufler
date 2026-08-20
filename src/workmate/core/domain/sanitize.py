"""Strażnicy treści notatki przed zapisem (Bramka 2, obrona w głąb).

Dwie czyste reguły domenowe, obie wołane PRZED dotknięciem dysku i obie zawodzące w stronę
odmowy: ``reject_dangerous_content`` (znaki, które nie mają prawa trafić do pliku) oraz
``odrzuc_wlasny_frontmatter`` (treść, która niesie własny nagłówek notatki). Druga stoi tu,
a nie przy jednym przypadku użycia, bo dotyczy KAŻDEJ drogi treści do pliku — tworzenia notatki
tak samo jak jej edycji.

Notatki to nasza „baza": zapis idzie WYŁĄCZNIE przez zwalidowany schemat
(``NoteMetadata``), slug tytułu (``[a-z0-9-]``) i rejestr projektów, więc
wstrzyknięcie strukturalne (path traversal, YAML, frontmatter) jest już
neutralizowane u źródła. Ten strażnik dokłada warstwę: odrzuca bajty zerowe i
znaki sterujące — nie mają legalnego zastosowania w treści notatki, a są
klasycznym wektorem wstrzyknięć (terminal, log, nazwa pliku). Czyste funkcje
domenowe, bez I/O.
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


def odrzuc_wlasny_frontmatter(new_body: str) -> None:
    """Podnieś ``WriteError``, gdy treść niesie WŁASNY nagłówek notatki — zamiast dołożyć go drugi
    raz.

    ``edit_note`` przyjmuje SAMĄ TREŚĆ; metadane zostają nietknięte i pisarz dokłada je sam
    (``render_note``). Model, który notatkę najpierw ODCZYTAŁ — przez `File(read)` albo przez
    `cat` po włączeniu powłoki — dostaje plik RAZEM z nagłówkiem, więc oddanie całości z powrotem
    jest zachowaniem naturalnym, nie egzotycznym.

    Bez tej bramki kończyło się to notatką z frontmatterem DWA RAZY: raz jako tekst na początku
    treści, raz dołożonym przez pisarza. Odtworzone na produkcji przy pierwszej realnej mutacji
    (2026-08-20): sędzia orzekł ``allow``, audyt zapisał ``status: ok``, człowiek dostał
    „Zrobione ✅" — a plik był uszkodzony, cicho i trwale. Kolejna edycja dokładałaby trzeci.

    ODMOWA, nie ciche obcięcie. Obcinanie musiałoby zgadywać, czy blok na początku jest
    nagłówkiem, czy treścią (poziomą linią, blokiem kodu, cytatem), a pomyłka kasowałaby
    użytkownikowi tekst bez śladu. Zdanie z komunikatu model czyta w tej samej turze i poprawia
    wywołanie; obcięcia nie zauważyłby nikt.

    **Rozpoznajemy NASZ nagłówek, nie „coś między kreskami" — i to jest cała ostrożność tej
    bramki.** Pierwsza redakcja pytała tylko, czy dalej stoi druga linia ``---``; tak szeroki
    warunek odmawiał treści całkiem poprawnej (dwie poziome kreski wokół akapitu), a komunikat
    kazał wtedy usunąć pola YAML, których w treści nie ma — polecenie niewykonalne inaczej niż
    przez skasowanie tekstu człowieka. Pytamy więc o POLE ze schematu ``NoteMetadata``: korupcja,
    o którą chodzi, to zawsze oddany z powrotem plik pisarza, a ten nosi ``title``/``project``.
    """
    # BOM nie jest białym znakiem dla ``lstrip()`` bez argumentu, a plik zapisany pod Windows
    # zaczyna się właśnie od niego — treść przechodziła wtedy bramkę i dawała dokładnie tę
    # korupcję, przed którą ta bramka stoi.
    linie = new_body.lstrip("\ufeff \t\r\n").splitlines()
    if not linie or linie[0].strip() != "---":
        return
    domkniecie = next(
        (nr for nr, linia in enumerate(linie[1:], start=1) if linia.strip() == "---"), None
    )
    # Sama pierwsza linia to w markdownie pozioma kreska i nią ma zostać.
    if domkniecie is None:
        return
    if not _niesie_pole_naglowka(linie[1:domkniecie]):
        return
    raise WriteError(
        "Treść zaczyna się od frontmatteru (`---`), a zapis notatki przyjmuje SAMĄ TREŚĆ — "
        "nagłówek (tytuł, projekt, data, uczestnicy) dokłada pisarz i nie bierze go z treści. "
        "Przekazanie całego pliku dałoby notatkę z nagłówkiem dwa razy. Ponów z treścią spod "
        "nagłówka, bez linii `---` i bez pól YAML."
    )


def _niesie_pole_naglowka(blok: list[str]) -> bool:
    """Czy blok między znacznikami niesie POLE nagłówka notatki — czy prozę między kreskami.

    Zbiór pól bierzemy z ``NoteMetadata``, a nie z listy przepisanej tutaj: nagłówek składa
    ``render_note`` z tego samego schematu, więc dopisanie pola w modelu ma domykać bramkę samo.
    Lista przepisana obok rozjechałaby się przy pierwszej takiej zmianie — i to po cichu, bo
    rozjazd widać dopiero na uszkodzonej notatce.
    """
    from workmate.core.domain.models import NoteMetadata

    pola = set(NoteMetadata.model_fields)
    return any(
        separator and klucz.strip() in pola
        for klucz, separator, _ in (linia.partition(":") for linia in blok)
    )
