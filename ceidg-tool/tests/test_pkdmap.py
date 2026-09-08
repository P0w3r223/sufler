"""Tablica przejścia PKD 2007 → 2025 jako mechanizm (ADR-0012).

Tu pytamy „czy kod robi to, co obiecuje" na tablicy trzyelementowej; czy to, co leży
w pakiecie, zgadza się z klasyfikacją, pyta osobno `tests/test_pkdmap_data.py`. Podział jest
ten sam, co między `test_assistant.py` a `test_assistant_pkd_data.py`, i z tego samego powodu:
test na pełnych danych, który jednocześnie sprawdza logikę, przestaje mówić, co się zepsuło.

Dwie rzeczy są tu nośne. Pierwsza: podział na rozszerzenia czyste i niejednoznaczne jest
**liczony z zawartości** tablicy i zależy od **wybranego zestawu kodów** — ta sama tablica
odpowiada inaczej, gdy operator poprosił o obie branże naraz. Druga: uszkodzony plik danych
ma padać jako `ConfigError` z polskim zdaniem. `normalize_pkd` rzuca `ValueError`, którego
`cli.py` nie łapie, więc każdy przeciek tego wyjątku to ślad stosu zamiast komunikatu —
w tym repozytorium już raz tak było.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ceidg_tool.errors import ConfigError
from ceidg_tool.pkdmap import KONIEC_PRZEJSCIA, TablicaPkd, load_pkd_map
from tests.support import pkd_map

DOBRA_TABLICA: dict[str, object] = {
    "poprzednicy": {"9621Z": ["9602Z"], "9622Z": ["9602Z"], "1423Z": ["1412Z"]},
    "nazwy_2007": {
        "9602Z": "Fryzjerstwo i pozostałe zabiegi kosmetyczne",
        "1412Z": "Produkcja odzieży roboczej",
    },
    "nazwy_2025": {
        "9621Z": "Fryzjerstwo",
        "9622Z": "Pielęgnacja urody",
        "1423Z": "Produkcja odzieży roboczej",
    },
}


def zapisz(tmp_path: Path, dane: object) -> Path:
    path = tmp_path / "pkd2007_2025.yaml"
    path.write_text(yaml.safe_dump(dane, allow_unicode=True), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------- rozszerzanie


def test_a_code_outside_the_table_expands_to_nothing() -> None:
    """Najczęstszy przypadek to brak zmiany — 464 z 728 podklas nie potrzebują niczego.

    Gdyby kod spoza tablicy był błędem, filtr PKD przestałby działać dla większości branż.
    """
    rozsz = pkd_map().rozszerz(["0111Z"])

    assert rozsz.is_empty()
    assert rozsz.kody_2007 == ()
    assert rozsz.wymaga_pytania is False


def test_an_ambiguous_predecessor_names_the_industries_it_drags_along() -> None:
    """Ekran potwierdzenia stoi na nazwach — sam kod `9622Z` nie mówi operatorowi niczego."""
    rozsz = pkd_map().rozszerz(["9621Z"])

    assert rozsz.kody_2007 == ("9602Z",)
    assert rozsz.czyste == ()
    assert rozsz.wymaga_pytania is True
    (poprzednik,) = rozsz.niejednoznaczne
    assert poprzednik.nazwa == "Fryzjerstwo i pozostałe zabiegi kosmetyczne"
    assert poprzednik.czysty is False
    assert poprzednik.rowniez == (
        ("9622Z", "Działalność w zakresie pielęgnacji urody i pozostała działalność kosmetyczna"),
    )


def test_a_predecessor_leading_nowhere_else_is_clean_and_asks_nothing() -> None:
    """Rozszerzenie czyste nie jest wyborem, tylko naprawą — nie ma o co pytać."""
    rozsz = pkd_map().rozszerz(["1423Z"])

    assert rozsz.kody_2007 == ("1412Z",)
    assert [p.kod for p in rozsz.czyste] == ["1412Z"]
    assert rozsz.niejednoznaczne == ()
    assert rozsz.wymaga_pytania is False


def test_asking_for_both_industries_makes_the_shared_predecessor_clean() -> None:
    """Niejednoznaczność jest **względna do wybranego zestawu**, nie własnością samego kodu.

    Kto poprosił i o fryzjerstwo, i o kosmetykę, dostanie z `9602Z` wyłącznie to, o co prosił,
    więc pytanie „poszerzyć?" byłoby pytaniem o różnicę, której nie ma.
    """
    rozsz = pkd_map().rozszerz(["9621Z", "9622Z"])

    assert rozsz.kody_2007 == ("9602Z",)
    assert [p.kod for p in rozsz.czyste] == ["9602Z"]
    assert rozsz.wymaga_pytania is False


def test_one_predecessor_shared_by_two_codes_is_added_once() -> None:
    """Powtórzony kod poszedłby w URL dwa razy i zniekształcił odcisk palca kryteriów."""
    rozsz = pkd_map().rozszerz(["9621Z", "9622Z", "1423Z"])

    assert rozsz.kody_2007 == ("1412Z", "9602Z")  # bez duplikatu i w stałej kolejności


def test_the_expansion_does_not_depend_on_the_order_of_the_chosen_codes() -> None:
    """Kolejność kodów w `--pkd` nie może zmienić zapytania ani jego odcisku palca."""
    tablica = pkd_map()

    assert tablica.rozszerz(["1423Z", "9621Z"]).kody_2007 == (
        tablica.rozszerz(["9621Z", "1423Z"]).kody_2007
    )


def test_a_repeated_code_does_not_make_its_own_expansion_ambiguous() -> None:
    """`--pkd 96.21.Z --pkd 9621Z` to jedna branża — powtórka nie może zmienić odpowiedzi."""
    rozsz = pkd_map().rozszerz(["9621Z", "9621Z"])

    assert rozsz.kody_2007 == ("9602Z",)
    assert rozsz.wymaga_pytania is True


def test_names_come_from_the_table_and_a_missing_one_is_never_invented() -> None:
    """Nazwa spoza tablicy byłaby zdaniem, którego źródło nie umie powiedzieć."""
    tablica = pkd_map()

    assert tablica.nazwa_2007("9602Z") == "Fryzjerstwo i pozostałe zabiegi kosmetyczne"
    assert tablica.nazwa_2007("0000X") is None
    assert tablica.nazwa_2025("9621Z") == "Fryzjerstwo"
    assert tablica.nazwa_2025("0111Z") is None


# ----------------------------------------------------------------------------- wczytywanie


def test_a_correct_file_loads_with_every_entry(tmp_path: Path) -> None:
    """Ścieżka szczęśliwa: plik z trzema kodami 2025 daje tablicę o trzech kodach."""
    tablica = load_pkd_map(zapisz(tmp_path, DOBRA_TABLICA))

    assert len(tablica) == 3
    assert tablica.rozszerz(["9621Z"]).kody_2007 == ("9602Z",)


def test_a_missing_file_says_how_to_build_it(tmp_path: Path) -> None:
    """Brak tablicy to nie awaria, tylko brak kroku — komunikat ma go podać dosłownie."""
    with pytest.raises(ConfigError) as caught:
        load_pkd_map(tmp_path / "nie-ma-mnie.yaml")

    assert "build_pkd_transition.py" in str(caught.value)
    assert caught.value.exit_code == 3


USZKODZENIA: tuple[tuple[str, object, str], ...] = (
    (
        "klucz_niekanoniczny",
        {**DOBRA_TABLICA, "poprzednicy": {"96.21.Z": ["9602Z"]}},
        "kanoniczn",
    ),
    (
        "klucz_nie_jest_podklasa",
        {**DOBRA_TABLICA, "poprzednicy": {"J": ["9602Z"]}},
        "podklasy PKD",
    ),
    (
        "poprzednik_niekanoniczny",
        {**DOBRA_TABLICA, "poprzednicy": {"9621Z": ["96.02.Z"]}},
        "kanoniczn",
    ),
    (
        "pusta_lista_poprzednikow",
        {**DOBRA_TABLICA, "poprzednicy": {"9621Z": []}},
        "pustą albo nielistową",
    ),
    (
        "poprzednicy_nie_sa_lista",
        {**DOBRA_TABLICA, "poprzednicy": {"9621Z": "9602Z"}},
        "pustą albo nielistową",
    ),
    ("brak_sekcji_poprzednicy", {"nazwy_2007": {}, "nazwy_2025": {}}, "'poprzednicy'"),
    ("brak_sekcji_nazwy_2007", {"poprzednicy": {"9621Z": ["9602Z"]}}, "'nazwy_2007'"),
    ("sekcja_nie_jest_mapa", {**DOBRA_TABLICA, "nazwy_2007": ["9602Z"]}, "'nazwy_2007'"),
    ("pusta_tablica", {"poprzednicy": {}, "nazwy_2007": {}, "nazwy_2025": {}}, "ani jednego"),
    (
        "kod_2007_bez_nazwy",
        {**DOBRA_TABLICA, "nazwy_2007": {"1412Z": "Produkcja odzieży roboczej"}},
        "nie ma nazwy",
    ),
    (
        "nazwa_2007_pusta",
        {**DOBRA_TABLICA, "nazwy_2007": {"9602Z": "  ", "1412Z": "Produkcja odzieży roboczej"}},
        "nie ma nazwy",
    ),
    ("plik_nie_jest_mapa", ["9621Z"], "'poprzednicy'"),
)


@pytest.mark.parametrize(
    ("dane", "fragment"),
    [(dane, fragment) for _, dane, fragment in USZKODZENIA],
    ids=[nazwa for nazwa, _, _ in USZKODZENIA],
)
def test_a_damaged_table_fails_as_a_config_error_with_a_polish_sentence(
    tmp_path: Path, dane: object, fragment: str
) -> None:
    """Nigdy `ValueError` ani ślad stosu: `cli.py` łapie wyłącznie `CeidgError`.

    Uszkodzenie tego pliku najpewniej wygląda właśnie tak — ktoś dopisze sekcję `J` albo klasę
    `62.01` — a wtedy operator ma dostać zdanie, które mówi, co jest nie tak i co z tym zrobić.
    """
    path = zapisz(tmp_path, dane)

    with pytest.raises(ConfigError) as caught:
        load_pkd_map(path)

    assert fragment in str(caught.value)
    assert str(path) in str(caught.value)


def test_unparsable_yaml_is_a_config_error_too(tmp_path: Path) -> None:
    """`yaml.YAMLError` nie jest `CeidgError`, więc bez opakowania wychodzi śladem stosu."""
    path = tmp_path / "pkd2007_2025.yaml"
    path.write_text("poprzednicy: [nie: domknięte\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Nie można wczytać tablicy"):
        load_pkd_map(path)


def test_an_empty_file_is_refused_rather_than_silently_disabling_the_expansion(
    tmp_path: Path,
) -> None:
    """Pusty plik po nieudanej przebudowie wyglądałby jak „nic nie trzeba dokładać"."""
    path = tmp_path / "pkd2007_2025.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="'poprzednicy'"):
        load_pkd_map(path)


# ----------------------------------------------------------------------------- data wygaśnięcia


def test_the_transition_deadline_lives_in_the_module_not_only_in_the_file_header() -> None:
    """Cały mechanizm ma jedno oczywiste miejsce startu do usunięcia po okresie przejściowym."""
    assert KONIEC_PRZEJSCIA == "2026-12-31"


def test_the_table_needs_no_io_after_construction() -> None:
    """`TablicaPkd` jest czysta: da się ją zbudować w pamięci i odpytać bez pliku.

    To jest szew, na którym stoi cała reszta testów przepływu — gdyby konstrukcja sięgała
    po plik, sprawdzenie kroku rocznika wymagałoby pełnej tablicy 264 kodów.
    """
    tablica = TablicaPkd({"9621Z": ("9602Z",)}, {"9602Z": "Fryzjerstwo"}, {"9621Z": "Fryzjerstwo"})

    assert len(tablica) == 1
    assert tablica.rozszerz(["9621Z"]).kody_2007 == ("9602Z",)
