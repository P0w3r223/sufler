"""``ToolSpec`` i wspólny kształt wyniku: koperta, trzy odpowiedzi odmowy i ślad zawężenia.

Każdy builder katalogu opakowuje funkcje narzędzi tym samym ``_envelope`` i odmawia
tymi samymi strukturami (``{status, error, tool, action, missing, hint}``) — model
poprawia się z odpowiedzi, bez sięgania po schemat. ``_puste`` nie jest odpowiedzią,
tylko pomocnikiem, który wylicza puste pola do komunikatu.

``_EVENTS_FILTERED_NOTE`` stoi tu, a nie przy jednym z dwóch builderów, bo renderują go
OBA — ``read_events_since`` (powierzchnia MCP, zamrożona) i ``Activity(action='events')``
(powierzchnia agenta). Trzymana przy którymkolwiek z nich, kazałaby jednej stronie
importować z drugiej."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from workmate.core.errors import RepositoryError

# Alias typu daty pod adnotacje pól, których model widzi pod nazwą ``date``. Adnotacje są tu
# napisami (``from __future__ import annotations``) rozwiązywanymi w globalach modułu, więc
# parametr o tej nazwie i tak nie przesłania typu — alias istnieje po to, żeby czytający nie
# musiał tego sprawdzać.
_DateField = date


@dataclass(frozen=True)
class ToolSpec:
    """Transport-neutralna definicja narzędzia: nazwa, opis i funkcja nad serwisami."""

    name: str
    description: str
    fn: Callable[..., dict[str, Any]]


# Ślad po filtrze idzie do WYNIKU (pole ``note`` w kopercie, ADR 0068 §5), nie do opisu — i
# tylko wtedy, gdy filtr faktycznie zawęził widok, żeby zdanie nie jechało w turach, których nie
# dotyczy. Bez tego śladu lista przefiltrowana jest nie do odróżnienia od pełnej, a model
# wyprowadza z niej twierdzenie o ŚWIECIE: na demo 2026-08-21 odpowiedział „najnowsze issue to
# #76", bo #77 — założone przez niego samego z Teamsów — leży pod ``source='teams'``.
_EVENTS_FILTERED_NOTE = (
    "Ten widok jest ZAWĘŻONY ({filtry}) — to nie jest pełna lista zdarzeń. Zanim powiesz, "
    "że czegoś nie ma albo co jest najnowsze, powtórz odczyt bez filtru."
)


def _envelope(
    build: Callable[[], dict[str, Any]],
    *,
    errors: tuple[type[Exception], ...] = (RepositoryError,),
) -> dict[str, Any]:
    """Wykonaj ``build`` i oddaj jego wynik; złap wskazane błędy → ``{"error": str(exc)}``.

    Jedno miejsce koperty błędów narzędzi. Narzędzie owija ciało w ``build()`` i oddaje je tu —
    dzięki temu jego NAGŁÓWEK/docstring/adnotacje zostają nietknięte (opis=docstring,
    schemat=sygnatura są ZAMROŻONE golden-testem, więc koperty NIE robimy dekoratorem na ``fn``).
    """
    try:
        return build()
    except errors as exc:
        return {"error": str(exc)}


def _brakuje_pol(tool: str, action: str, missing: list[str], hint: str) -> dict[str, Any]:
    """Odpowiedź na wywołanie bez pól wymaganych przez TĘ akcję (wzorzec ``action=…``).

    JSON Schema nie wyraża „jeśli ``action=save``, to ``title`` jest wymagany" — pola akcji
    są z konieczności opcjonalne w schemacie, więc walidacja per akcja żyje w dispatcherze.
    Kształt odpowiedzi jest strukturalny, nie prozą: model poprawia wywołanie z samej treści
    błędu, bez sięgania po schemat drugi raz.
    """
    return {
        "status": "invalid_request",
        "error": f"Akcja '{action}' wymaga pól, których nie podano: {', '.join(missing)}.",
        "tool": tool,
        "action": action,
        "missing": missing,
        "hint": hint,
    }


def _zla_akcja(tool: str, action: Any, dozwolone: tuple[str, ...]) -> dict[str, Any]:
    """Odpowiedź na akcję spoza zestawu — to INNY błąd niż brak pola i musi tak brzmieć.

    Przez ``_brakuje_pol`` wychodziło zdanie „Akcja 'save' wymaga pól, których nie podano:
    action" — a ``action`` została podana, tylko jest zła. Model dostawał instrukcję dołożenia
    pola, które właśnie wysłał; to zaproszenie do powtórzenia tego samego wywołania.

    ``allowed`` jest listą, nie prozą w ``hint``, bo cały sens tego kształtu polega na tym, że
    da się go odczytać bez parsowania zdania.
    """
    return {
        "status": "invalid_request",
        "error": f"Narzędzie '{tool}' nie ma akcji '{action}'.",
        "tool": tool,
        "action": action,
        "allowed": list(dozwolone),
        "hint": "dozwolone: " + ", ".join(dozwolone),
    }


def _nie_znaleziono(tool: str, action: str, error: str, hint: str) -> dict[str, Any]:
    """Odpowiedź na wywołanie poprawne strukturalnie, ale wskazujące na nieistniejący byt.

    Kształt jest CELOWO ten sam co przy braku pola (``tool``/``action``/``hint``). Dotąd gałąź
    „nie znaleziono" zwracała samo ``{"error": ...}``, więc model, który podał ZŁY klucz,
    dostawał mniej materiału do poprawy niż model, który nie podał ŻADNEGO — a to on jest
    bliżej celu. Podpowiedź jest ta sama, bo droga wyjścia jest ta sama: sprawdź rejestr.
    """
    return {
        "status": "not_found",
        "error": error,
        "tool": tool,
        "action": action,
        "hint": hint,
    }


def _puste(**pola: Any) -> list[str]:
    """Nazwy pól o wartości pustej — w kolejności deklaracji, bo taka wchodzi do komunikatu."""
    return [nazwa for nazwa, wartosc in pola.items() if wartosc in (None, "", [], ())]
