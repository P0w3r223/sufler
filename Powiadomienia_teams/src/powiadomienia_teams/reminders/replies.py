"""Parsowanie odpowiedzi pracownika z wiadomości czatu (czysta logika)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from html import unescape
from typing import Any

from powiadomienia_teams.graph.mapping import parse_graph_datetime

_TAGS = re.compile(r"<[^>]+>")

# Pamięć rozmowy interpretera: sufit liczby zapamiętanych wiadomości pracownika oraz STAŁE okno
# liczone od PIERWSZEJ zapamiętanej wiadomości (po jego upływie pamięć się zeruje). Patrz
# ``PendingReminder.employee_memory``/``memory_started_at`` i ``app._commit``.
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
_STRIP = ".,!?…:;-–„”\"'()"


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


def newest_incoming(
    messages: list[dict[str, Any]], me_id: str, member_id: str, after_iso: str = ""
) -> dict[str, Any] | None:
    """Najnowsza wiadomość OD ``member_id``, nie od nas, nowsza niż ``after_iso``; inaczej None.

    Porównanie po sparsowanym czasie (nie leksykograficznie po napisie), żeby różnice
    w precyzji ułamka sekundy z Graph nie przestawiały kolejności.

    Nadawca musi być DOKŁADNIE tą osobą, o której grafik pytamy. Warunek „ktokolwiek poza botem"
    wyglądał równoważnie tylko dopóki czat jest 1:1: Graph wstawia do wątku także wiadomości
    systemowe i wpisy innych tożsamości (aplikacje, konto dodane do rozmowy, migracja czatu na
    grupowy). Każda z nich stawała się „odpowiedzią pracownika" — szła do modelu, przesuwała
    watermark i mogła doprowadzić do zapisu W GRAFIKU PRACOWNIKA na podstawie cudzej treści,
    a prawdziwa odpowiedź pracownika (starsza niż przesunięty watermark) nie była już czytana.
    """
    after: datetime | None = None
    if after_iso:
        try:
            after = parse_graph_datetime(after_iso)
        except ValueError:
            after = None

    # GUID-y z Graph bywają zapisane różną wielkością liter (inny endpoint, inna wersja API, ręcznie
    # wpisane `ONLY_USER_IDS`). Porównanie wrażliwe na wielkość liter przy takim rozjeździe odsiewa
    # KAŻDĄ odpowiedź pracownika — cicho i na zawsze. `casefold` po obu stronach kosztuje tyle co
    # nic.
    nasze = me_id.casefold()
    pracownik = member_id.casefold()
    incoming: list[tuple[datetime, dict[str, Any]]] = []
    for message in messages:
        sender = ((message.get("from") or {}).get("user") or {}).get("id")
        if sender is None:
            continue
        nadawca = str(sender).casefold()
        if nadawca == nasze or nadawca != pracownik:
            continue
        created = _created_at(message)
        if created is None or (after is not None and created <= after):
            continue
        incoming.append((created, message))

    if not incoming:
        return None
    return max(incoming, key=lambda pair: pair[0])[1]


def is_pure_affirmation(text: str) -> bool:
    """Czy odpowiedź to WYŁĄCZNIE potwierdzenie (bez dodatkowej treści = bez poprawki).

    »tak«/»ok«/»ok dzięki« → True. »Ok, ale nie będzie mnie w czwartek« → False — jest poprawka
    (nawet bez cyfr!), więc trafi do reinterpretacji zamiast zapisać starą propozycję. Kierunek
    bezpieczny: gdy pojawi się JAKIEKOLWIEK słowo spoza potwierdzeń/uprzejmości, wolimy
    reinterpretować (najwyżej dodatkowe wywołanie modelu), niż zapisać niezmieniony grafik mimo
    prośby o zmianę. Zastępuje wcześniejszą kruchą heurystykę »są cyfry«.
    """
    tokens = [t.strip(_STRIP) for t in text.lower().split()]
    if not any(t in _AFFIRM for t in tokens):
        return False
    allowed = _AFFIRM | _FILLER
    return all(t in allowed for t in tokens)


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
    ``advance_memory`` (obie wołają ``_window_reset``), więc prompt i utrwalony stan nie
    rozjadą się.
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
    argumentami daje identyczny wynik — idempotencja wymagana przez ``app._commit`` (ta sama
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
