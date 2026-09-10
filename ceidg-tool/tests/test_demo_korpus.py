"""Korpus demo: dane wymyślone, rozkłady zmierzone (ADR-0014).

Po co osobny plik dla atrapy, która nikogo nie obsługuje: bo korpus **jest** materiałem
dowodowym pokazu. Demo, które pokazuje same szczęśliwe przypadki, uczy operatora nieprawdy
o rejestrze — nie padnie żadne z ostrzeżeń, które narzędzie wypisuje właśnie dlatego, że
rejestr taki nie jest. Dlatego testy niżej pilnują nie „czy się buduje", tylko czy niesie
własności, dla których został wygenerowany: oba roczniki PKD, statusy inne niż `AKTYWNY`
w każdym województwie, daty sprzed 1990, wrogą nazwę i identyfikatory w kanonicznej postaci.

Druga połowa to prowenienacja. Nazwy PKD w korpusie mają pochodzić **ze słownika**, a nie
z pamięci — CLAUDE.md zapisuje to jako pułapkę odwracającą jedyną kontrolę operatora:
ekran potwierdzenia pokazuje *nazwę* kodu, więc zły kod ma się czytać jako zła branża,
a zmyślona nazwa sprawia, że ekran zgadza się sam ze sobą. Korpus podlega tu tej samej
kontroli co reszta projektu: konfrontacji z wygenerowanym `pkd2025.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

import ceidg_tool.demo.korpus as korpus_mod
from ceidg_tool.assistant.pkd import DEFAULT_PKD_PATH, load_pkd
from ceidg_tool.criteria import nip_checksum_ok, regon_checksum_ok
from ceidg_tool.demo.korpus import (
    PARY_PKD,
    PREFIKS_DEMO,
    Korpus,
    WpisDemo,
    zbuduj_korpus,
    zmienione_w_oknie,
)
from ceidg_tool.errors import ConfigError
from ceidg_tool.pkdmap import DEFAULT_PKD_MAP_PATH, load_pkd_map
from ceidg_tool.recordid import GUID_WPISU, kanoniczny_id
from ceidg_tool.richtext import safe
from ceidg_tool.safetext import sanitize_text
from tests.support import criteria

# Odcisk domyślnego korpusu: SHA-256 pełnej treści wszystkich rekordów, pierwsze 16 znaków.
# Przypięty w teście, a nie liczony w locie, bo to jedyna forma, która wychwytuje **zależność
# od procesu**. Dwa wywołania w jednym procesie zgodzą się także wtedy, gdy generator korzysta
# z `hash()` napisu albo z globalnego `random` — a `PYTHONHASHSEED` jest losowany przy każdym
# starcie, więc pokaz puszczony drugiego dnia pokazywałby inne firmy niż wczoraj. Wartość
# zmierzona 2026-09-08 na `zbuduj_korpus()` bez argumentów.
#
# Treść, a nie same identyfikatory: odcisk po samych `id` nie widziałby ani statusu, ani
# rocznika PKD, ani dat — czyli wszystkiego, co losowanie faktycznie ustala.
#
# **Czym ta stała nie jest.** Nie opisuje „właściwej" zawartości korpusu i nie wolno jej
# czytać jako pomiaru: pilnuje wyłącznie tego, że dwa **procesy** widzą to samo. Świadoma
# zmiana korpusu (inne kody PKD, inne pola) zmienia ją z definicji i wtedy się ją aktualizuje
# — ale tylko razem z taką zmianą i nigdy po to, żeby test zzieleniał. Ta różnica jest tu
# wypisana, bo audyt 2026-09-08 znalazł w tym projekcie stałą zaktualizowaną do wygenerowanego
# pliku wbrew komentarzowi, który tego zabraniał (`test_pkdmap_data.py`).
#
# Zmierzona 2026-09-08 na `zbuduj_korpus()` bez argumentów, po dwóch świadomych zmianach
# korpusu tego dnia: przejściu nazw PKD na słownik i doprowadzeniu rekordu `/firmy` do
# kształtu, jaki ma w rejestrze (pełny `wlasciciel`, `link`, cały adres, część wpisów bez
# adresu). Każda z nich zmieniła tę wartość z definicji.
#
# Zmieniona 2026-09-10 przez trzecią świadomą zmianę: numer lokalu i spółka cywilna wspólnika,
# czyli dane dla filtrów `lokal`, `nip_sc` i `regon_sc` dopisanych dla parytetu z publiczną
# wyszukiwarką. Oba pola liczą się z numeru wpisu, a nie z `rng`, więc **reszta korpusu jest
# bit w bit ta sama** — odcisk zmienia się wyłącznie o zawartość nowych pól, a liczby, które
# `docs/demo-walkthrough.md` podaje jako przebieg pokazu, zostają w mocy.
ODCISK_KORPUSU = "a6dfb061705dd7c2"

# Wpis o indeksie 0 niesie nazwę wrogą i leży w wielkopolskiem, czyli w tym województwie,
# o które pokaz pyta. Wroga nazwa w podlaskiem nie pokazałaby niczego.
INDEKS_WROGIEGO = 0


def odcisk(korpus: Korpus) -> str:
    tresc = json.dumps(
        [w.jako_szczegoly(korpus.nazwy(w.rok_pkd)) for w in korpus.wpisy],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(tresc.encode()).hexdigest()[:16]


def wartosci(obiekt: Any) -> list[tuple[str, Any]]:
    """Wszystkie pary klucz-wartość rekordu, także zagnieżdżone (adres, właściciel, PKD)."""
    pary: list[tuple[str, Any]] = []
    if isinstance(obiekt, dict):
        for klucz, wartosc in obiekt.items():
            pary.append((klucz, wartosc))
            pary.extend(wartosci(wartosc))
    elif isinstance(obiekt, list):
        for element in obiekt:
            pary.extend(wartosci(element))
    return pary


@pytest.fixture(scope="module")
def korpus() -> Korpus:
    return zbuduj_korpus()


# ----------------------------------------------------------------------------- determinizm


def test_korpus_jest_ten_sam_w_kazdym_procesie(korpus: Korpus) -> None:
    """Pokaz ma być pokazem, a nie ruletką: te same firmy dziś i jutro, tu i u nowego właściciela.

    Odcisk przypięty na stałe łapie to, czego dwa wywołania w jednym procesie nie złapią —
    generator zależny od `hash()` albo od globalnego `random` zgadzałby się sam ze sobą
    w obrębie procesu i rozjeżdżał między uruchomieniami.
    """
    assert odcisk(korpus) == ODCISK_KORPUSU
    assert odcisk(zbuduj_korpus()) == ODCISK_KORPUSU
    assert len(korpus) == 240


def test_inne_ziarno_daje_inny_korpus(korpus: Korpus) -> None:
    """Kontrola pozytywna dla odcisku: gdyby ziarno nic nie robiło, test wyżej byłby pusty."""
    assert odcisk(zbuduj_korpus(ziarno=1)) != ODCISK_KORPUSU


# ----------------------------------------------------------------------------- rozkłady


def test_oba_roczniki_pkd_sa_w_korpusie_w_zmierzonej_proporcji(korpus: Korpus) -> None:
    """Rejestr jest w połowie przejścia (58,6 % rocznika 2007 — `docs/decisions.md`).

    Korpus z jednym rocznikiem pokazywałby narzędzie, w którym ostrzeżenie o luce pokrycia
    PKD nigdy nie pada, a jest to jedno ze zdań, dla których demo istnieje.
    """
    udzial_2007 = sum(w.rok_pkd == "2007" for w in korpus.wpisy) / len(korpus)

    assert {w.rok_pkd for w in korpus.wpisy} == {"2007", "2025"}
    assert 0.50 <= udzial_2007 <= 0.68, f"udział rocznika 2007 wynosi {udzial_2007:.2%}"


def test_kazde_wojewodztwo_niesie_wiecej_niz_jeden_status(korpus: Korpus) -> None:
    """Status ma być niezależny od województwa — inaczej filtr chowa całe klasy wpisów.

    To jest zamknięty defekt: `MIASTA` i `STATUSY` mają po pięć pozycji, więc indeksowanie
    obu tym samym `numer % 5` wiązało je na sztywno i pokaz filtrowany po wielkopolskiem
    oddawał 144 wpisy, wszystkie `AKTYWNY`. Zdanie „raport nie obejmuje wykreślonych" nie
    miało wtedy jak paść.
    """
    statusy: dict[str, set[str]] = {}
    for wpis in korpus.wpisy:
        statusy.setdefault(wpis.wojewodztwo, set()).add(wpis.status)

    assert len(statusy) >= 2, "korpus ma pokrywać więcej niż jedno województwo"
    for wojewodztwo, obecne in statusy.items():
        assert {"WYKRESLONY", "ZAWIESZONY"} <= obecne, f"{wojewodztwo}: statusy {sorted(obecne)}"


def test_sa_wpisy_rozpoczete_przed_1990(korpus: Korpus) -> None:
    """To one wypadają z zakresu przy pobieraniu w partiach — bez nich podział dat jest teorią."""
    najstarszy = min(w.data_rozpoczecia for w in korpus.wpisy)

    assert najstarszy.year < 1990, f"najstarszy wpis zaczyna się w {najstarszy.year}"


def test_czesc_wpisow_nie_ma_telefonu_ani_e_maila(korpus: Korpus) -> None:
    """Kontakty są w rejestrze dobrowolne, a podsumowanie mówi o tym operatorowi wprost.

    Korpus z kompletem kontaktów kazałby temu zdaniu wyglądać na awarię narzędzia; korpus
    bez ani jednego — na awarię danych. Zdanie jest prawdziwe tylko wtedy, gdy jest oba.
    """
    z_telefonem = sum(bool(w.telefon) for w in korpus.wpisy)
    z_mailem = sum(bool(w.email) for w in korpus.wpisy)

    assert 0 < z_telefonem < len(korpus)
    assert 0 < z_mailem < len(korpus)


# ----------------------------------------------------------------------------- wroga nazwa


def test_wroga_nazwa_lezy_w_wojewodztwie_o_ktore_pokaz_pyta(korpus: Korpus) -> None:
    """Wroga nazwa poza wynikiem zapytania jest ozdobą, nie dowodem.

    Wcześniej stała pod indeksem 3, czyli w podlaskiem — a pokaz pyta o wielkopolskie,
    więc `safetext` nie pojawiał się w skoroszycie ani razu.
    """
    wrogi = korpus.wpisy[INDEKS_WROGIEGO]

    assert wrogi.wojewodztwo == "wielkopolskie"
    assert wrogi.nazwa.startswith("=")
    assert "[" in wrogi.nazwa and "]" in wrogi.nazwa


def test_wroga_nazwa_jest_neutralizowana_przez_oba_zabezpieczenia(korpus: Korpus) -> None:
    """Arkusz chroni apostrof przed formułą, terminal — `richtext.safe` przed znacznikami `rich`.

    Jeden wpis, dwa mechanizmy: bez tego asercja o obecności wrogiej nazwy mówiłaby tylko,
    że korpus jest niebezpieczny, a nie że narzędzie sobie z nim radzi.
    """
    nazwa = korpus.wpisy[INDEKS_WROGIEGO].nazwa

    w_arkuszu = sanitize_text(nazwa)
    na_ekranie = safe(nazwa)

    assert w_arkuszu.startswith("'="), "arkusz wykonałby to jako formułę"
    assert w_arkuszu[1:] == nazwa
    # `rich` czyta `[red]` jako znacznik; `Text` niesie napis dosłownie, bez parsowania.
    assert na_ekranie.plain == nazwa
    assert na_ekranie.markup != na_ekranie.plain


# ----------------------------------------------------------------------------- tożsamość wpisu


def test_identyfikatory_maja_ksztalt_i_kanonizacje_identyfikatora_wpisu(korpus: Korpus) -> None:
    """Identyfikator demo przechodzi tę samą kanonizację co produkcyjny (ADR-0013).

    Prefiks jest szesnastkowy właśnie po to: gdyby nie był, `kanoniczny_id` przepuszczałby
    identyfikatory demo nietknięte i pokaz omijałby regułę, na której stoi tożsamość wpisu.
    """
    for wpis in korpus.wpisy:
        assert GUID_WPISU.fullmatch(wpis.id), f"{wpis.id!r} nie jest szesnastkowym GUID-em"
        assert wpis.id.startswith(PREFIKS_DEMO)
        assert kanoniczny_id(wpis.id) == wpis.id
        # Pisownia z `/zmiana` wraca do postaci kanonicznej — to jest cała treść ADR-0013.
        assert kanoniczny_id(wpis.id.lower()) == wpis.id

    assert len({w.id for w in korpus.wpisy}) == len(korpus), "identyfikatory się powtarzają"


def test_nip_i_regon_przechodza_walidacje_kryteriow(korpus: Korpus) -> None:
    """`Criteria` sprawdza sumę kontrolną, więc korpus z byle jakim NIP-em byłby nie do wyszukania.

    Suma liczona tu ponownie przez **produkcyjne** `nip_checksum_ok`, a nie przez prywatną
    funkcję z `demo.korpus`: atrapa sprawdzana własnym rachunkiem zgadza się sama ze sobą.
    """
    for wpis in korpus.wpisy:
        assert nip_checksum_ok(wpis.nip), wpis.nip
        assert regon_checksum_ok(wpis.regon), wpis.regon

    wybrany = korpus.wpisy[0]
    zapytanie = criteria(nip=wybrany.nip, regon=wybrany.regon)
    assert zapytanie.nip == (wybrany.nip,)


def test_nip_ma_prefiks_spoza_puli_urzedow_skarbowych(korpus: Korpus) -> None:
    """`999` nie jest przypisane żadnemu urzędowi, więc numer jest poprawny i **niczyj**.

    To jedyna rzecz dzieląca „dane wymyślone" od „dane cudze" w numerze o zdefiniowanej
    sumie kontrolnej: bez tego generator produkowałby prawdziwe NIP-y prawdziwych firm.
    """
    assert {w.nip[:3] for w in korpus.wpisy} == {"999"}


def test_zaden_wpis_nie_niesie_liczby_o_dlugosci_pesel(korpus: Korpus) -> None:
    """Identyfikatory osób nie są generowane w ogóle — i to ma być sprawdzalne, nie deklarowane.

    Token CEIDG niesie PESEL i cały ten projekt trzyma go poza logami, bazą i plikami.
    Korpus, który dorabiałby jedenastocyfrowy numer, wprowadzałby tę samą klasę danych
    tylnymi drzwiami, w pliku, który wolno wysłać komukolwiek.
    """
    jedenascie_cyfr = re.compile(r"\d{11}")
    trafienia = [
        (klucz, wartosc)
        for wpis in korpus.wpisy
        for klucz, wartosc in wartosci(wpis.jako_szczegoly(korpus.nazwy(wpis.rok_pkd)))
        if isinstance(wartosc, str) and jedenascie_cyfr.fullmatch(wartosc.replace(" ", ""))
    ]

    klucze = {
        k.lower()
        for wpis in korpus.wpisy
        for k, _ in wartosci(wpis.jako_szczegoly(korpus.nazwy(wpis.rok_pkd)))
    }

    assert trafienia == []
    assert not [k for k in klucze if "pesel" in k]


# ----------------------------------------------------------------------------- prowenienacja PKD


# Testy prowenienacji nazw PKD. Były `xfail(strict=True)` — korpus niósł `5610A` i `8690E`
# jako kody PKD 2025 (a one w PKD 2025 nie istnieją, to kody 2007) oraz trzy nazwy przepisane
# z pamięci. Naprawione 2026-09-08: `korpus.py` nie zapisuje już **żadnej** nazwy, tylko pary
# kodów, a nazwy czyta z `pkd2025.yaml` i `pkd2007_2025.yaml`. Znacznik zdjęty, bo `strict`
# zapaliłby się na nieoczekiwanym przejściu — a testy zostają, bo pilnują reguły, nie zdarzenia.


@pytest.mark.skipif(not DEFAULT_PKD_PATH.is_file(), reason="brak pkd2025.yaml")
def test_kody_rocznika_2025_istnieja_w_dostarczonym_slowniku() -> None:
    """Wpis deklarujący `rokPkd: 2025` ma nieść kody, które w PKD 2025 istnieją.

    Atrapa niosąca kod spoza rocznika, który sama deklaruje, jest tym rodzajem fikcji,
    przez który faza 4 wybrała zły słownik (`tests/conftest.py`, `rokPkd`). Tutaj stawka
    jest ta sama: `--pkd` z takim kodem odbije się od słownika asystenta, a rejestr demo
    odpowie na niego trafieniami — więc pokaz uczyłby, że kod działa.
    """
    slownik = load_pkd()

    brakujace = sorted(nowy for _, nowy in PARY_PKD if nowy not in slownik)

    assert brakujace == [], f"kody spoza PKD 2025: {brakujace}"


@pytest.mark.skipif(not DEFAULT_PKD_PATH.is_file(), reason="brak pkd2025.yaml")
def test_nazwy_kodow_2025_pochodza_ze_slownika_a_nie_z_pamieci() -> None:
    """Nazwa PKD jest jedyną kontrolą operatora — zmyślona odwraca ją przeciwko niemu.

    Ekran potwierdzenia pokazuje **nazwę** kodu, więc zły kod ma się czytać jako zła
    branża. Nazwa napisana z pamięci sprawia, że ekran zgadza się z tym, co generator
    myślał, że kod znaczy, zamiast z klasyfikacją (CLAUDE.md, ADR-0012).
    """
    slownik = load_pkd()
    korpus = zbuduj_korpus(ile=5)

    rozne = {kod: (nazwa, slownik.get(kod)) for kod, nazwa in korpus.nazwy_2025.items()}
    rozne = {kod: para for kod, para in rozne.items() if para[0] != para[1]}

    assert rozne == {}, f"nazwy niezgodne ze słownikiem: {rozne}"


@pytest.mark.skipif(not DEFAULT_PKD_MAP_PATH.is_file(), reason="brak pkd2007_2025.yaml")
def test_nazwy_kodow_2007_pochodza_z_klucza_przejscia() -> None:
    """Ta sama kontrola po drugiej stronie przejścia — tablica GUS zna nazwy rocznika 2007."""
    tablica = load_pkd_map()
    korpus = zbuduj_korpus(ile=5)

    rozne = {
        kod: (nazwa, tablica.nazwa_2007(kod))
        for kod, nazwa in korpus.nazwy_2007.items()
        if tablica.nazwa_2007(kod) != nazwa
    }

    assert rozne == {}, f"nazwy niezgodne z kluczem przejścia: {rozne}"


@pytest.mark.skipif(not DEFAULT_PKD_PATH.is_file(), reason="brak pkd2025.yaml")
def test_kod_spoza_slownika_wywraca_budowe_korpusu_zamiast_przejsc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reguła, nie zdarzenie: następnym razem ma paść od razu i głośno.

    Poprzednia wersja przechodziła każdą automatyczną kontrolę, bo kontroli nie było —
    kody i nazwy były po prostu wpisane. Ten test pilnuje, że wpisanie kodu spoza słownika
    kończy się błędem konfiguracji, a nie korpusem, który uczy nieprawdy."""
    monkeypatch.setattr(korpus_mod, "PARY_PKD", (("9602Z", "NIE-MA-1"),))

    with pytest.raises(ConfigError, match="spoza słownika PKD"):
        zbuduj_korpus(ile=5)


def test_para_9602z_9621z_stoi_po_obu_stronach_przejscia() -> None:
    """Na tej parze widać, że filtr po kodzie 2025 nie sięga wpisu z rocznika 2007.

    `9602Z` jest najczęstszym kodem nieosiągalnym przez `pkd2025.yaml` (6 811 rekordów
    w zmierzonym raporcie), a demo istnieje między innymi po to, żeby to pokazać na żywo.
    """
    korpus = zbuduj_korpus(ile=5)

    assert "9602Z" in korpus.nazwy_2007
    assert "9621Z" in korpus.nazwy_2025
    assert "9602Z" not in korpus.nazwy_2025


# ----------------------------------------------------------------------------- okno zmian


def test_zmienione_w_oknie_zaleza_wylacznie_od_granic_okna() -> None:
    """To samo okno pytane dwa razy oddaje ten sam zbiór — na tym stoi pokaz cache szczegółów.

    Pierwsze `aktualizuj` kupuje szczegóły, drugie dla tego samego okna nie kupuje ani
    jednego. Przy zbiorze losowym tej naprawy nie dałoby się pokazać.
    """
    korpus = zbuduj_korpus(ile=40)
    od = datetime(2026, 9, 1, tzinfo=UTC).replace(tzinfo=None)
    do = datetime(2026, 9, 5, tzinfo=UTC).replace(tzinfo=None)

    pierwsze = zmienione_w_oknie(korpus, od, do)
    drugie = zmienione_w_oknie(korpus, od, do)
    inne_okno = zmienione_w_oknie(korpus, od, do.replace(day=6))

    assert pierwsze == drugie
    assert set(pierwsze) != set(inne_okno)


def test_zmienione_w_oknie_oddaje_identyfikatory_malymi_literami() -> None:
    """`/zmiana` tak zwraca (`tests/fixtures/api_traits.yaml`) i to jest cały ADR-0013.

    Atrapa oddająca tu pisownię wielką zgadzałaby się sama ze sobą i nie mogłaby pokazać
    defektu, przez który `aktualizuj` zapisywał każdą zmienioną firmę dwa razy.
    """
    korpus = zbuduj_korpus(ile=40)
    od, do = datetime(2026, 9, 1), datetime(2026, 9, 5)

    identyfikatory = zmienione_w_oknie(korpus, od, do)

    assert identyfikatory, "okno bez żadnej zmiany nie pokazałby niczego"
    assert all(rid == rid.lower() for rid in identyfikatory)
    assert set(kanoniczny_id(rid) for rid in identyfikatory) <= {w.id for w in korpus.wpisy}


# ----------------------------------------------------------------------------- kształt rekordu


def test_lista_niesie_mniej_pol_niz_szczegoly() -> None:
    """`/firmy` i `/firma` różnią się zawartością — inaczej pobranie szczegółów byłoby bez sensu.

    Atrapa oddająca ten sam rekord na obu endpointach kasowałaby całą decyzję „lista czy
    szczegóły", którą tabela kosztów stawia operatorowi.
    """
    korpus = zbuduj_korpus(ile=1)
    wpis: WpisDemo = korpus.wpisy[0]

    lista = wpis.jako_lista()
    szczegoly = wpis.jako_szczegoly(korpus.nazwy(wpis.rok_pkd))

    assert set(lista) < set(szczegoly)
    # Różnica jest **zmierzona**, nie wymyślona: lista niesie pełne `wlasciciel` (z imieniem
    # i nazwiskiem), `link` i cały adres — tak wygląda 196 prawdziwych rekordów w `fixtures/`
    # i `probe_out/`. Szczegóły dokładają PKD, kontakt, adres korespondencyjny, obywatelstwa
    # i numer statusu. Pierwsza wersja atrapy okrajała listę do NIP-u, REGON-u i dwóch pól
    # adresu, przez co demo namawiało na szczegóły dowodem, którego rejestr nie dostarcza.
    dokladane = set(szczegoly) - set(lista)
    zawsze = {
        "pkd",
        "pkdGlowny",
        "rokPkd",
        "telefon",
        "email",
        "adresKorespondencyjny",
        "obywatelstwa",
        "numerStatusu",
    }
    # `spolki` dochodzi **warunkowo**, bo rejestr pomija pole, gdy przedsiębiorca nie jest
    # wspólnikiem. Twarde dopisanie go do zbioru przechodziłoby tylko dlatego, że wpis nr 0
    # akurat spółkę ma — czyli test opisywałby jeden wpis, a nie regułę.
    assert dokladane == zawsze | ({"spolki"} if wpis.spolka_nip else set())
    assert set(lista["wlasciciel"]) == {"imie", "nazwisko", "nip", "regon"}
    assert lista["link"] and "ulica" in lista["adresDzialalnosci"]
    # Ten sam wpis, ten sam identyfikator — kształt się różni, tożsamość nie.
    assert lista["id"] == szczegoly["id"] == wpis.id
    assert json.dumps(szczegoly, ensure_ascii=False)  # rekord musi dać się serializować


def test_lokal_i_spolka_sa_w_korpusie_i_nie_sa_sprzezone_z_adresem(korpus: Korpus) -> None:
    """Filtry `lokal`, `nip_sc` i `regon_sc` muszą mieć w pokazie co zwracać — i co pominąć.

    Dwie własności naraz, bo obie da się zepsuć jedną liczbą. Rozkład: numer lokalu jest
    w rejestrze wypełniony w 23 % wierszy (ADR-0016), a korpus, w którym byłby zawsze albo
    nigdy, czyniłby filtr niesprawdzalnym w jedną albo w drugą stronę. Niezależność: dzielniki
    (13 i 11) są pierwsze wobec długości `MIASTA` (5) i `ULICE` (6), więc lokale i spółki nie
    zbierają się w jednym mieście — to ten sam defekt sprzężenia, przez który każde
    województwo miało kiedyś zawsze ten sam status.
    """
    z_lokalem = [w for w in korpus.wpisy if w.numer_lokalu]
    ze_spolka = [w for w in korpus.wpisy if w.spolka_nip]

    assert 0.15 <= len(z_lokalem) / len(korpus) <= 0.32
    assert len(ze_spolka) >= 10
    assert len({w.miasto for w in z_lokalem}) > 1
    assert len({w.miasto for w in ze_spolka}) > 1
    # Spółka to jeden podmiot: NIP bez REGON-u albo odwrotnie opisywałby dane, których
    # rejestr nie wydaje.
    assert all(bool(w.spolka_nip) == bool(w.spolka_regon) for w in korpus.wpisy)
    assert all(nip_checksum_ok(w.spolka_nip) for w in ze_spolka)
    assert all(regon_checksum_ok(w.spolka_regon) for w in ze_spolka)
