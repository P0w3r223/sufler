"""Modele widoku i wszystkie zdania programu. Moduł czysty — bez biblioteki wyjścia.

Kształt `Block` skopiowany z `ceidg-tool/ceidg_tool/ui/texts.py` (kopia z 2026-09-10); treść
napisana od nowa. Dzięki temu każdy ekran daje się sprawdzić w teście bez terminala, a
`cli.py` nie układa ani jednego zdania (reguła granic 7) — co z kolei jest warunkiem, żeby
reguła 6 dała się w ogóle sprawdzić skanem.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .dziennik.odtworzenie import Odtworzenie
from .dziennik.zapis import Wpis
from .errors import KrsError
from .odpis.model import RODZAJ_SPRAWOZDANIE_FINANSOWE, Odpis
from .signals.katalog import Regula
from .signals.model import Nieustalony, Niewiadoma, Obserwacja, Ocena, Powod, Sygnal, Wykluczony

NAZWA = "krs-tool"

ZNACZNIK_SYNTETYCZNY = "ODPIS SYNTETYCZNY — dane wymyślone na potrzeby testów"

# Nazwy wzmianek po ludzku. Klucz spoza tej mapy pokazujemy w postaci surowej, zamiast
# pomijać — wzmianka, której nie znamy, jest informacją o rejestrze, nie śmieciem.
NAZWY_WZMIANEK = {
    RODZAJ_SPRAWOZDANIE_FINANSOWE: "sprawozdanie finansowe",
    "wzmiankaOZlozeniuOpiniiBieglegoRewidentaSprawozdaniaZBadania": "sprawozdanie z badania",
    "wzmiankaOZlozeniuUchwalyPostanowieniaOZatwierdzeniuRocznegoSprawozdaniaFinansowego": (
        "uchwała o zatwierdzeniu"
    ),
    "wzmiankaOZlozeniuSprawozdaniaZDzialalnosci": "sprawozdanie z działalności",
    "wzmiankaOZlozeniuSprawozdaniaZAtestacjiSprawozdawczosciZrownowazonegoRozwoju": (
        "atestacja sprawozdawczości zrównoważonego rozwoju"
    ),
}


@dataclass(frozen=True)
class Block:
    """Model widoku: tytuł, opcjonalna tabela, przypisy."""

    title: str
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_text(self) -> str:
        """Reprezentacja tekstowa — to na niej opierają się testy ekranów."""
        parts = [self.title]
        if self.headers:
            parts.append(" | ".join(self.headers))
        parts.extend(" | ".join(row) for row in self.rows)
        parts.extend(self.notes)
        return "\n".join(parts)


def pierwszy_ekran() -> Block:
    """Ekran powitalny.

    Niesie dwie rzeczy, które w tym projekcie są granicami, a nie ozdobą: **kogo narzędzie
    obsługuje** (spółki z KRS, nie jednoosobowe działalności — granica prawna, `docs/adr/0001`)
    oraz **że nie łączy się z rejestrem**. Operator ma to wiedzieć, zanim zapyta.
    """
    return Block(
        title=f"{NAZWA} — raport o ryzyku spółki na podstawie odpisu z KRS",
        headers=("zakres", "opis"),
        rows=(
            (
                "kogo obsługuje",
                "spółki wpisane do rejestru przedsiębiorców KRS",
            ),
            (
                "kogo nie obsługuje",
                "jednoosobowe działalności — te są w CEIDG i obsługuje je ceidg-tool",
            ),
            (
                "skąd bierze dane",
                "z odpisu zapisanego wcześniej przez operatora do pliku",
            ),
            (
                "czego nie robi",
                "nie łączy się z rejestrem i nie pobiera niczego samodzielnie",
            ),
        ),
        notes=(
            "Na tym etapie narzędzie nie ocenia jeszcze terminowości składania sprawozdań.",
            "Powód i stan prac: docs/status.md oraz docs/niezmierzone.md.",
        ),
    )


def _opis_wzmianki(rodzaj: str) -> str:
    return NAZWY_WZMIANEK.get(rodzaj, rodzaj)


def karta_podmiotu(odpis: Odpis) -> Block:
    """Karta podmiotu odczytana z odpisu.

    Dwie rzeczy są tu ważniejsze niż wygoda czytania. Po pierwsze, **wzmianka o nieczytelnym
    okresie jest wypisywana jako nieczytelna** razem ze swoim surowym zapisem — nie jest
    pomijana i nie jest zgadywana. Po drugie, karta z odpisu syntetycznego nosi znacznik,
    którego nie da się przeoczyć: raport z wymyślonych danych ma być nie do pomylenia
    z prawdziwym.
    """
    wiersze: list[tuple[str, str]] = [
        ("nazwa", odpis.nazwa),
        ("numer KRS", odpis.numer),
        ("forma prawna", odpis.forma_prawna or "nie podano"),
        ("NIP", odpis.nip or "nie podano"),
        ("REGON", odpis.regon or "nie podano"),
        ("stan rejestru na dzień", odpis.stan_z_dnia.isoformat()),
        (
            "dzień kończący rok obrotowy",
            odpis.dzien_konczacy_rok_obrotowy or "nie podano",
        ),
    ]
    for dzial in odpis.dzialy:
        wiersze.append((f"dział {dzial.numer}", _stan_dzialu(dzial.obecny, dzial.pusty)))
    for wzmianka in odpis.wzmianki:
        wiersze.append(
            (
                _opis_wzmianki(wzmianka.rodzaj),
                _opis_okresu(wzmianka.zapis_okresu, wzmianka.okres is not None)
                + f", złożono {wzmianka.data_zlozenia.isoformat()}",
            )
        )
    notatki = [
        "Karta pokazuje, co stoi w odpisie. Nie zawiera żadnej oceny ani zarzutu.",
    ]
    if odpis.wzmianki_nieczytelne():
        notatki.append(
            f"Nieczytelnych zapisów okresu: {len(odpis.wzmianki_nieczytelne())}. "
            "Zgłoszone powyżej w postaci surowej, celowo nieodgadywane."
        )
    if odpis.syntetyczny:
        notatki.insert(0, ZNACZNIK_SYNTETYCZNY)
    return Block(
        title=f"{ZNACZNIK_SYNTETYCZNY} — karta podmiotu" if odpis.syntetyczny else "karta podmiotu",
        headers=("pole", "wartość"),
        rows=tuple(wiersze),
        notes=tuple(notatki),
    )


def katalog_sygnalow(reguly: Sequence[Regula]) -> Block:
    """Katalog reguł na jedną stronę — artefakt do przeglądu przez człowieka znającego prawo.

    Przedmiotem przeglądu nie jest kod, tylko dwie kolumny: **podstawa prawna** i to, czy
    została potwierdzona w tekście ustawy. Reguła z niepotwierdzoną podstawą jest tu wypisana
    jako taka, a nie ukryta — zmyślony numer przepisu przeszedłby każdą kontrolę automatyczną,
    będąc po cichu nieprawdą.
    """
    wiersze = tuple(
        (
            regula.kod,
            regula.poziom.name.lower(),
            regula.podstawa_prawna
            + ("" if regula.podstawa_potwierdzona else "  [DO POTWIERDZENIA]"),
            regula.zywotnosc,
            "tak" if regula.moze_wystrzelic else "nie — patrz przypisy",
        )
        for regula in reguly
    )
    niepotwierdzone = [r.kod for r in reguly if not r.podstawa_potwierdzona]
    uspione = [r for r in reguly if not r.moze_wystrzelic]
    notatki = [
        f"Reguł w katalogu: {len(reguly)}.",
    ]
    if niepotwierdzone:
        notatki.append(
            "Podstawa prawna wymaga potwierdzenia w tekście ustawy przy regułach: "
            + ", ".join(niepotwierdzone)
            + "."
        )
    for regula in uspione:
        nierozstrzygalne = [p.kod for p in regula.przeslanki_wykluczajace if not p.ustalalna]
        notatki.append(
            f"Reguła {regula.kod} nie może dziś wyprodukować sygnału, bo nie da się rozstrzygnąć "
            f"przesłanek: {', '.join(nierozstrzygalne)}. To stan wymagany, nie usterka."
        )
    return Block(
        title="katalog reguł sygnałowych",
        headers=("kod", "poziom", "podstawa prawna", "żywotność", "może wystrzelić"),
        rows=wiersze,
        notes=tuple(notatki),
    )


# Kody obserwacji i przesłanek po ludzku. Kod spoza mapy pokazujemy surowy — tak samo jak
# wzmiankę o nieznanym rodzaju: brak tłumaczenia jest informacją, nie powodem do milczenia.
OPISY_OBSERWACJI = {
    Obserwacja.DZIAL_NIEPUSTY.value: "dział niepusty w odpisie",
    Obserwacja.DZIAL_PUSTY.value: "dział obecny i pusty",
    Obserwacja.DZIAL_NIEOBECNY_W_PLIKU.value: "działu nie ma w pliku",
    Obserwacja.WPIS_NIEROZROZNIALNY_W_DZIALE.value: (
        "dział niepusty, ale nie wiadomo, który wpis w nim stoi"
    ),
    Obserwacja.BRAK_WZMIANKI_ZA_OKRES.value: "brak wzmianki za okres kandydujący",
    Obserwacja.BRAK_CZYTELNEGO_OKRESU.value: (
        "w odpisie nie ma wzmianki z czytelnym okresem, więc nie ma od czego liczyć terminu"
    ),
    Obserwacja.WZMIANKA_O_NIECZYTELNYM_OKRESIE.value: (
        "w odpisie stoi wzmianka, której zapisu okresu nie umiemy odczytać — mogła dotyczyć "
        "okresu kandydującego"
    ),
    Obserwacja.OGRANICZNIK_NIE_UPLYNAL.value: (
        "ogranicznik zastępczy nie upłynął na dzień stanu rejestru"
    ),
    Obserwacja.ZALOZENIE_CIAGLOSCI_ROKU_OBROTOWEGO.value: (
        "okres kandydujący wskazano przy założeniu, że rok obrotowy jest tej samej długości"
    ),
}

OPISY_PRZESLANEK = {
    "zawieszenie_caloroczne": "zawieszenie działalności przez cały rok obrotowy",
    "rozpoczecie_w_ii_polroczu": "rozpoczęcie działalności w drugiej połowie roku obrotowego",
    "upadlosc_lub_restrukturyzacja": "postępowanie upadłościowe albo restrukturyzacyjne",
    "dzialalnosc_w_spadku": "przedsiębiorstwo w spadku",
    "oswiadczenie_art_70a": "oświadczenie o braku obowiązku sporządzenia sprawozdania",
    "poza_rejestrem_przedsiebiorcow": "podmiot poza rejestrem przedsiębiorców",
}

OPISY_POWODOW = {
    Powod.KATALOG_DEKLARUJE_NIEUSTALALNOSC.value: "z odpisu tego nie da się ustalić",
    Powod.CZYTNIK_NIE_WYCIAGA_DANEJ.value: "narzędzie jeszcze nie czyta tej danej z odpisu",
    Powod.ODPIS_NIE_ROZSTRZYGA.value: "ten odpis tego nie rozstrzyga",
    Powod.OBSERWACJA.value: "stan odpisu",
}

WERDYKT_SYGNAL = "sygnał"
WERDYKT_WYKLUCZONY = "wykluczone"
WERDYKT_NIEUSTALONY = "nieustalone"


def opis_kodu(kod: str) -> str:
    return OPISY_OBSERWACJI.get(kod) or OPISY_PRZESLANEK.get(kod, kod)


def opis_niewiadomej(niewiadoma: Niewiadoma) -> str:
    powod = OPISY_POWODOW.get(niewiadoma.powod.value, niewiadoma.powod.value)
    return f"{opis_kodu(niewiadoma.kod)} ({powod})"


def _opis_okresu_kandydujacego(sygnal: Sygnal | Nieustalony | Wykluczony) -> str:
    """Okres kandydujący nazywamy przez okres, po którym następuje — bo tamten stoi w odpisie.

    Dnia bilansowego okresu, którego w odpisie nie ma, nie wyprowadzamy (reguła granic 9
    i `signals/terminy.py`). „Rok obrotowy po okresie zakończonym D" jest zdaniem prawdziwym
    bez znajomości długości tamtego roku.
    """
    if sygnal.po_okresie is None or sygnal.termin is None:
        return ""
    return (
        f"; rok obrotowy po okresie zakończonym {sygnal.po_okresie.isoformat()}, "
        f"ogranicznik zastępczy {sygnal.termin.isoformat()}"
    )


def _opis_zalozen(wynik: Sygnal | Wykluczony | Nieustalony) -> str:
    """Założenia dopisują się do KAŻDEGO werdyktu, nie tylko do nieustalonego.

    Przy werdykcie mocnym znikają najgroźniej: czytelnik bierze wtedy wynik za rozstrzygnięcie
    bezwarunkowe. To jest poprawka po przeglądzie kroku 4.
    """
    if not wynik.zalozenia:
        return ""
    return "; przy założeniu: " + "; ".join(opis_niewiadomej(z) for z in wynik.zalozenia)


def _wiersz_wyniku(wynik: Sygnal | Wykluczony | Nieustalony) -> tuple[str, str, str, str]:
    if isinstance(wynik, Sygnal):
        werdykt = WERDYKT_SYGNAL
        szczegoly = opis_kodu(wynik.obserwacja.value) + _opis_okresu_kandydujacego(wynik)
    elif isinstance(wynik, Wykluczony):
        werdykt = WERDYKT_WYKLUCZONY
        szczegoly = opis_kodu(wynik.powod) + _opis_okresu_kandydujacego(wynik)
    else:
        werdykt = WERDYKT_NIEUSTALONY
        szczegoly = "; ".join(opis_niewiadomej(n) for n in wynik.nierozstrzygniete)
    return (
        wynik.regula.kod,
        werdykt,
        wynik.regula.poziom.name.lower(),
        szczegoly + _opis_zalozen(wynik),
    )


def ocena_ryzyka(ocena: Ocena) -> Block:
    """Wynik przejścia katalogu po odpisie — po jednym wierszu na regułę.

    Ekran kroku 4, a nie raport kroku 5: pokazuje werdykt każdej reguły i to, czego nie
    ustalono, bez sekcji o tym, czego narzędzie nie widzi wcale. Dwie rzeczy są w nim
    ważniejsze niż wygoda czytania. **Reguła nierozstrzygnięta zajmuje tyle samo miejsca co
    sygnał** — wynik, którego nie ma, ma być równie widoczny jak wynik, który jest. I **żaden
    wiersz nie niesie zarzutu**: najmocniejsze zdanie, jakie może tu paść, mówi o braku wpisu
    na dzień stanu rejestru.
    """
    wiersze = tuple(_wiersz_wyniku(wynik) for wynik in ocena.wyniki)
    notatki = [
        f"Ocena dotyczy stanu rejestru na dzień {ocena.stan_z_dnia.isoformat()} "
        "i nie zawiera zarzutu wobec podmiotu.",
        f"Sygnałów: {len(ocena.sygnaly())}. "
        f"Nieustalonych: {len(ocena.nieustalone())}. "
        f"Wykluczonych: {len(ocena.wykluczone())}.",
    ]
    niepotwierdzone = sorted(
        {s.regula.kod for s in ocena.sygnaly() if not s.regula.podstawa_potwierdzona}
    )
    if niepotwierdzone:
        notatki.append(
            "Podstawa prawna wymaga potwierdzenia w tekście ustawy przy regułach: "
            + ", ".join(niepotwierdzone)
            + "."
        )
    if ocena.syntetyczny:
        notatki.insert(0, ZNACZNIK_SYNTETYCZNY)
    tytul = f"ocena sygnałów rejestrowych — {ocena.nazwa}"
    return Block(
        title=f"{ZNACZNIK_SYNTETYCZNY} — {tytul}" if ocena.syntetyczny else tytul,
        # „poziom reguły", nie „poziom" — kolumna mówi, co byłoby na szali, gdyby reguła
        # została rozstrzygnięta, a nie jak groźny jest wynik. Przy wierszu nieustalonym sama
        # „terminalny" czyta się jak werdykt i to jest dokładnie to nieporozumienie, którego
        # ten produkt ma nie produkować.
        headers=("reguła", "werdykt", "poziom reguły", "co z odpisu wynika"),
        rows=wiersze,
        notes=tuple(notatki),
    )


def _stan_dzialu(obecny: bool, pusty: bool) -> str:
    """Trzy stany, nie dwa: brak działu w pliku to co innego niż dział pusty."""
    if not obecny:
        return "brak w pliku"
    return "pusty" if pusty else "niepusty"


def _opis_okresu(zapis: str, czytelny: bool) -> str:
    if not zapis:
        return "bez podanego okresu"
    return f"za okres {zapis}" if czytelny else f"nieczytelny zapis okresu: {zapis}"


def zapisano_ocene(wpis: Wpis, sciezka_dziennika: Path, sciezka_ladunku: Path) -> Block:
    """Potwierdzenie zapisu — z identyfikatorem, bo bez niego nie da się nic odtworzyć.

    Ekran mówi też, **gdzie leży ładunek**, i to nie jest szczegół techniczny: ładunek to kopia
    odpisu, czyli dane osób zasiadających w organach spółki. Operator ma wiedzieć, co zostało
    na jego dysku, zanim dowie się tego skądinąd.
    """
    return Block(
        title="zapisano w dzienniku",
        headers=("pole", "wartość"),
        rows=(
            ("identyfikator oceny", wpis.ocena_id),
            ("czas zapisu", wpis.czas),
            ("dziennik", str(sciezka_dziennika)),
            ("ładunek (kopia odpisu)", str(sciezka_ladunku)),
        ),
        notes=(
            f"Odtworzenie: krs-tool odtworz --ocena-id {wpis.ocena_id}",
            "Ładunek trzyma treść odpisu. Usuwa go polecenie `wyczysc-ladunki`; wpis "
            "w dzienniku zostaje, a odtworzenie zacznie wtedy odmawiać.",
        ),
    )


def wynik_odtworzenia(odtworzenie: Odtworzenie) -> Block:
    """Trzy możliwe odpowiedzi, z czego dwie są odmowami — i o to chodzi.

    Odtwarzalność, której nie da się obalić, jest deklaracją, a nie własnością. Ten ekran ma
    umieć powiedzieć „różni się" i nazwać reguły, po których to widać.
    """
    wpis = odtworzenie.wpis
    wiersze = [
        ("identyfikator oceny", wpis.ocena_id),
        ("ocena zapisana", wpis.czas),
        ("podmiot", f"{wpis.nazwa} ({wpis.numer})"),
        ("stan rejestru na dzień", wpis.stan_z_dnia),
        ("wynik", "identyczny" if odtworzenie.identyczne else "różni się"),
    ]
    notatki = [_zdanie_o_odtworzeniu(odtworzenie)]
    if odtworzenie.rozniace_sie_reguly:
        notatki.append(
            "Reguły, których werdykt się zmienił: "
            + ", ".join(odtworzenie.rozniace_sie_reguly)
            + "."
        )
    if odtworzenie.katalog_sie_zmienil:
        notatki.append(
            "Katalog reguł nie jest ten sam co przy zapisie oceny. To najczęstsza przyczyna "
            "różnicy i jedyna, która nie oznacza, że zmienił się materiał."
        )
    if odtworzenie.material_sie_zmienil:
        notatki.append(
            "Treść ładunku nie zgadza się ze skrótem zapisanym w dzienniku — ktoś zmienił "
            "plik po ocenie. Wynik poniżej opisuje plik dzisiejszy, nie ten oceniony."
        )
    return Block(
        title="odtworzenie oceny",
        headers=("pole", "wartość"),
        rows=tuple(wiersze),
        notes=tuple(notatki),
    )


def _zdanie_o_odtworzeniu(odtworzenie: Odtworzenie) -> str:
    if odtworzenie.identyczne:
        return "Przeliczenie z zachowanego ładunku dało dokładnie ten sam wynik co przy zapisie."
    return (
        "Przeliczenie z zachowanego ładunku dało inny wynik niż przy zapisie. Ocena nie czyta "
        "zegara, więc różnica bierze się z materiału albo z katalogu reguł — nigdy stąd, że "
        "minął czas."
    )


def podglad_czyszczenia(pliki: Sequence[Path]) -> Block:
    """Co zniknie, zanim cokolwiek zniknie."""
    return Block(
        title="ładunki do usunięcia",
        headers=("plik",),
        rows=tuple((str(plik),) for plik in pliki),
        notes=(
            f"Ładunków: {len(pliki)}. Nic jeszcze nie zostało usunięte.",
            "Usunięcie: powtórz polecenie z `--potwierdzam`. Wpisy w dzienniku zostaną, "
            "a odtworzenie tych ocen przestanie być możliwe.",
        ),
    )


def wyczyszczono_ladunki(liczba: int) -> Block:
    return Block(
        title="ładunki usunięte",
        notes=(
            f"Usuniętych ładunków: {liczba}. Dziennik pozostał nietknięty.",
            "Odtworzenie tych ocen odpowie teraz, że nie da się ich odtworzyć z zachowanych "
            "danych. To jest zamierzony skutek, nie usterka.",
        ),
    )


def blad_operatora(blad: KrsError) -> Block:
    """Błąd jako ekran, nie jako ślad stosu.

    Komunikaty tej taksonomii są pisane do operatora, a nie do programisty (`errors.py`), więc
    ślad stosu nie tylko nic mu nie mówi — **sugeruje usterkę narzędzia tam, gdzie zaszedł stan
    przewidziany**, jak wyczyszczona retencja albo plik, który nie jest odpisem.
    """
    return Block(
        title="nie udało się",
        rows=((str(blad),),),
        notes=(f"Kod wyjścia: {blad.exit_code}.",),
    )
