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
        strazniku = [
            h
            for h, n in zip(blok.handlers[: szerokie[0]], nazwy[: szerokie[0]], strict=True)
            if n & _PRZEPUSZCZAJACE
        ]
        if not strazniku:
            luki.append(f"{nazwa}:{blok.lineno} — handlery: {[sorted(n) for n in nazwy]}")
            continue
        # Sama OBECNOŚĆ handlera nie wystarcza: `except NIE_POLYKAJ: logger.exception(...)`
        # przechodziłby tę sondę, jednocześnie przywracając usterkę, dla której ten plik istnieje.
        #
        # Kryterium to „KOŃCZY SIĘ `raise` i nie ma `return`", nie „jest samym `raise`":
        # logowanie przed przekazaniem dalej bywa konieczne (w `_apply_confirmed_yes` to jedyne
        # miejsce, gdzie da się jeszcze powiedzieć, KOGO i którego tygodnia dotyczył przerwany
        # zapis — wyżej wyjątek już tego nie niesie). Zakazane jest POŁKNIĘCIE, nie kontekst.
        for h in strazniku:
            konczy_raise = bool(h.body) and isinstance(h.body[-1], ast.Raise)
            ma_return = any(isinstance(w, ast.Return) for w in ast.walk(h))
            if not konczy_raise or ma_return:
                luki.append(
                    f"{nazwa}:{h.lineno} — strażnik utraty sesji NIE przekazuje jej dalej "
                    f"(ciało nie kończy się `raise` albo zawiera `return`)"
                )
    assert not luki, "szeroki `except` przed strażnikiem utraty sesji:\n  " + "\n  ".join(luki)


# Wołania, które MUSZĄ iść przez szew — poza dwoma miejscami, które ten szew stanowią.
_NA_SKROTY = {"send_chat_message", "create_shift", "create_time_off"}
_WOLNO_NA_SKROTY = {
    ("wysylka.py", "send_chat_message"),
    ("listener.py", "create_shift"),
    ("listener.py", "create_time_off"),
}


def test_zadna_wysylka_nie_omija_szwu():
    """Autorytet strażnika stoi na zdaniu „wszystkie punkty wysyłki idą TĘDY" — sprawdźmy je.

    Sonda wyżej zna tylko dwie nazwy wołań. `client.send_chat_message(...)` wstawione wprost
    do `runtime/` ominęłoby ją, a razem z nią bramkę godzin ciszy i politykę `NIE_POLYKAJ` —
    przy wszystkich testach nadal zielonych.
    """
    naruszenia = []
    for nazwa, drzewo in _moduly():
        for wezel in ast.walk(drzewo):
            if not isinstance(wezel, ast.Call):
                continue
            wolanie = _nazwa_wolania(wezel)
            if wolanie in _NA_SKROTY and (nazwa, wolanie) not in _WOLNO_NA_SKROTY:
                naruszenia.append(f"{nazwa}:{wezel.lineno} — {wolanie}")
    assert not naruszenia, "wysyłka z pominięciem szwu:\n  " + "\n  ".join(naruszenia)


def test_zadna_wysylka_nie_stoi_poza_blokiem_try():
    """Po fali 4 (C3) niechronionych wysyłek ma być ZERO — asercja pusta listą, nie liczbą.

    Do fali 4 ten test przypinał LICZBĘ dwa: gałęzie „brak powodu wolnego" i `unclear` wołały
    `_commit` i zaraz po nim wysyłkę, bez żadnego `try`. Równość, a nie `not poza`, była wtedy
    świadoma — przy „niepusto" trzecia niechroniona wysyłka wpadałaby w tę samą znaną lukę
    i wyglądała na oczekiwaną. Zadziałało zgodnie z projektem: naprawa fali 4 zapaliła ten test
    na czerwono i wymusiła aktualizację.

    Teraz luki nie ma, więc pusta lista jest mocniejsza od każdej liczby — każda nowa wysyłka poza
    `try` zapali test, bez pytania, czy „mieści się w znanym limicie".
    """
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
                poza.append(f"{nazwa}:{_nazwa_wolania(wolanie)}:{wolanie.lineno}")
    assert poza == [], poza


def test_wycofanie_commitu_stoi_wylacznie_w_finally():
    """Druga reguła, dopisana w fali 4: `_wycofaj_commit` MUSI stać w `finally`.

    Reguła wyżej sprawdza samą OBUDOWĘ — czy wysyłka ma wokół siebie `try`. Po fali 4 niezmiennik
    jest mocniejszy: niedoręczona wiadomość ma COFNĄĆ commit. Wycofanie w `body` wykonałoby się
    także po udanej wysyłce (cofając poprawną obsługę), a w `except` ominęłoby wyjątki, które
    świadomie propagują bez handlera — `NIE_POLYKAJ` w miejscu prośby o potwierdzenie oraz
    wszystko w dwóch pozostałych miejscach, które handlera nie mają wcale.

    `finally` jest jedynym kształtem wykonującym się na KAŻDEJ drodze wyjścia, a o tym, czy
    pracownik zobaczył wiadomość, nie decyduje typ awarii. Ta sama zasada, dla której miejsce
    prośby o potwierdzenie wybrało `finally` zamiast `isinstance` w jednej gałęzi.
    """
    w_finally: set[tuple[str, int]] = set()
    wszystkie: set[tuple[str, int]] = set()
    for nazwa, drzewo in _moduly():
        for wezel in ast.walk(drzewo):
            if isinstance(wezel, ast.Try):
                for gałąź in wezel.finalbody:
                    for w in ast.walk(gałąź):
                        if isinstance(w, ast.Call) and _nazwa_wolania(w) == "_wycofaj_commit":
                            w_finally.add((nazwa, w.lineno))
            if isinstance(wezel, ast.Call) and _nazwa_wolania(wezel) == "_wycofaj_commit":
                wszystkie.add((nazwa, wezel.lineno))

    # Kontrola pozytywna: gdyby wycofanie zniknęło z kodu, pusty zbiór przechodziłby oba warunki
    # i strażnik pilnowałby niczego. Trzy miejsca to trzy wołania.
    assert len(wszystkie) == 3, sorted(wszystkie)
    assert wszystkie == w_finally, sorted(wszystkie - w_finally)
