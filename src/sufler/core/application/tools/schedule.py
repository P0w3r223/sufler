"""Katalog narzędzia ``Schedule`` — grafik pionu z Shifts (odczyt)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, ValidationError

if TYPE_CHECKING:
    from sufler.core.application.team_schedule import TeamScheduleService

from sufler.core.application.tools.spec import ToolSpec, _envelope
from sufler.core.errors import SuflerError

_SCHEDULE_DESC = """\
Grafik zmian i nieobecności zespołu z Teams Shifts — ODCZYT.

Zwraca `shifts` (zmiany) i `times_off` (urlopy, nieobecności) w zadanym oknie, wraz z `range`
(faktycznie użyty zakres), `timezone` i `people_without_entries` (osoby bez żadnego wpisu w tym
oknie — pole puste, gdy pytasz o jedną osobę).

Każda zmiana ma `work_mode`: 'stacjonarnie' (praca z biura — zielony kolor zmiany),
'zdalnie' (niebieski) albo 'urlop' (szary kolor zmiany — nieobecność/urlop wpisany jako
całodniowa zmiana; taką osobę traktuj jak nieobecną, nie jako pracującą). Wartość `null` znaczy
kolor bez ustalonego u nas znaczenia — podaj wtedy surowy kolor z pola `theme` i powiedz wprost,
że nie znasz jego znaczenia; nie zgaduj formy pracy.

Użyj, gdy pytanie dotyczy grafiku, zmian, dyżurów, tego kto pracuje, kto ma urlop albo wolne,
a także czy ktoś pracuje zdalnie czy stacjonarnie."""

# Prezentacja skróconego wyniku wraca kopertą (ADR 0068 §5), nie opisem — inaczej zdanie
# o przycięciu jechałoby w każdym żądaniu, także w tych, których nie dotyczy.
#
# Notka mówi WPROST, że `people_without_entries` liczy się z pełnego okna, bo to jedyne pole,
# które przy skróconych listach da się źle odczytać: osoba nieobecna zarówno w `shifts`, jak
# i wśród „bez wpisów", ma wpisy — tyle że poza sufitem. Wcześniejsze brzmienie („dotyczy tylko
# tego, co widać") twierdziło coś odwrotnego niż robi ``TeamScheduleService.schedule``, która
# wylicza to pole PRZED przycięciem — czyli kazało modelowi zaniżać zaufanie do jedynego pola,
# które przycięcie zostawia nienaruszonym.
_SCHEDULE_TRUNCATED_NOTE = (
    "Część wpisów nie zmieściła się w odpowiedzi (`omitted_entries`) — powiedz o tym "
    "i zaproponuj węższe okno albo filtr osoby. `people_without_entries` liczy się z CAŁEGO "
    "okna, nie z widocznej części, więc pozostaje wiarygodne; brak kogoś w tej liście ORAZ "
    "w `shifts` znaczy, że jego wpisy wypadły poza sufit."
)


def build_schedule_catalog(service: TeamScheduleService) -> list[ToolSpec]:
    """Zbuduj narzędzie ``Schedule`` — grafik Teams Shifts (ADR 0059), czysty ODCZYT bez bramki.

    Wstrzykiwane jako ``extra_catalog`` tylko gdy grafik jest włączony (istnieje cudzy cache MSAL).
    Błędy cichego tokenu/consentu materializują się DOPIERO przy wywołaniu (jako ``{"error": ...}``
    w kopercie), więc brak zgody Schedule.Read.All degraduje łagodnie, nie wywraca pollera.

    Krok 5.4b (ADR 0009 paczki wdrożeniowej) nie wchłania tu niczego — narzędzie od początku było
    jedno. Zmienia się nazwa (``get_team_schedule`` → ``Schedule``, spójnie z pozostałą czwórką)
    oraz **miejsce, w którym stoi proza o polach**: dotąd cała siedziała w opisie narzędzia,
    a wszystkie cztery pola szły do modelu z samym ``title`` i ``type``. To łamało bramkę wzorca
    („każde pole ma niepusty opis") — jedyne narzędzie agenta, które ją łamało.

    ``week`` dostaje ``Literal``, więc niepoprawna wartość przestaje być wyrażalna. Reguła nie
    znika z domeny (``resolve_schedule_range`` dalej ją sprawdza i daje czytelny błąd) — schemat
    jest pierwszą bramką, domena pozostaje tą, która obowiązuje.

    Narzędzie NIE jest na powierzchni MCP (tylko ``extra_catalog`` drzwi Teams), więc zmiana nazwy
    nie rusza zamrożonego baseline — inaczej niż przy Jirze, gdzie builder był współdzielony.
    """

    def schedule(
        week: Annotated[
            Literal["current", "previous", "next"],
            Field(
                description=(
                    "Który tydzień (poniedziałek–niedziela). Ignorowane, gdy podasz "
                    "`date_from`/`date_to`."
                )
            ),
        ] = "current",
        date_from: Annotated[
            str | None,
            Field(
                description=(
                    "Początek jawnego zakresu, RRRR-MM-DD. Podaj RAZEM z `date_to` albo wcale; "
                    "zakres ma pierwszeństwo przed `week`, maks. 31 dni."
                )
            ),
        ] = None,
        date_to: Annotated[
            str | None,
            Field(description="Koniec jawnego zakresu, RRRR-MM-DD — włącznie z tym dniem."),
        ] = None,
        person: Annotated[
            str | None,
            Field(
                description=(
                    "Imię i nazwisko, np. 'Jerzy Zastepski' — zawęża wynik do jednej osoby. "
                    "Dopasowanie ignoruje wielkość liter i polskie znaki; osoba nieznana albo "
                    "niejednoznaczna daje czytelną odmowę, nie pusty wynik."
                )
            ),
        ] = None,
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            wynik = service.schedule(
                week=week,
                date_from=date_from or "",
                date_to=date_to or "",
                person=person or "",
            )
            if wynik.get("truncated"):
                return {**wynik, "note": _SCHEDULE_TRUNCATED_NOTE}
            return wynik

        return _envelope(build, errors=(SuflerError, ValidationError))

    return [ToolSpec("Schedule", _SCHEDULE_DESC, schedule, taints=False)]
