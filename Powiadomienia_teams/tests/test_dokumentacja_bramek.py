"""Strażnik prozy o bramkach: liczby przechodzących testów nie mają prawa mieszkać w dokumentacji.

Dwie reguły, obie iterujące po rzeczach CHRONIONYCH, nie po zabezpieczeniach — ta sama zasada,
którą stoi ``test_szew_wysylki.py``: nowy plik prozy i nowy znacznik ``xfail`` wpadają pod regułę
bez niczyjej pamięci.

Powód, dla którego to w ogóle powstało: 2026-09-07 ``README.md`` twierdził w trzech miejscach
„403 passed, 10 xfailed" i „dziś nie przechodzi", podczas gdy zestaw dawał 459 przechodzących
i zero ``xfail``. Dwa sąsiadujące zdania przeczyły sobie nawzajem. Korzeniowy ``CLAUDE.md`` liczb
świadomie NIE podaje i mówi dlaczego — „zmienia się przy każdej naprawie, więc byłaby rozjazdem
z założenia". Ta reguła robi z tamtego zdania bramkę.

**Trzeciej reguły — spinającej wzmiankę o ``xfail`` z istnieniem znaczników — świadomie NIE ma.**
Konwencja ``xfail`` ma przeżyć brak przedmiotu: README opisuje ją w formie warunkowej („jeśli
stanie tam ``xfail``, ma mieć ``strict=True``"), a taki akapit jest wart utrzymania także wtedy,
gdy znaczników jest zero — czyli dziś. Reguła żądająca zgodności zapaliłaby się na poprawnym
tekście i kusiła, żeby ten tekst usunąć.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_KORZEN = Path(__file__).resolve().parent.parent
_TESTY = _KORZEN / "tests"

#: Pliki prozy pod-projektu objęte zakazem liczb. ``CHANGELOG.md`` jest POZA zakresem świadomie:
#: liczba przy WYDANIU jest faktem historycznym o tamtym momencie, a nie twierdzeniem o dzisiejszym
#: zestawie — i nie dezaktualizuje się.
_PROZA = (
    "README.md",
    "deploy/README-docker.md",
    "deploy/README-serwer.md",
    "deploy/DO-WYKONANIA.md",
)

#: Kształt podsumowania pytesta. Dopasowuje „403 passed", „10 xfailed", „2 failed" — a przepuszcza
#: narrację w rodzaju „bramka zaliczyła 792 testy", bo tamto nie jest twierdzeniem o wyniku biegu,
#: który da się dziś powtórzyć.
_WZORZEC_LICZBY = re.compile(r"\b\d+\s+(passed|failed|xfailed|xpassed|skipped|errors?)\b")


def _pliki_prozy() -> list[Path]:
    return [sciezka for nazwa in _PROZA if (sciezka := _KORZEN / nazwa).exists()]


def _znaczniki_xfail() -> list[tuple[str, int, ast.Call | ast.Attribute]]:
    """Każde użycie ``pytest.mark.xfail`` w ``tests/`` — z plikiem i linią.

    Iteracja idzie po ZNACZNIKACH, więc znacznik dopisany jutro w nowym pliku jest objęty regułą
    bez dopisywania czegokolwiek tutaj.

    Iterujemy po ATRYBUTACH ``…xfail``, a wołanie odzyskujemy z mapy — nie odwrotnie. Przejście
    po ``ast.walk`` w drugą stronę odwiedza ``Call`` ORAZ jego ``func``, więc poprawny
    ``pytest.mark.xfail(strict=True, reason=…)`` trafiał tam dwa razy: raz jako wołanie i raz jako
    goły atrybut, czyli jako „xfail bez argumentów". Pierwsza wersja tak właśnie robiła i oskarżyła
    jedyny poprawny znacznik w repozytorium. Atrybut występuje raz na wystąpienie, więc ta strona
    nie ma jak policzyć podwójnie.
    """
    znalezione: list[tuple[str, int, ast.Call | ast.Attribute]] = []
    for plik in sorted(_TESTY.rglob("*.py")):
        drzewo = ast.parse(plik.read_text(encoding="utf-8"))
        wolania = {id(w.func): w for w in ast.walk(drzewo) if isinstance(w, ast.Call)}
        for wezel in ast.walk(drzewo):
            if not (isinstance(wezel, ast.Attribute) and wezel.attr == "xfail"):
                continue
            znacznik: ast.Call | ast.Attribute = wolania.get(id(wezel), wezel)
            znalezione.append((plik.name, znacznik.lineno, znacznik))
    return znalezione


def test_kazdy_xfail_jest_strict_i_niesie_powod():
    """``xfail`` bez ``strict=True`` nie zapali się przy naprawie usterki, więc ją ukrywa.

    Bez ``reason`` znacznik nie mówi, CO jest zepsute — a to jedyna informacja, dla której te
    znaczniki w tym repozytorium w ogóle istniały (opisywały dziewięć usterek obrazu 0.2.19).
    """
    wadliwe = []
    for nazwa, linia, wezel in _znaczniki_xfail():
        if not isinstance(wezel, ast.Call):
            wadliwe.append(f"{nazwa}:{linia} — `xfail` bez argumentów (brak strict i reason)")
            continue
        argumenty = {kw.arg: kw.value for kw in wezel.keywords}
        strict = argumenty.get("strict")
        if not (isinstance(strict, ast.Constant) and strict.value is True):
            wadliwe.append(f"{nazwa}:{linia} — brak `strict=True`")
        powod = argumenty.get("reason")
        pusty = not (
            isinstance(powod, ast.Constant) and isinstance(powod.value, str) and powod.value.strip()
        )
        if pusty:
            wadliwe.append(f"{nazwa}:{linia} — brak niepustego `reason`")
    assert wadliwe == [], wadliwe


def test_proza_nie_podaje_liczb_bramki():
    """Liczba przechodzących testów w prozie dezaktualizuje się przy pierwszej naprawie.

    Reguła iteruje po PLIKACH PROZY, więc nowy plik dokumentacji wpada pod nią automatycznie —
    pod warunkiem dopisania go do ``_PROZA``; sonda niżej pilnuje, żeby ta lista nie wyparowała.
    """
    pliki = _pliki_prozy()
    if not pliki:
        pytest.skip("strażnik prozy — poza kontekstem repozytorium (obraz); biegnie w CI")
    assert len(pliki) >= 2, "lista plików prozy skurczyła się — reguła traci przedmiot ochrony"

    trafienia = []
    for sciezka in pliki:
        for numer, wiersz in enumerate(sciezka.read_text(encoding="utf-8").splitlines(), 1):
            if dopasowanie := _WZORZEC_LICZBY.search(wiersz):
                trafienia.append(
                    f"{sciezka.relative_to(_KORZEN)}:{numer} — {dopasowanie.group(0)!r}; "
                    f"liczby bramki przelicza `uv run pytest`, nie dokumentacja"
                )
    assert trafienia == [], trafienia
