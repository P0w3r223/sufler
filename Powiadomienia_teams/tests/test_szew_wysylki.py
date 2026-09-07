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


def _funkcje_kompensujace() -> list[tuple[str, ast.FunctionDef]]:
    """Funkcje, w których niedoręczona wiadomość MUSI cofnąć commit (ADR 0007, fala 4).

    Zakres po funkcji, a nie po całym ``runtime/``, bo są miejsca, które kompensować NIE MOGĄ
    i to jest zamierzone: gałąź ``decline`` ustawia status TERMINALNY (cofnięcie otwierałoby
    domkniętą decyzję), a ``_record_failure`` cofnięciem skasowałby jedyny mechanizm domykający
    pętlę deterministycznego błędu. Wspólne dla objętych: `_commit` utrwala obsługę wiadomości
    pracownika, wpis ZOSTAJE w obiegu, a jedynym powodem, dla którego ruszył do przodu, jest
    wiadomość, która właśnie nie doszła.
    """
    wynik = []
    for nazwa, drzewo in _moduly():
        for wezel in ast.walk(drzewo):
            if isinstance(wezel, ast.FunctionDef) and wezel.name == "_interpret_and_confirm":
                wynik.append((nazwa, wezel))
    return wynik


# Statusy, po których wpis jest TERMINALNY — `state.TERMINALNE`, powtórzone tu jako nazwy, bo
# strażnik czyta drzewo składni, a nie importuje modułu. Wysyłka po `_commit` z takim statusem
# NIE kompensuje i nie powinna: cofnięcie otwierałoby decyzję już domkniętą, a przy `APPLYING`
# — zapis do Shifts, który mógł się już wydarzyć.
_STATUSY_TERMINALNE = {"DECLINED", "APPLIED", "EXPIRED", "SELF_FILLED", "APPLYING"}


def _commit_przed(galezie: list[ast.stmt], blok: ast.Try) -> ast.Call | None:
    """Ostatnie wołanie `_commit` stojące w tej samej gałęzi PRZED danym `try`."""
    znaleziony = None
    for instrukcja in galezie:
        if instrukcja is blok:
            return znaleziony
        for w in ast.walk(instrukcja):
            if isinstance(w, ast.Call) and _nazwa_wolania(w) == "_commit":
                znaleziony = w
    return znaleziony


def _status_terminalny(wolanie: ast.Call | None) -> bool:
    """Czy `_commit` ustawia status terminalny — wtedy kompensacja jest ZAKAZANA, nie wymagana."""
    if wolanie is None:
        return False
    for kw in wolanie.keywords:
        if kw.arg == "status" and isinstance(kw.value, ast.Attribute):
            return kw.value.attr in _STATUSY_TERMINALNE
    return False


def _wycofania_warunkowe(blok: ast.Try) -> list[ast.Call]:
    """Wołania `_wycofaj_commit` w `finalbody`, stojące pod warunkiem `if not <flaga>`.

    Warunek jest częścią niezmiennika, nie stylem: bezwarunkowe wycofanie w `finally` wykonałoby
    się także po UDANEJ wysyłce i skasowałoby poprawną obsługę przy każdej odpowiedzi pracownika.
    To dokładnie ten skutek, dla którego odrzuciliśmy `body` — a w `finally` przechodził
    niezauważony, dopóki ta reguła liczyła same wołania.
    """
    znalezione = []
    for gałąź in blok.finalbody:
        for w in ast.walk(gałąź):
            if not isinstance(w, ast.If) or not isinstance(w.test, ast.UnaryOp):
                continue
            if not isinstance(w.test.op, ast.Not):
                continue
            for c in ast.walk(w):
                if isinstance(c, ast.Call) and _nazwa_wolania(c) == "_wycofaj_commit":
                    znalezione.append(c)
    return znalezione


def _listy_instrukcji(wezel: ast.AST) -> list[list[ast.stmt]]:
    """KAŻDA lista instrukcji pod `wezel` — `body`, ale też `orelse` i `finalbody`.

    Reguła niżej potrzebuje nie samego `Try`, tylko GAŁĘZI, w której on stoi: `_commit_przed`
    szuka poprzedzającego wołania `_commit` w tej samej liście. Pierwsza wersja czytała wyłącznie
    `body`, więc gałąź `else` łańcucha `if/elif/else` była dla reguły NIEWIDZIALNA — a stoi w niej
    jedna z trzech wysyłek, dla których ta reguła powstała (`unclear` w `_interpret_and_confirm`).
    Zieleniała przy zdjęciu kompensacji z tej gałęzi i przy dopisaniu tam czwartej wysyłki bez
    kompensacji; obie mutacje sprawdzone. Strażnik, którego da się obejść nie zauważywszy, usypia.
    """
    listy = []
    for rodzic in ast.walk(wezel):
        for pole in ("body", "orelse", "finalbody"):
            galezie = getattr(rodzic, pole, None)
            if isinstance(galezie, list):
                listy.append(galezie)
    return listy


def test_kazda_wysylka_wymagajaca_kompensacji_ja_ma():
    """Reguła iteruje po WYSYŁKACH, nie po kompensacjach — i to jest jej najważniejsza własność.

    Pierwsza wersja tej reguły (fala 4) chodziła po wołaniach `_wycofaj_commit` i sprawdzała, czy
    każde stoi w `finally`, z kontrolą pozytywną `len(...) == 3`. Zieleniała na dwa sposoby, oba
    potwierdzone mutacją: przy CZWARTEJ wysyłce dopisanej bez kompensacji (nowych wołań nie ma,
    więc trójka się zgadza) oraz przy zdjęciu warunku `if not dostarczono` (wołanie nadal jest
    w `finally`). Strzegła zbioru, który się nie rozszerza, wyglądając na strażnika kompletu.

    Kierunek iteracji jest tu więc całą różnicą: chodzimy po rzeczach CHRONIONYCH, więc nowa
    wysyłka wpada pod regułę bez niczyjej pamięci — tak samo jak w regule wyżej, która była
    zbudowana dobrze od początku.

    Sam kierunek nie wystarczy, jeśli ZASIĘG skanowania pomija część chronionych miejsc — patrz
    `_listy_instrukcji`. Wersja czytająca same `body` nie widziała gałęzi `else`, czyli jednej
    z trzech wysyłek, dla których ta reguła istnieje.
    """
    braki = []
    zbedne = []
    wysylek = 0
    for nazwa, funkcja in _funkcje_kompensujace():
        for galezie in _listy_instrukcji(funkcja):
            for wezel in galezie:
                if not isinstance(wezel, ast.Try):
                    continue
                w_body = [w for b in wezel.body for w in _wysylki(b)]
                if not w_body:
                    continue
                terminalny = _status_terminalny(_commit_przed(galezie, wezel))
                ma = bool(_wycofania_warunkowe(wezel))
                gdzie = f"{nazwa}:{funkcja.name}:try@{wezel.lineno}"
                if terminalny:
                    # Kontrola w DRUGĄ stronę: po statusie terminalnym kompensacja jest zakazana.
                    # Bez niej reguła pilnowałaby tylko jednego kierunku błędu.
                    if ma:
                        zbedne.append(gdzie)
                    continue
                wysylek += len(w_body)
                if not ma:
                    braki.append(gdzie)

    # Kontrola pozytywna BEZ przypiętej liczby: pilnuje, że przedmiot ochrony nie wyparował,
    # ale nie zamraża jego liczności — inaczej sama stałaby się tym, co ta reguła naprawia.
    assert wysylek > 0, "brak wysyłek w funkcjach kompensujących — reguła straciła przedmiot"
    assert braki == [], braki
    assert zbedne == [], zbedne


def test_zadne_wycofanie_nie_stoi_poza_finally():
    """Dopełnienie w drugą stronę: kompensacja poza `finally` nie chroni na każdej drodze wyjścia.

    W `body` wykonałaby się tylko po sukcesie, w `except` ominęłaby wyjątki propagujące bez
    handlera — `NIE_POLYKAJ` przy prośbie o potwierdzenie i wszystko w dwóch pozostałych miejscach,
    które handlera nie mają wcale.
    """
    poza = []
    for nazwa, drzewo in _moduly():
        w_finally = {
            id(c)
            for wezel in ast.walk(drzewo)
            if isinstance(wezel, ast.Try)
            for gałąź in wezel.finalbody
            for w in ast.walk(gałąź)
            if isinstance(w, ast.Call) and _nazwa_wolania(w) == "_wycofaj_commit"
            for c in [w]
        }
        for wezel in ast.walk(drzewo):
            if (
                isinstance(wezel, ast.Call)
                and _nazwa_wolania(wezel) == "_wycofaj_commit"
                and id(wezel) not in w_finally
            ):
                poza.append(f"{nazwa}:{wezel.lineno}")
    assert poza == [], poza
