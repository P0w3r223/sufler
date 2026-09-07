"""Strażnik źródła świeżości grafiku: ścieżka ZAPISU czyta ze snapshotu przebiegu (ADR 0009).

Od 0.2.20 istnieją dwa źródła danych o grafiku i różnią się STAWKĄ, nie mechaniką:

* ``SnapshotGrafiku`` — odczyt najwyżej raz na PRZEBIEG. Karmi sprawdzenie świeżości tuż przed
  nieodwracalnym zapisem do Shifts. Nieświeże dane znaczą tu DRUGI komplet wpisów w grafiku
  klienta, nie do cofnięcia.
* ``PamiecSamouzupelnien`` — wyniki ważne przez godziny, między przebiegami. Karmi wyłącznie
  krok 1.5, czyli podziękowanie dla kogoś, kto uzupełnił grafik sam. Nieświeże dane znaczą tu
  podziękowanie o obieg późniejsze.

Podmiana źródła w drugą stronę jest jedną linią i nie zapala żadnego testu zachowania — dopóki
ktoś nie potwierdzi „tak" na tygodniu, którego pamięć nie odświeżyła. Dlatego reguła iteruje po
ODCZYTACH GRAFIKU (rzecz chroniona), a nie po miejscach, w których pamięć wolno wołać.

`mypy` domyka to samo od strony typów: ``PamiecSamouzupelnien`` nie ma metod ``dla_tygodnia``
ani ``dla_tygodni``. Dwa niezależne sygnały na jedną pomyłkę są tu celowe.
"""

from __future__ import annotations

import ast
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parent.parent / "src" / "powiadomienia_teams" / "runtime"

#: Metody odczytu grafiku ze snapshotu przebiegu.
_ODCZYT_SNAPSHOTU = {"dla_tygodnia", "dla_tygodni"}

#: Nazwa odbiornika, pod którą wolno je wołać. Jedna, bo nazwa jest tu dokumentacją: `snapshot`
#: mówi „to źródło żyje jeden przebieg".
_ODBIORNIK = "snapshot"

#: Moduł DEFINIUJĄCY snapshot jest poza regułą: `dla_tygodni` woła tam `self.dla_tygodnia`,
#: co nie jest wyborem źródła, tylko wnętrzem klasy. Reguła dotyczy WOŁAJĄCYCH.
_POZA_REGULA = {"snapshot.py"}

#: Jedyna funkcja, której wolno sięgnąć do pamięci międzyprzebiegowej. Zbiór, nie napis, żeby
#: rozszerzenie było widoczne w diffie tego pliku — a nie ukryte w nowym wołaniu w `runtime/`.
_WOLNO_PAMIEC = {"_grafik_kroku_1_5"}

#: Funkcje ścieżki nieodwracalnego zapisu. Pamięć nie ma prawa w nich wystąpić.
_SCIEZKA_ZAPISU = {"_odsiej_juz_zapisane", "_apply_schedule", "_apply_confirmed_yes"}

#: Funkcje kroku 1.5 — tego, dla którego pamięć w ogóle powstała. ADR 0009 kończy się zdaniem
#: „jeśli przyszła zmiana każe krokowi 1.5 cokolwiek zapisać do Shifts, przesłanka tej decyzji
#: znika". Reguła niżej zamienia to zdanie w bramkę: proza nie jest bramką.
_KROK_1_5 = {
    "poll_replies",
    "_grafik_kroku_1_5",
    "_uzupelnili_sami",
    "zamknij_samodzielnie_uzupelnione",
    "podziekuj_za_samodzielne_uzupelnienie",
}

#: Metody, których wywołanie znaczy „piszemy do grafiku klienta".
_ZAPIS_DO_SHIFTS = {"create_shift", "create_time_off", "_apply_schedule"}


def _moduly() -> list[tuple[str, ast.Module]]:
    return [
        (p.name, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(_RUNTIME.glob("*.py"))
    ]


def _funkcja_dla_wezla(drzewo: ast.Module) -> dict[int, str]:
    """Mapa id(węzeł) → nazwa otaczającej funkcji, dla każdego węzła pod definicją funkcji."""
    gdzie: dict[int, str] = {}
    for funkcja in ast.walk(drzewo):
        if not isinstance(funkcja, ast.FunctionDef):
            continue
        for wezel in ast.walk(funkcja):
            gdzie.setdefault(id(wezel), funkcja.name)
    return gdzie


def test_odczyt_grafiku_idzie_wylacznie_ze_snapshotu():
    """Każde ``.dla_tygodnia``/``.dla_tygodni`` w ``runtime/`` woła się na `snapshot`."""
    obce = []
    czytajace: set[str] = set()
    for nazwa, drzewo in _moduly():
        if nazwa in _POZA_REGULA:
            continue
        gdzie = _funkcja_dla_wezla(drzewo)
        for wezel in ast.walk(drzewo):
            if not isinstance(wezel, ast.Call) or not isinstance(wezel.func, ast.Attribute):
                continue
            if wezel.func.attr not in _ODCZYT_SNAPSHOTU:
                continue
            czytajace.add(gdzie.get(id(wezel), "<poziom modułu>"))
            odbiornik = wezel.func.value
            if not (isinstance(odbiornik, ast.Name) and odbiornik.id == _ODBIORNIK):
                opis = getattr(odbiornik, "id", type(odbiornik).__name__)
                obce.append(f"{nazwa}:{wezel.lineno} — odbiornik {opis!r}, nie {_ODBIORNIK!r}")
    assert obce == [], obce
    # Zapadka na przedmiot ochrony. Samo `odczytow > 0` trzymałyby przy życiu wołania kroku 1.5,
    # więc wyprowadzenie ścieżki ZAPISU poza `runtime/` odebrałoby regule przedmiot po cichu.
    assert "_odsiej_juz_zapisane" in czytajace, (
        f"ścieżka zapisu nie czyta już grafiku w `runtime/` (czytają: {sorted(czytajace)}) — "
        f"reguła pilnowałaby wtedy wyłącznie kroku 1.5"
    )


def test_pamiec_wolana_tylko_z_jednego_miejsca():
    """Pamięć międzyprzebiegowa ma jedno wejście — rozszerzenie musi być widoczne TUTAJ."""
    obce = []
    wolan = 0
    for nazwa, drzewo in _moduly():
        gdzie = _funkcja_dla_wezla(drzewo)
        for wezel in ast.walk(drzewo):
            if not isinstance(wezel, ast.Call) or not isinstance(wezel.func, ast.Attribute):
                continue
            if wezel.func.attr != "dla_samouzupelnienia":
                continue
            wolan += 1
            funkcja = gdzie.get(id(wezel), "<poziom modułu>")
            if funkcja not in _WOLNO_PAMIEC:
                obce.append(f"{nazwa}:{wezel.lineno} — w funkcji {funkcja!r}")
    assert wolan > 0, "nikt nie woła pamięci — czy nazwa metody się zmieniła?"
    assert obce == [], obce


def test_sciezka_zapisu_nie_zna_pamieci():
    """Funkcje poprzedzające nieodwracalny zapis nie mają prawa nawet WYMIENIĆ pamięci.

    Kierunek zakazu jest szerszy niż samo wołanie: przekazanie pamięci dalej jako argumentu
    wystarczyłoby, żeby świeżość przed `create_shift` przestała być świeżością.
    """
    obce = []
    zbadanych = 0
    for nazwa, drzewo in _moduly():
        for funkcja in ast.walk(drzewo):
            if not isinstance(funkcja, ast.FunctionDef) or funkcja.name not in _SCIEZKA_ZAPISU:
                continue
            zbadanych += 1
            for wezel in ast.walk(funkcja):
                trafienie = (isinstance(wezel, ast.Name) and wezel.id == "pamiec") or (
                    isinstance(wezel, ast.arg) and wezel.arg == "pamiec"
                )
                if trafienie:
                    obce.append(f"{nazwa}:{funkcja.name} — wymienia `pamiec`")
    assert zbadanych == len(_SCIEZKA_ZAPISU), (
        f"znaleziono {zbadanych} z {len(_SCIEZKA_ZAPISU)} funkcji ścieżki zapisu — "
        f"czy któraś została przemianowana? reguła mierzyłaby wtedy mniej, niż deklaruje"
    )
    assert obce == [], obce


def test_krok_1_5_nie_pisze_do_shifts():
    """Zdanie z końca ADR 0009 zamienione w bramkę.

    ADR mówi: *„jeśli przyszła zmiana każe krokowi 1.5 cokolwiek zapisać do Shifts, przesłanka tej
    decyzji znika"* — bo cała zgoda na nieświeże dane stoi na tym, że najgorszym skutkiem jest
    podziękowanie, a nie wpis w grafiku klienta. Przeglądowi udało się wstawić pętlę
    `create_shift` w krok 1.5 przy wszystkich trzech pozostałych regułach ZIELONYCH; to jest ta
    czwarta.
    """
    obce = []
    zbadanych: set[str] = set()
    for nazwa, drzewo in _moduly():
        for funkcja in ast.walk(drzewo):
            if not isinstance(funkcja, ast.FunctionDef) or funkcja.name not in _KROK_1_5:
                continue
            zbadanych.add(funkcja.name)
            for wezel in ast.walk(funkcja):
                if not isinstance(wezel, ast.Call):
                    continue
                wolane = getattr(wezel.func, "attr", None) or getattr(wezel.func, "id", None)
                if wolane in _ZAPIS_DO_SHIFTS:
                    obce.append(f"{nazwa}:{funkcja.name}:{wezel.lineno} — woła {wolane!r}")
    assert zbadanych == _KROK_1_5, (
        f"znaleziono {sorted(zbadanych)} z {sorted(_KROK_1_5)} — czy któraś funkcja kroku 1.5 "
        f"została przemianowana? reguła mierzyłaby wtedy mniej, niż deklaruje"
    )
    assert obce == [], obce
