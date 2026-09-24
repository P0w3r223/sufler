"""`jsonout` — jedyny piszący na stdout i jego maska (ADR-0024, reguła granic 15).

Skan w `tests/test_boundaries.py` pilnuje **kształtu**: że serializuje jeden moduł i że jej
argumentem jest spacer maskujący. Tutaj jest to, czego skan udowodnić nie może — że spacer
naprawdę dosięga każdego miejsca, w którym sekret potrafi się schować.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ceidg_tool.jsonout import wypisz, zamaskuj, zapisz

# Kształt, który `config.SECRET_PATTERNS` rozpoznaje bez rejestrowania wartości — ten sam
# okaz, którym posługuje się `tests/test_console.py`.
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"


# --------------------------------------------------------------- cztery pułapki spaceru


def test_a_secret_in_a_value_is_masked() -> None:
    """Przypadek oczywisty — i jedyny, który miałaby maska napisana bez namysłu."""
    assert JWT not in zapisz({"blad": {"komunikat": f"odrzucono {JWT}"}})


def test_a_secret_in_a_key_is_masked() -> None:
    """Pułapka 1: klucze też są napisami.

    `uwagi[].kod` i klucze `kryteria` przechodzą tędy tak samo jak wartości. Maska położona
    wyłącznie na wartościach zostawiłaby otwartą dokładnie połowę struktury — i to tę połowę,
    której nikt nie ogląda, bo klucze „się przecież nie zmieniają".
    """
    assert JWT not in zapisz({f"pole-{JWT}": 1})


def test_a_secret_inside_a_path_is_masked() -> None:
    """Pułapka 2: `Path` nie jest `str`.

    `Wynik.koperta` zamienia ścieżki na napisy wcześniej, ale `dodatki` wypełnia korzeń
    kompozycji i nic nie broni mu podać `Path` wprost. Maska sprawdzająca `isinstance(x, str)`
    przepuściłaby ją nietkniętą — a ścieżka bywa jedynym polem niosącym cudzy katalog domowy.
    """
    assert JWT not in zapisz({"pliki": [Path("c:/tmp") / JWT / "a.xlsx"]})


def test_a_secret_two_levels_down_is_masked() -> None:
    """Pułapka 3: spacer ma schodzić w `dict`, `list` i `tuple`.

    Maska jednopoziomowa przechodzi każdy test napisany na płaskiej kopercie, a koperta płaska
    nie jest — `uwagi` to lista słowników, `kryteria` to słownik z listami.
    """
    gleboko = {"uwagi": [{"tekst": f"w środku {JWT}"}], "kryteria": {"pkd": (JWT,)}}

    assert JWT not in zapisz(gleboko)


def test_an_unknown_type_fails_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pułapka 4: nieznany typ ma się wywrócić **przed** pierwszym zapisem.

    `json.dump(obj, sys.stdout)` wyglądałby czyściej i miał dokładnie ten defekt: pola do
    miejsca awarii zdążyłyby wyjść, więc wołający dostałby połowę dokumentu JSON, a po niej
    ślad stosu. Serializacja idzie najpierw do napisu, więc stdout zostaje pusty.

    Cichego zamieniania nieznanej wartości na napis tu **nie ma** i być nie powinno: ukryłoby
    defekt w miejscu, w którym nikt go już nie zobaczy.
    """
    bufor = io.StringIO()
    monkeypatch.setattr(sys, "stdout", bufor)

    with pytest.raises(TypeError):
        wypisz({"kiedy": datetime.now(tz=UTC)})

    assert bufor.getvalue() == ""


# --------------------------------------------------------------- postać dokumentu


def test_the_document_is_pure_ascii_and_still_polish() -> None:
    """Czyste ASCII, bo kodowanie stdout nie jest nasze — zmierzone 2026-09-23.

    Bez `PYTHONUTF8=1` `sys.stdout.encoding` jest na tej maszynie **cp1250**, więc dokument
    pisany z `ensure_ascii=False` wychodził z „ł" jako bajt `0xb3`: poprawny cp1250, nie-UTF-8
    i odrzucany przez `jq`, bo RFC 8259 wymaga do wymiany UTF-8. Zmierzone na prawdziwym
    wyjściu polecenia, nie założone — pierwsza wersja ADR-0024 mówiła `ensure_ascii=False`.

    Dwie asercje, bo to jest jedno twierdzenie o dwóch końcach: bajty przechodzą przez każde
    kodowanie strumienia, a parser zwraca z nich z powrotem polskie zdanie.
    """
    wiersz = zapisz({"tekst": "zażółć gęślą jaźń"})

    assert wiersz.isascii()
    assert json.loads(wiersz)["tekst"] == "zażółć gęślą jaźń"


def test_control_characters_are_escaped_whatever_ensure_ascii_says() -> None:
    """Ucieczka znaków sterujących **nie** jest tym samym co ucieczka znaków spoza ASCII.

    Od tego zależy, czy sekwencja ESC z rejestru steruje terminalem tego, kto czyta stdout.
    Osobny test od poprzedniego, bo gdyby ktoś wrócił do `ensure_ascii=False` dla czytelności,
    ten ma zostać zielony — to są dwie niezależne własności jednego wywołania.
    """
    wiersz = zapisz({"tekst": "nazwa \x1b[31m\x00 z rejestru"})

    assert "\x1b" not in wiersz
    assert "\\u001b" in wiersz and "\\u0000" in wiersz


def test_booleans_stay_booleans() -> None:
    """`bool` jest podklasą `int`, więc spacer łatwo zamienia `demo` w `1`.

    `"demo": 1` przeszłoby każdy parser i zepsuło jedyny znacznik pokazu, który czyta agent
    (ADR-0014, znacznik szósty).
    """
    odczytane = json.loads(zapisz({"demo": True, "ile": 1}))

    assert odczytane["demo"] is True
    assert odczytane["ile"] == 1


def test_the_document_is_one_line_with_one_trailing_newline() -> None:
    """Jeden wiersz, żeby `tail -1 log | jq` działało na przechwyconym wyjściu.

    Dokument wielowierszowy mieszałby się w oku z ludzką narracją idącą na stderr, a przy
    przerwanym zapisie dałby się wziąć za kompletny.
    """
    wiersz = zapisz({"a": 1, "b": [1, 2]})

    assert wiersz.endswith("\n")
    assert wiersz.count("\n") == 1


def test_key_order_is_the_order_the_envelope_built() -> None:
    """Powtarzalna **i** czytelna: `wersja` i `status` z przodu, nie `blad` alfabetycznie."""
    wiersz = zapisz({"wersja": 1, "status": "ok", "blad": None})

    assert wiersz.startswith('{"wersja": 1, "status": "ok"')


def test_the_walk_leaves_alone_what_json_already_knows() -> None:
    """Maska ma maskować, nie przepisywać. Liczby, `None` i zagnieżdżenia wychodzą bez zmian."""
    wejscie = {"ile": 7, "nic": None, "lista": [1, {"x": "zwykły tekst"}]}

    assert zamaskuj(wejscie) == wejscie


# --------------------------------------------------------------- strumień


def test_stdout_is_resolved_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ten sam powód co przy konsoli `rich`: strumień zapamiętany przy imporcie zamraża się.

    Przechwytywanie wyjścia w testach CLI podmienia `sys.stdout` już po zaimportowaniu
    pakietu, więc moduł trzymający referencję pisałby obok — a sprawdzenie „koperta wyszła
    na stdout" byłoby wtedy niemożliwe dokładnie tam, gdzie jest potrzebne.
    """
    bufor = io.StringIO()
    monkeypatch.setattr(sys, "stdout", bufor)

    wypisz({"polecenie": "szukaj-pkd"})

    assert json.loads(bufor.getvalue())["polecenie"] == "szukaj-pkd"


def test_bytes_are_not_quietly_turned_into_a_list_of_numbers() -> None:
    """`bytes` jest `Sequence`, więc spacer bez osobnej gałęzi rozłożyłby go na liczby.

    `[122, 97]` przeszłoby przez parser jako poprawny JSON i byłoby cichym złym kodowaniem —
    a bajty nie mają w kopercie czego szukać, więc właściwą odpowiedzią jest hałas.
    """
    with pytest.raises(TypeError):
        zapisz({"co": b"za"})
