"""Cykl życia przypomnienia: wygaśnięcie po oknie odpowiedzi i sprzątanie wpisów terminalnych.

Czysta logika (wstrzykiwany ``now``), operuje na ``PendingReminder`` w pamięci — bez I/O, w pełni
testowalna. Wygaśnięcie mierzymy od OSTATNIEJ AKTYWNOŚCI (watermark), a nie od sztywnego nudge'a,
żeby nie zamykać okna komuś w środku rozmowy.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from powiadomienia_teams.graph.mapping import parse_graph_datetime
from powiadomienia_teams.state import APPLIED, DECLINED, EXPIRED, PendingReminder

_TERMINAL = frozenset({APPLIED, DECLINED, EXPIRED})


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
    """Czy minęło okno odpowiedzi (brak aktywności przez ``window_hours``). Bez kotwicy → False."""
    anchor = _anchor(pending)
    return anchor is not None and now >= anchor + timedelta(hours=window_hours)


def prune_terminal(
    state: dict[str, PendingReminder], now: datetime, retain_hours: int
) -> dict[str, PendingReminder]:
    """Usuń wpisy TERMINALNE (applied/declined/expired) starsze niż ``retain_hours`` od kotwicy.

    Wpisy otwarte oraz świeże terminalne zostają. ``retain_hours`` powinno być ≥ oknu odpowiedzi,
    żeby nie ruszać idempotencji zapisu w aktywnym oknie. Wpis terminalny bez kotwicy zostawiamy
    (nie znamy jego wieku). Zwraca NOWY słownik (niemutujący wejścia).
    """
    kept: dict[str, PendingReminder] = {}
    for key, pending in state.items():
        if pending.status in _TERMINAL:
            anchor = _anchor(pending)
            if anchor is not None and now >= anchor + timedelta(hours=retain_hours):
                continue  # dość stary wpis terminalny — wyrzuć
        kept[key] = pending
    return kept
