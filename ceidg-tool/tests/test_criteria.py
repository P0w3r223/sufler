from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest
from pydantic import ValidationError

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.criteria import (
    Criteria,
    nip_checksum_ok,
    normalize_pkd,
    normalize_regon,
    regon_checksum_ok,
)
from tests.support import criteria


def test_nip_checksum_accepts_documented_example() -> None:
    assert nip_checksum_ok("3563457932")
    assert nip_checksum_ok("1112223344") is False


def test_nip_is_normalized_and_validated() -> None:
    c = criteria(nip=["356-345-79-32", " 3563457932 "])
    assert c.nip == ("3563457932",)
    with pytest.raises(ValidationError, match="sumę kontrolną"):
        criteria(nip="3563457933")
    with pytest.raises(ValidationError, match="10 cyfr"):
        criteria(nip="12345")


def test_regon_checksum_9_and_14_digits() -> None:
    assert regon_checksum_ok("618155359")
    assert normalize_regon("618-155-359") == "618155359"
    assert regon_checksum_ok("618155358") is False
    with pytest.raises(ValueError):
        normalize_regon("1234")


def test_pkd_is_canonicalized_to_compact_upper() -> None:
    assert normalize_pkd("62.01.Z") == "6201Z"
    assert normalize_pkd("6201z") == "6201Z"
    with pytest.raises(ValueError):
        normalize_pkd("62.01")
    c = criteria(pkd=["62.01.Z", "6201Z", "01.11.z"])
    assert c.pkd == ("0111Z", "6201Z")  # kanonicznie posortowane


def test_status_accepts_lowercase_and_rejects_unknown() -> None:
    assert criteria(status="aktywny").status == ("AKTYWNY",)
    with pytest.raises(ValidationError):
        criteria(status=["AKTYWNY", "NIEISTNIEJACY"])


def test_wojewodztwo_case_insensitive_but_known() -> None:
    assert criteria(wojewodztwo="PODLASKIE").wojewodztwo == ("podlaskie",)
    with pytest.raises(ValidationError, match="nieznane województwo"):
        criteria(wojewodztwo="podlaskiee")


def test_dates_must_be_in_order() -> None:
    criteria(data_od=date(2014, 1, 1), data_do=date(2014, 12, 31))
    with pytest.raises(ValidationError, match="data_od"):
        criteria(data_od=date(2015, 1, 1), data_do=date(2014, 12, 31))


def test_kod_pocztowy_format() -> None:
    assert criteria(kod="15-333").kod == ("15-333",)
    with pytest.raises(ValidationError):
        criteria(kod="15333")


def test_single_string_becomes_tuple_and_empties_are_dropped() -> None:
    c = criteria(miasto="Białystok", nazwa=["", "  ", "Adam"])
    assert c.miasto == ("Białystok",)
    assert c.nazwa == ("Adam",)


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Criteria.model_validate({"page": 1})


def test_to_params_renders_dialect_from_profile(profile: ApiProfile) -> None:
    c = criteria(
        wojewodztwo="podlaskie",
        pkd="62.01.Z",
        status=["ZAWIESZONY", "AKTYWNY"],
        data_od=date(2014, 1, 1),
        data_do=date(2014, 12, 31),
        nip="3563457932",
    )
    assert c.to_params(profile) == [
        ("datado", "2014-12-31"),
        ("dataod", "2014-01-01"),
        ("nip", "3563457932"),
        ("pkd", "6201Z"),
        ("status", "AKTYWNY"),
        ("status", "ZAWIESZONY"),
        ("wojewodztwo", "PODLASKIE"),
    ]

    dotted = profile.model_copy(
        update={"pkd_format": "dotted", "wojewodztwo_case": "lower", "list_param_suffix": "[]"}
    )
    assert ("pkd[]", "62.01.Z") in c.to_params(dotted)
    assert ("wojewodztwo[]", "podlaskie") in c.to_params(dotted)


def test_fingerprint_is_stable_and_order_independent() -> None:
    a = criteria(status=["AKTYWNY", "ZAWIESZONY"], wojewodztwo="podlaskie")
    b = criteria(wojewodztwo="PODLASKIE", status=["AKTYWNY", "ZAWIESZONY"])
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint() != criteria(wojewodztwo="podlaskie").fingerprint()
    assert len(a.fingerprint()) == 16


def test_is_empty_and_describe() -> None:
    assert Criteria().is_empty()
    c = criteria(wojewodztwo="podlaskie", data_od=date(2014, 1, 1), szczegoly=True)
    assert not c.is_empty()
    text = c.describe()
    assert "województwo: podlaskie" in text
    assert "2014-01-01" in text
    assert "ze szczegółami" in text


# ------------------------------------------------------- rocznik PKD 2007 (ADR-0012)

WASKIE = criteria(miasto="Łomża", pkd="9621Z")
SZEROKIE = criteria(miasto="Łomża", pkd="9621Z", pkd_2007="9602Z")


# Pola dopisane do `Criteria` już po tym, jak narzędzie zaczęło zapisywać odciski w bazie.
# Każde z nich musi znikać z `canonical_json`, dopóki jest puste — inaczej pierwsza
# aktualizacja po przerwanym pobraniu zabiera operatorowi wznowienie.
POLA_DOPISANE_PO_FAKCIE = ("pkd_2007", "nip_sc", "regon_sc", "budynek", "lokal")


def odcisk_bez_pol(c: Criteria, pola: tuple[str, ...]) -> str:
    """Odcisk policzony algorytmem sprzed dopisania `pola` — czyli po prostu bez nich.

    Liczony tutaj, a nie wołany z produkcji: gdyby test sięgnął po `canonical_json()`,
    sprawdzałby zgodność funkcji z samą sobą. Chodzi o coś innego — czy odciski zapisane
    w bazie **przed** aktualizacją nadal wskazują te same kryteria.
    """
    dane = {k: v for k, v in c.model_dump(mode="json").items() if k not in pola}
    surowy = json.dumps(dane, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(surowy.encode("utf-8")).hexdigest()[:16]


def odcisk_sprzed_adr_0012(c: Criteria) -> str:
    """Odcisk sprzed ADR-0012, czyli sprzed **wszystkich** dopisanych od tamtej pory pól.

    Przez rok istnienia tej funkcji dopisanym polem było wyłącznie `pkd_2007`, więc pomijała
    jedno. Po 2026-09-10 pomija pięć, i to nie jest kosmetyka: gdyby została przy jednym,
    test poniżej mówiłby „odciski sprzed aktualizacji przetrwały", sprawdzając odcisk, którego
    żadna wersja narzędzia nigdy nie policzyła.
    """
    return odcisk_bez_pol(c, POLA_DOPISANE_PO_FAKCIE)


def test_pkd_2007_is_validated_and_deduplicated_like_pkd() -> None:
    """To pole trafia do URL tak samo jak `pkd`, więc kontrakt musi być ten sam."""
    c = criteria(pkd="9621Z", pkd_2007=["96.02.Z", "9602z"])
    assert c.pkd_2007 == ("9602Z",)
    with pytest.raises(ValidationError, match="62.01"):
        criteria(pkd_2007="9602")


def test_both_pkd_fields_render_as_the_same_api_parameter(profile: ApiProfile) -> None:
    """API zna jeden filtr `pkd`, a powtórzone `pkd=` działa jak OR (zmierzone 2026-09-07)."""
    params = SZEROKIE.to_params(profile)

    assert [v for k, v in params if k == "pkd"] == ["9602Z", "9621Z"]
    assert WASKIE.wszystkie_pkd() == ("9621Z",)
    assert SZEROKIE.wszystkie_pkd() == ("9602Z", "9621Z")


def test_a_code_present_in_both_fields_is_sent_once(profile: ApiProfile) -> None:
    """Powtórzone `pkd=` nie zmieniłoby wyniku, ale zmieniłoby URL i odcisk — więc nie."""
    c = criteria(pkd="9621Z", pkd_2007="9621Z")

    assert c.wszystkie_pkd() == ("9621Z",)
    assert [v for k, v in c.to_params(profile) if k == "pkd"] == ["9621Z"]


def test_an_empty_vintage_field_leaves_every_earlier_fingerprint_intact() -> None:
    """Dodanie pola nie może unieważnić odcisków przebiegów sprzed aktualizacji.

    Odcisk jest kluczem do wznowienia. Gdyby puste `pkd_2007` weszło do `canonical_json`,
    każdy przerwany przebieg z poprzedniej wersji stałby się w bazie nieodnajdywalny —
    operator straciłby wznawianie w chwili, w której narzędzie się zaktualizowało.
    """
    assert "pkd_2007" not in WASKIE.canonical_json()
    assert WASKIE.fingerprint() == odcisk_sprzed_adr_0012(WASKIE)


def test_the_wide_query_keeps_its_own_fingerprint() -> None:
    """Wąskie i szerokie to dwie różne populacje — wspólny odcisk pomieszałby je w bazie."""
    assert "pkd_2007" in SZEROKIE.canonical_json()
    assert SZEROKIE.fingerprint() != WASKIE.fingerprint()
    assert SZEROKIE.fingerprint() != odcisk_sprzed_adr_0012(SZEROKIE)


def test_the_description_says_which_codes_we_added_ourselves() -> None:
    """Operator ma widzieć różnicę między tym, o co prosił, a tym, co dołożył program.

    Gdyby `9602Z` stanął w jednym rzędzie z `9621Z`, ekran przedstawiałby nasz dodatek
    jako jego własny wybór — a to jedyne miejsce, w którym tę różnicę widać.
    """
    text = SZEROKIE.describe()

    assert "PKD: 9621Z" in text
    assert "dodatkowo kody PKD 2007: 9602Z" in text
    assert "dodatkowo" not in WASKIE.describe()


def test_kryteria_przezywaja_zapis_do_bazy_i_odczyt_z_niej() -> None:
    """Zapisane kryteria mają wskazywać tę samą populację po ponownym wczytaniu.

    Test dotyczył do 2026-09-10 pliku zapytania; plik został wycofany (ADR-0022), ale ta sama
    droga tam i z powrotem zachodzi przy **wznowieniu**: `store` trzyma `criteria_json`,
    a `find_resumable` porównuje odciski policzone po obu stronach. Gdyby serializacja gubiła
    kolejność albo pole, przerwany szeroki przebieg przestałby być odnajdywalny — czyli
    dokładnie ta strata, przed którą broni `_dedupe` i walidacja w `_z_rocznikiem`.
    """
    wczytane = Criteria.model_validate(json.loads(SZEROKIE.canonical_json()))

    assert wczytane == SZEROKIE
    assert wczytane.fingerprint() == SZEROKIE.fingerprint()
    assert "pkd_2007" not in WASKIE.canonical_json()


def test_the_vintage_field_alone_is_still_a_query() -> None:
    """`pkd_2007` liczy się jako filtr — inaczej wznowienie szerokiego przebiegu zostałoby
    odrzucone jako „brak kryteriów"."""
    assert not criteria(pkd_2007="9602Z").is_empty()


# ------------------------------------------- parytet z publiczną wyszukiwarką (2026-09-10)

# Poprawne sumy kontrolne, inaczej `Criteria` odrzuci wartość, zanim test cokolwiek sprawdzi.
NIP_SPOLKI = "3563457932"
REGON_SPOLKI = "618155359"


def test_spolka_cywilna_jest_osobnym_filtrem_a_nie_odmiana_nipu(profile: ApiProfile) -> None:
    """`nip_sc` i `regon_sc` to własne parametry API — wspólne z `nip` byłyby innym pytaniem.

    Publiczna wyszukiwarka CEIDG ma je w sekcji danych podstawowych i pyta nimi o spółkę,
    do której należy przedsiębiorca, a nie o niego samego. Sklejenie ich z `nip` dałoby
    zapytanie o firmę o NIP-ie spółki — czyli o nikogo.
    """
    c = criteria(nip_sc=f" {NIP_SPOLKI} ", regon_sc=REGON_SPOLKI)
    params = dict(c.to_params(profile))

    assert c.nip_sc == (NIP_SPOLKI,) and c.regon_sc == (REGON_SPOLKI,)
    assert params["nip_sc"] == NIP_SPOLKI
    assert params["regon_sc"] == REGON_SPOLKI
    assert "nip" not in params and "regon" not in params


def test_spolka_cywilna_przechodzi_te_sama_sume_kontrolna_co_nip() -> None:
    """Literówka ma paść lokalnie, a nie po żądaniu — tak samo jak przy `nip`."""
    with pytest.raises(ValidationError, match="sumę kontrolną"):
        criteria(nip_sc="3563457933")
    with pytest.raises(ValidationError, match="9 albo 14 cyfr"):
        criteria(regon_sc="1234")


def test_numer_budynku_i_lokalu_ida_do_api_jako_tekst(profile: ApiProfile) -> None:
    """Rejestr trzyma „12A" i „3/5", więc numer jest tekstem — `int` odrzuciłby oba."""
    c = criteria(budynek="12A", lokal="3/5")
    params = dict(c.to_params(profile))

    assert params["budynek"] == "12A"
    assert params["lokal"] == "3/5"


@pytest.mark.parametrize("pole", POLA_DOPISANE_PO_FAKCIE)
def test_kazde_dopisane_pole_znika_z_odcisku_dopoki_jest_puste(pole: str) -> None:
    """Odcisk jest kluczem do wznowienia, więc dopisanie pola nie może go zmienić.

    Test parametryzowany po **spisie** pól, a nie napisany dla jednego z nich: piąte pole
    dopisane bez wpisu w `canonical_json` przewraca się tu dopiero wtedy, gdy zostanie
    dopisane także do spisu — i to jest jedyne miejsce, w którym ta zależność jest widoczna.
    """
    assert pole not in WASKIE.canonical_json()
    assert WASKIE.fingerprint() == odcisk_bez_pol(WASKIE, POLA_DOPISANE_PO_FAKCIE)


def test_kazde_dopisane_pole_zmienia_odcisk_gdy_jest_uzyte() -> None:
    """Kontrola pozytywna: pole pomijane **zawsze** kasowałoby różnicę między populacjami.

    Bez tego testu poprawnym sposobem przejścia poprzedniego byłoby wyrzucenie czwórki
    z `canonical_json` na stałe — a wtedy pobranie z filtrem po numerze lokalu i bez niego
    dzieliłyby jeden odcisk, czyli jeden przebieg w bazie.
    """
    uzycia = {
        "pkd_2007": criteria(miasto="Łomża", pkd_2007="9602Z"),
        "nip_sc": criteria(miasto="Łomża", nip_sc=NIP_SPOLKI),
        "regon_sc": criteria(miasto="Łomża", regon_sc=REGON_SPOLKI),
        "budynek": criteria(miasto="Łomża", budynek="12A"),
        "lokal": criteria(miasto="Łomża", lokal="3"),
    }
    baza = criteria(miasto="Łomża")

    assert set(uzycia) == set(POLA_DOPISANE_PO_FAKCIE)
    odciski = {baza.fingerprint(), *(c.fingerprint() for c in uzycia.values())}
    assert len(odciski) == len(uzycia) + 1


def test_numer_bez_ulicy_jest_kandydatem_do_zdjecia_przy_zerze_trafien() -> None:
    """Numer domu zeruje wynik częściej niż miejscowość, więc ma stać wyżej w poszerzeniach."""
    c = criteria(miasto="Łomża", ulica="Kwiatowa", budynek="12A", lokal="3")
    pola = [pole for pole, _ in c.poszerzenia()]

    assert pola.index("budynek") < pola.index("miasto")
    assert pola.index("lokal") < pola.index("miasto")
    assert all(not kandydat.is_empty() for _, kandydat in c.poszerzenia())
