"""Strażnik kierunku zależności między pakietami — obiecany w docstringu `agent/odczyt.py`.

`runtime` importuje `agent` (listener woła interpreter i narzędzia), więc import w drugą stronę
zamknąłby cykl. Dlatego `agent.odczyt.ZrodloTygodnia` jest `Protocol`, a nie importem
`runtime.snapshot`, a wspólny typ danych mieszka w `domain`, dokąd wolno sięgać obu warstwom.

Reguła iteruje po PAKIETACH, nie po znanej liście zakazanych par: nowy pakiet w `src/` wchodzi
do grafu sam i cykl z jego udziałem zapala test bez niczyjej pamięci.
"""

from __future__ import annotations

import ast
from pathlib import Path

_PAKIET = "powiadomienia_teams"
_SRC = Path(__file__).resolve().parent.parent / "src" / _PAKIET


def _warstwa(sciezka: Path) -> str:
    """Węzeł grafu: nazwa pod-pakietu albo — dla modułu stojącego wprost w pakiecie — jego nazwa.

    Moduły korzenia (`config`, `state`, `messages`, `alerts`) są OSOBNYMI węzłami, nie jednym
    workiem. Zlanie ich w jeden dawało cykle wyłącznie z tego zlania: `config` importuje `domain`,
    a `domain` nic z korzenia — ale wspólny węzeł „korzeń" pokazywał to jako pętlę.
    """
    wzgledna = sciezka.relative_to(_SRC)
    return wzgledna.parts[0] if len(wzgledna.parts) > 1 else wzgledna.stem


def _graf() -> dict[str, set[str]]:
    """Kto kogo importuje, w rozdzielczości POD-PAKIETU (import wewnątrz warstwy pomijamy)."""
    graf: dict[str, set[str]] = {}
    for plik in sorted(_SRC.rglob("*.py")):
        skad = _warstwa(plik)
        drzewo = ast.parse(plik.read_text(encoding="utf-8"))
        for wezel in ast.walk(drzewo):
            moduly: list[str] = []
            if isinstance(wezel, ast.ImportFrom) and wezel.module:
                moduly.append(wezel.module)
            elif isinstance(wezel, ast.Import):
                moduly.extend(alias.name for alias in wezel.names)
            for modul in moduly:
                czesci = modul.split(".")
                if czesci[0] != _PAKIET or len(czesci) < 2:
                    continue
                dokad = czesci[1]
                if dokad != skad and dokad != "__init__":
                    graf.setdefault(skad, set()).add(dokad)
    return graf


def _cykle(graf: dict[str, set[str]]) -> list[list[str]]:
    """Wszystkie cykle w grafie — zwykły DFS, bo warstw jest siedem, nie siedem tysięcy."""
    znalezione: list[list[str]] = []

    def idz(wezel: str, sciezka: list[str]) -> None:
        for nastepny in sorted(graf.get(wezel, ())):
            if nastepny in sciezka:
                cykl = sciezka[sciezka.index(nastepny) :] + [nastepny]
                if cykl not in znalezione:
                    znalezione.append(cykl)
                continue
            idz(nastepny, sciezka + [nastepny])

    for start in sorted(graf):
        idz(start, [start])
    return znalezione


def test_pakiety_nie_tworza_cyklu():
    """Cykl między pakietami znaczy, że warstwy przestały być warstwami."""
    graf = _graf()
    assert graf, "graf zależności jest pusty — reguła nie dotknęła przedmiotu pomiaru"
    assert len(graf) >= 3, f"graf ma tylko {len(graf)} warstw — czy `_warstwa` nadal działa?"
    assert _cykle(graf) == [], _cykle(graf)


def test_agent_nie_siega_do_runtime():
    """Kierunek jest jednostronny i to jest cała cena `Protocol` w `agent/odczyt.py`.

    Test stoi OBOK reguły o cyklach, a nie zamiast niej: cykl złapałby dopiero import w obie
    strony, a już sam import `runtime` z `agent` odbiera sens tamtemu rozwiązaniu.
    """
    assert "runtime" not in _graf().get("agent", set())
