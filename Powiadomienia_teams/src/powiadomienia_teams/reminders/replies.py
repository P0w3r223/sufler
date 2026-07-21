"""Parsowanie odpowiedzi pracownika z wiadomości czatu (czysta logika)."""
from __future__ import annotations

import re
from datetime import datetime
from html import unescape
from typing import Any

from powiadomienia_teams.graph.mapping import parse_graph_datetime

_TAGS = re.compile(r"<[^>]+>")
_AFFIRM = {
    "tak", "ok", "okej", "okey", "spoko", "potwierdzam", "zgoda",
    "pasuje", "dokładnie", "git", "zgadza", "jasne", "super",
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
    messages: list[dict[str, Any]], me_id: str, after_iso: str = ""
) -> dict[str, Any] | None:
    """Najnowsza wiadomość nie od nas i nowsza niż `after_iso`; inaczej None.

    Porównanie po sparsowanym czasie (nie leksykograficznie po napisie), żeby różnice
    w precyzji ułamka sekundy z Graph nie przestawiały kolejności.
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
        if sender is None or sender == me_id:
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
