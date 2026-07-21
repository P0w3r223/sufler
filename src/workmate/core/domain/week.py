"""Matematyka tygodnia dla przebiegów cyklicznych (ADR 0035) — termin i okno raportowania.

Czyste funkcje ze wstrzykiwanym ``now``: żadnego zegara, żadnego I/O. Odporne na zmianę czasu —
wall-clock składamy przez ``datetime.combine(..., tzinfo=tz)``, więc „piątek 16:00" oznacza
lokalne 16:00 po obu stronach przejścia DST, a nie stały offset od UTC.

Strefę bierzemy jako ``ZoneInfo``, nie jako liczbę minut. ADR 0034 przyjął stały offset jako
kompromis pilotażu, ale tutaj granice tygodnia decydują, do KTÓREGO dnia trafi cudza godzina
pracy w obcym systemie — błąd DST przesunąłby wpis na sąsiedni dzień.

PRZENIESIONE z ``Powiadomienia_teams/scheduler/weekly.py`` (osobny projekt uv, więc import nie
jest darmowy — patrz ADR 0035 § port-vs-share). Utrzymywać zgodnie z oryginałem; różnica jest
JEDNA i celowa: tamten moduł patrzy W PRZÓD (grafik na przyszły tydzień), a ``reported_week``
tutaj patrzy WSTECZ (godziny już przepracowane).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo

# 0=poniedziałek … 6=niedziela (jak ``datetime.weekday()``). Piątek jest domyślny, bo raport
# zamyka tydzień pracy — w niedzielę nikt go już nie przeczyta na czas.
FRIDAY = 4


def week_monday(now: datetime, tz: tzinfo) -> datetime:
    """Lokalna północ poniedziałku bieżącego tygodnia (tz-aware), dla dowolnej strefy ``now``."""
    local = now.astimezone(tz)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def reported_week(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Okno raportowania ``[start, end)`` — tydzień, który WŁAŚNIE się kończy (bieżący pon.–ndz.).

    Zwraca lokalne północe tz-aware. Przedział jest półotwarty: początek wchodzi, koniec nie —
    dzięki temu sąsiednie tygodnie nie nachodzą na siebie i żadna godzina nie zostanie policzona
    dwa razy ani zgubiona na styku.

    Uruchomienie w piątek 16:00 raportuje tydzień OD poniedziałku TEGO tygodnia. Weekend po
    terminie wpada więc do raportu dopiero za tydzień — świadomy kompromis: alternatywą byłoby
    raportowanie tygodnia poprzedniego, czyli wysyłanie ludziom danych sprzed 12 dni.
    """
    start = week_monday(now, tz)
    return start, start + timedelta(days=7)


def next_run(
    now: datetime,
    *,
    tz: ZoneInfo,
    weekday: int = FRIDAY,
    hour: int = 16,
    minute: int = 0,
) -> datetime:
    """Najbliższy ``weekday`` o ``hour``:``minute`` w strefie ``tz``, ŚCIŚLE po ``now``.

    ``now`` może być w dowolnej strefie — przeliczamy je do ``tz``. Ścisła nierówność chroni przed
    natychmiastowym powtórzeniem: przebieg zakończony dokładnie o 16:00 nie wyzwoli się od razu
    drugi raz.
    """
    local_now = now.astimezone(tz)
    days_ahead = (weekday - local_now.weekday()) % 7
    target_date = local_now.date() + timedelta(days=days_ahead)
    candidate = datetime.combine(target_date, time(hour, minute), tzinfo=tz)
    if candidate <= local_now:
        candidate = datetime.combine(target_date + timedelta(days=7), time(hour, minute), tzinfo=tz)
    return candidate


def previous_run(
    now: datetime,
    *,
    tz: ZoneInfo,
    weekday: int = FRIDAY,
    hour: int = 16,
    minute: int = 0,
) -> datetime:
    """Ostatnie wystąpienie ``weekday`` o ``hour``:``minute`` w ``now`` lub PRZED nim.

    Odwrotność ``next_run`` — służy do nadrobienia przebiegu pominiętego, gdy proces nie żył
    w terminie (wyłączony serwer, restart). Wołający ogranicza nadrabianie własnym sufitem dni,
    żeby po dłuższej przerwie nie rozesłać nieaktualnych godzin.
    """
    local_now = now.astimezone(tz)
    days_behind = (local_now.weekday() - weekday) % 7
    candidate = datetime.combine(
        local_now.date() - timedelta(days=days_behind), time(hour, minute), tzinfo=tz
    )
    if candidate > local_now:
        candidate = datetime.combine(
            candidate.date() - timedelta(days=7), time(hour, minute), tzinfo=tz
        )
    return candidate


def week_label(start: datetime) -> str:
    """Etykieta tygodnia ISO (``2026-W29``) — do nazw plików i treści wiadomości.

    Ta sama etykieta w nazwie arkusza i w DM pozwala człowiekowi rozpoznać, że dostał PONOWNIE
    ten sam tydzień — jedyna obrona przed podwójnym importem, bo importu dokonuje on sam
    i nasza idempotencja go nie obejmuje (ADR 0035 § Consequences).
    """
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
