"""Strażnik kontraktu między słownictwem `state` a słownictwem raportu (`messages`).

`messages` celowo nie zna statusów, a `state` nie zna pozycji raportu. Oba spotykają się dokładnie
w jednym miejscu — `service.STATUS_DO_POZYCJI` — i to miejsce jest zwykłym słownikiem napisów.
Literówka w wartości dawała `TypeError` dopiero przy piątkowym przebiegu u klienta, czyli w chwili,
w której podsumowanie i tak już nie wyjdzie.

Reguła iteruje po WPISACH MAPY, więc nowy status dołożony do niej wpada pod nią bez dopisywania
czegokolwiek tutaj. Obiecywał ją docstring `service.py` i `messages.py`, a pliku nie było.
"""

from __future__ import annotations

from dataclasses import fields

from powiadomienia_teams import state as st
from powiadomienia_teams.messages import LiczbyTygodnia
from powiadomienia_teams.runtime.service import STATUS_DO_POZYCJI, ZLICZANE_STATUSY


def test_kazda_pozycja_mapy_jest_polem_raportu():
    """Wartość mapy to NAZWA POLA `LiczbyTygodnia` — literówka ma paść tu, nie u klienta."""
    pola = {f.name for f in fields(LiczbyTygodnia)}
    assert STATUS_DO_POZYCJI, "mapa statusów jest pusta — reguła straciła przedmiot ochrony"
    obce = sorted(
        f"{status!r} → {pozycja!r}"
        for status, pozycja in STATUS_DO_POZYCJI.items()
        if pozycja not in pola
    )
    assert obce == [], obce


def test_kazdy_klucz_mapy_jest_znanym_statusem():
    """Klucz to status z `state`. Status wymyślony tutaj nie zliczy nigdy ani jednego wpisu."""
    znane = st.TERMINALNE | {st.AWAITING_REPLY, st.AWAITING_CONFIRM}
    obce = sorted(status for status in STATUS_DO_POZYCJI if status not in znane)
    assert obce == [], obce


def test_zliczane_statusy_pokrywaja_sie_z_mapa():
    """Dwa wyrażenia tej samej wiedzy muszą zostać jednym — inaczej rozjadą się przy pierwszym
    nowym statusie, a rozjazd znaczy wpisy niepoliczone w podsumowaniu."""
    assert frozenset(STATUS_DO_POZYCJI) == ZLICZANE_STATUSY


def test_kazdy_status_terminalny_ma_swoja_pozycje():
    """Status, którego mapa nie zna, wpada do rubryki »nierozpoznane«. To zamierzone dla statusów
    z PRZYSZŁYCH wydań (N34), ale nie dla tych, które to wydanie samo zapisuje: taki wpis znikałby
    z raportu, o którym administrator ma podejmować decyzje."""
    brak = sorted(status for status in st.TERMINALNE if status not in STATUS_DO_POZYCJI)
    assert brak == [], brak
