"""Warstwa czysta asystenta (ADR-0011): schemat, słownik PKD, prompt, tłumaczenie na `Criteria`.

Cały plik działa bez sieci, bez klucza i bez SDK — to jest ta część fazy 4, którą da się
sprawdzić w całości offline, i dlatego powstaje pierwsza.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import pytest
import yaml

from ceidg_tool.assistant import Assistant, AssistantResult, OgraniczenieKod
from ceidg_tool.assistant.pkd import PKD_VINTAGE, load_pkd, lookup, validate_codes
from ceidg_tool.assistant.prompt import build_question, build_system
from ceidg_tool.assistant.schema import AssistantAnswer, json_schema
from ceidg_tool.assistant.translate import to_criteria, to_result
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ConfigError
from ceidg_tool.pipeline import Deps

# Słownik zastępczy: pięć kodów wystarczy do każdej własności, której pilnują te testy, a pełny
# plik ma własny zestaw sprawdzeń (`test_assistant_pkd_data.py`), bo tam pytanie brzmi inaczej —
# nie „czy kod działa", tylko „czy zawartość zgadza się z klasyfikacją".
#
# Kody i nazwy są **prawdziwe i z PKD 2025**, przypięte do wygenerowanego słownika przez
# `test_assistant_pkd_data.py`. Do audytu 2026-09-07 stały tu kody z PKD 2007 (`3030Z`, `4120Z`,
# `6201Z` — trzy z pięciu nie istnieją w 2025) z nazwami przepisanymi dosłownie z rocznika 2007,
# a moduł pod testem ma na sztywno `PKD_VINTAGE = "PKD 2025"` i wstrzykuje ten napis do promptu.
# To jest dokładnie ta awaria, przez którą wybrano zły rocznik klasyfikacji: ręcznie napisana
# atrapa opisująca rzeczywistość, której nie ma. `6201Z` był w niej kodem, który `CLAUDE.md`
# wymienia z nazwiska jako godło tego defektu.
SLOWNIK = {
    "0111Z": "Uprawa zbóż innych niż ryż, roślin strączkowych i roślin oleistych na nasiona",
    "3031Z": "Produkcja cywilnych statków powietrznych, statków kosmicznych i podobnych maszyn",
    "4100A": "Roboty budowlane związane ze wznoszeniem budynków mieszkalnych",
    "4711Z": (
        "Sprzedaż detaliczna niewyspecjalizowana z przewagą żywności, napojów lub wyrobów "
        "tytoniowych"
    ),
    "6210B": "Pozostała działalność w zakresie programowania",
}


def write_pkd(tmp_path: Path, mapping: dict[str, str]) -> Path:
    path = tmp_path / "pkd.yaml"
    path.write_text(yaml.safe_dump(mapping, allow_unicode=True), encoding="utf-8")
    return path


# --- schemat -----------------------------------------------------------------------------


def test_the_schema_cannot_carry_a_record_cap() -> None:
    """`max_rekordow` nie istnieje w schemacie i to jest zabezpieczenie, nie przeoczenie.

    Sprawdzamy przez `model_validate`, a nie przez konstruktor, bo odpowiedź modelu wchodzi
    dokładnie tą drogą — z JSON-a, nie z wywołania w Pythonie.

    `flow.prepare_fetch` pomija całą gałąź progu 50 tys., gdy limit jest ustawiony. Model,
    który przeczytałby „kilka firm" jako `max_rekordow=50`, wyłączyłby scenariusz odporności 9
    po cichu. Zgody i zabezpieczeń nie da się tu poszerzyć nie dlatego, że ktoś ich pilnuje,
    tylko dlatego, że **nie ma takiego pola**.
    """
    with pytest.raises(ValueError, match="max_rekordow"):
        AssistantAnswer.model_validate({"max_rekordow": 50})


@pytest.mark.parametrize("pole", ["srodowisko", "token", "produkcja", "limit", "page"])
def test_the_schema_cannot_carry_anything_transport_shaped(pole: str) -> None:
    """Pola transportowe stoją poza `Criteria` od 2026-09-05 dokładnie z tego powodu."""
    with pytest.raises(ValueError, match=pole):
        AssistantAnswer.model_validate({pole: "cokolwiek"})


def test_the_json_schema_is_closed() -> None:
    """Strukturalne wyjście wymaga `additionalProperties: false` i pełnego `required`.

    Pydantic nie robi ani jednego, ani drugiego sam: pola z wartością domyślną nie trafiają do
    `required`. Schemat, który po cichu przestałby być domknięty, przepuszczałby pola spoza
    `AssistantAnswer` — czyli dokładnie to, przed czym `extra="forbid"` ma chronić.
    """
    schema = json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definicja in schema.get("$defs", {}).values():
        if definicja.get("type") == "object" and "properties" in definicja:
            assert definicja["additionalProperties"] is False


# --- słownik PKD -------------------------------------------------------------------------


def test_a_dictionary_key_out_of_canonical_form_is_refused(tmp_path: Path) -> None:
    """Uszkodzony słownik ma paść przy wczytaniu, a nie objawić się chybioną podpowiedzią."""
    path = write_pkd(tmp_path, {"62.10.B": "Działalność związana z oprogramowaniem"})

    with pytest.raises(ConfigError, match="kanoniczn"):
        load_pkd(path)


def test_a_dictionary_entry_without_a_name_is_refused(tmp_path: Path) -> None:
    path = write_pkd(tmp_path, {"6210B": "   "})

    with pytest.raises(ConfigError, match="nie ma nazwy"):
        load_pkd(path)


def test_lookup_accepts_the_dotted_form_operators_actually_type() -> None:
    assert lookup("62.10.B", SLOWNIK) == SLOWNIK["6210B"]
    assert lookup("6210b", SLOWNIK) == SLOWNIK["6210B"]
    assert lookup("9999Z", SLOWNIK) is None
    assert lookup("nie-kod", SLOWNIK) is None


def test_a_code_outside_the_dictionary_is_refused_by_name() -> None:
    """Komunikat mówi, czego brakuje i w jakiej klasyfikacji.

    Sonda zmierzyła, że API odpowiada na nieznany `pkd` **kodem 204, nie 400** — więc bez
    słownika kod zmyślony dawałby „Brak firm spełniających kryteria", nie do odróżnienia od
    pustego rejestru. Cała wartość tego sprawdzenia siedzi w treści komunikatu.
    """
    with pytest.raises(ConfigError) as caught:
        validate_codes(["9999Z"], SLOWNIK)

    assert "9999Z" in str(caught.value)
    assert PKD_VINTAGE in str(caught.value)


def test_codes_are_normalised_and_deduplicated() -> None:
    assert validate_codes(["62.10.B", "6210B", "4100A"], SLOWNIK) == ("6210B", "4100A")


# --- prompt ------------------------------------------------------------------------------


def test_the_system_block_is_byte_identical_between_builds() -> None:
    """Niestabilny prefiks to cache, który nigdy nie odczyta — i pełna cena każdego pytania."""
    assert build_system(SLOWNIK) == build_system(dict(reversed(list(SLOWNIK.items()))))


def test_the_system_block_lists_codes_in_order() -> None:
    blok = build_system(SLOWNIK)
    pozycje = [blok.index(kod) for kod in sorted(SLOWNIK)]

    assert pozycje == sorted(pozycje)
    assert all(SLOWNIK[kod] in blok for kod in SLOWNIK)


def test_todays_date_is_in_the_question_not_in_the_cached_prefix() -> None:
    """Data w bloku systemowym siedziałaby przed całym słownikiem i unieważniała cache."""
    blok = build_system(SLOWNIK)
    pytanie = build_question("firmy budowlane", dzisiaj=date(2026, 9, 7))

    assert "2026-09-07" not in blok
    assert "2026-09-07" in pytanie and "firmy budowlane" in pytanie


def test_the_question_carries_nothing_but_the_date_and_the_sentence() -> None:
    """§B po stronie danych: do modelu idzie pytanie i słownik, pobrane rekordy nigdy.

    Test porównuje całą turę użytkownika ze wzorcem o dwóch podstawieniach, więc dopisanie
    czegokolwiek — ścieżki, tokenu, katalogu danych, rekordu — jest czerwonym testem.
    """
    pytanie = build_question("  firmy w Łomży  ", dzisiaj=date(2026, 9, 7))

    assert pytanie == "Dzisiejsza data: 2026-09-07.\nZdanie użytkownika: firmy w Łomży"


# --- tłumaczenie na Criteria -------------------------------------------------------------


def test_a_well_formed_answer_becomes_the_expected_criteria() -> None:
    answer = AssistantAnswer(
        wojewodztwo=("podlaskie",),
        miasto=("Białystok",),
        pkd=("41.00.A",),
        status=("aktywny",),
        data_od="2025-01-01",
        data_do="2025-12-31",
        szczegoly=True,
    )

    kryteria = to_criteria(answer, SLOWNIK)

    assert kryteria == Criteria(
        wojewodztwo=("podlaskie",),
        miasto=("Białystok",),
        pkd=("4100A",),
        status=("AKTYWNY",),
        data_od=date(2025, 1, 1),
        data_do=date(2025, 12, 31),
        szczegoly=True,
    )
    assert kryteria.max_rekordow is None


def test_a_bad_pkd_code_is_refused_before_criteria_is_built() -> None:
    """Kolejność sprawdzeń jest treścią komunikatu, nie szczegółem implementacji."""
    answer = AssistantAnswer(pkd=("9999Z",), nip=("0000000000",))

    with pytest.raises(ConfigError, match="9999Z"):
        to_criteria(answer, SLOWNIK)


def test_a_bad_nip_checksum_is_refused_by_criteria() -> None:
    """Suma kontrolna NIP-u to już robota `criteria.py` — asystent nic tu nie omija."""
    answer = AssistantAnswer(nip=("1234567890",))

    with pytest.raises(ConfigError, match="suma kontrolna|NIP"):
        to_criteria(answer, SLOWNIK)


def test_an_unknown_status_is_refused_by_criteria() -> None:
    answer = AssistantAnswer(status=("PRAWIE_AKTYWNY",))

    with pytest.raises(ConfigError):
        to_criteria(answer, SLOWNIK)


@pytest.mark.parametrize("wartosc", ["wczoraj", "2025-13-01", "01.01.2025"])
def test_a_date_that_is_not_iso_is_refused_with_a_sentence(wartosc: str) -> None:
    """Model dostaje dzisiejszą datę i ma zwracać daty bezwzględne — reszta jest błędem."""
    with pytest.raises(ConfigError, match="RRRR-MM-DD"):
        to_criteria(AssistantAnswer(data_od=wartosc), SLOWNIK)


def test_an_empty_answer_produces_empty_criteria() -> None:
    """Pusta interpretacja nie jest wyjątkiem — łapie ją `is_empty()` przed żądaniem."""
    kryteria = to_criteria(AssistantAnswer(), SLOWNIK)

    assert kryteria.is_empty()


def test_the_result_pairs_codes_with_names_from_the_local_dictionary() -> None:
    """Nazwa PKD pochodzi ze słownika lokalnego, nigdy od modelu — i składa to warstwa czysta.

    Wcześniejsza wersja tego testu budowała `AssistantResult` ręcznie i asertowała własne
    wejście: żaden kod produkcyjny nie leżał na ścieżce. Parowanie siedzi teraz w `to_result`,
    więc nie ma szwu, przez który dałoby się podać nazwę skądinąd. Model zresztą nie ma jak jej
    podać — w schemacie nie istnieje pole na nazwę — ale to jest własność, na której stoi całe
    potwierdzenie, i musi mieć funkcję do wywołania.
    """
    wynik = to_result(
        AssistantAnswer(pkd=("41.00.A", "6210B"), ograniczenia=(OgraniczenieKod.SPOLKI_W_KRS,)),
        SLOWNIK,
    )

    assert wynik.kody_pkd == (
        ("4100A", SLOWNIK["4100A"]),
        ("6210B", SLOWNIK["6210B"]),
    )
    assert wynik.ograniczenia == (OgraniczenieKod.SPOLKI_W_KRS,)
    assert wynik.kryteria.pkd == ("4100A", "6210B")


# Pola `Criteria`, których model **nie ma prawa** ustawiać. Nie są to wyjątki dla wygody: każde
# z nich rozstrzyga coś, co należy do operatora, a nieobecność w schemacie jest tym, co to
# gwarantuje — nie strażnik w kodzie (ADR-0011, decyzja 2; ADR-0012, sub-decyzja 2).
POZA_ZASIEGIEM_MODELU = {
    "max_rekordow",  # limit rekordów: scenariusz 9 opiera się na tym, że schemat go nie ma
    "pkd_2007",  # rozszerzenie o rocznik 2007: wybór populacji zapada na ekranie, nie w modelu
}


# Odpowiedź z **każdym** polem wypełnioną wartością rozróżnialną. Służy do sprawdzenia, że
# tłumaczenie niczego nie gubi — a nie do sprawdzenia, że pola istnieją.
PELNA_ODPOWIEDZ = AssistantAnswer(
    nazwa=("Kowalski",),
    nip=("5252248481",),
    regon=("123456785",),
    imie=("Jan",),
    nazwisko=("Kowalski",),
    wojewodztwo=("podlaskie",),
    powiat=("łomżyński",),
    gmina=("Łomża",),
    miasto=("Łomża",),
    ulica=("Długa",),
    kod=("18-400",),
    pkd=("6210B",),
    status=("AKTYWNY",),
    data_od="2025-01-01",
    data_do="2025-12-31",
    szczegoly=True,
)


def test_every_answer_field_reaches_criteria() -> None:
    """Pole dopisane do schematu i zapomniane w tłumaczeniu cicho **poszerza** zapytanie.

    To jest kierunek drogi: zgubiony filtr znaczy więcej rekordów, więcej żądań i dłuższy
    przebieg, a nie błąd.

    Test porównywał wcześniej **zbiory nazw pól** dwóch modeli i ani razu nie wołał
    `to_criteria` — czyli sprawdzał, że pola *istnieją*, obiecując w docstringu, że
    *docierają*. Audyt 2026-09-07 pokazał to mutacją: po usunięciu ośmiu z szesnastu
    przekazań (`nazwa`, `regon`, `imie`, `nazwisko`, `powiat`, `gmina`, `ulica`, `kod`)
    cała suita — 856 testów — nadal przechodziła. `to_criteria` jest ręcznym przepisaniem
    pole po polu, więc dokładnie ta różnica jest tu nośna.

    Zostaje porównanie zbiorów (łapie pole **dodane** do schematu i zapomniane), a dochodzi
    przejście przez tłumaczenie z wartościami rozróżnialnymi (łapie pole **przestające**
    docierać).
    """
    pola_modelu = set(AssistantAnswer.model_fields) - {"ograniczenia"}
    pola_kryteriow = set(Criteria.model_fields) - POZA_ZASIEGIEM_MODELU
    assert pola_modelu == pola_kryteriow

    kryteria = to_criteria(PELNA_ODPOWIEDZ, SLOWNIK)

    puste = sorted(
        pole
        for pole in pola_modelu
        if not getattr(kryteria, pole)
        if getattr(PELNA_ODPOWIEDZ, pole)
    )
    assert puste == [], f"pola zgubione w tłumaczeniu: {puste}"


def test_the_model_cannot_widen_the_query_to_the_2007_vintage() -> None:
    """`pkd_2007` jest polem `Criteria`, a nie polem schematu — i to jest cała gwarancja.

    Gdyby model mógł je ustawić, wybierałby populację: dołożenie `9602Z` do zapytania
    o fryzjerów wciąga kosmetyczki, co jest decyzją operatora, nie modelu (ADR-0012). Tak jak
    przy `max_rekordow`, chroni nas **nieobecność** pola, więc to ona jest tu asercją.
    """
    assert "pkd_2007" not in AssistantAnswer.model_fields
    assert "pkd_2007" in Criteria.model_fields
    # Odpowiedź modelu przechodząca przez tłumaczenie nigdy nie rozszerza rocznika sama z siebie.
    wynik = to_criteria(AssistantAnswer(pkd=("9621Z",)), {"9621Z": "Działalność fryzjerska"})
    assert wynik.pkd_2007 == ()


def test_a_validation_error_reaches_the_operator_as_polish_sentences() -> None:
    """Operator dostaje zdanie, nie zrzut pydantica z odnośnikiem do errors.pydantic.dev.

    Decyzja 3 z ADR-0011 odrzuciła generowanie `Criteria` wprost przez model właśnie po to, żeby
    błąd walidacji wracał jako nasz komunikat; wklejony `ValidationError` był połową tego wyniku.
    """
    with pytest.raises(ConfigError) as caught:
        to_criteria(AssistantAnswer(nip=("1234567890",)), SLOWNIK)

    tresc = str(caught.value)
    assert "nip:" in tresc and "sumę kontrolną" in tresc
    assert "pydantic.dev" not in tresc
    assert "input_value" not in tresc and "Value error" not in tresc


# --- brak słownika i budowanie go ze źródła ----------------------------------------------


def test_a_missing_dictionary_says_how_to_build_it(tmp_path: Path) -> None:
    """Brak słownika wyłącza asystenta jak brak klucza — z instrukcją, a nie ze śladem stosu.

    Praca bez słownika byłaby gorsza od odmowy: model mógłby podać kod spoza klasyfikacji, API
    odpowiedziałoby 204 (sonda 2026-09-05), a operator przeczytałby „brak firm" zamiast „zły
    kod" — czyli dokładnie to pomylenie, przed którym słownik ma chronić.
    """
    with pytest.raises(ConfigError) as caught:
        load_pkd(tmp_path / "nie-ma.yaml")

    assert "build_pkd.py" in str(caught.value)
    assert PKD_VINTAGE in str(caught.value)


def test_the_builder_keeps_only_subclasses_and_writes_canonical_keys(tmp_path: Path) -> None:
    """Konwerter bierze podklasy, pomija sekcje/działy/grupy i normalizuje kod.

    `Criteria.pkd` przyjmuje wyłącznie podklasę, więc dział „62" w słowniku byłby pozycją,
    której nie da się użyć — a przy okazji zawyżałby liczbę wpisów pilnowaną przez test.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from build_pkd import czytaj, zapisz

    zrodlo = tmp_path / "gus.csv"
    zrodlo.write_text(
        "Symbol;Nazwa\n"
        "J;INFORMACJA I KOMUNIKACJA\n"
        "62;Działalność związana z oprogramowaniem i doradztwem\n"
        "62.01;Działalność związana z oprogramowaniem\n"
        "62.10.B;Działalność   związana z oprogramowaniem\n"
        "41.00.A;Roboty budowlane związane ze wznoszeniem budynków\n",
        encoding="utf-8",
    )

    pary = dict(czytaj(zrodlo, None, None))

    assert pary == {
        "6210B": "Działalność związana z oprogramowaniem",  # białe znaki znormalizowane
        "4100A": "Roboty budowlane związane ze wznoszeniem budynków",
    }

    out = tmp_path / "pkd.yaml"
    zapisz(pary, out=out, zrodlo=zrodlo, suma="abc123", rocznik="2025", podstawa=None)

    wczytane = load_pkd(out)
    assert wczytane == pary
    naglowek = out.read_text(encoding="utf-8")
    assert "Polska Klasyfikacja Działalności 2025" in naglowek and "abc123" in naglowek


def test_a_key_that_is_not_a_pkd_code_at_all_is_a_config_error(tmp_path: Path) -> None:
    """Sekcja `J` albo klasa `62.01` w słowniku to najbardziej prawdopodobne uszkodzenie.

    `normalize_pkd` rzuca wtedy `ValueError`, a `ValueError` nie jest `CeidgError` — więc
    `cli.py` by go nie złapał i operator zobaczyłby ślad stosu zamiast zdania i kodu wyjścia 3.
    Poprzednia wersja tego sprawdzenia używała `62.10.B`, czyli kodu **poprawnego kształtem**,
    i trafiała w gałąź, która działała.
    """
    for klucz in ("J", "62.01", "62"):
        path = write_pkd(tmp_path, {klucz: "Cokolwiek"})
        with pytest.raises(ConfigError, match="nie jest kodem podklasy"):
            load_pkd(path)


def test_the_builder_survives_a_name_with_quotes_and_backslashes(tmp_path: Path) -> None:
    """Nazwa z cudzysłowem dawała plik, którego `load_pkd` nie wczyta — po ręcznym pobraniu z GUS.

    Ukośnik byłby gorszy od cudzysłowu: w stylu cudzysłowowym YAML **interpretuje** sekwencje,
    więc `\t` w nazwie stałby się tabulatorem po cichu i nikt by się nie dowiedział.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from build_pkd import zapisz

    trudna = 'Nazwa z "cudzysłowem", ' + chr(92) + " ukośnikiem i " + chr(92) + "t"
    pary = {"6210B": trudna, "4100A": "Roboty budowlane"}
    out = tmp_path / "pkd.yaml"

    zapisz(
        pary, out=out, zrodlo=tmp_path / "zrodlo.csv", suma="abc123", rocznik="2025", podstawa=None
    )

    assert load_pkd(out) == pary


# --- wpięcie w przepływ (ADR-0011, decyzja 5) ---------------------------------------------


class ScriptedAssistant:
    """Atrapa asystenta: oddaje przygotowane wyniki, notuje zadane opisy."""

    def __init__(self, wyniki: list[AssistantResult]) -> None:
        self.wyniki = list(wyniki)
        self.opisy: list[str] = []

    def interpret(self, opis: str, *, dzisiaj: date) -> AssistantResult:
        self.opisy.append(opis)
        return self.wyniki.pop(0)


def wynik_dla(**pola: object) -> AssistantResult:
    answer = AssistantAnswer.model_validate(pola)
    return to_result(answer, SLOWNIK)


def flow_deps(assistant: Assistant | None) -> Deps:
    """Minimalne `Deps` — przepływ opisu nie dotyka ani sieci, ani bazy.

    `cast`, bo budowanie prawdziwego `Deps` wymagałoby pliku bazy i profilu. Ograniczenie tego,
    czego atrapa dotyka, jest tu asercją samą w sobie: gdyby przepływ zaczął czytać coś jeszcze,
    ten test padnie — i tak się stało 2026-09-07, gdy doszło gaszenie paska. To była prawidłowa
    czerwień, bo pasek asystenta **musi** zgasnąć przed interpretacją: żywy `rich` nadpisuje
    wszystko, co po nim wypisane, i na to samo przewrócił się kreator przy bramce 3 rok wcześniej.
    `NullEvents` dopisane więc świadomie, a nie żeby uciszyć test."""
    from types import SimpleNamespace

    from ceidg_tool.progress import NullEvents

    return cast(
        "Deps",
        SimpleNamespace(assistant=assistant, assistant_reason=None, events=NullEvents()),
    )


def test_a_confirmed_interpretation_spends_no_ceidg_request() -> None:
    """Krok stoi **przed** `prepare_fetch`, więc `count` jeszcze nie padł.

    To jest własność, z której wynika cała reszta: niezmiennik „dokładnie jedno żądanie
    `count`", tabela kosztów i ścieżka zgody zostają nietknięte, bo asystent kończy pracę,
    zanim którakolwiek się zacznie.
    """
    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from tests.support import RecordingView

    view = RecordingView()
    prompter = ScriptedPrompter({"zatwierdz_interpretacje": "tak"})
    asystent = ScriptedAssistant([wynik_dla(miasto=["Białystok"], pkd=["4100A"])])

    kryteria = collect_from_description(
        flow_deps(asystent), prompter, view, opis="firmy budowlane w Białymstoku"
    )

    assert kryteria is not None and kryteria.miasto == ("Białystok",)
    assert asystent.opisy == ["firmy budowlane w Białymstoku"]
    blok = view.block_titled("Interpretacja")
    assert "4100A" in blok.as_text() and SLOWNIK["4100A"] in blok.as_text()


def test_choosing_questions_falls_back_without_criteria() -> None:
    """„pytania po kolei" to pełnoprawna odpowiedź, nie awaria — kreator idzie dalej."""
    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from tests.support import RecordingView

    asystent = ScriptedAssistant([wynik_dla(miasto=["Łomża"])])
    kryteria = collect_from_description(
        flow_deps(asystent),
        ScriptedPrompter({"zatwierdz_interpretacje": "pytania"}),
        RecordingView(),
        opis="cokolwiek",
    )

    assert kryteria is None


def test_correcting_the_description_asks_the_assistant_again() -> None:
    """„popraw opis" to druga interpretacja, wciąż bez żadnego żądania do CEIDG."""
    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from tests.support import RecordingView

    asystent = ScriptedAssistant(
        [wynik_dla(miasto=["Łomża"]), wynik_dla(miasto=["Białystok"], pkd=["6210B"])]
    )
    prompter = ScriptedPrompter(
        {"zatwierdz_interpretacje": ["popraw", "tak"], "opis": "firmy IT w Białymstoku"}
    )

    kryteria = collect_from_description(
        flow_deps(asystent), prompter, RecordingView(), opis="firmy w Łomży"
    )

    assert kryteria is not None and kryteria.miasto == ("Białystok",)
    assert asystent.opisy == ["firmy w Łomży", "firmy IT w Białymstoku"]


def test_without_an_assistant_the_flow_refuses_with_a_sentence() -> None:
    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from tests.support import RecordingView

    with pytest.raises(ConfigError, match="niedostępny"):
        collect_from_description(
            flow_deps(None), ScriptedPrompter({}), RecordingView(), opis="cokolwiek"
        )


def test_the_confirmation_cannot_be_taken_by_the_unattended_mode() -> None:
    """`--tak` nie zatwierdza interpretacji za operatora — kończy się kodem 3.

    Harmonogram działający na zdaniu, którego nikt nie przeczytał, jest dokładnie tym, czego
    ta flaga ma nie robić; jego drogą jest plik YAML zapisany z wyniku asystenta.
    """
    from ceidg_tool.ui.prompts import ZATWIERDZ_INTERPRETACJE, DefaultsPrompter

    assert ZATWIERDZ_INTERPRETACJE.safe_default is False
    with pytest.raises(ConfigError, match="nieinteraktywny"):
        DefaultsPrompter().ask(ZATWIERDZ_INTERPRETACJE)


# --- spójność kodów ograniczeń (znalezisko z grupy A) -------------------------------------


def test_every_limitation_code_is_known_to_both_the_model_and_the_operator() -> None:
    """Enum, opis dla modelu i zdanie dla operatora muszą mieć **te same** klucze.

    Trzy zbiory, trzy sposoby na cichą awarię, i żaden nie krzyczy sam z siebie:

    - kod bez opisu dla modelu → model nigdy się o nim nie dowie, więc go nie użyje;
    - kod bez zdania dla operatora → `interpretation` pomija go w `if kod in …`, czyli
      ograniczenie zostaje **przemilczane** dokładnie wtedy, gdy model je zauważył;
    - zdanie bez kodu → martwy tekst, którego nic nie pokaże.

    Do 2026-09-07 nic tego nie pilnowało, a dopisanie `KOD_PKD_Z_INNEGO_ROCZNIKA` po przebiegu
    A5 było pierwszą okazją, żeby to złamać.
    """
    from ceidg_tool.assistant.schema import OGRANICZENIA_DLA_MODELU
    from ceidg_tool.ui.texts import OGRANICZENIA_ZDANIA

    kody = {kod.value for kod in OgraniczenieKod}

    assert {kod.value for kod in OGRANICZENIA_DLA_MODELU} == kody
    assert set(OGRANICZENIA_ZDANIA) == kody


def test_the_prompt_names_every_limitation_code() -> None:
    """Model wybiera wyłącznie z tego, co zobaczył — lista w promptcie musi być pełna."""
    blok = build_system(SLOWNIK)

    for kod in OgraniczenieKod:
        assert kod.value in blok, f"{kod.value} nie trafił do promptu"


def test_the_prompt_tells_the_model_to_keep_the_code_set_narrow() -> None:
    """Znalezisko 2 z grupy A: to samo zdanie dało raz 4 kody, raz 15 — czyli dwie różne roboty.

    Instrukcja jest jedyną dźwignią, bo liczba kodów przekłada się wprost na liczbę żądań.
    Test pilnuje, że ta dźwignia w ogóle jest w promptcie — nie tego, jak model się zachowa.
    """
    blok = build_system(SLOWNIK)

    assert "najwęższy" in blok
    assert "wszystkich podklas działu" in blok


def test_an_unavailable_assistant_reports_the_real_reason() -> None:
    """Powód, gdy go znamy — nie domysł (znalezisko z przebiegu B6).

    Przy odłożonym słowniku PKD komunikat mówił „brak klucza API albo pakietu `anthropic`",
    czyli wskazywał dwie rzeczy, które akurat były na miejscu. `load_pkd` potrafi powiedzieć,
    czego brakuje i jakim poleceniem to zbudować; to zdanie jest cenniejsze niż nasze domyślne
    i nie ma powodu, żeby ginęło w `_build_assistant`.
    """
    from types import SimpleNamespace

    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from tests.support import RecordingView

    powod = "Brak słownika PKD 2025 (…). Zbuduj go: python scripts/build_pkd.py <plik>"
    deps = cast("Deps", SimpleNamespace(assistant=None, assistant_reason=powod))

    with pytest.raises(ConfigError, match="build_pkd"):
        collect_from_description(deps, ScriptedPrompter({}), RecordingView(), opis="cokolwiek")


def test_the_progress_bar_is_closed_before_the_interpretation_is_printed() -> None:
    """Żywy `rich` nadpisuje wszystko, co po nim wypisane — także ekran interpretacji.

    Kreator przewrócił się na tym raz, przy bramce 3 w 2026-09-06, i wtedy `pipeline` nauczył
    się gasić pasek razem z operacją. Asystent jest szóstym kanałem postępu i doszedł już po
    tamtej poprawce, więc lekcji nie odziedziczył: pasek żył do końca polecenia, czyli przez
    całe czytanie interpretacji i przez pytanie o jej zatwierdzenie.

    Test pilnuje **kolejności**, a nie samego faktu zamknięcia: gaszenie po wypisaniu bloku
    byłoby tak samo bezużyteczne jak jego brak.
    """
    from types import SimpleNamespace

    from ceidg_tool.ui.flow import collect_from_description
    from ceidg_tool.ui.prompts import ScriptedPrompter
    from ceidg_tool.ui.texts import Block
    from tests.support import RecordingView

    kolejnosc: list[str] = []

    class SledzoneZdarzenia:
        def __getattr__(self, _name: str) -> object:
            return lambda *a, **k: None

        def close(self) -> None:
            kolejnosc.append("close")

    class SledzonyWidok(RecordingView):
        def block(self, block: Block) -> None:
            kolejnosc.append("block")
            super().block(block)

    asystent = ScriptedAssistant([wynik_dla(miasto=["Białystok"], pkd=["4100A"])])
    deps = cast(
        "Deps",
        SimpleNamespace(assistant=asystent, assistant_reason=None, events=SledzoneZdarzenia()),
    )

    collect_from_description(
        deps,
        ScriptedPrompter({"zatwierdz_interpretacje": "tak"}),
        SledzonyWidok(),
        opis="firmy budowlane w Białymstoku",
    )

    assert kolejnosc.index("close") < kolejnosc.index("block"), kolejnosc
