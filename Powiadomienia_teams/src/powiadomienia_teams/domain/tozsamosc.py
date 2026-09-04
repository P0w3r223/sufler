"""Porównywanie identyfikatorów AAD — jedno miejsce zamiast dziesięciu `==`.

Identyfikatory użytkowników przychodzą z trzech niezależnych źródeł: `GET /me`, `list_members`
i `list_chat_messages`. Graph nie obiecuje, że odda ten sam GUID w tej samej wielkości liter,
a operator wkleja czwarte źródło do `ONLY_USER_IDS` ręcznie — czasem z portalu, czyli w klamrach.
Porównanie znak w znak dawało trzy różne usterki naraz (bot brał własną wiadomość za odpowiedź
pracownika, cudza treść przechodziła jako jego odpowiedź, a pilotaż milczał), więc polityka
mieszka tu, a nie w każdym miejscu porównania osobno.

**Normalizujemy PRZY PORÓWNANIU, nie zmieniamy samego identyfikatora.** To nie jest kosmetyka
i nie wolno tego odwrócić: klucze w pliku stanu to surowe id z Graph, zapisane tygodnie wcześniej,
a `PendingReminder.member_id` trafia stamtąd wprost do `POST`-a tworzącego zmianę w Shifts.
Znormalizowanie „żywej" strony rozminęłoby `state.get(member.user_id)` (`runtime.nudge`) z wpisem
już istniejącym — osoba z otwartą rozmową dostałaby DRUGĄ prośbę, a obok powstałby drugi wpis
o ten sam tydzień, oba mogące dojść do zapisu w grafiku.

Jedyny wyjątek to `Settings.only_user_ids`: pochodzi wyłącznie z `env`, nie trafia ani do stanu,
ani do Graph, więc normalizuje się je RAZ, na granicy (`config.Settings.__post_init__`). Dzięki
temu nowe miejsce porównania nie musi pamiętać o `casefold` po tamtej stronie.

Wzorzec wzięty z `domain/powody.py`, gdzie ten sam zabieg (`normalize`) obowiązuje dla nazw
powodów nieobecności — projekt zna ten idiom i stosuje go świadomie tam, gdzie porównuje napisy
pochodzące od dwóch różnych systemów.

**Gdzie tego NIE stosować:** `reminders.guards.ensure_single_owner`. To jedyne miejsce, w którym
rozluźnienie porównania OSŁABIA zabezpieczenie zamiast je wzmacniać — strażnik stoi na granicy
nieodwracalnego zapisu do Shifts, a obie jego strony pochodzą dziś z jednej wartości, więc nie
zapala się przypadkiem. Kierunek błędu jest tam bezpieczny (odmowa zapisu), a tutaj — nie.
"""

from __future__ import annotations


def znormalizuj(identyfikator: str) -> str:
    """Postać kanoniczna identyfikatora AAD do PORÓWNAŃ (nie do zapisu).

    `casefold`, nie `lower`: GUID-y są ASCII, ale ta funkcja bywa wołana na tym, co wkleił
    człowiek, a `casefold` jest właściwym narzędziem do porównań bez względu na alfabet.
    Klamry `{...}` schodzą, bo portal Azure kopiuje id właśnie w tej postaci, a Graph nigdy jej
    nie zwraca — bez tego wklejenie z portalu cicho wypada z pilotażu.
    """
    # Białe znaki obcinamy DWA RAZY — przed klamrami i po nich. Klamra potrafi opakowywać spację
    # (`"{ }"`), a wtedy jednokrotne obcięcie zostawia napis niepusty, który nie pasuje do nikogo:
    # dokładnie ta cicha awaria, którą ten moduł ma zamykać.
    return identyfikator.strip().strip("{}").strip().casefold()


def ten_sam(a: str, b: str) -> bool:
    """Czy dwa identyfikatory wskazują tę samą osobę.

    Pusty napis nie jest tożsamością i nie może się z niczym zrównać — `GET /me`, które zwróciło
    pusto, zamieniłoby filtr konta bota w „każdy jest botem" i wyciszyło cały nasłuch.
    """
    if not a or not b:
        return False
    return znormalizuj(a) == znormalizuj(b)
