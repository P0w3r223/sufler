"""Parsowanie odpowiedzi pracownika z wiadomości czatu (czysta logika)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import unescape
from typing import Any

from powiadomienia_teams.domain.czas import parse_graph_datetime
from powiadomienia_teams.domain.tozsamosc import ten_sam, znormalizuj

_TAGS = re.compile(r"<[^>]+>")

# Pamięć rozmowy interpretera: sufit liczby zapamiętanych wiadomości pracownika oraz STAŁE okno
# liczone od PIERWSZEJ zapamiętanej wiadomości (po jego upływie pamięć się zeruje). Patrz
# ``PendingReminder.employee_memory``/``memory_started_at`` i ``runtime.listener._commit``.
MEMORY_CAP = 10
MEMORY_WINDOW = timedelta(hours=1)
_AFFIRM = {
    "tak",
    "ok",
    "okej",
    "okey",
    "spoko",
    "potwierdzam",
    "zgoda",
    "pasuje",
    "dokładnie",
    "git",
    "zgadza",
    "jasne",
    "super",
}
# Uprzejmości dopuszczalne obok potwierdzenia (nie są poprawką grafiku).
_FILLER = {"", "no", "dzięki", "dzieki", "dziękuję", "dziekuje", "wielkie", "i", "też"}
# Interpunkcja nienosząca treści: po jej obcięciu token ma być samym słowem.
_STRIP = ".,!?…:;-–„”\"'()"
# Ta sama lista BEZ pytajnika — obowiązuje wyłącznie przy bramce nieodwracalnego zapisu
# (``is_pure_affirmation``). ``?`` jest jedynym znakiem z ``_STRIP``, który ODWRACA sens
# wypowiedzi: „tak!" to zgoda, „tak?" to pytanie o zgodę. Zawężenie stoi tutaj, a nie w samym
# ``_STRIP``, bo tamta stała opisuje interpunkcję ozdobną w ogóle, a bramka zapisu jest jedynym
# miejscem, w którym „prawie na pewno tak" jest za mało (pozycja B8 planu rozwoju).
_STRIP_BRAMKA_ZAPISU = _STRIP.replace("?", "")


def message_text(message: dict[str, Any]) -> str:
    """Wyłuskaj czysty tekst z wiadomości czatu (usuwa tagi HTML, odkodowuje encje)."""
    body = message.get("body") or {}
    content = body.get("content") or ""
    return unescape(_TAGS.sub(" ", content)).strip()


def _created_at(message: dict[str, Any]) -> datetime | None:
    raw = message.get("createdDateTime")
    if not raw:
        return None
    try:
        return parse_graph_datetime(str(raw))
    except ValueError:
        return None


def incoming_after(
    messages: list[dict[str, Any]], me_id: str, after_iso: str = "", *, nadawca: str
) -> list[dict[str, Any]]:
    """WSZYSTKIE wiadomości ADRESATA nowsze niż `after_iso`, od NAJSTARSZEJ do najnowszej.

    Wcześniej brana była wyłącznie najnowsza, a watermark przeskakiwał na nią — więc pozostałe
    ginęły bezpowrotnie, nie trafiając nawet do pamięci rozmowy. To nie była wąska krawędź:
    ``next_poll_delay`` rozciąga odstęp odpytywania geometrycznie, więc po godzinie ciszy od
    nudge'a bot zagląda na czat raz na ``poll_max_interval_s``. Pracownik piszący dwa dymki pod
    rząd („pon–pt 8–16", a chwilę później „w piątek mnie nie będzie") był interpretowany wyłącznie
    z drugiego: bot potwierdzał sam urlop i po »tak« zapisywał do Shifts jeden dzień wolny i zero
    zmian. Pisanie w kilku dymkach jest w czacie normą, więc to przypadek typowy, nie brzegowy.

    Porównanie po sparsowanym czasie (nie leksykograficznie po napisie), żeby różnice
    w precyzji ułamka sekundy z Graph nie przestawiały kolejności.

    ``nadawca`` (id pracownika, o którego grafik pytamy) jest WYMAGANY i wyłącznie nazwany.
    Wymagany, bo warunek „nie bot" wygląda równoważnie tylko dopóki czat jest 1:1: gdy wątek
    przestanie nim być, cudza treść staje się „odpowiedzią pracownika", idzie do modelu, przesuwa
    watermark i może skończyć ZAPISEM W GRAFIKU tej osoby. Nazwany, bo ``after_iso`` jest trzecim
    argumentem pozycyjnym i tak bywa wołany — parametr wstawiony przed nim po cichu przyjąłby
    watermark jako tożsamość. Brak argumentu ma być głośnym ``TypeError``.

    Tożsamości porównujemy przez ``domain.tozsamosc``, nie ``==``: Graph nie obiecuje tej samej
    wielkości liter w ``/me``, ``list_members`` i ``list_chat_messages``, a przy rozjeździe bot
    brał WŁASNY komunikat za odpowiedź pracownika.

    Wiadomość odrzucona jako cudza NIE jest tym samym co „pracownik milczy" — patrz
    ``obcy_nadawcy`` i wołający, który musi z tego zrobić ``UNKNOWN``, a nie ``NOTHING_NEW``.
    """
    after: datetime | None = None
    if after_iso:
        try:
            after = parse_graph_datetime(after_iso)
        except ValueError:
            after = None

    incoming: list[tuple[datetime, dict[str, Any]]] = []
    for message in messages:
        sender = ((message.get("from") or {}).get("user") or {}).get("id")
        if sender is None or ten_sam(str(sender), me_id):
            continue
        if not ten_sam(str(sender), nadawca):
            continue
        created = _created_at(message)
        if created is None or (after is not None and created <= after):
            continue
        incoming.append((created, message))

    return [message for _, message in sorted(incoming, key=lambda pair: pair[0])]


def obcy_nadawcy(
    messages: list[dict[str, Any]], me_id: str, after_iso: str = "", *, nadawca: str
) -> list[str]:
    """Nadawcy, którzy nie są ani botem, ani adresatem — czyli powód, by NIE orzekać o ciszy.

    Istnieje osobno od ``incoming_after`` z jednego powodu: pusta lista wiadomości znaczy
    u wołającego ``NOTHING_NEW``, a to JEDYNA przesłanka uprawniająca do wygaszenia wpisu
    (``lifecycle.should_expire``). Gdyby odsianie cudzej treści zlewało się z ciszą, każde
    błędne odrzucenie kończyłoby się nieprawdziwym „nie dostałem odpowiedzi" wysłanym
    pracownikowi i terminalnym zamknięciem tematu — czyli skutkiem nieodwracalnym.

    Czat, w którym leży wiadomość, której nie umiemy przypisać, to nie „cisza pracownika",
    tylko „nie rozumiem tego wątku". Wołający robi z tego ``UNKNOWN`` i woła operatora.

    ``after_iso`` zawęża do wiadomości NOWSZYCH niż watermark — z tego samego powodu, dla którego
    robi to ``incoming_after``. Bez tej granicy jeden stary natręt ciążyłby wpisowi bez końca:
    obieg nie mógłby orzec ciszy, a więc ani wygasić wpisu, ani sprawdzić samodzielnego
    uzupełnienia. Po przesunięciu watermarku stara cudza treść przestaje mieć znaczenie.

    Zwraca id w postaci SUROWEJ (do logu i alertu), choć porównuje znormalizowane — operator ma
    zobaczyć dokładnie to, co przyszło z Graph.
    """
    after: datetime | None = None
    if after_iso:
        try:
            after = parse_graph_datetime(after_iso)
        except ValueError:
            after = None

    obcy: list[str] = []
    widziane: set[str] = set()
    for message in messages:
        sender = ((message.get("from") or {}).get("user") or {}).get("id")
        if sender is None:
            continue
        if ten_sam(str(sender), me_id) or ten_sam(str(sender), nadawca):
            continue
        # Deduplikacja po TOŻSAMOŚCI, nie po napisie: ta sama osoba w dwóch pisowniach to nadal
        # jedna osoba, a alert wymieniający ją dwa razy każe operatorowi szukać drugiego intruza.
        # Zachowujemy pierwszą napotkaną postać surową — operator ma zobaczyć to, co przyszło
        # z Graph, nie nasz wynik normalizacji.
        created = _created_at(message)
        if created is None or (after is not None and created <= after):
            continue
        kanoniczny = znormalizuj(str(sender))
        if kanoniczny in widziane:
            continue
        widziane.add(kanoniczny)
        obcy.append(str(sender))
    return obcy


def is_pure_affirmation(text: str) -> bool:
    """Czy odpowiedź to WYŁĄCZNIE potwierdzenie (bez dodatkowej treści = bez poprawki).

    »tak«/»ok«/»ok dzięki« → True. »Ok, ale nie będzie mnie w czwartek« → False — jest poprawka
    (nawet bez cyfr!), więc trafi do reinterpretacji zamiast zapisać starą propozycję. Kierunek
    bezpieczny: gdy pojawi się JAKIEKOLWIEK słowo spoza potwierdzeń/uprzejmości, wolimy
    reinterpretować (najwyżej dodatkowe wywołanie modelu), niż zapisać niezmieniony grafik mimo
    prośby o zmianę. Zastępuje wcześniejszą kruchą heurystykę »są cyfry«.

    »tak?« NIE jest tu potwierdzeniem (0.2.13, B8): pytajnik zostaje przy tokenie, więc token
    wypada ze słownika i porcja idzie do reinterpretacji — tą samą drogą co »taak«. Reszta
    interpunkcji jest obcinana bez zmian, bo tylko pytajnik zamienia zgodę w pytanie o zgodę.
    """
    tokens = [t.strip(_STRIP_BRAMKA_ZAPISU) for t in text.lower().split()]
    if not any(t in _AFFIRM for t in tokens):
        return False
    allowed = _AFFIRM | _FILLER
    return all(t in allowed for t in tokens)


# Słowa, które po DOMKNIĘCIU tematu są grzecznością, a nie nową sprawą. Szerzej niż ``_FILLER``,
# bo tu błąd w drugą stronę jest tani: wiadomość uznana za uprzejmość zostaje po prostu
# przeczytana i odłożona — nic nie jest zapisywane ani wysyłane.
_UPRZEJMOSCI = _FILLER | {
    "dzięks",
    "dzieks",
    "thx",
    "thanks",
    "bardzo",
    "serdecznie",
    "pozdrawiam",
    "pozdro",
    "pozdr",
    "miłego",
    "milego",
    "dnia",
    "weekendu",
    "wieczoru",
    "również",
    "rowniez",
    "nawzajem",
    "wzajemnie",
}
# Potwierdzenia, które po ODMOWIE są przyjęciem jej do wiadomości („ok", „jasne"). Po wygaśnięciu
# już nie: przypomnienie mówi wprost „wystarczy odpisać »ok«, żeby zapisać propozycję".
_POTWIERDZENIA_ODMOWY = _AFFIRM | {"dobrze", "rozumiem", "przyjąłem", "przyjęłam", "okk"}


def jest_uprzejmoscia(text: str, *, z_potwierdzeniami: bool) -> bool:
    """Czy wiadomość to WYŁĄCZNIE grzeczność („dzięki", „👍", przy ``z_potwierdzeniami`` też „ok").

    Po domknięciu tematu taka wiadomość NIE może go wznowić. Zmierzone na żywym modelu
    (2026-09-25, 3/3): odmowa, a po niej „dzięki" → wznowienie → model czyta „dzięki" jako
    ``confirm`` → bot wysyła „Zapiszę grafik… Potwierdź »tak«" komuś, kto właśnie odmówił,
    a w poniedziałek dokłada „Nie doczekałem się potwierdzenia". Nic się nie zapisuje (bramka
    „tak"), ale rozmowa wygląda, jakby bot nie słuchał.

    Token bez żadnej litery ani cyfry (emotka, „!!!") też jest grzecznością. Pusta treść
    (np. sama naklejka albo załącznik) — również: nie ma w niej niczego do interpretacji.
    Cokolwiek poza tym (godziny, dzień, „jednak", „zapisz") → nie grzeczność, temat się wznawia.
    """
    dozwolone = _UPRZEJMOSCI | (_POTWIERDZENIA_ODMOWY if z_potwierdzeniami else set())
    tokens = [t.strip(_STRIP) for t in text.lower().split()]
    return all(t in dozwolone or not any(z.isalnum() for z in t) for t in tokens)


def _window_reset(started_at: str, current_at: str, window: timedelta) -> bool:
    """Czy STAŁE okno pamięci minęło: bieżąca wiadomość jest >= ``window`` po kotwicy.

    Kotwica (``started_at``) to czas PIERWSZEJ zapamiętanej wiadomości. Pusty lub nieparsowalny
    znacznik → False (bezpiecznie NIE zeruj — spójne z tolerancją ``lifecycle._anchor``).
    """
    if not started_at:
        return False
    try:
        anchor = parse_graph_datetime(started_at)
        current = parse_graph_datetime(current_at)
    except ValueError:
        return False
    return current - anchor >= window


def history_for_llm(
    memory: list[str], started_at: str, current_at: str, *, window: timedelta = MEMORY_WINDOW
) -> list[str]:
    """WCZEŚNIEJSZE wiadomości pracownika (bez bieżącej) do przekazania interpreterowi.

    Zwraca ``[]``, gdy stałe okno minęło (kontekst startuje od nowa) lub gdy brak historii.
    ``current_at`` to createdDateTime bieżącej wiadomości — decyzja o resecie jest wspólna z
    ``advance_memory`` (obie wołają ``_window_reset``), więc prompt i utrwalony stan nie rozjadą
    się.
    """
    if _window_reset(started_at, current_at, window):
        return []
    return list(memory)


def advance_memory(
    memory: list[str],
    started_at: str,
    current_at: str,
    current_text: str,
    *,
    window: timedelta = MEMORY_WINDOW,
    cap: int = MEMORY_CAP,
) -> tuple[list[str], str]:
    """Nowa (pamięć, kotwica) PO zapisaniu bieżącej wiadomości pracownika.

    Wyliczane z niezmienionych wejść (nie akumulowane na miejscu), więc ponowienie z tymi samymi
    argumentami daje identyczny wynik — idempotencja wymagana przez ``runtime.listener._commit`` (ta
    sama
    wiadomość nie może się zdublować przy ponownej obsłudze). Po upływie okna zeruje pamięć i
    zakotwicza ją na bieżącej wiadomości; sufit ``cap`` przycina od najstarszej, ale kotwicy NIE
    rusza (okno pozostaje liczone od pierwszej interakcji, nie kroczące).
    """
    if _window_reset(started_at, current_at, window):
        memory, started_at = [], ""
    if not started_at:
        started_at = current_at
    memory = (list(memory) + [current_text])[-cap:]
    return memory, started_at
