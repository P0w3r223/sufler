"""Sprawdzenia **zawartości** dostarczonej tablicy przejścia PKD 2007 → 2025 (ADR-0012).

`tests/test_pkdmap.py` pyta „czy kod działa" na tablicy trzyelementowej. Tu pytanie brzmi
inaczej: „czy to, co leży w pakiecie, zgadza się z kluczem GUS". Ta sama para plików i ten sam
powód, co przy `tests/test_assistant_pkd_data.py`.

Powód, dla którego ten plik w ogóle istnieje, stoi w `CLAUDE.md`: tablica przepisana z pamięci
albo ze streszczonej strony przechodzi wszystkie automatyczne sprawdzenia — kanoniczność kodów,
liczbę wpisów, zgodność ze słownikiem 2025 — będąc cicho błędna w nazwach, których nikt nie
czyta obok siebie. A ekran potwierdzenia pokazuje **nazwę**, więc zmyślona nazwa sprawia, że
ekran zgadza się sam ze sobą i kontrola operatora znika. Sprawdzenia niżej nie zastąpią
porównania kilkunastu pozycji z wyszukiwarką GUS — to należy do właściciela i tylko ono łapie
błąd u źródła.

Podział 55 / 209 jest tu **liczony z pliku**, nie odczytany z komentarza: gdyby stał w danych
jako liczba, byłby deklaracją, a nie własnością tablicy.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import pytest
import yaml

from ceidg_tool.assistant.pkd import DEFAULT_PKD_PATH, load_pkd
from ceidg_tool.criteria import normalize_pkd
from ceidg_tool.pkdmap import DEFAULT_PKD_MAP_PATH, KONIEC_PRZEJSCIA, TablicaPkd, load_pkd_map
from tests.support import NAZWY_2007_TESTOWE, POPRZEDNICY_TESTOWI

pytestmark = pytest.mark.skipif(
    not DEFAULT_PKD_MAP_PATH.is_file(),
    reason=(
        f"brak {DEFAULT_PKD_MAP_PATH.name} — zbuduj go z oficjalnego klucza GUS: "
        "PYTHONUTF8=1 python scripts/build_pkd_transition.py PKD/KluczePKD_2007_2025.xlsx"
    ),
)

# 264 kody PKD 2025 wymagające przynajmniej jednego filtra z PKD 2007 — policzone przez
# `scripts/build_pkd_transition.py` z `KluczePKD_2007_2025.xlsx` (arkusz „2007-2025", poziom 5),
# przekonwertowanego z pliku pobranego przez właściciela z GUS 2026-09-07. Wartość pochodzi
# **z pliku źródłowego**, nie z pamięci ani z publikacji; ADR-0012 zapisuje ten sam pomiar.
#
# Ta sama pokusa co przy słowniku 2025: gdy test się zaczerwieni, odruchem jest wpisać tu nową
# liczbę. Wtedy sprawdzenie staje się samozgodne. Jego zadaniem jest być **głośne** przy cichej
# utracie albo duplikacji wierszy w przebudowie.
OCZEKIWANE_KODY_2025 = 357

# Rozszerzenia czyste (poprzednik prowadzi wyłącznie tam) i niejednoznaczne (wciąga cudzą
# branżę). Te dwie liczby **nie stoją w pliku** — `pkdmap` liczy je z rozgałęzienia kodów 2007
# i z listy kodów żywych w obu rocznikach.
#
# Przesunięte 2026-09-07 po przeglądzie: generator odrzucał wcześniej poprzedników, którzy sami
# są żywymi kodami PKD 2025, z uzasadnieniem, które nie było prawdziwe. Zabierało to 230 mapowań
# i zostawiało 93 kody 2025 zupełnie bez pomocy — w tym kluby fitness i piekarnie. Teraz wchodzą
# jako niejednoznaczne, bo dokładając taki kod, bierze się też jego dzisiejszą branżę.
OCZEKIWANE_CZYSTE = 51
OCZEKIWANE_NIEJEDNOZNACZNE = 306

ZRODLO = "KluczePKD_2007_2025.xlsx"


@lru_cache(maxsize=1)
def surowa_tablica() -> dict[str, Any]:
    dane = yaml.safe_load(DEFAULT_PKD_MAP_PATH.read_text(encoding="utf-8"))
    assert isinstance(dane, dict)
    return dane


@lru_cache(maxsize=1)
def tablica() -> TablicaPkd:
    """Wczytana raz na cały plik: 264 kody razy `load_pkd_map()` to trzydzieści sekund.

    Wczytanie jest tu przedmiotem jednego sprawdzenia (liczba wpisów), a nie każdego —
    reszta pyta o zawartość, więc powtarzanie go zamienia test danych w test wydajności.
    """
    return load_pkd_map()


def naglowek() -> str:
    """Komentarze z góry pliku — prowenienacja, której `yaml.safe_load` nie widzi."""
    linie: list[str] = []
    for linia in DEFAULT_PKD_MAP_PATH.read_text(encoding="utf-8").splitlines():
        if not linia.startswith("#"):
            break
        linie.append(linia)
    return "\n".join(linie)


def test_every_key_is_already_canonical() -> None:
    """Klucz w innej postaci niż kanoniczna nigdy nie zostałby odnaleziony przez `rozszerz`."""
    dane = surowa_tablica()

    zle = [
        kod
        for sekcja in ("poprzednicy", "nazwy_2007", "nazwy_2025")
        for kod in dane[sekcja]
        if normalize_pkd(str(kod)) != str(kod)
    ]

    assert zle == [], f"kody poza postacią kanoniczną: {zle[:5]}"


def test_the_entry_count_matches_the_official_key() -> None:
    """Liczba wpisów jest kotwicą przeciw cichej utracie albo duplikacji wierszy."""
    assert len(load_pkd_map()) == OCZEKIWANE_KODY_2025


def test_the_clean_ambiguous_split_is_a_property_of_the_file() -> None:
    """Podział liczony z tablicy, a nie zapisany w niej — inaczej byłby deklaracją.

    To jedyny powód, dla którego jedne kody rozszerzają się bez pytania, a inne z pytaniem.
    Gdyby przebudowa cicho przesunęła granicę, operator zacząłby dostawać poszerzone wyniki
    bez ekranu, który je tłumaczy.
    """
    mapa = tablica()

    czyste = [kod for kod in surowa_tablica()["poprzednicy"] if not _pyta(kod)]
    niejednoznaczne = [kod for kod in surowa_tablica()["poprzednicy"] if _pyta(kod)]

    assert len(mapa) == len(czyste) + len(niejednoznaczne)
    assert (len(czyste), len(niejednoznaczne)) == (
        OCZEKIWANE_CZYSTE,
        OCZEKIWANE_NIEJEDNOZNACZNE,
    )


def _pyta(kod2025: str) -> bool:
    return tablica().rozszerz([kod2025]).wymaga_pytania


def test_every_expanded_code_belongs_to_the_2025_dictionary() -> None:
    """Kod 2025 spoza słownika nigdy nie trafiłby do `criteria.pkd`, więc byłby martwy.

    Sprawdzenie sięga po **drugi** wygenerowany plik, więc łapie rozjazd między dwiema
    przebudowami — jedyny rodzaj błędu, którego tablica sama o sobie nie zauważy.
    """
    slownik = load_pkd()

    brakujace = sorted(k for k in surowa_tablica()["poprzednicy"] if k not in slownik)

    assert brakujace == [], f"klucze spoza pkd2025.yaml: {brakujace[:5]}"


def test_a_predecessor_that_is_also_a_live_2025_code_is_declared_as_such() -> None:
    """Kod stojący po obu stronach rocznika musi być wymieniony w `zywe_2025` — inaczej milczy.

    Do przeglądu 2026-09-07 takich kodów w tablicy w ogóle nie było: generator je odrzucał,
    twierdząc, że „filtr 2025 już je obejmuje". Obejmuje wtedy **rekord**, a nie **branżę,
    o którą pyta operator**: `8551Z` znaczy dziś „Pozostałe formy edukacji sportowej", więc
    dokładając go do zapytania o kluby fitness, bierze się inną branżę. Teraz takie kody są
    w tablicy, a `zywe_2025` jest tym, co każe `pkdmap` powiedzieć o tym operatorowi. Kod
    obecny po obu stronach i **nieujęty** na tej liście rozszerzałby zapytanie po cichu.
    """
    slownik = load_pkd()
    dane = surowa_tablica()
    zywe = set(dane.get("zywe_2025") or [])

    obustronne = {k for kody in dane["poprzednicy"].values() for k in kody} & set(slownik)

    assert sorted(obustronne - zywe) == [], "poprzednik żywy w 2025, ale nieoznaczony"
    assert sorted(zywe - obustronne) == [], "oznaczony jako żywy, choć nie ma go w PKD 2025"
    assert zywe, "lista pusta znaczy, że generator znowu odrzuca te mapowania"
    # `6201Z` zostaje przypadkiem wzorcowym po drugiej stronie: klasyczny kod „oprogramowanie"
    # w PKD 2025 nie istnieje, więc może być wyłącznie poprzednikiem i nigdy nie jest „żywy".
    assert "6201Z" not in slownik and "6201Z" not in zywe


def test_every_2007_code_carries_a_name_from_the_official_source() -> None:
    """Ekran potwierdzenia pokazuje nazwę — kod bez niej odbiera operatorowi jedyną kontrolę."""
    dane = surowa_tablica()
    nazwy = dane["nazwy_2007"]

    bez_nazwy = sorted(
        k
        for kody in dane["poprzednicy"].values()
        for k in kody
        if not str(nazwy.get(k, "")).strip()
    )

    assert bez_nazwy == [], f"kody PKD 2007 bez nazwy: {bez_nazwy[:5]}"


def test_every_industry_named_on_the_screen_has_a_2025_name_too() -> None:
    """Zdanie „…ale obejmuje też" wypisuje nazwy 2025; pusta zrobiłaby z niego goły kod."""
    mapa = tablica()

    bezimienne = sorted(
        {
            kod
            for kod2025 in surowa_tablica()["poprzednicy"]
            for poprzednik in mapa.rozszerz([kod2025]).niejednoznaczne
            for kod, nazwa in poprzednik.rowniez
            if not nazwa.strip()
        }
    )

    assert bezimienne == [], f"kody 2025 bez nazwy w tablicy: {bezimienne[:5]}"


def test_the_hairdressing_case_from_the_adr_is_what_the_data_says() -> None:
    """Przypadek, na którym stoi decyzja ADR-0012 i przejście gate-3 — sprawdzony wprost.

    Jeśli `9602Z` przestanie prowadzić do dwóch branż, cały krok pytania o rocznik traci
    powód, a jego testy przechodziłyby na przypadku, którego już nie ma.
    """
    rozsz = tablica().rozszerz(["9621Z"])

    assert rozsz.kody_2007 == ("9602Z",)
    assert rozsz.wymaga_pytania is True
    (poprzednik,) = rozsz.niejednoznaczne
    assert [kod for kod, _ in poprzednik.rowniez] == ["9622Z"]


def test_the_software_code_asymmetry_from_the_adr_is_what_the_data_says() -> None:
    """`--pkd 6201Z` sięga rejestru, a asystent ten sam kod odrzuca — tablica to tłumaczy."""
    mapa = tablica()

    prowadzi_do = sorted(
        kod for kod, kody in surowa_tablica()["poprzednicy"].items() if "6201Z" in kody
    )

    assert prowadzi_do == ["6210A", "6210B"]
    assert mapa.nazwa_2007("6201Z") == "Działalność związana z oprogramowaniem"


def test_the_test_double_still_agrees_with_the_generated_table() -> None:
    """Atrapa z `tests/support.py` opisuje trzy prawdziwe pozycje — i ma nimi zostać.

    Bez tego sprawdzenia miniaturowa tablica mogłaby po cichu zacząć opisywać klasyfikację,
    której już nie ma, a wszystkie testy przepływu przechodziłyby na fikcji. Dokładnie ten
    defekt wyprodukowała jedna ręcznie wpisana linia `rokPkd` w `tests/conftest.py`.
    """
    dane = surowa_tablica()

    for kod2025, oczekiwani in POPRZEDNICY_TESTOWI.items():
        assert tuple(dane["poprzednicy"][kod2025]) == oczekiwani, kod2025
    for kod2007, nazwa in NAZWY_2007_TESTOWE.items():
        assert dane["nazwy_2007"][kod2007] == nazwa, kod2007


# ----------------------------------------------------------------------------- prowenienacja


def test_the_header_records_where_the_table_came_from() -> None:
    """Bez pliku źródłowego i jego sumy nie da się powiedzieć, co ta tablica opisuje."""
    tekst = naglowek()

    assert ZRODLO in tekst
    assert "SHA-256 źródła:" in tekst
    assert "scripts/build_pkd_transition.py" in tekst
    assert "Dz.U. 2024 poz. 1936" in tekst


def test_the_header_carries_the_same_expiry_date_as_the_module() -> None:
    """Tablica jest wygasająca; rozjazd daty w pliku i w kodzie zgubiłby moment usunięcia."""
    assert f"Wygasa: {KONIEC_PRZEJSCIA}" in naglowek()


def test_the_dictionary_the_table_is_checked_against_is_the_generated_one() -> None:
    """Sprawdzenia wyżej opierają się na `pkd2025.yaml` — jego brak czyniłby je pustymi."""
    assert DEFAULT_PKD_PATH.is_file()
    assert len(load_pkd()) > len(tablica())
