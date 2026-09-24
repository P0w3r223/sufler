"""Jedyne wyjście na stdout: koperta wyniku i nic poza nią (ADR-0024, reguła granic 15).

`jsonout.py` jest dla stdout tym, czym `richtext.py` jest dla `rich` — jeden moduł, jeden
piszący, jeden neutralizator. Dzięki temu na pytanie „czy sekret może wyjść tym kanałem"
odpowiada się przez przeczytanie jednego pliku, tak samo jak reguła 11 pozwala odpowiedzieć
o połączeniach przez przeczytanie `httpclient.py`.

**Maskowanie, a nie ucieczka znaków — to są dwie różne rzeczy i łatwo je pomylić.** `json.dumps`
ucieka znaki sterujące (U+0000-U+001F, w tym ESC), więc jest prawdziwym neutralizatorem dla
terminalowej połowy §B — i **nie maskuje niczego**. To jest odbicie lustrzane `strip_control`,
którego ADR-0009 świadomie nie przyjął jako neutralizatora reguły 10 właśnie dlatego, że nie
maskuje, a ładunek tokenu niesie numer PESEL. Wektor jest realny: komunikat błędu to jedyne pole
koperty, które potrafi nieść adres URL.

Ten moduł nie zna `Wynik` i nie ma go znać: bierze gotowy słownik, tak samo jak `richtext` bierze
gotowy napis. Kształt koperty należy do `ui/wynik.py`, czyli do warstwy **wyżej**; odwrócenie tej
zależności zrobiłoby z modułu piszącego moduł wiedzący, co pisze.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import mask_tokens


def zamaskuj(wartosc: object) -> object:
    """Jeden rekurencyjny spacer maskujący — po kluczach i po wartościach.

    Cztery pułapki, każda popełniona w pierwszej wersji czegoś podobnego:

    1. **Klucze też są napisami.** `uwagi[].kod` i klucze `kryteria` przechodzą tędy tak samo
       jak wartości; maska tylko na wartościach zostawiłaby otwartą połowę struktury.
    2. **`Path` zanim `str`.** Ścieżki przychodzą już zamienione na napis z `Wynik.koperta`,
       ale gałąź zostaje, bo `dodatki` wypełnia korzeń kompozycji i nic nie broni mu podać
       `Path` wprost. Maska sprawdzająca `isinstance(x, str)` przepuściłaby ją nietkniętą.
    3. **Zejść w `dict`, `list` i `tuple`, zostawić `int`, `bool`, `None`.** `bool` jest
       podklasą `int` i musi wyjść jako `true`/`false`, a nie jako `1`/`0`.
    4. **Nieznany typ zostaje nietknięty** — i to jest celowe. `json.dumps` wywróci się na nim
       głośno, **zanim** cokolwiek trafi na stdout (patrz `wypisz`), więc wołający dostaje
       pusty stdout i ślad stosu zamiast połowy dokumentu JSON. Ciche zamienianie na napis
       ukryłoby defekt w miejscu, gdzie nikt go już nie zobaczy.
    """
    if isinstance(wartosc, str):
        return mask_tokens(wartosc)
    if isinstance(wartosc, Path):
        return mask_tokens(str(wartosc))
    if isinstance(wartosc, Mapping):
        return {mask_tokens(str(klucz)): zamaskuj(pole) for klucz, pole in wartosc.items()}
    # `str` i `bytes` są `Sequence`, więc kolejność gałęzi jest tu nośna — napis złapał się
    # wyżej, a `bytes` nie ma czego szukać w kopercie i niech się wywróci na `json.dumps`.
    if isinstance(wartosc, Sequence) and not isinstance(wartosc, bytes):
        return [zamaskuj(pole) for pole in wartosc]
    return wartosc


def zapisz(koperta: Mapping[str, object]) -> str:
    """Koperta jako jeden wiersz czystego ASCII, zakończony znakiem nowej linii.

    Jeden wiersz, nie `indent=2`: w przechwyconym logu dokument jest wtedy jedną linią, którą
    da się wziąć przez `tail -1 … | jq`, i nie sposób go pomylić z ludzką narracją idącą na
    stderr.

    **`ensure_ascii` zostaje włączone, wbrew pierwszej wersji ADR-0024 — zmierzone 2026-09-23.**
    Bez `PYTHONUTF8=1` `sys.stdout.encoding` jest tu **cp1250**, więc `ensure_ascii=False`
    dawało dokument, w którym „ł" wychodziło bajtem `0xb3`: poprawny cp1250 i nie-UTF-8, czyli
    dokument, który `jq` odrzuca, bo RFC 8259 wymaga do wymiany UTF-8. Ucieczka `\\u0142` jest
    czystym ASCII, przechodzi przez **każde** kodowanie strumienia, a parser zwraca z niej
    z powrotem „ł". Czytelność surowego stdout była jedynym argumentem za `False` i przestała
    nim być z chwilą, gdy ludzka narracja przeniosła się na stderr (decyzja 2).

    Kolejność kluczy jest kolejnością wstawiania, czyli tą z `Wynik.koperta`: `wersja`,
    `polecenie` i `status` na początku. `sort_keys=True` też byłoby powtarzalne, ale stawiałoby
    `blad` na czele, a `zapytania` na końcu — powtarzalność bez czytelności.
    """
    return json.dumps(zamaskuj(koperta), ensure_ascii=True) + "\n"


def wypisz(koperta: Mapping[str, object]) -> None:
    """Zapisuje kopertę na stdout — jednym wywołaniem `write`, po udanej serializacji.

    Kolejność jest tu całą treścią. Serializacja idzie najpierw do napisu, więc pole
    nieznanego typu wywraca się **przed** pierwszym zapisem: wołający dostaje pusty stdout,
    a nie połowę dokumentu JSON, do której dopisze się ślad stosu. `json.dump(obj, sys.stdout)`
    wyglądałby czyściej i miał dokładnie ten defekt.

    `sys.stdout` rozwiązywany przy wywołaniu, nie zapamiętany — inaczej przechwytywanie
    wyjścia w testach CLI przestałoby działać, tak samo jak przy konsoli `rich`.
    """
    sys.stdout.write(zapisz(koperta))
