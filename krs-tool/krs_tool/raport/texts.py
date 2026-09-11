"""Raport jako model widoku. Moduł czysty — bez biblioteki wyjścia i bez zegara.

Ten moduł układa **wszystkie** zdania raportu; oba renderery poniżej tylko je rysują. Dzięki
temu raport daje się sprawdzić w teście bez terminala i bez pliku, a skan zamkniętego leksykonu
(reguła granic 11) ma jedno miejsce do przeczytania zamiast dwóch kanałów.

Trzy sekcje są przedmiotem odbioru kroku 5 i żadnej nie wolno skrócić redakcją:

**Sygnały.** Każdy niesie cztery rzeczy: poziom, podstawę prawną, dzień obserwacji wzięty ze
`stanZDnia` odpisu — nigdy z zegara — i **cytat z odpisu**. Sygnał bez cytatu jest
twierdzeniem bez źródła, a czytelnik nie ma jak sprawdzić, skąd się wziął.

**Nierozstrzygnięte.** Osobna sekcja, nie przypis. Reguła, której nie dało się rozstrzygnąć,
ma być tak samo widoczna jak ta, która zapłonęła — razem z tym, **kto** ją zamyka: pomiar,
zmiana w narzędziu, czy inny odpis.

**Czego to narzędzie nie twierdzi.** Wymienia wprost, czego nie widzi — w tym rejestru
dłużników niewypłacalnych, czyli najmocniejszego pojedynczego sygnału, jaki w ogóle istnieje
w tej dziedzinie. Przemilczenie źródła, do którego się nie zagląda, jest w raporcie o ryzyku
gorsze niż brak raportu: czytelnik bierze milczenie za nieobecność wpisu.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..odpis.model import RODZAJ_SPRAWOZDANIE_FINANSOWE, Dzial, Odpis
from ..signals.model import Nieustalony, Niewiadoma, Obserwacja, Ocena, Powod, Sygnal
from ..texts import ZNACZNIK_SYNTETYCZNY, Block, opis_kodu, opis_niewiadomej

NAGLOWKI_SYGNALOW = ("reguła", "poziom", "podstawa prawna", "obserwacja na dzień", "cytat z odpisu")

ZNACZNIK_DO_POTWIERDZENIA = "[DO POTWIERDZENIA]"
BRAK_CYTATU = "brak nazw pól do zacytowania"

# Kto zamyka daną niewiadomą. Rozróżnienie jest w wyniku od kroku 4; tutaj zamienia się na
# zdanie, bo czytelnik raportu ma wiedzieć, na co czeka, a nie tylko że czeka.
KTO_ZAMYKA = {
    Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC.value: (
        "pomiar albo stanowisko ministerstwa — z samego odpisu tego nie da się ustalić"
    ),
    Powod.CZYTNIK_NIE_WYCIAGA_DANEJ.value: "zmiana w tym narzędziu — rejestr tę daną niesie",
    Powod.ODPIS_NIE_ROZSTRZYGA.value: "inny odpis albo wgląd w zawartość działu",
    Powod.OBSERWACJA.value: "stan tego odpisu",
}

CZEGO_NIE_TWIERDZI = (
    "Nie widzi rejestru dłużników niewypłacalnych. To jest najmocniejszy pojedynczy sygnał "
    "w tej dziedzinie i narzędzie do niego nie zagląda — brak wpisu w tym raporcie nie znaczy, "
    "że tam nic nie ma.",
    "Nie ocenia, czy dokumenty złożono w ustawowym czasie. Rejestr publikuje datę złożenia, "
    "nie datę zatwierdzenia, więc z danych publicznych tego się nie da wyprowadzić.",
    "Nie czyta treści sprawozdań finansowych — żadnych kwot, wskaźników ani opinii biegłego.",
    "Nie łączy się z rejestrem. Wszystko powyżej pochodzi z pliku, który zapisał operator, "
    "i opisuje stan rejestru na podany dzień, nie stan dzisiejszy.",
    "Nie mówi, który wpis stoi w niepustym dziale. Cytowane nazwy pól są przepisane z pliku "
    "i nie zostały z niczym zestawione.",
    "Nie ocenia osób fizycznych ani jednoosobowych działalności — te są poza rejestrem "
    "przedsiębiorców KRS i poza zakresem tego narzędzia.",
)


@dataclass(frozen=True)
class Raport:
    """Raport gotowy do narysowania w dowolnym kanale."""

    tytul: str
    sekcje: tuple[Block, ...]

    def as_text(self) -> str:
        return "\n\n".join([self.tytul, *(sekcja.as_text() for sekcja in self.sekcje)])


def zbuduj_raport(ocena: Ocena, odpis: Odpis) -> Raport:
    """Raport z oceny i odpisu, z którego ta ocena powstała.

    Odpis jest tu drugi raz — po to, żeby sekcja sygnałów mogła **zacytować** to, na czym
    sygnał stoi. Warstwa sygnałów tego cytatu nie niesie i nieść nie może: nazwy pól w dziale
    są dla niej niewidoczne (reguła granic 13), bo reguła przypięta do zgadniętej nazwy jest
    nie do odróżnienia od przypiętej do zmierzonej.
    """
    tytul = f"raport o sygnałach rejestrowych — {ocena.nazwa}"
    return Raport(
        tytul=f"{ZNACZNIK_SYNTETYCZNY} — {tytul}" if ocena.syntetyczny else tytul,
        sekcje=(
            _sekcja_podmiotu(ocena, odpis),
            _sekcja_sygnalow(ocena, odpis),
            _sekcja_nierozstrzygnietych(ocena),
            _sekcja_wykluczonych(ocena),
            _sekcja_czego_nie_twierdzi(ocena),
        ),
    )


def _sekcja_podmiotu(ocena: Ocena, odpis: Odpis) -> Block:
    return Block(
        title="podmiot",
        headers=("pole", "wartość"),
        rows=(
            ("nazwa", ocena.nazwa),
            ("numer KRS", ocena.numer),
            ("forma prawna", odpis.forma_prawna or "nie podano"),
            ("NIP", odpis.nip or "nie podano"),
            ("stan rejestru na dzień", ocena.stan_z_dnia.isoformat()),
            ("źródło", "plik odpisu zapisany przez operatora"),
        ),
        notes=(ZNACZNIK_SYNTETYCZNY,) if ocena.syntetyczny else (),
    )


def _sekcja_sygnalow(ocena: Ocena, odpis: Odpis) -> Block:
    sygnaly = ocena.sygnaly()
    wiersze = tuple(_wiersz_sygnalu(sygnal, ocena, odpis) for sygnal in sygnaly)
    uwagi = [_podsumowanie_sygnalow(sygnaly)]
    niepotwierdzone = [s.regula.kod for s in sygnaly if not s.regula.podstawa_potwierdzona]
    if niepotwierdzone:
        uwagi.append(
            "Podstawa prawna nie została potwierdzona w tekście ustawy przy: "
            + ", ".join(niepotwierdzone)
            + ". Do czasu potwierdzenia traktuj ten wiersz jako wskazówkę, nie jako podstawę."
        )
    uwagi.extend(_uwagi_o_zalozeniach(sygnaly))
    return Block(title="sygnały", headers=NAGLOWKI_SYGNALOW, rows=wiersze, notes=tuple(uwagi))


def _podsumowanie_sygnalow(sygnaly: tuple[Sygnal, ...]) -> str:
    if not sygnaly:
        return (
            "Żadna reguła katalogu nie zapłonęła na tym odpisie. To nie znaczy, że podmiot jest "
            "bez ryzyka — znaczy, że w tym, co ten odpis pokazuje, nie ma wpisu z katalogu."
        )
    return f"Sygnałów: {len(sygnaly)}. Każdy dotyczy wpisu w rejestrze, nie oceny podmiotu."


def _uwagi_o_zalozeniach(wyniki: Sequence[Sygnal] | Sequence[Nieustalony]) -> list[str]:
    """Założenia werdyktów tej sekcji — jedno zdanie, bez powtórzeń.

    Stoją przy sekcji, w której stoi werdykt, bo założenie oderwane od wyniku nie znaczy nic.
    """
    zalozenia: list[Niewiadoma] = [z for wynik in wyniki for z in wynik.zalozenia]
    if not zalozenia:
        return []
    return [
        "Założenia, przy których policzono powyższe: "
        + "; ".join(sorted({opis_niewiadomej(z) for z in zalozenia}))
        + "."
    ]


def _wiersz_sygnalu(sygnal: Sygnal, ocena: Ocena, odpis: Odpis) -> tuple[str, ...]:
    """Cztery rzeczy plus kod reguły. Golden-test pada, gdy którakolwiek zniknie."""
    podstawa = sygnal.regula.podstawa_prawna
    if not sygnal.regula.podstawa_potwierdzona:
        podstawa = f"{podstawa}  {ZNACZNIK_DO_POTWIERDZENIA}"
    return (
        sygnal.regula.kod,
        sygnal.regula.poziom.name.lower(),
        podstawa,
        ocena.stan_z_dnia.isoformat(),
        _cytat(sygnal, odpis),
    )


def _cytat(sygnal: Sygnal, odpis: Odpis) -> str:
    """Dosłowny fragment odpisu, na którym stoi sygnał.

    Przy sygnale z działu cytujemy **nazwy pól przepisane z pliku**, bo to jedyne, co plik
    o tym dziale mówi ponad jego niepustość. Przy sygnale o braku wzmianki cytujemy surowy
    zapis okresu ostatniej wzmianki — ten, który rejestr wystawił, nie nasz przekład.
    """
    if sygnal.obserwacja is Obserwacja.DZIAL_NIEPUSTY:
        numer = sygnal.regula.numer_dzialu
        dzial = odpis.dzial(numer) if numer is not None else None
        return _cytat_dzialu(dzial)
    if sygnal.obserwacja is Obserwacja.BRAK_WZMIANKI_ZA_OKRES:
        return _cytat_ostatniej_wzmianki(odpis)
    return opis_kodu(sygnal.obserwacja.value)


def _cytat_dzialu(dzial: Dzial | None) -> str:
    if dzial is None or not dzial.klucze:
        return BRAK_CYTATU
    return f"dział {dzial.numer}, pola w pliku: " + ", ".join(dzial.klucze)


def _cytat_ostatniej_wzmianki(odpis: Odpis) -> str:
    wzmianki = [
        w
        for w in odpis.wzmianki
        if w.rodzaj == RODZAJ_SPRAWOZDANIE_FINANSOWE and w.okres is not None
    ]
    if not wzmianki:
        return BRAK_CYTATU
    ostatnia = max(wzmianki, key=lambda w: w.data_zlozenia)
    return (
        f"ostatnia wzmianka: {ostatnia.zapis_okresu}, złożono {ostatnia.data_zlozenia.isoformat()}"
    )


def _sekcja_nierozstrzygnietych(ocena: Ocena) -> Block:
    wiersze = tuple(
        (wynik.regula.kod, opis_kodu(niewiadoma.kod), KTO_ZAMYKA.get(niewiadoma.powod.value, ""))
        for wynik in ocena.nieustalone()
        for niewiadoma in wynik.nierozstrzygniete
    )
    return Block(
        title="nierozstrzygnięte",
        headers=("reguła", "czego nie ustalono", "kto to zamyka"),
        rows=wiersze,
        notes=(
            _podsumowanie_nierozstrzygnietych(ocena.nieustalone()),
            *_uwagi_o_zalozeniach(ocena.nieustalone()),
        ),
    )


def _podsumowanie_nierozstrzygnietych(nieustalone: tuple[Nieustalony, ...]) -> str:
    if not nieustalone:
        return "Każda reguła katalogu została rozstrzygnięta na tym odpisie."
    return (
        f"Reguł nierozstrzygniętych: {len(nieustalone)}. Nierozstrzygnięte to nie jest ani "
        "słabsze tak, ani słabsze nie — to znaczy, że tego z tego odpisu nie widać."
    )


def _sekcja_wykluczonych(ocena: Ocena) -> Block:
    wykluczone = ocena.wykluczone()
    return Block(
        title="wykluczone",
        headers=("reguła", "dlaczego nie dotyczy"),
        rows=tuple((w.regula.kod, opis_kodu(w.powod)) for w in wykluczone),
        notes=(
            f"Reguł wykluczonych: {len(wykluczone)}. Wykluczenie jest rozstrzygnięciem: "
            "tego wpisu w odpisie nie ma.",
        ),
    )


def _sekcja_czego_nie_twierdzi(ocena: Ocena) -> Block:
    uwagi = list(CZEGO_NIE_TWIERDZI)
    if ocena.syntetyczny:
        uwagi.insert(
            0, f"{ZNACZNIK_SYNTETYCZNY}. Ten raport nie opisuje żadnej istniejącej spółki."
        )
    return Block(title="czego to narzędzie nie twierdzi", notes=tuple(uwagi))
