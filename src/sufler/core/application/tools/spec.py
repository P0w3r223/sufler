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
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sufler.core.errors import RepositoryError

# Alias typu daty pod adnotacje pól, których model widzi pod nazwą ``date``. Adnotacje są tu
# napisami (``from __future__ import annotations``) rozwiązywanymi w globalach modułu, więc
# parametr o tej nazwie i tak nie przesłania typu — alias istnieje po to, żeby czytający nie
# musiał tego sprawdzać.
_DateField = date


@dataclass(frozen=True)
class ToolSpec:
    """Transport-neutralna definicja narzędzia: nazwa, opis, funkcja i pochodzenie WYNIKU.

    ``taints`` odpowiada na jedno pytanie (ADR 0066 R2): czy WYNIK tego narzędzia niesie treść
    spoza bramek zdolności. Mieszka tutaj, a nie w liście napisów u konsumenta, bo to własność
    NARZĘDZIA — jedzie z nim przy zmianie nazwy, przy przeniesieniu do innego buildera i przy
    dołożeniu drugich drzwi (ADR 0073). Lista napisów w adapterze przeżywała rename zielona,
    a jedynym objawem była skaza, która nigdy się nie zapala.

    Domyślne ``False`` jest wygodą dla atrap w testach, nie odpowiedzią. W pakiecie
    ``core/application/tools/`` cisza jest zabroniona: strażnik AST wymaga WYPISANEGO
    ``taints=`` przy każdym ``ToolSpec(...)`` i wskazuje plik z linią, gdy go brak.
    """

    name: str
    description: str
    fn: Callable[..., dict[str, Any]]
    taints: bool = field(default=False, kw_only=True)


# Ślad po filtrze idzie do WYNIKU (pole ``note`` w kopercie, ADR 0068 §5), nie do opisu — i
# tylko wtedy, gdy filtr faktycznie zawęził widok, żeby zdanie nie jechało w turach, których nie
# dotyczy. Bez tego śladu lista przefiltrowana jest nie do odróżnienia od pełnej, a model
# wyprowadza z niej twierdzenie o ŚWIECIE: na demo 2026-08-21 odpowiedział „najnowsze issue to
# #76", bo #77 — założone przez niego samego z Teamsów — leży pod ``source='teams'``.
_EVENTS_FILTERED_NOTE = (
    "Ten widok jest ZAWĘŻONY ({filtry}) — to nie jest pełna lista zdarzeń; przy twierdzeniu "
    "o kompletności powtórz odczyt bez filtru."
)

# Notka BEZWARUNKOWA — o tym, czym ta warstwa JEST, a nie o tym, jak ją zawężono. Jedzie w KAŻDEJ
# odpowiedzi, także pełnej, bo poprzednia wersja opisu odsyłała po stan GitHuba do widoku, który
# stanu nie zna: 2026-09-04 na pytanie o otwarte zgłoszenia padła tabela dziesięciu, wszystkich
# zamkniętych, a jedyne otwarte nie mogło się w niej pojawić. Notka o zawężeniu tego nie łapała,
# bo widok NIE BYŁ zawężony — był kompletny i mimo to nie odpowiadał na zadane pytanie.
# Idzie polem ``note`` w kopercie (ADR 0068 §5), nie w opisie: tam nie ma sufitu bajtów.
#
# ZDANIE PRZEPISANE PRZY ETAPIE 1 (ADR 0071 decyzja 1, 2026-09-07). Do tego dnia mówiło
# „zamknięć zgłoszeń nie zapisuje w ogóle" i było prawdziwe; mapper zaczął je emitować, więc
# zdanie zmieniło się W TYM SAMYM COMMICIE — wymusiła to bramka
# ``tests/core/test_warstwa_zdarzen_mowi_prawde.py``, która iteruje po REPERTUARZE MAPPERA (rzecz
# chroniona), a nie po etykietach notifiera (zabezpieczenie). Zerwała się sama, z komunikatem
# mówiącym, co poprawić — tak, jak zaprojektowano ją dzień wcześniej.
#
# Na miejsce starego zdania wchodzi konsekwencja decyzji 5 (odroczone ``issue_reopened``), a nie
# samo „zapisujemy zamknięcia": bez niej powtórzylibyśmy KLASĘ incydentu — widok wyglądałby na
# odpowiedź o stan, będąc nią tylko dopóki nikt niczego nie otworzył na nowo.
_EVENTS_LAYER_NOTE = (
    "Ta warstwa to HISTORIA tego, co most ZAPISAŁ — nie stan GitHuba. Zapisuje otwarcia "
    "i zamknięcia zgłoszeń, ale NIE ponowne otwarcia: zgłoszenie zamknięte i otwarte na nowo "
    "wygląda tu wciąż na zamknięte."
)

# Klauzula WSPÓLNA obu notek wyżej, wydzielona 2026-09-07. Obie kończyły się dosłownie tym samym
# zdaniem („Zanim powiesz, że czegoś nie ma albo co jest najnowsze…"), a odkąd notki się SKŁADAJĄ,
# model dostawał je w jednej odpowiedzi dwa razy. Powtórzenie w prompcie nie jest neutralne: uczy,
# że tekst obok wyniku jest wypełniaczem. Zdanie jedzie więc raz, na końcu złożonej notki, a każda
# z notek wnosi tylko własny POWÓD ostrożności.
_EVENTS_CAUTION_NOTE = (
    "Zanim powiesz, że czegoś nie ma albo co jest najnowsze, powiedz, w co zajrzałeś."
)

# Okno agregacji ``summary`` (ADR 0071 decyzja 10, dopisane 2026-09-07 z pytania recenzenta).
# Liczniki idą z okna ``limit`` (domyślnie 50, sufit 200), a lista ``recent`` pokazuje z niego
# PIERWSZE 20 — i ta druga liczba nie stała nigdzie. To ta sama klasa co incydent, dla którego
# powstała notka warstwy: widok wygląda na komplet, bo nic nie mówi, że nim nie jest.
_EVENTS_SUMMARY_WINDOW_NOTE = (
    "Liczniki policzone z okna {okno} ostatnich zdarzeń projektu; lista `recent` pokazuje "
    "z tego okna pierwsze {pokazane}."
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
