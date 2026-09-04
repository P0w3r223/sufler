"""Tożsamość zespołu: most Teams (AAD) ↔ Jira — bez logiki, tylko model.

Dawniej część domeny kart czasu (ADR 0035/0036, wycofane 0055); dziś współdzielona przez
autoryzację notatki ze spotkania (B2 / ADR 0042: kto może użyć ``/notatka``) i "moje zadania"
Jira (ADR 0054: czyje zadania pokazać). Oba potrzebują tylko „kto to jest" — żadnego nie obchodzi
ewidencja czasu, więc pole ``git_email`` (most do commitów, wyłącznie kart czasu) zniknęło razem
z modułem, który je konsumował.
"""

from __future__ import annotations

from pydantic import BaseModel


class Person(BaseModel):
    """Rozwiązana tożsamość członka pionu — identyfikatory z różnych systemów, żaden z pozostałych.

    ``source_id`` to klucz wpisu w mapie tożsamości, ``aad_user_id`` adresuje konto Teams/Entra,
    ``jira_user`` (e-mail albo accountId) to konto Jiry tej osoby.

    ``aad_user_id`` jest WYMAGANE, bo to ono ADRESUJE człowieka: każda bramka autoryzacji
    rozstrzyga po nim (odczyt bazy wiedzy, notatka ze spotkania, powłoka, mutacja notatek).
    Wpis bez niego nie autoryzowałby niczego i byłby cichym niebytem — osoba „jest w mapie",
    a każda bramka ją odrzuca.

    ``jira_user`` jest OPCJONALNE (ADR 0070 §1). Puste znaczy **osoba nie ma konta Jira**, a nie
    „konto nieznane" — to stan trwały i legalny, nie wpis do uzupełnienia. Taka osoba jest pełnym
    członkiem pionu, ale narzędzia ``Jira`` nie dostaje w ogóle (ADR 0070 §3).

    Mapowanie jest jawną konfiguracją, nie dopasowaniem po nazwisku: zły ``jira_user`` pokazałby
    czyjeś zadania komuś innemu.
    """

    source_id: str
    aad_user_id: str
    jira_user: str = ""
    display_name: str = ""
