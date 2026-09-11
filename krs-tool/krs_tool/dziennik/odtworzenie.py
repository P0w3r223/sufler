"""Odtworzenie oceny sprzed czasu — polecenie, które **ma prawo zawieść**.

Odtwarzalność bywa deklarowana i prawie nigdy nie jest sprawdzana, bo sprawdzenie wymaga, żeby
ktoś napisał kod, który potrafi powiedzieć „nie". Ten moduł potrafi powiedzieć trzy rzeczy
i wszystkie trzy są prawdziwymi odpowiedziami:

- **identyczne** — ten sam odpis, ten sam katalog, te same werdykty;
- **różni się** — i wtedy wymienia reguły, po których to widać, oraz mówi, czy zmienił się
  katalog, bo to jest najczęstsza przyczyna i najłatwiejsza do przeoczenia;
- **nie da się odtworzyć z zachowanych danych** — ładunek wyczyszczono z retencji, wpis
  w dzienniku został. To nie jest usterka, tylko skutek decyzji o retencji, i tak brzmi.

Ocena nie czyta zegara (ADR-0001 decyzja 6), więc różnica między „wtedy" a „teraz" może wziąć
się wyłącznie z materiału albo z katalogu — nigdy z tego, że minął dzień. Bez tej własności
odtworzenie nie miałoby czego twierdzić.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import OdpisNieczytelnyError
from ..odpis.czytanie import wczytaj_odpis
from ..signals.katalog import Regula
from ..signals.ocena import ocen_odpis
from .ladunki import Ladunki
from .skroty import skrot_katalogu, skrot_tekstu, skrot_wyniku
from .zapis import Dziennik, Wpis


@dataclass(frozen=True)
class Odtworzenie:
    """Co wyszło z przeliczenia starej oceny na dzisiejszym kodzie i katalogu."""

    wpis: Wpis
    identyczne: bool
    skrot_wyniku_teraz: str
    katalog_sie_zmienil: bool
    material_sie_zmienil: bool
    rozniace_sie_reguly: tuple[str, ...]


def odtworz(katalog_magazynu: Path, ocena_id: str, reguly: Sequence[Regula]) -> Odtworzenie:
    """Przelicza ocenę o danym identyfikatorze z zachowanego ładunku."""
    wpis = _wpis(katalog_magazynu, ocena_id)
    tresc = Ladunki(katalog_magazynu).wczytaj(wpis.ocena_id)
    ocena = ocen_odpis(wczytaj_odpis(_struktura(tresc)), reguly)
    teraz = skrot_wyniku(ocena)
    werdykty = {wynik.regula.kod: type(wynik).__name__.lower() for wynik in ocena.wyniki}
    return Odtworzenie(
        wpis=wpis,
        identyczne=teraz == wpis.skrot_wyniku,
        skrot_wyniku_teraz=teraz,
        katalog_sie_zmienil=skrot_katalogu(reguly) != wpis.skrot_katalogu,
        material_sie_zmienil=skrot_tekstu(tresc) != wpis.skrot_odpisu,
        rozniace_sie_reguly=_roznice(wpis.werdykty, werdykty),
    )


def _wpis(katalog_magazynu: Path, ocena_id: str) -> Wpis:
    wpis = Dziennik(katalog_magazynu).ostatni(ocena_id)
    if wpis is None:
        raise OdpisNieczytelnyError(
            f"W dzienniku nie ma oceny o identyfikatorze {ocena_id}. "
            "Identyfikatory wypisuje polecenie, które ocenę zapisało."
        )
    return wpis


def _struktura(tresc: str) -> Mapping[str, Any]:
    dane = json.loads(tresc)
    if not isinstance(dane, Mapping):  # pragma: no cover - ładunek powstaje z odczytanego pliku
        raise OdpisNieczytelnyError("Ładunek nie zawiera obiektu JSON")
    return dane


def _roznice(wtedy: Mapping[str, str], teraz: Mapping[str, str]) -> tuple[str, ...]:
    """Reguły, których werdykt się zmienił — razem z tymi, które doszły albo zniknęły.

    Reguła dopisana do katalogu po zapisie oceny nie ma z czym się porównać i **właśnie
    dlatego** jest różnicą: ocena sprzed jej dopisania nie odpowiadała na pytanie, które ta
    reguła zadaje.
    """
    kody = set(wtedy) | set(teraz)
    return tuple(sorted(kod for kod in kody if wtedy.get(kod) != teraz.get(kod)))
