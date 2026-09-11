"""Arytmetyka terminów ustawowych. Jedyny producent `TerminUstawowy` (reguła granic 9).

Trzy rzeczy, które ten moduł robi inaczej, niż podpowiada odruch:

**Nie zna dnia dzisiejszego.** Reguła granic 4 zabrania warstwie sygnałów zegara, więc termin
porównuje się tu wyłącznie z datą pochodzącą z odpisu — `stanZDnia`. Zdanie, które narzędzie
umie wypowiedzieć, brzmi „na dzień stanu rejestru D", i to jest zdanie uczciwe.

**Nie zna liczby dwanaście.** Reguła granic 10 zabrania w tym pakiecie literałów innych niż
0 i 1, także tutaj. Miesięcy w roku jest dwanaście dlatego, że tyle ich ma kalendarz — więc
bierzemy tę liczbę z kalendarza, zamiast wpisywać ją drugi raz.

**Nie zna 31 grudnia.** Dzień bilansowy przychodzi z odpisu jako `DzienBilansowy`, typ, który
powstaje wyłącznie w `odpis/czytanie.py`. Rok obrotowy nie musi być kalendarzowy, a po zmianie
bywa dłuższy niż dwanaście miesięcy.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import NewType

from ..odpis.model import DzienBilansowy
from .katalog import Termin

# Reguła granic 9, część druga: termin ustawowy powstaje wyłącznie tutaj. Poza tym modułem typ
# wolno przenosić i czytać, nie wolno wytwarzać — mypy strict pilnuje, a skan w
# `tests/test_granice.py` pokazuje, że potrafi zapłonąć.
TerminUstawowy = NewType("TerminUstawowy", date)

MIESIECY_W_ROKU = len(calendar.month_abbr) - 1


def _plus_miesiace(dzien: date, miesiecy: int) -> date:
    """Termin oznaczony w miesiącach — z zasadą z art. 112 kodeksu cywilnego.

    Termin kończy się w dniu o tej samej liczbie porządkowej, a gdy takiego dnia w miesiącu
    nie ma — w dniu ostatnim. Bez tego 31 sierpnia plus sześć miesięcy byłoby datą, której
    kalendarz nie zna, a najprostsza „naprawa" (przelanie nadmiaru na marzec) przesuwałaby
    termin poza dzień wskazany przez ustawę.
    """
    indeks = dzien.month - 1 + miesiecy
    rok = dzien.year + indeks // MIESIECY_W_ROKU
    miesiac = indeks % MIESIECY_W_ROKU + 1
    _, ostatni_dzien = calendar.monthrange(rok, miesiac)
    return date(rok, miesiac, min(dzien.day, ostatni_dzien))


def termin_zastepczy(dzien_bilansowy: DzienBilansowy, termin: Termin) -> TerminUstawowy:
    """Ogranicznik zastępczy dla okresu zakończonego danym dniem bilansowym.

    **To nie jest termin złożenia sprawozdania i nie wolno go tak nazywać.** Termin właściwy
    biegnie od dnia zatwierdzenia, a rejestr publikuje wyłącznie datę złożenia — więc
    z danych publicznych jest nieudowadnialny (`docs/niezmierzone.md`, wiersz 3). Liczba
    miesięcy i dni pochodzi z katalogu, nie stąd: gdyby ustawodawca ruszył którąkolwiek,
    zmiana jest w YAML-u, w miejscu, które czyta prawnik.

    **Czego tu nie ma:** przesunięcia z art. 115 k.c., gdy termin wypada w niedzielę albo
    święto. Kierunek błędu jest ku zapłonowi — ogranicznik czyta się jako upłynięty do trzech
    dni za wcześnie — więc to nie jest przeoczenie, tylko pozycja w `docs/pomiary.md`
    (wiersz 11) i pytanie do przeglądu prawnego: tablica świąt ruchomych w `signals/` zderza
    się z regułą granic 10, a niezmierzone opóźnienie publikacji wzmianki jest o rząd
    wielkości większe.
    """
    po_miesiacach = _plus_miesiace(dzien_bilansowy, termin.miesiecy)
    return TerminUstawowy(po_miesiacach + timedelta(days=termin.dni))


def termin_nastepnego_okresu(termin: TerminUstawowy) -> TerminUstawowy:
    """Ten sam ogranicznik, przesunięty o rok obrotowy do przodu.

    **Niesie założenie i dlatego nie liczy nowego dnia bilansowego**: zakłada, że kolejny rok
    obrotowy jest tej samej długości co poprzedni. Dla spółki, która rok obrotowy zmieniła,
    to nieprawda. Dzień bilansowy powstaje wyłącznie z odpisu (reguła granic 9), a odpis nie
    niesie dnia bilansowego okresu, którego w nim nie ma — więc przesuwamy termin, o którym
    wiadomo, skąd się wziął, i wpisujemy założenie do wyniku jako pozycję nierozstrzygniętą.
    """
    return TerminUstawowy(_plus_miesiace(termin, MIESIECY_W_ROKU))
