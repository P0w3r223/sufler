"""`szukaj-pkd` — czysta warstwa wyszukiwania i ekran wyniku (ADR-0026).

Cały plik działa offline, bez tokenu i bez bazy: to jest własność samego polecenia, nie
udogodnienie testu. Test uruchamiający je przez CLI sprawdza tę własność wprost.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ceidg_tool.cli import app, limit_wierszy
from ceidg_tool.pkdmap import TablicaPkd
from ceidg_tool.pkdszukaj import DOMYSLNY_LIMIT, szukaj, zloz
from ceidg_tool.ui import texts

SLOWNIK = {
    "9621Z": "Działalność fryzjerska",
    "9622Z": "Działalność w zakresie pielęgnacji urody",
    "6210B": "Pozostała działalność w zakresie programowania",
    "4120Z": "Roboty budowlane związane ze wznoszeniem budynków",
    "1071Z": "Produkcja pieczywa; produkcja świeżych wyrobów ciastkarskich",
}

TABLICA = TablicaPkd(
    poprzednicy={"9621Z": ("9602Z",), "9622Z": ("9602Z",), "6210B": ("6201Z",)},
    nazwy_2007={"9602Z": "Fryzjerstwo i pozostałe zabiegi kosmetyczne", "6201Z": "Oprogramowanie"},
    nazwy_2025=SLOWNIK,
)


# ----------------------------------------------------------------------------- składanie


def test_fold_handles_the_letter_that_has_no_decomposition() -> None:
    """`ł` i `Ł` nie mają rozkładu kanonicznego — NFKD zostawia je nietknięte.

    Zmierzone 2026-09-23: `unicodedata.normalize("NFKD", "Łódź")` daje `"Łodz"`, nie `"lodz"`.
    Fold zbudowany na samym NFKD przepuszcza więc „ó" i „ź", a na „ł" się zatrzymuje — czyli
    szukanie po „lodz" mija „Łódź" w ciszy. Test nazwany od tej jednej litery, bo to ona jest
    całą treścią usterki i bez nazwy nikt by nie wiedział, czego pilnuje.
    """
    assert zloz("Łódź") == "lodz"
    assert zloz("ŁÓDŹ") == zloz("łódź") == "lodz"
    assert zloz("Odzież") == "odziez"


# ----------------------------------------------------------------------------- kod


def test_a_2007_code_shows_both_branches_it_leads_to() -> None:
    """`9602Z` pokrywa fryzjerstwo **i** kosmetykę — to jest wybór, który stawia `--pkd-2007`.

    `ui/flow` pyta operatora o rocznik dopiero w trakcie pobierania, mając już policzone dwie
    populacje. To polecenie pozwala mu przyjść na tamto pytanie przygotowanym, a gubienie
    którejkolwiek z gałęzi odbierałoby mu dokładnie tę informację.
    """
    wynik = szukaj("9602Z", SLOWNIK, TABLICA)

    assert wynik.odczytano_jako == "kod"
    assert len(wynik.trafienia) == 1
    assert wynik.trafienia[0].rocznik == 2007
    assert [k for k, _ in wynik.trafienia[0].nastepcy] == ["9621Z", "9622Z"]


def test_a_2025_code_shows_what_the_2007_switch_would_add() -> None:
    wynik = szukaj("96.21.Z", SLOWNIK, TABLICA)

    assert wynik.odczytano_jako == "kod"
    trafienie = wynik.trafienia[0]
    assert trafienie.kod == "9621Z" and trafienie.rocznik == 2025
    assert [p.kod for p in trafienie.poprzednicy] == ["9602Z"]
    # Niejednoznaczność jest tym, co operator ma zobaczyć: ten sam stary kod prowadzi też gdzie
    # indziej, więc `--pkd-2007` dołoży cudzą branżę.
    assert [k for k, _ in trafienie.poprzednicy[0].rowniez] == ["9622Z"]


@pytest.mark.parametrize("zapis", ["6210B", "6210b", "62.10.B", "62.10.b"])
def test_every_spelling_of_a_code_behaves_as_it_does_in_a_query(zapis: str) -> None:
    """Ta sama normalizacja co `--pkd`, więc nie ma dwóch różnych rozumień jednego kodu."""
    assert szukaj(zapis, SLOWNIK, TABLICA).trafienia[0].kod == "6210B"


def test_an_unknown_code_is_not_answered_with_phrase_advice() -> None:
    """Kod spoza obu roczników dostaje zdanie o kodzie, nie „spróbuj innego słowa".

    Zastrzeżenie nie na temat uczy pomijać zastrzeżenia, a to polecenie ma ich dwa, z których
    jedno jest nośne (rocznik).
    """
    blok = texts.pkd_search(szukaj("9999Z", SLOWNIK, TABLICA))

    assert any("ani w PKD 2025" in n for n in blok.notes)
    assert not any("innego słowa" in n for n in blok.notes)
    assert not any("w nazwach, nie w znaczeniach" in n for n in blok.notes)


# ----------------------------------------------------------------------------- fraza


def test_a_phrase_matches_names_case_and_diacritic_insensitively() -> None:
    assert [t.kod for t in szukaj("FRYZJERSK", SLOWNIK, TABLICA).trafienia] == ["9621Z"]
    assert [t.kod for t in szukaj("pieczywa", SLOWNIK, TABLICA).trafienia] == ["1071Z"]


def test_a_phrase_result_is_capped_and_says_so() -> None:
    """Sto wierszy w tabeli `rich` wypycha trafienie poza górną krawędź terminala.

    Ta sama klasa defektu co zawijana ścieżka pliku w podsumowaniu (ADR-0024): wynik jest
    poprawny i nie do przeczytania.
    """
    duzy = {f"01{n:02d}Z": f"Uprawa rośliny numer {n}" for n in range(60)}

    wynik = szukaj("uprawa", duzy, None)

    assert len(wynik.trafienia) == DOMYSLNY_LIMIT
    assert wynik.wszystkich == 60
    assert wynik.obciete
    assert any("--wszystkie" in n for n in texts.pkd_search(wynik).notes)


def test_wszystkie_lifts_the_cap() -> None:
    duzy = {f"01{n:02d}Z": f"Uprawa rośliny numer {n}" for n in range(60)}

    wynik = szukaj("uprawa", duzy, None, limit=limit_wierszy(True))

    assert len(wynik.trafienia) == 60 and not wynik.obciete


def test_the_transition_caveat_is_on_every_screen() -> None:
    """Zdanie o roczniku jest powodem, dla którego to polecenie nie jest ozdobą.

    Kod bywa ważny, a zapytanie i tak niepełne — 8,6 % rejestru jest nieosiągalne żadnym kodem
    z PKD 2025. Ekran, który o tym milczy przy pustym wyniku, jest najbardziej mylący właśnie
    wtedy, bo operator czyta „nie ma takiej branży".
    """
    for zapytanie in ("fryzjer", "9621Z", "9999Z", "czegoś takiego nie ma"):
        blok = texts.pkd_search(szukaj(zapytanie, SLOWNIK, TABLICA))

        assert any("jeden rocznik" in n for n in blok.notes), zapytanie


# ----------------------------------------------------------------------------- polecenie


def test_the_command_needs_no_token_no_network_and_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trzy twierdzenia ADR-0026 decyzji 5 w jednej asercji — katalog danych nie powstaje.

    `_settings()` rozwiązuje token i potrafi się wywalić, a szukanie w pliku leżącym w pakiecie
    nie ma prawa wymagać poświadczenia; to jest też pierwsze polecenie, które ktoś uruchamia,
    konfigurując narzędzie. Gdyby `build_deps` albo `setup_logging` tu wpełzły, katalog by
    powstał i ten test by to zobaczył.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CEIDG_TOKEN", raising=False)
    monkeypatch.setenv("CEIDG_DATA_DIR", str(tmp_path / "dane"))

    wynik = CliRunner().invoke(app, ["szukaj-pkd", "9621Z"])

    assert wynik.exit_code == 0, wynik.output
    assert "9621Z" in wynik.output
    assert not (tmp_path / "dane").exists()


def test_the_command_has_no_demo_flag() -> None:
    """ADR-0026 decyzja 5: znacznik bez czego oznaczać.

    Znaczniki z ADR-0014 oddzielają dane rejestru od wymyślonych, a to polecenie rejestru nie
    dotyka — `--demo` byłby tu ozdobą, a ozdobny znacznik osłabia pozostałe pięć.
    """
    wynik = CliRunner().invoke(app, ["szukaj-pkd", "--help"])

    assert "--demo" not in wynik.output
