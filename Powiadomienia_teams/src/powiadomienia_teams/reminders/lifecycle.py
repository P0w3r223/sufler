"""Cykl życia przypomnienia: wygaśnięcie po oknie odpowiedzi i sprzątanie wpisów terminalnych.

Czysta logika (wstrzykiwany ``now``), operuje na ``PendingReminder`` w pamięci — bez I/O, w pełni
testowalna. Wygaśnięcie mierzymy od OSTATNIEJ AKTYWNOŚCI (watermark), a nie od sztywnego nudge'a,
żeby nie zamykać okna komuś w środku rozmowy.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol, TypeVar

from powiadomienia_teams.graph.mapping import parse_graph_datetime
from powiadomienia_teams.state import APPLIED, DECLINED, EXPIRED, SELF_FILLED, PendingReminder

_TERMINAL = frozenset({APPLIED, DECLINED, EXPIRED, SELF_FILLED})


class ReadOutcome(Enum):
    """Co ustalił odczyt czatu tej osoby w BIEŻĄCYM przebiegu.

    To przesłanka orzeczenia o wygaśnięciu, nie szczegół techniczny: komunikat domknięcia mówi
    „Nie dostałem odpowiedzi", czyli twierdzi coś o ZACHOWANIU PRACOWNIKA. Wolno je wypowiedzieć
    wyłącznie wtedy, gdy naprawdę zajrzeliśmy do czatu i naprawdę nic tam nie było.
    """

    HANDLED = "handled"  # była nowa wiadomość i została obsłużona
    NOTHING_NEW = "nothing_new"  # odczyt się powiódł, nowej wiadomości nie ma
    # Nic pewnego nie ustaliliśmy: odczyt czatu padł ALBO obsługa wywróciła się w połowie. Jedno
    # i drugie znaczy to samo dla wygaszania — nie ma podstaw, by twierdzić „nie odpisał".
    UNKNOWN = "unknown"


def _anchor(pending: PendingReminder) -> datetime | None:
    """Czas odniesienia = ostatnia aktywność (``watermark``) z fallbackiem na czas nudge'a.

    Dopóki pracownik nie odpisał, ``watermark == nudged_at`` (okno liczone od powiadomienia). Po
    pierwszej odpowiedzi ``watermark`` się przesuwa, więc mierzymy CISZĘ — nie wygaszamy nikogo w
    trakcie dialogu, a odpowiedź „na styk" naturalnie przedłuża okno. Brak obu → ``None`` (nie znamy
    wieku wpisu, więc traktujemy jako niewygasalny — samo się naprawi przy kolejnym nudge'u).
    """
    for iso in (pending.watermark, pending.nudged_at):
        if iso:
            try:
                return parse_graph_datetime(iso)
            except ValueError:
                continue
    return None


def is_expired(pending: PendingReminder, now: datetime, window_hours: int) -> bool:
    """Czy minął TERMIN okna (brak aktywności przez ``window_hours``). Bez kotwicy → False.

    Czysty predykat czasu — sam w sobie NIE wystarcza do wygaszenia; patrz ``should_expire``.
    """
    anchor = _anchor(pending)
    return anchor is not None and now >= anchor + timedelta(hours=window_hours)


def should_expire(
    pending: PendingReminder, now: datetime, window_hours: int, *, read: ReadOutcome
) -> bool:
    """Czy wolno ORZEC wygaśnięcie: minął termin ORAZ mamy na to dowód z udanego odczytu.

    Sam termin nie wystarcza, bo mierzymy go znacznikami czasu wiadomości (czas serwera Graph),
    a orzekamy o czymś innym: że pracownik miał szansę odpowiedzieć i tego nie zrobił. Te dwie
    rzeczy rozjeżdżają się, gdy usługa NIE SŁUCHAŁA — po przestoju dłuższym niż okno (utrata
    sesji czeka na ręczne ``--login``) budżet ciszy jest wypalony, choć nikt nie milczał.

    Bez tego warunku pierwszy przebieg po przestoju wysyłał w JEDNYM cyklu prośbę o potwierdzenie
    i zaraz po niej „Nie dostałem odpowiedzi", a pending lądował w terminalnym ``EXPIRED`` — więc
    „tak" pracownika nie było już nigdy czytane. ``UNKNOWN`` blokuje wygaszenie z tego samego
    powodu: awaria odczytu czatu to brak dowodu, a nie dowód braku.
    """
    if read is not ReadOutcome.NOTHING_NEW:
        return False
    return is_expired(pending, now, window_hours)


# Ile OKIEN ODPOWIEDZI może przeżyć wpis nieterminalny, zanim zamkniemy go bez dowodu.
# Wielokrotność,
# a nie osobna liczba godzin: sufit ma być jawnie LUŹNIEJSZY niż zwykłe wygaśnięcie, żeby zadziałał
# wyłącznie tam, gdzie normalna droga (`should_expire`) jest trwale zablokowana.
HARD_CEILING_MULTIPLIER = 3


def past_hard_ceiling(
    pending: PendingReminder,
    now: datetime,
    window_hours: int,
    *,
    multiplier: int = HARD_CEILING_MULTIPLIER,
) -> bool:
    """Czy wpis nieterminalny przekroczył TWARDY sufit wieku — zamykamy go bez dowodu odczytu.

    ``should_expire`` słusznie odmawia wygaszenia bez udanego odczytu czatu („brak dowodu ≠ dowód
    braku"). Ale gdy odczyt pada TRWALE (czat usunięty, pracownik wyłączony z tenanta, `chat_id`
    z czasów innej instalacji), ta odmowa jest wieczna: wpis nigdy nie staje się terminalny, nigdy
    nie podlega ``prune_terminal`` i nigdy nie znika ze stanu — a `run_once` co tydzień omija tę
    osobę, bo jej wpis „istnieje". Sufit domyka ten przypadek od góry.

    Zamknięcie z sufitu jest CICHE (patrz ``app._close_bez_dowodu``): nie wolno wysłać „nie
    dostałem odpowiedzi" komuś, o kim nadal nic nie wiemy. Operator dostaje alert, pracownik nie
    dostaje nieprawdziwego zarzutu.
    """
    anchor = _anchor(pending)
    return anchor is not None and now >= anchor + timedelta(hours=window_hours * multiplier)


def ready_for_self_fill_check(pending: PendingReminder, now: datetime, min_idle_s: int) -> bool:
    """Czy wolno zajrzeć do Shifts, bo pracownik MILCZY od dłuższej chwili (nie odpisuje na czacie).

    Ciszę mierzymy tą samą kotwicą co wygaśnięcie (``_anchor``: ostatnia aktywność, potem czas
    nudge'a) — sprawdzamy grafik dopiero, gdy bot NAPRAWDĘ już czeka, a nie zaraz po nudge'u.
    ``min_idle_s < 0`` wyłącza funkcję; ``0`` sprawdza przy każdym cichym cyklu. Bez kotwicy →
    False.
    """
    if min_idle_s < 0:
        return False
    anchor = _anchor(pending)
    return anchor is not None and now >= anchor + timedelta(seconds=min_idle_s)


class _MaZakonczenie(Protocol):
    """Cokolwiek, co ma koniec w czasie — ``Shift`` i ``TimeOff`` spełniają to strukturalnie."""

    @property
    def end(self) -> datetime: ...


_T = TypeVar("_T", bound=_MaZakonczenie)


def still_writable(items: Iterable[_T], now: datetime) -> tuple[_T, ...]:
    """Zostaw wpisy, które jeszcze się nie skończyły — reszty nie ma po co zapisywać.

    Okno odpowiedzi jest obietnicą wobec pracownika, ale użyteczność zapisu ma własny termin.
    Kryterium to KONIEC wpisu, nie początek: zmiana trwająca w tej chwili jest nadal prawdziwa
    i warto mieć ją w grafiku, natomiast zmiana zakończona wczoraj zaśmieca grafik dniem, który
    minął — a to menedżer czyta jako stan faktyczny.

    Filtr stoi TU, a nie w regule zamykającej całą rozmowę, bo tamta jest zbyt tępa: zamykała
    temat po czasie ODCZYTU, więc „tak" wysłane o 23:58 przepadało, gdy najbliższy przebieg
    wypadał po północy — mimo że nie minął jeszcze ani jeden dzień. Ratujemy część tygodnia,
    która wciąż jest przed nami, zamiast odrzucać wszystko albo zapisywać przeszłość.
    """
    return tuple(item for item in items if item.end > now)


def prune_terminal(
    state: dict[str, PendingReminder], now: datetime, retain_hours: int
) -> dict[str, PendingReminder]:
    """Usuń wpisy TERMINALNE (applied/declined/expired/self_filled) starsze niż ``retain_hours``.

    Wpisy otwarte oraz świeże terminalne zostają. ``retain_hours`` powinno być ≥ oknu odpowiedzi,
    żeby nie ruszać idempotencji zapisu w aktywnym oknie. Wpis terminalny bez kotwicy zostawiamy
    (nie znamy jego wieku). Zwraca NOWY słownik (niemutujący wejścia).

    Wpis z NIEWYSŁANĄ wiadomością odłożoną na okno wysyłki zostaje niezależnie od wieku: godziny
    ciszy przesuwają wysyłkę, nie kasują jej, a domknięcie odłożone w piątek wieczorem czeka do
    poniedziałku rana — czyli dłużej niż typowe ``retain_hours``.
    """
    kept: dict[str, PendingReminder] = {}
    for key, pending in state.items():
        if pending.status in _TERMINAL and not pending.odlozona_wiadomosc:
            anchor = _anchor(pending)
            if anchor is not None and now >= anchor + timedelta(hours=retain_hours):
                continue  # dość stary wpis terminalny — wyrzuć
        kept[key] = pending
    return kept
