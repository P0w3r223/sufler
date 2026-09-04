"""Jedyne wejście do wysyłania wiadomości — szew, na którym stoją godziny ciszy.

Dwie funkcje, bo są dwa RÓŻNE kanały i mylenie ich kosztowałoby albo wtargnięcie, albo utratę
sygnału awarii:

* ``do_pracownika`` — podlega godzinom ciszy. Wiadomość o 23:40 na czacie prywatnym jest
  wtargnięciem, choćby była uprzejma.
* ``do_administratora`` — NIE podlega. Cotygodniowe podsumowanie jest dead man's switchem: brak
  wiadomości w piątek bywa jedynym sygnałem awarii w instalacji bez monitoringu, więc przesunięcie
  go „na dozwoloną godzinę" zamieniałoby alarm w ciszę. Tak samo działają dziś alerty w ``DRY_RUN``.

**Dlaczego szew RZUCA, a nie pomija po cichu.** Pominięcie wygląda na łagodniejsze, a jest gorsze
w każdym z trzynastu punktów wysyłki, bo stan jest utrwalany PRZED nieodwracalnym skutkiem
(semantyka „co najwyżej raz"): przy prośbie tygodniowej powstałby pending mówiący „zapytano",
którego nikt nie dostał — a idempotencja `run_once` nie zaczepi tej osoby drugi raz w tym tygodniu;
przy prośbie o potwierdzenie stan twierdziłby, że bot o coś poprosił, a pracownik nie miałby o czym
wiedzieć i po terminie usłyszałby „nie doczekałem się potwierdzenia".

Dlatego cisza jest EGZEKWOWANA tutaj, ale ROZSTRZYGANA wyżej: ``poll_replies`` i ``run_once``
sprawdzają ją na wejściu i **odkładają całą pracę** (stan i wiadomość zostają razem). W praktyce ten
wyjątek nie ma więc jak polecieć — i to jest jego cel. Jest siatką na nowe miejsce wysyłki dopisane
kiedyś bez tej wiedzy (pozycja D5 planu dokłada przypomnienia i wznowienia rozmowy): takie miejsce
położy testy, zamiast napisać do kogoś w środku nocy.

Że wszystkie punkty wysyłki idą TĘDY, pilnuje strażnik statyczny czytający `runtime/` drzewem
składni (`tests/test_szew_wysylki.py`) — dokładnie tak, jak N28 pilnuje nazwisk w logach.
Do 0.2.19 to zdanie było NIEPRAWDĄ: odsyłało do `tests/test_cisza.py`, gdzie takiego strażnika
nigdy nie było (w całym `tests/` nie występował ani jeden `ast.parse`). Dlatego szeroki
`except Exception` w `_apply_confirmed_yes` mógł połknąć utratę sesji i przeżyć wydanie. Trzynaście
rozproszonych warunków to kształt odrzucony świadomie w N14 i tutaj obowiązuje ta sama zasada:
wygaszanie stoi przy wysyłce, a nie w pięciu miejscach, które o niej pamiętają.
"""
from __future__ import annotations

import logging
from datetime import datetime

from powiadomienia_teams import alerts
from powiadomienia_teams.config import Settings
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.runtime import operator
from powiadomienia_teams.runtime.cisza import najblizsza_dozwolona, wolno_pisac

logger = logging.getLogger(__name__)


class CiszaError(RuntimeError):
    """Próba napisania do pracownika w godzinach ciszy — błąd w kodzie, nie awaria zewnętrzna."""


NIE_POLYKAJ = (AuthExpiredError, CiszaError)
"""Wyjątki z punktu wysyłki, których NIE WOLNO połknąć — propagują z każdego wołającego.

Nieudana wysyłka jest zwykle logowana i na tym koniec: stan bywa już utrwalony, więc ponowienia
nie będzie, a jedna niedostarczona wiadomość nie może zabić pętli. Dwa wyjątki są inne:

* ``AuthExpiredError`` — dotyczy CAŁEJ usługi, nie tej jednej wiadomości. Połknięty zamienia
  utratę sesji w serię niewysłanych wiadomości, o której nikt się nie dowie, bo alert utraty sesji
  wychodzi z miejsca, do którego wyjątek już nie doleciał.
* ``CiszaError`` — to nie awaria zewnętrzna, tylko sygnał, że któryś punkt wysyłki OMINĄŁ bramkę
  godzin ciszy. Połknięty degraduje szew do „pomijania po cichu", czyli do tego, czego ma być
  zaprzeczeniem: pracownik nic nie dostaje, a stan twierdzi, że dostał.

Stała istnieje, bo ta polityka była skopiowana do dziewięciu miejsc razem z pięciowierszowym
uzasadnieniem — i **rozjechała się**: przy wprowadzeniu (0.2.13) sześć z jedenastu bloków `try`
wokół wysyłki nie propagowało utraty sesji. Jedna nazwa daje jedno miejsce do zmiany i pozwala
strażnikowi statycznemu sprawdzić kształt, którego przy dziewięciu kopiach sprawdzić się nie dało.
"""


def do_pracownika(
    settings: Settings, client: GraphClient, chat_id: str, html: str, *, teraz: datetime
) -> str:
    """Wyślij wiadomość do pracownika. W godzinach ciszy RZUCA ``CiszaError``.

    ``teraz`` jest wstrzykiwane, a nie czytane z zegara systemowego, bo to samo rozstrzygnięcie
    podejmuje warstwa wyżej i obie muszą patrzeć na tę samą chwilę — inaczej pętla przepuszczałaby
    pracę, której szew potem odmawia, i odwrotnie.
    """
    if not wolno_pisac(teraz, settings.okno_ciszy):
        # Alert PRZED rzuceniem, bo to jedyny kanał niezależny od ciszy — a kilka punktów wysyłki
        # domyka temat w bloku `except Exception` i wyjątek by tam ucichł. Sytuacja jest błędem
        # w kodzie (nowa ścieżka wysyłki bez bramki w pętli), więc waga jak przy tripwire N5.
        logger.critical("Próba napisania do pracownika w godzinach ciszy — punkt wysyłki bez bramki")
        operator.alert(
            settings,
            "NARUSZENIE: próba wysyłki w godzinach ciszy",
            "Któryś punkt wysyłki pominął bramkę godzin ciszy w pętli usługi. Wiadomość NIE "
            "została wysłana. To błąd w kodzie, nie awaria zewnętrzna — zgłoś go z logiem usługi.",
            waga=alerts.KRYTYCZNY,
        )
        raise CiszaError(
            f"Godziny ciszy ({settings.cisza_od_h}:00–{settings.cisza_do_h}:00) — nie piszemy do "
            f"pracowników; najbliższa dozwolona chwila: "
            f"{najblizsza_dozwolona(teraz, settings.okno_ciszy).isoformat()}"
        )
    return client.send_chat_message(chat_id, html)


def do_administratora(client: GraphClient, chat_id: str, html: str) -> str:
    """Wyślij wiadomość na kanał OPERATORSKI — bez względu na godzinę (patrz docstring modułu)."""
    return client.send_chat_message(chat_id, html)
