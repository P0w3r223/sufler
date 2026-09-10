"""Syntetyczny rejestr dla trybu demo — dane wymyślone, własności zmierzone (ADR-0014).

Korpus jest **generowany**, nie nagrany. Nagranie ruchu produkcyjnego byłoby najprostsze
i jest jedyną opcją, której ten projekt nie może sobie pozwolić: anonimizator przepisuje
dziś host i GUID-y, a NIP-y w query stringach zostawia, i to on już raz skasował dokładnie
tę własność, którą testy miały sprawdzać (ADR-0013). Generowanie usuwa całą klasę tego
błędu zamiast dokładać kolejną kontrolę.

Ryzyko przenosi się za to z danych na **zachowanie**: atrapa może się rozjechać z rejestrem.
Na to jest przyrząd, który projekt już ma — `tests/fixtures/api_traits.yaml` mówi, co
zmierzono, a `tests/test_api_traits.py` trzyma przy tym fixtures. Ten korpus podlega temu
samemu testowi, więc „atrapa zgadza się sama ze sobą" nie jest tu możliwe.

Rozkłady odtworzone z produkcji (`probe_out/raport_sample.zip`, 287 256 wierszy), bo demo,
które pokazuje same szczęśliwe przypadki, uczy operatora nieprawdy:

* **58,6 % wpisów niesie rocznik PKD 2007**, a nie 2025 — więc filtr po kodzie 2025 pomija
  część rejestru i ostrzeżenie o tym ma się w demie *pokazać*, a nie być teorią;
* kilka wpisów zaczyna działalność **przed 1990** (najstarszy realny: 1957), bo to one
  wypadają z zakresu przy pobieraniu w partiach;
* jeden wpis niesie **nazwę wrogą**: wiodące `=` (formuła w arkuszu) i nawiasy kwadratowe
  (znaczniki `rich` na ekranie). `safetext` i `richtext.safe` mają być widoczne w akcji;
* są wpisy `WYKRESLONY` i `ZAWIESZONY`, bo raport ich nie obejmuje i ostrzeżenie o tym
  jest prawdziwe tylko wtedy, gdy takie wpisy istnieją.

Żadnego PESEL-u tu nie ma i nie może być: identyfikatory osób nie są generowane w ogóle.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..assistant.pkd import load_pkd
from ..errors import ConfigError
from ..pkdmap import load_pkd_map

# Prefiks zarezerwowany dla demo. Szesnastkowy, bo `recordid.GUID_WPISU` wymaga hex —
# identyfikator demo ma przechodzić tę samą kanonizację co produkcyjny, inaczej demo
# omijałoby regułę, której pilnuje ADR-0013. Widoczny w kolumnie `id` skoroszytu, więc
# jest to **konwencja czytelna dla człowieka**, nie zabezpieczenie: prawdziwym znacznikiem
# trybu są osobny katalog danych, wiersz w `Metadane`, prefiks nazwy pliku i pierwszy ekran.
PREFIKS_DEMO = "DEC0DE00"

NIP_WAGI = (6, 5, 7, 2, 3, 4, 5, 6, 7)
REGON_WAGI = (8, 9, 2, 3, 4, 5, 6, 7)

STATUSY = ("AKTYWNY", "AKTYWNY", "AKTYWNY", "ZAWIESZONY", "WYKRESLONY")

# Pary „kod 2007 → jego następca w PKD 2025", **wzięte z wygenerowanej tablicy przejścia**,
# nie wymyślone. Para jest w demie celowo: to na niej widać, że filtr po kodzie 2025 nie
# widzi wpisu, który został przy roczniku 2007.
#
# Ten moduł **nie zapisuje żadnej nazwy PKD**, i to jest naprawa, nie ozdoba. Pierwsza wersja
# miała nazwy przepisane z pamięci i zawierała `5610A` oraz `8690E` jako kody PKD 2025 — a one
# w PKD 2025 nie istnieją w ogóle; do tego trzy nazwy nie zgadzały się ze słownikiem. Jest to
# dokładnie ta pułapka, którą `CLAUDE.md` nazywa najgorszą w tym projekcie: lista nazw pisana
# z pamięci przechodzi każdą automatyczną kontrolę, a ekran potwierdzenia pokazuje operatorowi
# **nazwę** kodu, więc zły kod ma się czytać jako zła branża. Zmyślona nazwa sprawia, że ekran
# zgadza się z pomyłką. Nazwy pochodzą teraz wyłącznie z `pkd2025.yaml` i `pkd2007_2025.yaml`,
# a kod spoza słownika wywraca budowę korpusu od razu i głośno.
PARY_PKD: tuple[tuple[str, str], ...] = (
    ("9602Z", "9621Z"),  # fryzjerstwo — rozgałęzia się też na 9622Z, stąd niejednoznaczność
    ("6201Z", "6210B"),  # oprogramowanie
    ("5610A", "5611Z"),  # gastronomia
    ("8690E", "8691A"),  # opieka zdrowotna
    ("4711Z", "4791Z"),  # handel detaliczny
)

IMIONA = ("Anna", "Marek", "Katarzyna", "Piotr", "Magdalena", "Tomasz", "Ewa", "Krzysztof")
NAZWISKA = ("Kowalska", "Nowak", "Wiśniewski", "Wójcik", "Kamińska", "Lewandowski", "Zielińska")
# Miasto, powiat, województwo, kod. **Powiat nie jest tu zgadywany.** Miasto na prawach
# powiatu ma w rejestrze `powiat` równy własnej nazwie, nie przymiotnikowi — widać to
# w fixtures projektu: `miasto=Kalisz` niesie `powiat=Kalisz`, a `kaliski` należy do wsi
# `Korzeniew`; tak samo `Łódź`, `Kraków`, `Gdańsk`, `Warszawa`. Pierwsza wersja wpisała
# tu `kaliski`, `poznański`, `białostocki` i `łomżyński` z pamięci, więc operator uczący
# się wzorca na demie pisałby do produkcji zapytanie, które nie zwraca nic. Ta sama klasa
# błędu co nazwy PKD: dane odniesienia przechodzące każdą kontrolę, bo nikt ich nie sprawdza.
# `Gniezno` zostaje z `gnieźnieński`, bo prawa powiatu ma tylko pięć miast wielkopolskich
# i Gniezna wśród nich nie ma — para jest tu celowo, żeby oba kształty były w korpusie.
MIASTA = (
    ("Poznań", "Poznań", "wielkopolskie", "61-001"),
    ("Gniezno", "gnieźnieński", "wielkopolskie", "62-200"),
    ("Kalisz", "Kalisz", "wielkopolskie", "62-800"),
    ("Białystok", "Białystok", "podlaskie", "15-001"),
    ("Łomża", "Łomża", "podlaskie", "18-400"),
)
ULICE = ("Kwiatowa", "Polna", "Lipowa", "Krótka", "Ogrodowa", "Słoneczna")


def _nazwy() -> tuple[dict[str, str], dict[str, str]]:
    """Nazwy obu roczników **ze słownika**, nie z pamięci. Brak kodu = głośny błąd.

    Ładowane raz, przy budowie korpusu. `assistant.pkd.load_pkd` importuje się bez opcjonalnej
    extry `asystent` (sprawdzone), więc tryb demo nie zyskuje przez to zależności od SDK.
    """
    slownik_2025 = load_pkd()
    tablica = load_pkd_map()
    nazwy_2025: dict[str, str] = {}
    nazwy_2007: dict[str, str] = {}
    for stary, nowy in PARY_PKD:
        nazwa_nowa = slownik_2025.get(nowy)
        nazwa_stara = tablica.nazwa_2007(stary)
        if nazwa_nowa is None or nazwa_stara is None:
            raise ConfigError(
                f"Korpus demo odwołuje się do kodu spoza słownika PKD "
                f"(2007: {stary}, 2025: {nowy}). Popraw `PARY_PKD` — nazw nie wolno tu wpisywać "
                "ręcznie, bo ekran potwierdzenia pokazuje operatorowi właśnie nazwę."
            )
        # Samo istnienie obu kodów nie wystarcza. Para `9602Z → 6210B` przeszłaby tę kontrolę
        # i postawiła nazwę „programowanie" obok fryzjerskiego kodu z 2007 — czyli dokładnie
        # ten obrazek, którego ma nie być. Następstwo jest w tablicy, więc jest sprawdzalne.
        if stary not in tablica.poprzednicy(nowy):
            raise ConfigError(
                f"Para PKD w korpusie demo nie jest przejściem: {stary} (2007) nie jest "
                f"poprzednikiem {nowy} (2025) w `pkd2007_2025.yaml`."
            )
        nazwy_2025[nowy] = nazwa_nowa
        nazwy_2007[stary] = nazwa_stara
    return nazwy_2007, nazwy_2025


def _cyfra_kontrolna(cyfry: list[int], wagi: tuple[int, ...]) -> int | None:
    suma = sum(w * c for w, c in zip(wagi, cyfry, strict=True))
    reszta = suma % 11
    return None if reszta == 10 else reszta


def nip(rng: random.Random) -> str:
    """NIP z poprawną cyfrą kontrolną — inaczej walidacja `Criteria` odrzuciłaby demo.

    Prefiks `999` nie jest przypisany żadnemu urzędowi skarbowemu, więc wygenerowany numer
    jest formalnie poprawny i **nie trafia w istniejący podmiot**. To jedyna rzecz, która
    dzieli „dane wymyślone" od „dane cudze" w numerze o zdefiniowanej sumie kontrolnej."""
    while True:
        cyfry = [9, 9, 9] + [rng.randint(0, 9) for _ in range(6)]
        kontrolna = _cyfra_kontrolna(cyfry, NIP_WAGI)
        if kontrolna is not None:
            return "".join(map(str, [*cyfry, kontrolna]))


def regon(rng: random.Random) -> str:
    while True:
        cyfry = [rng.randint(0, 9) for _ in range(8)]
        kontrolna = _cyfra_kontrolna(cyfry, REGON_WAGI)
        if kontrolna is not None:
            return "".join(map(str, [*cyfry, kontrolna]))


def _spolka(numer: int, ziarno: int) -> tuple[str, str]:
    """NIP i REGON spółki cywilnej co jedenastego wpisu; dla reszty dwa puste napisy.

    Własny `Random`, zasiany numerem i ziarnem, a nie strumień korpusu — z tego samego
    powodu, dla którego numer lokalu liczy się z `numer`: dołożenie losowania do wspólnego
    strumienia przestawiłoby każdy późniejszy wpis. Ziarno wchodzi do zasiewu, bo bez niego
    dwa korpusy o różnych ziarnach opisywałyby różne firmy w tej samej spółce — to ta sama
    pułapka, którą `_id_demo` opisuje przy identyfikatorach.

    Jedno wywołanie zwraca **parę**, bo to jest jedna spółka: dwa niezależne wywołania
    dawałyby NIP jednego podmiotu przy REGON-ie innego, czyli dane, które nie mogą istnieć.
    """
    if numer % 11:
        return "", ""
    rng = random.Random(f"spolka-{ziarno}-{numer}")
    return nip(rng), regon(rng)


def _id_demo(numer: int, ziarno: int) -> str:
    """Identyfikator wpisu: kształt 8-4-4-4-12, szesnastkowy, z prefiksem demo.

    Reszta pochodzi z SHA-256 numeru porządkowego **i ziarna**, więc korpus jest
    deterministyczny — to samo demo dwa razy pokazuje te same firmy, co przy widowni jest
    różnicą między pokazem a ruletką.

    Ziarno wchodzi do skrótu, bo bez niego zmieniało wszystkie pola **poza** identyfikatorem:
    dwa korpusy o różnych ziarnach opisywały różne firmy pod tymi samymi identyfikatorami,
    a trwała baza demo scaliłaby je po cichu w jeden wpis."""
    ogon = hashlib.sha256(f"demo-{ziarno}-{numer}".encode()).hexdigest()[:20].upper()
    return f"{PREFIKS_DEMO}-{ogon[0:4]}-{ogon[4:8]}-{ogon[8:12]}-{ogon[12:24].ljust(12, '0')}"


@dataclass(frozen=True)
class WpisDemo:
    """Jeden syntetyczny przedsiębiorca. Kształt pól jak w odpowiedzi `/firma`."""

    id: str
    nazwa: str
    nip: str
    regon: str
    imie: str
    nazwisko: str
    miasto: str
    powiat: str
    wojewodztwo: str
    kod_pocztowy: str
    ulica: str
    rok_pkd: str
    pkd_glowny: str
    pkd: tuple[str, ...]
    status: str
    data_rozpoczecia: date
    telefon: str
    email: str
    numer_budynku: int
    # Numer lokalu bywa pusty częściej, niż bywa wypełniony: 23 % wierszy archiwum (ADR-0016).
    # Pusty napis, a nie `None`, bo tak zachowuje się reszta pól tekstowych tego wpisu.
    numer_lokalu: str
    # NIP i REGON spółki cywilnej wspólnika — puste dla większości wpisów. Proporcja
    # (co dwunasty) jest **wybrana, nie zmierzona**: nikt nie policzył, jaka część wpisów
    # należy do spółki cywilnej, a wiadomo tylko, że 3,16 % archiwum ma status „wyłącznie
    # w formie spółki cywilnej", co jest innym pytaniem — wspólnik może prowadzić też
    # działalność własną i mieć status aktywny. Korpus bez ani jednej spółki uczyniłby
    # filtr `nip_sc` niesprawdzalnym w pokazie, a to jedyne miejsce, gdzie go widać offline.
    spolka_nip: str
    spolka_regon: str
    # Rejestr oddaje `adresDzialalnosci: {}` dla części wpisów (76 z 196 zmierzonych).
    bez_adresu: bool

    def _adres(self) -> dict[str, Any]:
        """Adres działalności; pusty dla części wpisów, bo tak zwraca rejestr.

        Zmierzone: w 196 prawdziwych rekordach listy adres jest kompletny w 120 i **pusty**
        w 76 (`docs/decisions.md:35` notuje to samo dla starszych wpisów). Korpus, w którym
        adres jest zawsze, kazałby operatorowi liczyć na kolumnę, która na produkcji bywa
        pusta w dwóch przypadkach na pięć."""
        if self.bez_adresu:
            return {}
        adres = {
            "wojewodztwo": self.wojewodztwo,
            "powiat": self.powiat,
            "gmina": self.miasto,
            "miasto": self.miasto,
            "ulica": self.ulica,
            "budynek": str(self.numer_budynku),
            "kod": self.kod_pocztowy,
            "kraj": "Polska",
        }
        # Brakujące pole jest **pomijane**, nie wysyłane jako puste — tak zwraca rejestr
        # (`docs/decisions.md`: „absent fields are omitted, not null").
        if self.numer_lokalu:
            adres["lokal"] = self.numer_lokalu
        return adres

    def jako_lista(self) -> dict[str, Any]:
        """Rekord w kształcie `/firmy`.

        Lista jest w rejestrze **bogatsza**, niż zakładała pierwsza wersja tej atrapy:
        niesie pełne `wlasciciel` (z imieniem i nazwiskiem), `link` i cały adres — zmierzone
        na 196 rekordach w `tests/fixtures/` i `probe_out/`. Uboższa atrapa odwracała tu
        argument, dla którego demo w ogóle istnieje: operator czytał w tabeli kosztów, że
        tania gałąź daje „nazwa, NIP, REGON, adres działalności", a potem widział skoroszyt
        z dwiema kolumnami adresu — czyli demo namawiało na szczegóły dowodem, którego
        rejestr nie dostarcza. Szczegóły dokładają PKD, kontakt i adres korespondencyjny;
        i tylko tyle."""
        return {
            "id": self.id,
            "nazwa": self.nazwa,
            "wlasciciel": {
                "imie": self.imie,
                "nazwisko": self.nazwisko,
                "nip": self.nip,
                "regon": self.regon,
            },
            "adresDzialalnosci": self._adres(),
            "status": self.status,
            "dataRozpoczecia": self.data_rozpoczecia.isoformat(),
            "link": f"https://przykład.invalid/demo/{self.id}",
        }

    def jako_szczegoly(self, nazwy: Mapping[str, str]) -> dict[str, Any]:
        """Rekord w kształcie `/firma` — pełny, i to on niesie PKD oraz adres.

        Nazwy kodów przychodzą z zewnątrz, bo pochodzą ze słownika, a nie z tego modułu.
        Wpis nie ma prawa ich znać: gdyby je pamiętał, wróciłaby lista pisana z pamięci."""
        kody = nazwy
        spolki = [{"nip": self.spolka_nip, "regon": self.spolka_regon}] if self.spolka_nip else []
        return {
            "id": self.id,
            "nazwa": self.nazwa,
            "wlasciciel": {
                "imie": self.imie,
                "nazwisko": self.nazwisko,
                "nip": self.nip,
                "regon": self.regon,
            },
            "adresDzialalnosci": self._adres(),
            # Adres korespondencyjny jest w rejestrze wyłącznie w szczegółach — i to jest
            # jedna z rzeczy, które tabela kosztów obiecuje za droższą gałąź.
            "adresKorespondencyjny": self._adres(),
            "obywatelstwa": ["PL"],
            "numerStatusu": "1" if self.status == "AKTYWNY" else "2",
            "rokPkd": self.rok_pkd,
            "pkdGlowny": {"kod": self.pkd_glowny, "nazwa": kody.get(self.pkd_glowny, "")},
            "pkd": [{"kod": k, "nazwa": kody.get(k, "")} for k in self.pkd],
            "telefon": self.telefon,
            "email": self.email,
            "status": self.status,
            "dataRozpoczecia": self.data_rozpoczecia.isoformat(),
            "link": f"https://przykład.invalid/demo/{self.id}",
            # Spółki cywilne są w rejestrze wyłącznie w szczegółach — jak adres
            # korespondencyjny. Pole znika, gdy wspólnikiem nie jest: rejestr pomija
            # brakujące, zamiast wysyłać pustą listę.
            **({"spolki": spolki} if spolki else {}),
        }


@dataclass
class Korpus:
    """Zbiór wpisów demo plus nazwy PKD obu roczników. Deterministyczny przy tym ziarnie."""

    wpisy: list[WpisDemo] = field(default_factory=list)
    nazwy_2007: dict[str, str] = field(default_factory=dict)
    nazwy_2025: dict[str, str] = field(default_factory=dict)

    def nazwy(self, rok_pkd: str) -> Mapping[str, str]:
        """Nazwy kodów właściwego rocznika — jedyne źródło nazw dla odpowiedzi `/firma`."""
        return self.nazwy_2007 if rok_pkd == "2007" else self.nazwy_2025

    def __len__(self) -> int:
        return len(self.wpisy)

    def wedlug_id(self) -> dict[str, WpisDemo]:
        return {w.id: w for w in self.wpisy}


def zbuduj_korpus(*, ile: int = 240, ziarno: int = 20260908) -> Korpus:
    """Buduje korpus o zmierzonych proporcjach, deterministycznie.

    `ile` domyślnie 240, bo przy `limit=25` daje dziesięć stron — dość, by pasek postępu
    i limiter były widoczne, i mało, żeby demo mieściło się w minutach. Rozkłady nie są
    losowe „mniej więcej": rocznik PKD idzie w proporcji zmierzonej na produkcji, a wpisy
    skrajne (sprzed 1990, wroga nazwa, status inny niż aktywny) są wstawiane celowo, bo
    demo bez nich pokazywałoby narzędzie, którego ostrzeżenia nigdy nie padają.
    """
    rng = random.Random(ziarno)
    nazwy_2007, nazwy_2025 = _nazwy()
    wpisy: list[WpisDemo] = []
    for numer in range(ile):
        miasto, powiat, wojewodztwo, kod = MIASTA[numer % len(MIASTA)]
        # 58,6 % rocznika 2007 — proporcja zmierzona na 287 256 wierszach (audyt 2026-09-08).
        rok_pkd = "2007" if rng.random() < 0.586 else "2025"
        kody = list(nazwy_2007 if rok_pkd == "2007" else nazwy_2025)
        glowny = kody[numer % len(kody)]
        pozostale = tuple(k for k in rng.sample(kody, k=rng.randint(0, 2)) if k != glowny)
        imie, nazwisko = IMIONA[numer % len(IMIONA)], NAZWISKA[numer % len(NAZWISKA)]
        spolka_nip, spolka_regon = _spolka(numer, ziarno)
        # Garść wpisów sprzed 1990 — to one wypadają z zakresu przy pobieraniu w partiach.
        if numer % 47 == 0:
            start = date(1957 + numer % 30, 1 + numer % 12, 1 + numer % 28)
        else:
            start = date(2005, 1, 1) + timedelta(days=rng.randint(0, 7000))
        wpisy.append(
            WpisDemo(
                id=_id_demo(numer, ziarno),
                nazwa=f"{nazwisko} {imie} — usługi",
                nip=nip(rng),
                regon=regon(rng),
                imie=imie,
                nazwisko=nazwisko,
                miasto=miasto,
                powiat=powiat,
                wojewodztwo=wojewodztwo,
                kod_pocztowy=kod,
                ulica=ULICE[numer % len(ULICE)],
                numer_budynku=1 + numer % 90,
                # Oba pola liczą się z `numer`, a **nie** z `rng`, i to jest decyzja, nie skrót:
                # każde nowe losowanie przesuwa strumień, więc dopisanie jednego pola zmieniłoby
                # statusy, daty i kontakty wszystkich pozostałych wpisów — czyli dołożenie
                # numeru lokalu unieważniłoby liczby, które `docs/demo-walkthrough.md` podaje
                # jako przebieg pokazu. Dzielniki są pierwsze wobec długości `MIASTA` (5),
                # `ULICE` (6), `IMIONA` (8) i `NAZWISKA` (7), żeby nie powtórzyć defektu
                # sprzężenia z `numer % 5`, przez który każde województwo miało zawsze
                # ten sam status.
                # 23 % wypełnienia — proporcja zmierzona na 287 256 wierszach archiwum.
                numer_lokalu=str(1 + numer % 29) if numer % 13 < 3 else "",
                spolka_nip=spolka_nip,
                spolka_regon=spolka_regon,
                # Około dwóch na pięć wpisów bez adresu — proporcja zmierzona (76/196).
                bez_adresu=rng.random() < 0.39,
                rok_pkd=rok_pkd,
                pkd_glowny=glowny,
                pkd=(glowny, *pozostale),
                # Status z losowania, a **nie** z `numer % len(STATUSY)`. `MIASTA` i `STATUSY`
                # mają po pięć pozycji, więc indeksowanie obu tym samym `numer % 5` wiązało
                # je na sztywno: każde województwo dostawało zawsze te same statusy i pokaz
                # filtrowany po wielkopolskim pokazywał 144 wpisy, wszystkie `AKTYWNY`.
                # Ostrzeżenie o tym, że raport nie obejmuje wykreślonych, nie miało wtedy
                # jak paść — a to jedno z zdań, które demo ma czynić widocznym.
                status=rng.choice(STATUSY),
                # Kontakty są w rejestrze **dobrowolne**, więc bywają puste — i to jest
                # jedno ze zdań o ograniczeniach, które narzędzie wypisuje operatorowi.
                # Korpus bez ani jednego telefonu kazałby podsumowaniu mówić „0 (0%)",
                # czyli pokazywać to ograniczenie jako awarię, a nie jako własność danych.
                telefon=f"+48 {rng.randint(500, 899)} {rng.randint(100, 999)} "
                f"{rng.randint(100, 999)}"
                if rng.random() < 0.45
                else "",
                email=f"{nazwisko.lower()}.{imie.lower()}@przyklad.invalid"
                if rng.random() < 0.35
                else "",
                data_rozpoczecia=start,
            )
        )
    # Nazwa wroga: wiodące `=` jest formułą w arkuszu, nawiasy kwadratowe są znacznikami
    # `rich` na ekranie. Jeden wpis wystarczy, żeby oba zabezpieczenia były w demie widoczne
    # zamiast deklarowane. Wartość pochodzi z rejestru publicznego — każdy może tam wpisać
    # cokolwiek, i to jest właśnie powód, dla którego `safetext` istnieje.
    # Wpis o indeksie 0 leży w Poznaniu, więc wroga nazwa trafia do najczęściej pokazywanego
    # zapytania. Wcześniej stała pod indeksem 3, czyli w podlaskiem — poza wynikiem demo.
    wpisy[0] = _z_nazwa(wpisy[0], '=HIPERŁĄCZE("http://zły.invalid")[red]Fryzjer[/red]')
    return Korpus(wpisy, nazwy_2007=nazwy_2007, nazwy_2025=nazwy_2025)


def _z_nazwa(wpis: WpisDemo, nazwa: str) -> WpisDemo:
    return WpisDemo(**{**wpis.__dict__, "nazwa": nazwa})


def zmienione_w_oknie(korpus: Korpus, od: datetime, do: datetime, *, ile: int = 12) -> list[str]:
    """Identyfikatory „zmienione" w oknie — deterministycznie z granic okna.

    Wynik zależy wyłącznie od `[od, do]`, więc to samo okno pytane dwa razy oddaje ten sam
    zbiór. To jest własność, na której stoi demo naprawy A1: pierwsze `aktualizuj` kupuje
    szczegóły, drugie dla tego samego okna nie kupuje ani jednego — a gdyby zbiór był losowy,
    nie dałoby się tego pokazać.

    **Małymi literami**, bo `/zmiana` tak zwraca (`tests/fixtures/api_traits.yaml`), a to
    jest dokładnie ta różnica, przez którą `aktualizuj` gubił kiedyś wszystkie szczegóły.
    """
    ziarno = f"{od.isoformat()}|{do.isoformat()}"
    rng = random.Random(int(hashlib.sha256(ziarno.encode()).hexdigest()[:12], 16))
    wybrane = rng.sample(korpus.wpisy, k=min(ile, len(korpus.wpisy)))
    return [w.id.lower() for w in wybrane]
