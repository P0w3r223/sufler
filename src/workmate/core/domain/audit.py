"""Dziennik audytu (Faza 0, ADR 0067) — czysta projekcja argumentów narzędzia do zapisu BEZ treści.

Rejestrujemy AKCJE i ŚCIEŻKI, nigdy treść. Pole spoza allowlisty — i pole allowlisty, które jest
podejrzanie długie albo nieskalarne — redukujemy do znacznika ``<typ:długość>``. Klucz pola zostaje
zawsze: audyt ma wiedzieć, JAKIE argumenty padły, nie jaka była ich treść. Nowe narzędzie jest
domyślnie w pełni zredagowane, dopóki jego pole nie trafi na allowlistę — bezpieczna wartość
domyślna (ADR 0067 §1.3). Body notatki, bajty pliku i komenda ``Bash`` NIGDY nie trafiają do zapisu.

Bez I/O: to czysta funkcja, testowalna bez bazy. Fizyczny zapis stoi w adapterze, pseudonimizacja
nadawcy/rozmowy w warstwie aplikacji (reużywa ``core.domain.metrics.pseudonymize``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from workmate.core.domain.sanitize import strip_control_chars

if TYPE_CHECKING:
    from workmate.core.domain.mutation import Verdict

# Pola STRUKTURALNE bezpieczne do zapisu wprost (akcja / identyfikator / ścieżka / filtr), wspólne
# dla narzędzi. Wszystko poza tą listą to potencjalna treść → znacznik typu i długości. Świadomie
# WĄSKA: łatwiej dopisać pole, gdy pomiar pokaże, że go brakuje, niż wykryć wyciek treści po fakcie.
# Nazwy skonfrontowane z rzeczywistym katalogiem ``core/application/tools/`` (ADR 0067, review
# Fazy 0): ``name`` (create_file/read_file — nazwa pliku scope'a), ``file_format``/``image_format``
# (enum formatu odpowiedzi), ``week`` (Schedule, ISO), ``since``/``until`` (filtry dat Jira).
# Treść (``content``/``body``/``command``/``image_base64``/``title``/``query``) świadomie POZA nią.
_SAFE_FIELDS = frozenset(
    {
        "action",
        "key",
        "number",
        "issue",
        "project",
        "company",
        "note_id",
        "id",
        "kind",
        "source",
        "format",
        "file_format",
        "image_format",
        "filename",
        "name",
        "path",
        "limit",
        "week",
        "since",
        "until",
    }
)

# Nawet pole z allowlisty redagujemy, gdy jest podejrzanie długie — „path" o 2 kB to nie ścieżka,
# tylko treść wciśnięta w strukturalnie wyglądające pole.
_MAX_FIELD_CHARS = 128


def _redact(value: Any) -> str:
    """Znacznik ``<typ:rozmiar>`` bez treści: długość str/bytes, liczba elementów kolekcji."""
    if isinstance(value, str):
        return f"<str:{len(value)}>"
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}:{len(value)}>"
    if isinstance(value, Mapping):
        return f"<dict:{len(value)}>"
    return f"<{type(value).__name__}>"


def _project_value(name: str, value: Any) -> Any:
    """Zostaw wartość pola strukturalnego (krótką, skalarną); w innym wypadku zwróć znacznik."""
    if name not in _SAFE_FIELDS:
        return _redact(value)
    # ``bool`` jest podklasą ``int`` — obie (i ``float``) to bezpieczne skalary, bez treści.
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and len(value) <= _MAX_FIELD_CHARS:
        return value
    return _redact(value)


def project_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Zredaguj argumenty narzędzia do zapisu: pola strukturalne zostają, reszta → znaczniki.

    Zwraca słownik JSON-serializowalny (skalary albo znaczniki ``<typ:długość>``). Kolejność
    kluczy zachowana dla czytelności dziennika.
    """
    return {name: _project_value(name, value) for name, value in arguments.items()}


def project_verdict(verdict: Verdict, reason: str = "") -> str:
    """Złóż wartość kolumny ``judge_verdict``: werdykt + uzasadnienie, obie zredagowane.

    Werdykt sędziego mutacji (ADR 0065 §8) ląduje w TYM SAMYM wierszu, co wywołanie narzędzia,
    które go wywołało — dlatego jest projekcją, a nie osobnym zapisem. Sam werdykt to skalar
    z domeny (``allow``/``refuse``/``confirm``), więc idzie wprost.

    Uzasadnienie jest inne i to jest cała treść tej funkcji. Pisze je MODEL, który przed chwilą
    czytał notatkę — mogącą być wrogą (ADR 0065 R10 zakłada wprost sędziego pod wstrzyknięciem) —
    więc tekst przechodzi DWIE redakcje, nie jedną:

    1. **Znaki sterujące i złamania wiersza znikają.** To jedyne pole tego dziennika, które
       trafiałoby do bazy surowo: ``arg_summary`` idzie przez ``json.dumps`` (escapuje ``\n``
       i ``\x1b``), a powody kwarantanny przez własne spłaszczenie. Czytnik operatora drukuje
       ``judge_verdict`` wprost, więc powód z ``\n`` rozbijałby listing na wiersze wyglądające
       jak kolejne wpisy, a sekwencja ANSI szłaby prosto do jego terminala.
    2. **Sufit długości** — ten sam, co przy polu z allowlisty: powyżej ``_MAX_FIELD_CHARS``
       zostaje znacznik ``<str:długość>``. To jedyna mechaniczna gwarancja, jaką da się dać
       tekstowi swobodnemu, że fragment bazy wiedzy nie wjedzie do dziennika w środku zdania.

    Poniżej sufitu zapisujemy dosłownie — bo zdanie „notatka opisuje inny projekt niż podany"
    jest dokładnie tym, po co ta kolumna istnieje, a bez niego wiersz mówi „refuse" i nic więcej.
    """
    # Spłaszczenie białych znaków po zdjęciu sterujących: sam ``strip_control_chars`` zostawia
    # ``\n`` i tabulatory (jest strażnikiem treści notatki, nie formatu wiersza dziennika).
    powod = " ".join(strip_control_chars(reason).split())
    if not powod:
        return verdict
    if len(powod) > _MAX_FIELD_CHARS:
        powod = _redact(powod)
    return f"{verdict}: {powod}"
