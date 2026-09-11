"""Ładunki ocen — treść odpisów, czyli jedyne miejsce w tym drzewie, z którego się kasuje.

Ładunek to kopia pliku, na którym powstała ocena. Bez niego `odtworz` nie ma czego przeliczyć,
a z nim narzędzie trzyma na dysku dane osób zasiadających w organach spółki — łącznie z tymi
z działu 2. Dlatego ładunek jest **osobny od dziennika i usuwalny niezależnie od niego**:
retencja materiału i pamięć o tym, że ocena się odbyła, to dwie różne potrzeby i dwie różne
decyzje.

Ten moduł jest wyjątkiem od reguły granic 12 i jedynym. Skan pilnuje, że `unlink` nie pojawia
się nigdzie indziej w `dziennik/`, bo skasowanie linii dziennika i skasowanie ładunku wyglądają
w kodzie podobnie, a znaczą co innego: pierwsze zaciera ślad po ocenie, drugie jest higieną.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import NieodtwarzalneZZachowanychError

KATALOG_LADUNKOW = "ladunki"
ROZSZERZENIE = ".json"


class Ladunki:
    """Magazyn treści odpisów: po jednym pliku na ocenę."""

    def __init__(self, katalog: Path) -> None:
        self._katalog = katalog / KATALOG_LADUNKOW

    @property
    def katalog(self) -> Path:
        return self._katalog

    def sciezka(self, ocena_id: str) -> Path:
        return self._katalog / f"{ocena_id}{ROZSZERZENIE}"

    def zapisz(self, ocena_id: str, tresc: str) -> Path:
        """Zapisuje treść odpisu pod identyfikatorem oceny.

        Ten sam odpis oceniony drugi raz ma ten sam identyfikator i tę samą treść, więc zapis
        jest idempotentny — nadpisanie bajt w bajt tym samym nie jest utratą niczego.
        """
        self._katalog.mkdir(parents=True, exist_ok=True)
        sciezka = self.sciezka(ocena_id)
        sciezka.write_text(tresc, encoding="utf-8")
        return sciezka

    def wczytaj(self, ocena_id: str) -> str:
        """Treść odpisu albo wyjątek nazywający rzecz po imieniu.

        Brak ładunku **nie jest usterką** — jest stanem po wyczyszczeniu retencji i tak ma się
        przedstawiać. Dlatego wyjątek mówi „nie da się odtworzyć z zachowanych danych",
        a nie „brak pliku".
        """
        sciezka = self.sciezka(ocena_id)
        if not sciezka.is_file():
            raise NieodtwarzalneZZachowanychError(
                f"Ładunek oceny {ocena_id} został usunięty z retencji. Wpis w dzienniku "
                "zostaje, ale odtworzenie wymaga treści odpisu, której już nie ma."
            )
        return sciezka.read_text(encoding="utf-8")

    def lista(self) -> tuple[Path, ...]:
        if not self._katalog.is_dir():
            return ()
        return tuple(sorted(self._katalog.glob(f"*{ROZSZERZENIE}")))

    def wyczysc(self) -> int:
        """Usuwa wszystkie ładunki i zwraca ich liczbę. Dziennika nie dotyka."""
        pliki = self.lista()
        for plik in pliki:
            plik.unlink()
        return len(pliki)
