"""Strażnik metadanej ``ToolSpec.taints`` — iteruje po NARZĘDZIACH, nie po wyzwalaczach (ADR 0073).

Poprzednik tej bramki chodził po zbiorze ``_TAINTING_TOOLS`` i pytał, czy każdy jego element
pochodzi z któregoś z CZTERECH builderów wypisanych z palca. Builderów jest piętnaście. Bramka
wyglądała na strażnika kompletu, a strzegła zbioru, który sam się nie rozszerza: narzędzie
dołożone gdziekolwiek indziej wchodziło bez pytania, a jedynym objawem byłaby skaza, która nigdy
się nie zapala.

Kierunek jest tu odwrócony. Sonda znajduje **każde** wywołanie ``ToolSpec(...)`` w pakiecie
``core/application/tools/`` i dla każdego żąda odpowiedzi na pytanie ADR 0066 R2: czy WYNIK tego
narzędzia niesie treść spoza bramek zdolności. Nowe narzędzie nie ma jak się prześlizgnąć —
nie dlatego, że ktoś pamiętał o dopisaniu go do listy, tylko dlatego, że bez odpowiedzi bramka
pada i wskazuje plik z linią.

Czytamy AST, nie wywołujemy builderów: nazwa i odpowiedź stoją w tym samym wywołaniu, jako
literały. Dzięki temu sonda nie potrzebuje ani jednej atrapy serwisu — a to ona decyduje, czy
strażnik obejmuje CAŁY pakiet, czy tylko tę jego część, dla której dało się wyklikać fikstury.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PAKIET = Path(__file__).resolve().parents[2] / "src" / "sufler" / "core" / "application" / "tools"

# Odpowiedź dla KAŻDEGO narzędzia budowanego w pakiecie. Dopisanie wpisu jest darmowe; jego brak
# zrywa bramkę razem ze wskazaniem pliku i linii. ``True`` = wynik niesie treść spoza bramek
# zdolności (ADR 0066 R2), więc rozmowa dostaje lepką skazę.
#
# Uzasadnienia, których nie widać z samej wartości:
# * ``ReadFile``/``ListFiles`` — brzmią niewinnie, ale katalog roboczy trzyma ODŁOŻONE ZAŁĄCZNIKI
#   (ADR 0064) i przeżywa rollover, bo jest per (kanał, wątek);
# * ``CreateFile`` — model zapisuje WŁASNĄ treść, niczego nie wciąga;
# * ``read_events_since`` — ta sama warstwa zdarzeń co ``Activity``, więc ta sama odpowiedź.
#   Powierzchnia MCP nie ma rozmowy, którą można skazić, ale metadana opisuje NARZĘDZIE, nie
#   powierzchnię — inaczej ta sama treść miałaby dwie odpowiedzi zależnie od drzwi;
# * ``Jira``/``get_my_jira_*`` — ``True`` od 2026-09-09 (dopisek do ADR 0066). Akcja ``task``
#   oddaje ``description`` i pięć komentarzy DOSŁOWNIE, pisanych przez kogokolwiek z kontem
#   w Jirze — ta sama klasa autorstwa, co treści zgłoszeń GitHuba, wymienione w zbiorze
#   wyzwalaczy wprost, i co ``Activity``, które ma ``True`` od początku. Wcześniejsze ``False``
#   uzasadniano tym, że to „treść zza bramek zdolności" — ale bramka zdolności mówi, KTO MOŻE
#   ZAWOŁAĆ narzędzie, a wyzwalacz pyta, KTO NAPISAŁ TREŚĆ;
# * ``Schedule``/``Project``/notatki — treść WŁASNA pionu, czytana typowanymi narzędziami.
#   Gdyby skaziły, każda rozmowa byłaby skażona i sygnał nie znaczyłby nic (ADR 0066 R2).
_ODPOWIEDZI = {
    "Activity": True,
    "Bash": True,
    "File": True,
    "Jira": True,
    "ListFiles": True,
    "ReadFile": True,
    "get_my_jira_history": True,
    "get_my_jira_tasks": True,
    "read_events_since": True,
    "CreateFile": False,
    "Project": False,
    "ReplyWithFile": False,
    "Schedule": False,
    "SendDocument": False,
    "SendImage": False,
    "get_note": False,
    "get_project_status": False,
    "list_projects": False,
    "save_note": False,
    "search_notes": False,
}


def _wywolania_toolspec() -> list[tuple[str, int, str | None, bool | None]]:
    """Każde ``ToolSpec(...)`` w pakiecie: (plik, linia, nazwa narzędzia, wartość ``taints``).

    ``None`` w dwóch ostatnich polach znaczy „nie literał" — sonda ma o tym meldować, a nie
    milcząco pomijać: wywołanie zbudowane dynamicznie wymyka się tej bramce, więc ma zwrócić
    uwagę człowieka, zanim stanie się drogą obok niej.
    """
    znalezione: list[tuple[str, int, str | None, bool | None]] = []
    for plik in sorted(_PAKIET.rglob("*.py")):
        drzewo = ast.parse(plik.read_text(encoding="utf-8"), filename=str(plik))
        for wezel in ast.walk(drzewo):
            if not isinstance(wezel, ast.Call):
                continue
            wolany = wezel.func
            if not (isinstance(wolany, ast.Name) and wolany.id == "ToolSpec"):
                continue
            nazwa = (
                wezel.args[0].value
                if wezel.args
                and isinstance(wezel.args[0], ast.Constant)
                and isinstance(wezel.args[0].value, str)
                else None
            )
            taints: bool | None = None
            for kw in wezel.keywords:
                if kw.arg == "taints" and isinstance(kw.value, ast.Constant):
                    taints = bool(kw.value.value)
            znalezione.append((plik.name, wezel.lineno, nazwa, taints))
    return znalezione


def test_pakiet_narzedzi_w_ogole_ma_wywolania_do_sprawdzenia():
    """Kontrola pozytywna samej sondy: pusty wynik przechodziłby każdą asercję niżej.

    To jest ta sama wada, którą przegląd 2026-09-07 złapał w sondzie preflightu paczki —
    sonda konstrukcyjnie ślepa na to, o co pyta, wygląda identycznie jak działająca.
    """
    assert len(_wywolania_toolspec()) >= 20


def test_kazde_narzedzie_odpowiada_na_pytanie_o_pochodzenie_wyniku():
    """Cisza jest zabroniona: ``taints=`` ma być WYPISANE przy każdym ``ToolSpec`` w pakiecie.

    Domyślne ``False`` w dataklasie jest wygodą dla atrap w testach, nie odpowiedzią. Tutaj
    domysł jest gorszy od braku bramki: wygląda jak decyzja, a jest przeoczeniem.
    """
    bez_odpowiedzi = [
        f"{plik}:{linia}" for plik, linia, _nazwa, taints in _wywolania_toolspec() if taints is None
    ]
    assert not bez_odpowiedzi, "ToolSpec bez wypisanego taints= (ADR 0073): " + ", ".join(
        bez_odpowiedzi
    )


def test_nazwa_narzedzia_jest_literalem():
    """Nazwa zbudowana dynamicznie wymyka się i tej bramce, i bramce opisów — ma być widoczna."""
    dynamiczne = [
        f"{plik}:{linia}" for plik, linia, nazwa, _t in _wywolania_toolspec() if nazwa is None
    ]
    assert not dynamiczne, "ToolSpec o nazwie spoza literału: " + ", ".join(dynamiczne)


def test_ta_sama_nazwa_ma_te_sama_odpowiedz_w_kazdym_wariancie():
    """``File`` powstaje w trzech wariantach bramkowania, ``Project`` w dwóch.

    Wariant to inny ZAKRES AKCJI, nie inne pochodzenie wyniku. Rozjazd między wariantami byłby
    skazą zależną od tego, którą bramkę operator akurat otworzył — czyli od czegoś, czego nikt
    nie wiąże z pochodzeniem treści.
    """
    po_nazwie: dict[str, set[bool]] = {}
    for _plik, _linia, nazwa, taints in _wywolania_toolspec():
        if nazwa is not None and taints is not None:
            po_nazwie.setdefault(nazwa, set()).add(taints)
    rozjazd = {nazwa: wartosci for nazwa, wartosci in po_nazwie.items() if len(wartosci) > 1}
    assert not rozjazd, f"ta sama nazwa, różne odpowiedzi: {rozjazd}"


@pytest.mark.parametrize("nazwa,taints", sorted(_ODPOWIEDZI.items()))
def test_odpowiedz_zgadza_sie_z_rejestrem(nazwa: str, taints: bool):
    """Rejestr trzyma odpowiedzi w jednym czytelnym miejscu — przestawienie flagi w kodzie
    ma zerwać bramkę, a nie przejść jako szczegół diffu."""
    w_kodzie = {n: t for _p, _l, n, t in _wywolania_toolspec() if n is not None and t is not None}
    assert w_kodzie[nazwa] is taints


def test_rejestr_pokrywa_kazde_narzedzie_pakietu():
    """Komplet w obie strony: narzędzie bez wpisu i wpis bez narzędzia to ta sama wada —
    rejestr, który przestał opisywać kod."""
    w_kodzie = {n for _p, _l, n, _t in _wywolania_toolspec() if n is not None}
    assert w_kodzie == set(_ODPOWIEDZI)
