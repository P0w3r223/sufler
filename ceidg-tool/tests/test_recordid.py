"""Kanonizacja identyfikatora wpisu — co wolno podnieść do wielkich liter i czego nie wolno.

`recordid` jest jednym miejscem, w którym identyfikator przestaje być napisem od API i staje
się tożsamością wpisu (ADR-0013). Ma więc dwie połowy i obie są nośne:

* **rusza** GUID-y szesnastkowe, bo rejestr zwraca ten sam wpis wielkimi literami z `/firmy`
  i `/firma`, a małymi z `/zmiana` — bez tego `firma.id` był kluczem wrażliwym na pisownię
  i każda zmieniona firma trafiała do bazy dwa razy;
* **nie rusza** niczego innego, bo identyfikator raportu ma ten sam kształt 8-4-4-4-12, nie
  jest szesnastkowy, jest istotny co do wielkości liter (wchodzi wprost do adresu pobrania
  archiwum), a identyfikatory wierszy raportu (`NIP:…`, `REGON:…`, `HASH:<małe hex>`) są
  własnym schematem `reports.record_id_for`.

Druga połowa jest tu ważniejsza, bo nie ma własnego objawu: rozluźnienie wzorca do
`[0-9A-Za-z]` zepsułoby pobieranie raportów i dorobiłoby wierszom raportu drugą tożsamość —
ten sam defekt, tylko na drugim źródle i bez nocnego runu, który by go pokazał.
"""

from __future__ import annotations

import hashlib

import pytest

from ceidg_tool.recordid import GUID_WPISU, kanoniczne_id, kanoniczny_id
from ceidg_tool.reports import record_id_for

# Ten sam wpis w trzech pisowniach. Górna i dolna są zmierzone (`tests/fixtures/api_traits.yaml`);
# mieszana nie pada z API, ale przechodzi przez tę samą gałąź kodu i pilnuje, żeby kanonizacja
# nie zależała od tego, *jak bardzo* napis jest mały.
GUID_UPPER = "18578BAF-BAC7-42B9-AA7E-4C3666132E69"
GUID_LOWER = "18578baf-bac7-42b9-aa7e-4c3666132e69"
GUID_MIXED = "18578Baf-BaC7-42b9-Aa7E-4c3666132E69"

# Produkcyjny kształt identyfikatora `/raporty`: 8-4-4-4-12 i litery spoza zestawu
# szesnastkowego (`j`, `r`, `u`, `d`, `M`, `R`…). Wchodzi wprost do adresu pobrania.
RAPORT_ID = "F3Aj3APe-9rud-A9rR-IId9-jMe3FedeR3e9"


@pytest.mark.parametrize("pisownia", [GUID_UPPER, GUID_LOWER, GUID_MIXED])
def test_wszystkie_pisownie_jednego_wpisu_daja_jedna_tozsamosc(pisownia: str) -> None:
    """Sedno ADR-0013: równość identyfikatorów jest równością wpisów, nie napisów."""
    assert kanoniczny_id(pisownia) == GUID_UPPER


def test_dwie_pisownie_z_dwoch_endpointow_sa_tym_samym_kluczem() -> None:
    """Napisy różne, tożsamość jedna — to jest zdanie, którego baza nie umiała powiedzieć.

    `/zmiana` oddaje małymi, `/firma` wielkimi; dopóki klucz główny był wrażliwy na
    wielkość liter, para poniżej opisywała dwa wiersze zamiast jednego."""
    assert GUID_LOWER != GUID_UPPER
    assert kanoniczny_id(GUID_LOWER) == kanoniczny_id(GUID_UPPER)


def test_kanonizacja_jest_idempotentna() -> None:
    """`store._record_id` woła ją po `client._records_of` — drugi raz nie może nic zmienić."""
    raz = kanoniczny_id(GUID_LOWER)
    assert kanoniczny_id(raz) == raz


@pytest.mark.parametrize(
    "wartosc",
    [GUID_UPPER, GUID_LOWER, GUID_MIXED, RAPORT_ID, "NIP:1234567890", "", "cokolwiek"],
)
def test_kanonizacja_zmienia_wylacznie_wielkosc_liter(wartosc: str) -> None:
    """Niezmiennik obejmujący obie gałęzie: wolno zmienić pisownię, nie wolno treści.

    Gdyby kanonizacja kiedykolwiek zaczęła obcinać, dopełniać albo normalizować myślniki,
    identyfikator z bazy przestałby pasować do identyfikatora z adresu URL — a to jest
    dokładnie ta klasa błędu, którą ten moduł ma zamykać, nie otwierać."""
    assert kanoniczny_id(wartosc).lower() == wartosc.lower()
    assert len(kanoniczny_id(wartosc)) == len(wartosc)


def test_identyfikator_raportu_przechodzi_nietkniety() -> None:
    """Kształt GUID-a, ale nie szesnastkowy — i istotny co do wielkości liter."""
    assert kanoniczny_id(RAPORT_ID) == RAPORT_ID
    assert GUID_WPISU.fullmatch(RAPORT_ID) is None


def test_identyfikatory_raportow_roznia_sie_pisownia() -> None:
    """Kontrola odwrotna do tej dla wpisów: tu ujednolicenie pisowni **jest** defektem.

    Adres pobrania archiwum niesie identyfikator dosłownie, więc dwie pisownie to dwa różne
    raporty, z których jeden nie istnieje. Test padnie, gdy ktoś rozluźni `GUID_WPISU`."""
    assert kanoniczny_id(RAPORT_ID) != kanoniczny_id(RAPORT_ID.upper())


def test_identyfikatory_wierszy_raportu_przechodza_nietkniete() -> None:
    """`NIP:…`, `REGON:…`, `HASH:<małe hex>` — własny schemat `reports.record_id_for`.

    Skrót jest zapisany małymi literami, więc rozluźnienie wzorca kanonizacji nadałoby
    wierszom raportu drugą tożsamość — ten sam defekt, tylko na drugim źródle."""
    nip = record_id_for({"Nip": "1234567890"})
    regon = record_id_for({"Regon": "123456785"})
    skrot = record_id_for({"NazwaPodmiotu": "Firma bez numerów"})
    assert skrot.startswith("HASH:") and skrot[5:].islower()
    for rid in (nip, regon, skrot):
        assert kanoniczny_id(rid) == rid


def test_skrot_wiersza_raportu_nie_jest_guidem_mimo_szesnastkowosci() -> None:
    """Sam skrót, bez przedrostka, też ma zostać w spokoju — brakuje mu myślników.

    To jest ta granica, o którą naprawdę chodzi: `HASH:` chroni przedrostkiem, ale wzorzec
    ma odrzucać ciąg szesnastkowy również wtedy, gdy nikt go nie oznaczył."""
    digest = hashlib.sha256(b"wiersz").hexdigest()[:32]
    assert GUID_WPISU.fullmatch(digest) is None
    assert kanoniczny_id(digest) == digest


@pytest.mark.parametrize(
    ("etykieta", "wartosc"),
    [
        ("bez myślników", "18578BAFBAC742B9AA7E4C3666132E69"),
        ("w klamrach", "{18578BAF-BAC7-42B9-AA7E-4C3666132E69}"),
        ("z przedrostkiem urn", "urn:uuid:18578BAF-BAC7-42B9-AA7E-4C3666132E69"),
        ("ze spacją na końcu", "18578BAF-BAC7-42B9-AA7E-4C3666132E69 "),
        ("ze spacją na początku", " 18578BAF-BAC7-42B9-AA7E-4C3666132E69"),
        ("z nową linią", "18578BAF-BAC7-42B9-AA7E-4C3666132E69\n"),
        ("o grupę za krótki", "18578BAF-BAC7-42B9-AA7E"),
        ("z grupą o złej długości", "18578BAF-BAC-42B9-AA7E-4C3666132E69"),
        ("o znak za długi", "18578BAF-BAC7-42B9-AA7E-4C3666132E690"),
        ("pusty", ""),
    ],
)
def test_ksztalty_bliskie_guidowi_nie_sa_kanonizowane(etykieta: str, wartosc: str) -> None:
    """Wzorzec dopina się do całości napisu — sąsiedzi GUID-a nie są GUID-ami.

    Biały znak na końcu ma tu osobną pozycję, bo `$` w wyrażeniu sam z siebie przepuszcza
    ogon `\\n`; całości pilnuje dopiero `fullmatch`. Kanonizacja napisu z ogonem podniosłaby
    do wielkich liter klucz, który i tak nie pasuje do żadnego innego zapisu — czyli zrobiła
    by z jednej tożsamości dwie, w drugą stronę niż defekt z ADR-0013."""
    assert kanoniczny_id(wartosc) == wartosc, etykieta


@pytest.mark.parametrize("pozycja", [0, 9, 14, 19, 24, 35])
def test_litera_spoza_zestawu_szesnastkowego_wyklucza_guid_wpisu(pozycja: int) -> None:
    """Każda z pięciu grup musi być szesnastkowa — to odróżnia wpis od raportu.

    Pozycje są brane z obu krańców każdej grupy, bo wzorzec pisany ręcznie łatwo rozluźnić
    w jednej z nich i nie zauważyć: pozostałe cztery nadal by go trzymały."""
    znaki = list(GUID_UPPER)
    znaki[pozycja] = "G"
    podrobka = "".join(znaki)
    assert GUID_WPISU.fullmatch(podrobka) is None
    assert kanoniczny_id(podrobka) == podrobka


def test_kanoniczne_id_zachowuje_kolejnosc_i_powtorzenia() -> None:
    """Kolejność jest nośna: `link_ids` zapisuje pozycję identyfikatora na stronie."""
    wejscie = [GUID_LOWER, RAPORT_ID, GUID_UPPER, GUID_LOWER]
    assert kanoniczne_id(wejscie) == [GUID_UPPER, RAPORT_ID, GUID_UPPER, GUID_UPPER]


def test_kanoniczne_id_pustej_sekwencji() -> None:
    """Strona `/zmiana` bez identyfikatorów kończy paginację, a nie wywraca kanonizacji."""
    assert kanoniczne_id([]) == []
