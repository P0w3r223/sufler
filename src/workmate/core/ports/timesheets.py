"""Porty cotygodniowej karty czasu (ADR 0035) — źródło godzin, zapis arkusza, tożsamości.

``Protocol`` jak pozostałe porty: dowolna implementacja o zgodnych sygnaturach jest akceptowana
bez dziedziczenia, więc orkiestrację testujemy w całości na atrapach — bez openpyxl, bez Graph,
bez sieci.

Metody są SYNCHRONICZNE. To proces wsadowy (raz w tygodniu, kilkanaście osób), a nie serwer —
asynchroniczność kupiłaby tu wyłącznie złożoność. Wyjątkiem jest wysyłka do Teams, która reużywa
istniejący, asynchroniczny ``TeamsNotifier``; drzwi mostkują te dwa światy.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from workmate.core.domain.timesheet import Person, WorkEntry


class HoursSource(Protocol):
    """Źródło przepracowanych godzin — jedyne miejsce, które wie, skąd biorą się liczby.

    Świadomie wąski kontrakt: „daj wpisy z okna". Dzięki temu podmiana źródła (eksport z RCP,
    arkusz kadrowy, API) nie dotyka ani domeny, ani generowania arkusza, ani wysyłki.
    """

    def read(self, since: date, until: date) -> list[WorkEntry]:
        """Wpisy czasu z okna PÓŁOTWARTEGO ``[since, until)`` — dla WSZYSTKICH osób.

        Rozbicie na osoby robi rdzeń (``group_by_source_id``), nie adapter: filtrowanie po
        stronie źródła znaczyłoby, że każdy nowy adapter musi samodzielnie powtórzyć izolację
        osób, a to kontrola bezpieczeństwa (ADR 0035) — ma być JEDNA i w rdzeniu.
        """
        ...


class SheetWriter(Protocol):
    """Zapis arkusza importu na dysk — jedyne miejsce znające format pliku (xlsx)."""

    def write(self, path: str, headers: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> None:
        """Zapisz arkusz pod ``path``; nadpisz, jeśli istnieje.

        Nagłówki i wiersze są już WYPROJEKTOWANE przez rdzeń — adapter niczego nie interpretuje.
        Nadpisanie jest zamierzone: ścieżka jest deterministyczna (osoba + tydzień), więc powtórny
        przebieg po awarii ma dać ten sam plik, a nie drugi obok.

        **KAŻDA komórka musi zostać zapisana jako TEKST** — to wymóg BEZPIECZEŃSTWA, nie
        kosmetyka. Biblioteki arkuszy wnioskują typ z treści: napis zaczynający się od ``=``
        staje się FORMUŁĄ (``=cmd|'/c calc'!A0``), a komentarz pochodzi ze źródła godzin,
        którego jeszcze nie znamy. Plik jawnie każemy człowiekowi otworzyć, więc wnioskowanie
        typu trzeba wyłączyć po stronie implementacji — rdzeń nie może tego zagwarantować,
        bo nie wie, czym plik zostanie zapisany. Dodatkowo chroni to znaczniki czasu przed
        „pomocnym" przekształceniem w natywny typ daty.
        """
        ...


class IdentityDirectory(Protocol):
    """Mapowanie ``source_id`` ze źródła godzin na tożsamości w Teams i w Jirze.

    Trzy systemy, trzy identyfikatory, żaden nie wynika z pozostałych. Implementacja MUSI być
    fail-closed: nieznane ``source_id`` → ``None``, czyli brak pliku i brak wiadomości. Nigdy
    dopasowanie po nazwisku — zły ``jira_user`` wpisze czyjeś godziny na CUDZE konto Jiry przy
    imporcie, a worklogi są create-only i nieusuwalne narzędziem (ADR 0034).
    """

    def resolve(self, source_id: str) -> Person | None:
        """Zwróć tożsamość dla ``source_id`` albo ``None``, gdy nie da się jej ustalić PEWNIE."""
        ...
