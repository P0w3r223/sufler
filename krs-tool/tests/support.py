"""Wspólne narzędzia testów.

`ZakazSieciError` mieszka tutaj, a nie w `conftest.py`, bo importuje go zarówno sam zakaz, jak
i test tego zakazu — a `conftest` nie jest modułem, który wypada importować po nazwie.
"""

from __future__ import annotations

from typing import Any, NoReturn


class ZakazSieciError(RuntimeError):
    """Podniesione, gdy cokolwiek w suicie próbuje otworzyć połączenie."""


def odmowa_polaczenia(*args: Any, **kwargs: Any) -> NoReturn:
    """Zamiennik każdej drogi do gniazda."""
    raise ZakazSieciError(
        "Ten pod-projekt nie łączy się z niczym (docs/adr/0001, decyzja 2). "
        "Jeżeli potrzebujesz połączenia, to jest decyzja właściciela, nie zmiana w teście."
    )
