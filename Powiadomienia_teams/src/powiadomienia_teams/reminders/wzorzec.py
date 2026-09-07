"""Wnioskowanie wzorca pracy z historii czterech tygodni (czysta funkcja, zero I/O).

Dzisiejsza propozycja to »jak w zeszłym tygodniu« (`propose.proposal_from_last_week`). W pracy
zmianowej jest fałszywa u każdego, kto pracuje w rytmie przemiennym: osoba na cyklu ranna /
popołudniowa dostaje co tydzień propozycję z niewłaściwej połowy cyklu. Ten moduł liczy wzorzec
z HISTORII i mówi, **jak bardzo można mu wierzyć** — bo propozycja jest jedno „tak" od
nieodwracalnego zapisu (N1), a `create_shift` nie deduplikuje.

**Kto liczy wzorzec: kod.** To ta sama rodzina zadań co N22 (daty i dni liczy kod, nie model):
deterministyczna funkcja nad danymi z Graph, którą da się przypiąć złotym korpusem i sprawdzić
mutacją. Model dostaje gotową propozycję i zajmuje się rozumieniem odpowiedzi człowieka.

Dwie reguły nadrzędne, obie z planu rozwoju (D1), obie nośne dla bezpieczeństwa:

- **Twarda reguła alfabetu.** Zbiór grafików dnia, które wolno zaproponować, to dokładnie zbiór
  zaobserwowany w historii. Żadnego uśredniania: dla osoby pracującej 6:00–14:00 i 14:00–22:00
  propozycją nigdy nie będzie 10:00–18:00, choć to poprawna średnia.
- **Dopasowanie dokładne.** 6:00–14:00 i 6:15–14:00 to dwa różne grafiki i tak ma zostać.
  Tolerancja wymagałaby reguły zaokrąglania, a każda taka reguła jest cichym wymyślaniem godzin.

Priorytet całej pozycji: **lepiej częściej pytać, niż częściej zgadywać.** Algorytm, który raz na
dwadzieścia razy zaproponuje sensownie wyglądającą bzdurę, jest gorszy od takiego, który mówi
„nie wiem, powiedz mi sam" — dlatego każda wątpliwość (remis, jeden tydzień danych, świeża zmiana
wzorca) schodzi w dół tabeli pewności, a nie w górę.

Rozstrzygnięcia, których plan nie zawierał, podjęte przy pisaniu i policzone korpusem:

1. **Pewność całej propozycji bierze się z dnia NAJSŁABSZEGO.** Wiadomość jest jedna na cały
   tydzień, więc człowiek musi wiedzieć, jak bardzo przyglądać się temu, co najsłabiej udowodnione.
2. **Remis w interwale modalnym (2:2) to »rozrzut bez wzorca«**, nie losowanie zwycięzcy.
3. **Nieobecność nie jest dowodem o rytmie pracy — na ŻADNYM poziomie.** Tydzień urlopowy nie może
   przeczyć wzorcowi (reguła świeżości go pomija), a dzień urlopowy jest dla swojego weekdaya
   BRAKIEM DANYCH, nie „dniem wolnym". Drugie wyszło z przeglądu: samo odsiewanie tygodni
   (`_PROG_DNI_URLOPU`) puszczało cztery długie weekendy pod rząd — cztery tygodnie po dwa dni
   urlopu, każdy poniżej progu — i dawało propozycję „poniedziałek i piątek wolne" z pewnością
   WYSOKĄ oraz wsparciem 4 z 4. Liczba wsparcia **wzmacniała** wtedy złą odpowiedź.
4. **Brak danych o jednym dniu zabiera CAŁĄ propozycję**, a nie sam ten dzień: propozycja bez
   jednego dnia jest dla wołającego nieodróżnialna od propozycji z tym dniem wolnym. Wariant
   dokładniejszy („o piątku nic nie wiem") wymaga odbiorcy, który umie to powiedzieć człowiekowi,
   więc należy do wpięcia w obieg, nie tutaj.

**Do ustalenia przy wpinaniu w obieg:** normalizacja `motyw`. Dopasowanie jest dokładne, więc
`None`, `""` i różnica wielkości liter z adaptera Graph rozszczepią alfabet — kierunek jest
bezpieczny (spadek wsparcia albo remis, czyli mniej propozycji), ale potrafi zamienić realny
wzorzec w „brak propozycji" bez śladu. Ten moduł nie ma jak się przed tym bronić, bo świadomie
nie zna `Shift`.

**Czego ten moduł świadomie NIE rozstrzyga (decyzja klienta 4.2/4):** czy dzień oznaczony jako
wolny, w który wchodzi nocka z dnia poprzedniego, liczy się do odsiewania tygodni
nieinformatywnych. `dni_urlopu` jest tu brane od wołającego bez korekty o zmiany przechodzące
przez północ; utrwala to `test_wzorzec.py` osobnym testem opisanym jako stan faktyczny, nie
decyzja. Złoty korpus nie ma dla tego przypadku pozycji — dopisze ją dopiero rozstrzygnięcie.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import NamedTuple

# Odsiewanie tygodni: urlop przez WIĘKSZOŚĆ dni tygodnia (4 z 7). Taki tydzień nie jest dowodem,
# że osoba „zwykle nie pracuje" — wchodzi do mianownika (ile mamy tygodni), nie do licznika
# (ile potwierdza dany grafik). Bez tego dwutygodniowy urlop kasowałby wzorzec całego kwartału.
_PROG_DNI_URLOPU = 4

# Alternacja rozpoznawalna jednoznacznie dopiero przy czterech tygodniach: przy trzech ciąg
# A, B, A jest nieodróżnialny od „przeważnie A z jednym wyjątkiem", a to dwie różne propozycje.
_MIN_TYGODNI_ALTERNACJI = 4

# Progi wsparcia dla dnia: ile tygodni z danymi potwierdza proponowany grafik.
_WSPARCIE_WYSOKIE = 3
_WSPARCIE_MINIMALNE = 2

# Gdy KAŻDY dzień rozstrzygnęła alternacja, nie ma dnia, który mógłby być najsłabszy. Tabela
# pewności wymienia „czystą alternację" jako osobną podstawę pewności WYSOKIEJ, więc pusty zbiór
# dni zwykłych wchodzi do oceny z wartością progu, a nie z zerem.
_WSPARCIE_CZYSTEJ_ALTERNACJI = _WSPARCIE_WYSOKIE

_DNI_TYGODNIA = range(7)


class Interwal(NamedTuple):
    """Jedna zmiana w rozkładzie dnia — ściana zegara lokalnego, nie instant UTC.

    `motyw` to kolor Shifts, czyli tryb pracy (blue = zdalnie, green = stacjonarnie). Wchodzi do
    tożsamości interwału świadomie: 8:00–16:00 zdalnie i 8:00–16:00 stacjonarnie to dla człowieka
    dwa różne dni, a reguła alfabetu zabrania proponować cokolwiek, czego nie było w historii.

    `koniec` mniejszy lub równy `poczatek` oznacza zmianę przechodzącą przez północ (nocka) —
    ten sam niezmiennik co w `domain.models.Shift`: zmiana należy do dnia, w którym się ZACZYNA.
    """

    poczatek: time
    koniec: time
    motyw: str | None = None


#: Rozkład jednego dnia. Pusty zbiór to dzień wolny — i jest pełnoprawną wartością alfabetu:
#: stabilny weekend jest wzorcem dokładnie tak samo jak stabilna zmiana ranna.
DzienGrafiku = frozenset[Interwal]


@dataclass(frozen=True)
class TydzienHistorii:
    """Jeden tydzień odczytany z Shifts.

    `dni` kluczowane weekdayem (0 = poniedziałek … 6 = niedziela). Brak klucza znaczy **dzień
    bez pracy**, nie »brak danych« — odczyt Graph obejmuje cały tydzień, więc nieobecność zmiany
    jest informacją, a nie luką.

    `dni_urlopu` to dni z wpisem czasu wolnego. Działają na DWÓCH poziomach: tydzień z urlopem
    przez większość dni jest nieinformatywny w całości, a pojedynczy dzień urlopowy jest brakiem
    danych dla swojego weekdaya. Patrz docstring modułu, punkt 3, i decyzja 4.2/4.
    """

    poczatek: date
    dni: Mapping[int, DzienGrafiku] = field(default_factory=dict)
    dni_urlopu: frozenset[int] = frozenset()

    def dzien(self, weekday: int) -> DzienGrafiku:
        return self.dni.get(weekday, frozenset())

    @property
    def nieinformatywny(self) -> bool:
        return len(self.dni_urlopu) >= _PROG_DNI_URLOPU


class Pewnosc(Enum):
    """Jak bardzo człowiek ma się przyglądać propozycji. Funkcja bezpieczeństwa, nie kosmetyka."""

    WYSOKA = "wysoka"
    SREDNIA = "srednia"
    NISKA = "niska"
    BRAK = "brak"


class Podstawa(Enum):
    """Klucz zdania, którym propozycja tłumaczy się pracownikowi.

    Enum, nie tekst: treść wiadomości jest stałą w `messages.py` i nie wolno jej składać tutaj
    (N13 — do pracownika nie trafia żaden tekst wygenerowany poza zamkniętym zbiorem stałych).
    """

    WZORZEC_POTWIERDZONY = "wzorzec_potwierdzony"
    BYWALO_ROZNIE = "bywalo_roznie"
    OSTATNI_TYDZIEN = "ostatni_tydzien"
    BRAK_HISTORII = "brak_historii"


@dataclass(frozen=True)
class Propozycja:
    """Wynik wnioskowania.

    `dni` puste znaczy **brak propozycji z wzorca** — wołający ma wtedy zejść do dzisiejszego
    zachowania (»jak w zeszłym tygodniu«) albo, przy `Pewnosc.BRAK`, zadać pytanie otwarte.
    Rozróżnia je `podstawa`.

    `wsparcie` (dzień → ile tygodni z danymi potwierdza jego grafik) jest po to, żeby dało się
    zobaczyć, DLACZEGO pewność jest taka, a nie inna — bez czytania kodu.
    """

    dni: Mapping[int, DzienGrafiku]
    pewnosc: Pewnosc
    podstawa: Podstawa
    wsparcie: Mapping[int, int] = field(default_factory=dict)


def wnioskuj(historia: Sequence[TydzienHistorii]) -> Propozycja:
    """Wywnioskuj grafik na najbliższy tydzień z historii.

    `historia[0]` to tydzień NAJŚWIEŻSZY (W-1), `historia[-1]` najstarszy. Kolejność jest częścią
    kontraktu — na niej stoi reguła świeżości i rozpoznanie alternacji.

    Kroki, w tej kolejności: odsiej tygodnie nieinformatywne → dla każdego dnia osobno odrzuć
    obserwacje z dni urlopowych → rozpoznaj alternację albo grafik modalny → policz wsparcie →
    sprawdź, czy W-1 nie przeczy → zejdź do dzisiejszego zachowania, jeśli wzorca nie ma.
    """
    if not historia:
        return _bez_propozycji(Pewnosc.BRAK, Podstawa.BRAK_HISTORII)

    informatywne = [
        (indeks, tydzien) for indeks, tydzien in enumerate(historia) if not tydzien.nieinformatywny
    ]
    if not informatywne:
        # Sam urlop w całym oknie. Nie ma czego wnioskować, ale to nie jest „brak historii":
        # pracownik ma za sobą tydzień, do którego wolno się odwołać.
        return _bez_propozycji(Pewnosc.NISKA, Podstawa.OSTATNI_TYDZIEN)

    dni: dict[int, DzienGrafiku] = {}
    wsparcie: dict[int, int] = {}
    alternacyjne: set[int] = set()

    for weekday in _DNI_TYGODNIA:
        # Dzień urlopowy to dla tego weekdaya BRAK DANYCH, nie „dzień wolny". Odsiewanie na
        # poziomie tygodnia (`_PROG_DNI_URLOPU`) tego nie łapie: cztery długie weekendy pod rząd
        # to cztery tygodnie po dwa dni urlopu, czyli poniżej progu — a przed tą poprawką dawały
        # propozycję „poniedziałek i piątek wolne" z pewnością WYSOKĄ i wsparciem 4 z 4, czyli
        # z liczbą, która złą odpowiedź WZMACNIAŁA. Nieobecność nie jest dowodem na rytm pracy —
        # ta sama zasada, która na poziomie tygodnia stoi w `_przeczy_wzorcowi`.
        obserwacje = [
            (indeks, tydzien.dzien(weekday))
            for indeks, tydzien in informatywne
            if weekday not in tydzien.dni_urlopu
        ]
        if len(obserwacje) < _WSPARCIE_MINIMALNE:
            # Za mało dowodów o tym dniu. Świadomie schodzi CAŁA propozycja, a nie sam dzień:
            # pominięcie dnia w wyniku jest dla wołającego nieodróżnialne od „ten dzień jest
            # wolny", czyli od twierdzenia, którego nie mamy z czego postawić. Cena: pracownik
            # z powtarzalnym urlopem w jeden dzień tygodnia dostaje dzisiejsze »jak w zeszłym
            # tygodniu«. Rozwiązanie dokładniejsze (propozycja częściowa) wymaga odbiorcy, który
            # umie powiedzieć „o piątku nic nie wiem" — czyli należy do wpięcia w obieg.
            return _bez_propozycji(Pewnosc.NISKA, Podstawa.OSTATNI_TYDZIEN)

        kontynuacja = _kontynuacja_alternacji(obserwacje)
        if kontynuacja is not None:
            dni[weekday] = kontynuacja
            wsparcie[weekday] = [rozklad for _, rozklad in obserwacje].count(kontynuacja)
            alternacyjne.add(weekday)
            continue

        modalny = _modalny([rozklad for _, rozklad in obserwacje])
        if modalny is None:
            # Remis — dwa grafiki z tym samym wsparciem. Wybór któregokolwiek byłby zgadywaniem
            # pod pozorem obliczenia, więc cała propozycja schodzi do dzisiejszego zachowania.
            #
            # Dlaczego CAŁA, a nie sam dzień sporny: propozycja bez jednego dnia jest dla
            # wołającego nieodróżnialna od propozycji z tym dniem wolnym. Reguła „pewność z dnia
            # najsłabszego" opisuje dzień, o którym coś wiemy słabo; tutaj nie wiemy nic.
            return _bez_propozycji(Pewnosc.NISKA, Podstawa.OSTATNI_TYDZIEN)
        dni[weekday] = modalny
        wsparcie[weekday] = [rozklad for _, rozklad in obserwacje].count(modalny)

    return _oceniona(
        dni=dni,
        wsparcie=wsparcie,
        alternacyjne=alternacyjne,
        najswiezszy=historia[0],
    )


def _oceniona(
    *,
    dni: Mapping[int, DzienGrafiku],
    wsparcie: Mapping[int, int],
    alternacyjne: set[int],
    najswiezszy: TydzienHistorii,
) -> Propozycja:
    """Przypisz pewność wyliczonej propozycji — patrz tabela w planie rozwoju (D1).

    Wiersz tabeli „albo tylko 2 tygodnie danych" NIE ma tu własnego warunku i nie jest to
    przeoczenie: wsparcie nie może przekroczyć długości okna, więc dwa tygodnie dają najwyżej
    dwa potwierdzenia, a to jest poniżej progu pewności wysokiej. Warunek na długość okna stał tu
    do sondy mutacyjnej D1 i był NIEOSIĄGALNY — czytał się jak zabezpieczenie, którym nie był.
    Własność pilnuje `test_wzorzec.py::test_dwa_tygodnie_nigdy_nie_daja_pewnosci_wysokiej`.
    """
    zwykle = [ile for weekday, ile in wsparcie.items() if weekday not in alternacyjne]
    najslabszy_dzien = min(zwykle) if zwykle else _WSPARCIE_CZYSTEJ_ALTERNACJI

    if najslabszy_dzien < _WSPARCIE_MINIMALNE:
        # Jeden tydzień danych albo rozrzut — wzorzec nie dokłada niczego ponad »jak ostatnio«.
        return _bez_propozycji(Pewnosc.NISKA, Podstawa.OSTATNI_TYDZIEN)

    if _przeczy_wzorcowi(najswiezszy, dni, alternacyjne):
        # Reguła świeżości. Osoba, która właśnie przeszła z rannej na popołudniową, dostawałaby
        # przez trzy tygodnie WYSOKĄ pewność dla odpowiedzi nieaktualnej — a to gorsze niż
        # dzisiejsze
        # »jak w zeszłym tygodniu«, bo tamto patrzy przynajmniej na właściwy tydzień.
        return Propozycja(dni, Pewnosc.SREDNIA, Podstawa.BYWALO_ROZNIE, wsparcie)

    if najslabszy_dzien >= _WSPARCIE_WYSOKIE:
        return Propozycja(dni, Pewnosc.WYSOKA, Podstawa.WZORZEC_POTWIERDZONY, wsparcie)

    return Propozycja(dni, Pewnosc.SREDNIA, Podstawa.BYWALO_ROZNIE, wsparcie)


def _bez_propozycji(pewnosc: Pewnosc, podstawa: Podstawa) -> Propozycja:
    return Propozycja({}, pewnosc, podstawa, {})


def _modalny(obserwacje: Sequence[DzienGrafiku]) -> DzienGrafiku | None:
    """Najczęstszy rozkład dnia; `None`, gdy dwa są tak samo częste (remis)."""
    licznik = Counter(obserwacje).most_common()
    if len(licznik) > 1 and licznik[0][1] == licznik[1][1]:
        return None
    return licznik[0][0]


def _kontynuacja_alternacji(
    obserwacje: Sequence[tuple[int, DzienGrafiku]],
) -> DzienGrafiku | None:
    """Rozkład na najbliższy tydzień, jeśli dzień chodzi w rytmie A, B, A, B — inaczej `None`.

    Każda obserwacja niesie SWÓJ INDEKS w oryginalnej historii (0 = W-1), bo alternacja jest
    własnością ciągu tygodni SĄSIEDNICH. Odsianie tygodnia w środku okna — urlopowego albo
    z urlopem w tym jednym dniu — zwija listę i skleja fazy, których obok siebie nie było:
    `[A, urlop, B, A, B]` wygląda wtedy jak czysta alternacja, choć chronologicznie jej PRZECZY.
    Stąd dwa warunki: obserwacje muszą być kolejne i muszą zaczynać się od W-1 (bez najświeższego
    tygodnia nie wiadomo, w której połowie rytmu jest tydzień docelowy).

    Ciąg musi być ścisły na całym oknie: jedno odstępstwo znaczy, że to nie alternacja, tylko
    przewaga z wyjątkiem — a te dwie rzeczy dają różne propozycje.
    """
    if len(obserwacje) < _MIN_TYGODNI_ALTERNACJI:
        return None

    indeksy = [indeks for indeks, _ in obserwacje]
    if indeksy != list(range(len(indeksy))):
        return None

    rozklady = [rozklad for _, rozklad in obserwacje]
    ostatni, przedostatni = rozklady[0], rozklady[1]
    if ostatni == przedostatni:
        return None

    for indeks, rozklad in enumerate(rozklady):
        oczekiwany = ostatni if indeks % 2 == 0 else przedostatni
        if rozklad != oczekiwany:
            return None

    # Po W-1 (`ostatni`) w rytmie przemiennym idzie ten drugi.
    return przedostatni


def _przeczy_wzorcowi(
    najswiezszy: TydzienHistorii,
    dni: Mapping[int, DzienGrafiku],
    alternacyjne: set[int],
) -> bool:
    """Czy W-1 mówi co innego niż wywnioskowany wzorzec.

    Dni rozstrzygnięte alternacją są POMIJANE: tam różnica wobec W-1 jest właśnie wzorcem, a nie
    zaprzeczeniem. Tydzień nieinformatywny nie przeczy niczemu — urlop nie jest dowodem na zmianę
    rytmu, a bez tego wyłączenia każdy powrót z wakacji zbijałby pewność.

    **Pojedynczy dzień urlopowy jest pomijany z tego samego powodu, co cały tydzień urlopowy.**
    Rozszerzenie N32 na poziom DNIA (przegląd D1) trafiło do ``wnioskuj``, ale nie tutaj —
    a ``dzien()`` zwraca dla dnia urlopowego pusty rozkład, czyli „dzień wolny". Jeden dzień
    urlopu w W-1 ścinał więc pewność z WYSOKIEJ na ŚREDNIĄ i pracownik dostawał zdanie
    „bywało różnie" o rytmie, który się nie zmienił. Kierunek był bezpieczny (pewność w dół,
    więc N33 nietknięty), ale zdanie było po prostu nieprawdziwe.
    """
    if najswiezszy.nieinformatywny:
        return False
    return any(
        najswiezszy.dzien(weekday) != rozklad
        for weekday, rozklad in dni.items()
        if weekday not in alternacyjne and weekday not in najswiezszy.dni_urlopu
    )
