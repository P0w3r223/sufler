"""Okno wysyłki wiadomości INICJOWANYCH przez bota (godziny ciszy).

Czysta logika kalendarzowa (wstrzykiwany ``moment``, brak I/O), liczona w strefie zespołu.
Dotyczy WYŁĄCZNIE wiadomości, które bot zaczyna sam: cotygodniowej prośby, domknięcia po
wygaśnięciu okna odpowiedzi i podziękowania za samodzielne uzupełnienie grafiku. Odpowiedź na
wiadomość pracownika nie podlega oknu — to on rozpoczął rozmowę i czeka na reakcję.

Okno jest domknięte z lewej i otwarte z prawej (``start_hour <= godzina < end_hour``), więc
``8..18`` znaczy „od 8:00 do 17:59".
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo

_DNI_TYGODNIA = 7


def in_send_window(
    moment: datetime,
    tz: tzinfo,
    *,
    start_hour: int,
    end_hour: int,
    weekdays: tuple[int, ...],
) -> bool:
    """Czy w ``moment`` (dowolna strefa) wolno wysłać wiadomość inicjowaną przez bota."""
    local = moment.astimezone(tz)
    return local.weekday() in weekdays and start_hour <= local.hour < end_hour


def next_send_window(
    moment: datetime,
    tz: tzinfo,
    *,
    start_hour: int,
    end_hour: int,
    weekdays: tuple[int, ...],
) -> datetime:
    """Najbliższa chwila od ``moment`` (włącznie), w której wolno wysłać — tz-aware w ``tz``.

    Zwraca sam ``moment`` (przeliczony do ``tz``), gdy okno jest już otwarte. Szukamy dnia po
    dniu, a nie arytmetyką na minutach, bo dozwolone dni bywają nieciągłe (np. tylko wtorek
    i czwartek), a ``datetime.combine(..., tzinfo=tz)`` daje poprawny wall-clock także przy
    zmianie czasu — tak samo jak ``scheduler.weekly.next_run``.
    """
    local = moment.astimezone(tz)
    if in_send_window(local, tz, start_hour=start_hour, end_hour=end_hour, weekdays=weekdays):
        return local
    # +1 dzień na wypadek, gdy dziś jest dniem roboczym, ale po godzinach — wtedy najbliższe
    # otwarcie wypada dopiero jutro (lub później).
    for offset in range(_DNI_TYGODNIA + 1):
        dzien = (local + timedelta(days=offset)).date()
        if (local + timedelta(days=offset)).weekday() not in weekdays:
            continue
        otwarcie = datetime.combine(dzien, time(start_hour), tzinfo=tz)
        if otwarcie >= local:
            return otwarcie
    # Nieosiągalne przy niepustym `weekdays` (walidowane w `config.Settings.validate`), ale lepiej
    # zwrócić moment przyszły niż przeszły — wysyłka odłożona jest zawsze bezpieczniejsza.
    return local + timedelta(days=_DNI_TYGODNIA)
