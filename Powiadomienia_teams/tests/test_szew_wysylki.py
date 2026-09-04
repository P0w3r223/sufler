"""Strażnik statyczny szwu wysyłki — czyta `runtime/` drzewem składni.

Docstring `runtime/wysylka.py` powołuje się na „strażnika statycznego czytającego ``runtime/``
drzewem składni (``tests/test_cisza.py``)". **Taki strażnik nie istniał** — `test_cisza.py`
testuje wyłącznie arytmetykę okna ciszy, a w całym `tests/` nie było ani jednego `ast.parse`.
Dlatego usterka w `_apply_confirmed_yes` (szeroki `except Exception` łykający `AuthExpiredError`
przed strażnikiem w `_process_pending`) mogła powstać i przeżyć wydanie.

Ten plik dopisuje obiecanego strażnika. Sprawdza KSZTAŁT, nie zachowanie, więc łapie całą klasę
usterki naraz — także w kodzie, którego nikt nie pokrył testem jednostkowym. Uzasadnienie polityki
jest w docstringu stałej `NIE_POLYKAJ`; tutaj jest tylko jej egzekwowanie.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Wywołania, po których stan bywa już utrwalony, a skutek jest nieodwracalny: wiadomość
# u pracownika albo wpis w grafiku klienta. To wokół nich obowiązuje polityka `NIE_POLYKAJ`.
_WYSYLKOWE = {"do_pracownika", "_apply_schedule"}

# Nazwy, których obecność PRZED szerokim `except` znaczy „utrata sesji propaguje".
# `NIE_POLYKAJ` to kanoniczna krotka; `AuthExpiredError` wprost jest dopuszczalne tam, gdzie
# `CiszaError` jest nieosiągalny (zapis do grafiku nie przechodzi przez bramkę ciszy).
_PRZEPUSZCZAJACE = {"NIE_POLYKAJ", "AuthExpiredError"}

_RUNTIME = Path(__file__).resolve().parent.parent / "src" / "powiadomienia_teams" / "runtime"


def _nazwa_wolania(wezel: ast.Call) -> str:
    cel = wezel.func
    if isinstance(cel, ast.Name):
        return cel.id
    if isinstance(cel, ast.Attribute):
        return cel.attr
    return ""


def _wysylki(wezel: ast.AST) -> list[ast.Call]:
    return [
        n for n in ast.walk(wezel) if isinstance(n, ast.Call) and _nazwa_wolania(n) in _WYSYLKOWE
    ]


def _nazwy_handlera(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"<bare>"}
    wezly = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    nazwy = set()
    for w in wezly:
        if isinstance(w, ast.Name):
            nazwy.add(w.id)
        elif isinstance(w, ast.Attribute):
            nazwy.add(w.attr)
    return nazwy


def _moduly() -> list[tuple[str, ast.Module]]:
    pliki = sorted(_RUNTIME.glob("*.py"))
    assert pliki, f"nie znalazłem modułów w {_RUNTIME} — strażnik badałby pustkę"
    return [(p.name, ast.parse(p.read_text(encoding="utf-8"))) for p in pliki]


def _bloki_z_wysylka() -> list[tuple[str, ast.Try]]:
    znalezione = []
    for nazwa, drzewo in _moduly():
        for wezel in ast.walk(drzewo):
            if isinstance(wezel, ast.Try) and any(_wysylki(b) for b in wezel.body):
                znalezione.append((nazwa, wezel))
    return znalezione


def test_szew_wysylki_w_ogole_istnieje():
    """Sonda samego strażnika: gdyby przestał cokolwiek znajdować, milczałby na zielono."""
    bloki = _bloki_z_wysylka()
    assert len(bloki) >= 10, f"strażnik widzi tylko {len(bloki)} bloków — zmieniły się nazwy?"


def test_kazdy_try_wokol_wysylki_przepuszcza_utrate_sesji():
    """Szeroki `except` wokół wysyłki MUSI mieć przed sobą handler przepuszczający utratę sesji.

    Połknięta `AuthExpiredError` zamienia utratę sesji w serię niewysłanych wiadomości, o której
    nikt się nie dowie: alert utraty sesji wychodzi z miejsca, do którego wyjątek już nie doleciał.
    Kolejność handlerów jest istotna — Python bierze PIERWSZY pasujący, więc `except Exception`
    stojący wcześniej przechwytuje wszystko.
    """
    luki = []
    for nazwa, blok in _bloki_z_wysylka():
        nazwy = [_nazwy_handlera(h) for h in blok.handlers]
        szerokie = [i for i, n in enumerate(nazwy) if n & {"Exception", "BaseException", "<bare>"}]
        if not szerokie:
            continue
        przed = nazwy[: szerokie[0]]
        if not any(n & _PRZEPUSZCZAJACE for n in przed):
            luki.append(f"{nazwa}:{blok.lineno} — handlery: {[sorted(n) for n in nazwy]}")
    assert not luki, "szeroki `except` przed strażnikiem utraty sesji:\n  " + "\n  ".join(luki)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LUKA 0.2.19 (nie testu): dwie wysyłki w `_interpret_and_confirm` nie mają wokół siebie "
        "ŻADNEGO `try` — gałąź 'brak powodu wolnego' i gałąź 'unclear'. Nie chroni ich więc ani "
        "polityka `NIE_POLYKAJ`, ani rekompensata w `finally`, którą ma sąsiednia gałąź prośby "
        "o potwierdzenie. Wyjątek z wysyłki leci do `_process_pending`, gdzie `_record_failure` "
        "podbija licznik — a `_commit` przesunął już watermark, więc wiadomość pracownika jest "
        "za nim i kolejny cykl jej nie zobaczy. To ten sam problem co xfail "
        "`test_nieudana_prosba_o_potwierdzenie_nie_zostawia_wpisu_w_awaiting_confirm` i domykamy "
        "go razem z nim (PR C: watermark). strict=True — naprawa zapali XPASS."
    ),
)
def test_kazda_wysylka_stoi_w_bloku_try():
    """Wysyłka bez `try` nie ma jak podlegać polityce `NIE_POLYKAJ` — jest poza szwem."""
    # Porównanie po (plik, linia), NIE po `id()` węzła: `_bloki_z_wysylka` i `_moduly` parsują
    # źródła osobno, więc obiekty AST z obu przebiegów nigdy nie byłyby tożsame — test xfailowałby
    # zawsze, także po naprawie, i wskazywałby wszystkie wysyłki zamiast dwóch niechronionych.
    chronione = {
        (nazwa, w.lineno)
        for nazwa, blok in _bloki_z_wysylka()
        for b in blok.body
        for w in _wysylki(b)
    }
    poza = []
    for nazwa, drzewo in _moduly():
        for wolanie in _wysylki(drzewo):
            if (nazwa, wolanie.lineno) not in chronione:
                poza.append(f"{nazwa}:{wolanie.lineno} — {_nazwa_wolania(wolanie)}")
    assert not poza, "wysyłka poza jakimkolwiek `try`:\n  " + "\n  ".join(poza)
