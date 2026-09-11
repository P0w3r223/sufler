"""Modele widoku i wszystkie zdania programu. Moduł czysty — bez biblioteki wyjścia.

Kształt `Block` skopiowany z `ceidg-tool/ceidg_tool/ui/texts.py` (kopia z 2026-09-10); treść
napisana od nowa. Dzięki temu każdy ekran daje się sprawdzić w teście bez terminala, a
`cli.py` nie układa ani jednego zdania (reguła granic 7) — co z kolei jest warunkiem, żeby
reguła 6 dała się w ogóle sprawdzić skanem.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .odpis.model import Odpis
from .signals.katalog import Regula

NAZWA = "krs-tool"

ZNACZNIK_SYNTETYCZNY = "ODPIS SYNTETYCZNY — dane wymyślone na potrzeby testów"

# Nazwy wzmianek po ludzku. Klucz spoza tej mapy pokazujemy w postaci surowej, zamiast
# pomijać — wzmianka, której nie znamy, jest informacją o rejestrze, nie śmieciem.
NAZWY_WZMIANEK = {
    "wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego": "sprawozdanie finansowe",
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


def _stan_dzialu(obecny: bool, pusty: bool) -> str:
    """Trzy stany, nie dwa: brak działu w pliku to co innego niż dział pusty."""
    if not obecny:
        return "brak w pliku"
    return "pusty" if pusty else "niepusty"


def _opis_okresu(zapis: str, czytelny: bool) -> str:
    if not zapis:
        return "bez podanego okresu"
    return f"za okres {zapis}" if czytelny else f"nieczytelny zapis okresu: {zapis}"
