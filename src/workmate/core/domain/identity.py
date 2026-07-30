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

    Mapowanie jest jawną konfiguracją, nie dopasowaniem po nazwisku: zły ``jira_user`` pokazałby
    czyjeś zadania komuś innemu.
    """

    source_id: str
    aad_user_id: str
    jira_user: str
    display_name: str = ""
