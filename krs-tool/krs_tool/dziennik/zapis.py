"""Dziennik ocen — plik, który tylko rośnie (reguła granic 12).

Jedna ocena to jedna linia JSON. Format jest taki, a nie inny, z jednego powodu: **operator ma
móc otworzyć ten plik w edytorze tekstu i zobaczyć, co narzędzie o kim powiedziało i kiedy**,
bez żadnego narzędzia pośredniczącego. Baza danych spełniałaby tę samą funkcję techniczną
i żadnej z tych ludzkich.

**Rozdział dziennika od ładunku istnieje od pierwszego dnia i to jest cała decyzja tego kroku.**
Linia dziennika jest maleńka i zostaje na zawsze; ładunek — treść odpisu, czyli dane osób
w organach spółki — mieszka osobno i wolno go skasować. Gdyby dołożyć ten rozdział później,
byłaby to migracja żywych danych osobowych, czyli dokładnie ta operacja, której nikt nigdy nie
robi w dobrym momencie.

Zapis wyłącznie przez dopisanie. Skasowanie czegokolwiek w tym module jest niemożliwe —
pilnuje tego skan z `tests/test_granice.py`, który nie przepuści ani `unlink`, ani trybu
otwarcia innego niż dopisujący albo czytający. Kasowanie ładunków ma własny moduł i własne
polecenie, bo to jest inna operacja o innych skutkach.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..signals.katalog import Regula
from ..signals.model import Ocena
from .skroty import skrot_katalogu, skrot_wyniku

NAZWA_DZIENNIKA = "dziennik.jsonl"

# Identyfikator oceny to początek skrótu odpisu: ten sam plik zawsze daje ten sam numer, więc
# odtworzenie ma czego szukać bez pamiętania czegokolwiek, a dwie oceny tego samego materiału
# nie rozjeżdżają się na dwa numery.
DLUGOSC_IDENTYFIKATORA = 16
KSZTALT_IDENTYFIKATORA = re.compile(f"[0-9a-f]{{{DLUGOSC_IDENTYFIKATORA}}}")


@dataclass(frozen=True)
class Wpis:
    """Jedna ocena widziana z dziennika. Bez treści odpisu — ta mieszka w ładunku."""

    ocena_id: str
    czas: str
    numer: str
    nazwa: str
    stan_z_dnia: str
    syntetyczny: bool
    skrot_odpisu: str
    skrot_katalogu: str
    skrot_wyniku: str
    sygnaly: int
    nieustalone: int
    wykluczone: int
    werdykty: dict[str, str]

    def jako_linia(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


def wpis_z_oceny(ocena: Ocena, czas: str, skrot_odpisu: str, reguly: Sequence[Regula]) -> Wpis:
    """Wpis dziennika złożony z oceny i z tego, na czym ona stała.

    W linii lądują **werdykty po kodach reguł**, a nie tylko ich skrót. Skrót odpowiada na
    pytanie „czy to samo", werdykty na pytanie „co dokładnie się zmieniło" — a to drugie jest
    tym, po co ktokolwiek sięga po odtworzenie. Linia pozostaje jedna i pozostaje czytelna
    w edytorze, bo reguł jest dziesięć, nie dziesięć tysięcy.
    """
    return Wpis(
        ocena_id=skrot_odpisu[:DLUGOSC_IDENTYFIKATORA],
        czas=czas,
        numer=ocena.numer,
        nazwa=ocena.nazwa,
        stan_z_dnia=ocena.stan_z_dnia.isoformat(),
        syntetyczny=ocena.syntetyczny,
        skrot_odpisu=skrot_odpisu,
        skrot_katalogu=skrot_katalogu(reguly),
        skrot_wyniku=skrot_wyniku(ocena),
        sygnaly=len(ocena.sygnaly()),
        nieustalone=len(ocena.nieustalone()),
        wykluczone=len(ocena.wykluczone()),
        werdykty={wynik.regula.kod: type(wynik).__name__.lower() for wynik in ocena.wyniki},
    )


class Dziennik:
    """Plik dziennika w katalogu magazynu. Otwierany do dopisania albo do czytania."""

    def __init__(self, katalog: Path) -> None:
        self._sciezka = katalog / NAZWA_DZIENNIKA

    @property
    def sciezka(self) -> Path:
        return self._sciezka

    def dopisz(self, wpis: Wpis) -> None:
        """Dokłada linię na końcu. Nigdy nie dotyka linii, które już tam stoją."""
        self._sciezka.parent.mkdir(parents=True, exist_ok=True)
        with self._sciezka.open("a", encoding="utf-8") as plik:
            plik.write(wpis.jako_linia() + "\n")

    def wpisy(self) -> tuple[Wpis, ...]:
        """Wszystkie wpisy w kolejności zapisu."""
        if not self._sciezka.is_file():
            return ()
        with self._sciezka.open("r", encoding="utf-8") as plik:
            return tuple(_wpis(linia) for linia in plik if linia.strip())

    def ostatni(self, ocena_id: str) -> Wpis | None:
        """Najnowszy wpis o danym identyfikatorze.

        Ten sam odpis oceniony dwa razy daje ten sam identyfikator i **dwie linie** — bo
        dziennik nie nadpisuje. Odtwarzamy wobec najnowszej, bo to ona opisuje stan, który
        operator ma przed oczami.
        """
        pasujace = [wpis for wpis in self.wpisy() if wpis.ocena_id == ocena_id]
        return pasujace[-1] if pasujace else None


def _wpis(linia: str) -> Wpis:
    try:
        dane: dict[str, Any] = json.loads(linia)
    except json.JSONDecodeError as blad:
        raise ConfigError(f"Uszkodzona linia dziennika: {blad}") from blad
    brakujace = sorted(set(Wpis.__annotations__) - set(dane))
    if brakujace:
        raise ConfigError(
            f"Linia dziennika bez pól {brakujace}. Dziennik pochodzi ze starszej wersji "
            "narzędzia albo został ręcznie zmieniony — w obu przypadkach nie zgaduj."
        )
    wpis = Wpis(**{pole: dane[pole] for pole in Wpis.__annotations__})
    if not KSZTALT_IDENTYFIKATORA.fullmatch(wpis.ocena_id):
        raise ConfigError(
            f"Identyfikator oceny {wpis.ocena_id!r} nie ma kształtu skrótu. Identyfikator "
            "wskazuje plik ładunku, więc linia zmieniona ręcznie mogłaby kazać narzędziu "
            "sięgnąć poza magazyn."
        )
    return wpis
