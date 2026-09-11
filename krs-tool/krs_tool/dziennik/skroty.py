"""Skróty, po których poznaje się, że dwie oceny są tą samą oceną. Moduł czysty.

Skrót wyniku liczy się z **werdyktów**, nie z wydruku: zmiana redakcyjna w `texts.py` nie ma
prawa wyglądać jak zmiana oceny, a zmiana reguły ma. Skrót katalogu liczy się z pól reguł,
a nie z bajtów plików YAML — poprawiony komentarz prawnika nie jest zmianą oceny.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from ..signals.katalog import Regula
from ..signals.model import Nieustalony, Ocena, Sygnal, Wykluczony


def skrot_tekstu(tekst: str) -> str:
    """SHA-256 treści pliku odpisu — tożsamość materiału, z którego powstała ocena."""
    return hashlib.sha256(tekst.encode("utf-8")).hexdigest()


def skrot_katalogu(reguly: Sequence[Regula]) -> str:
    """Odcisk katalogu reguł: kody, poziomy, podstawy i to, czy zostały potwierdzone."""
    postac = [
        {
            "kod": r.kod,
            "poziom": r.poziom.name,
            "rodzaj": r.rodzaj.value,
            "zakres": r.zakres.value if r.zakres else None,
            "zrodlo": r.zrodlo,
            "podstawa_prawna": r.podstawa_prawna,
            "podstawa_potwierdzona": r.podstawa_potwierdzona,
            "wzmianka": r.wzmianka,
            "przeslanki": [p.kod for p in r.przeslanki_wykluczajace],
        }
        for r in reguly
    ]
    return _skrot_struktury(postac)


def skrot_wyniku(ocena: Ocena) -> str:
    """Odcisk werdyktów. Dwie oceny o tym samym odcisku mówią o podmiocie dokładnie to samo."""
    return _skrot_struktury([_werdykt(wynik) for wynik in ocena.wyniki])


def _werdykt(wynik: Sygnal | Wykluczony | Nieustalony) -> dict[str, object]:
    wspolne: dict[str, object] = {
        "kod": wynik.regula.kod,
        "termin": wynik.termin.isoformat() if wynik.termin else None,
        "po_okresie": wynik.po_okresie.isoformat() if wynik.po_okresie else None,
        "zalozenia": [z.kod for z in wynik.zalozenia],
    }
    if isinstance(wynik, Sygnal):
        return {**wspolne, "werdykt": "sygnal", "obserwacja": wynik.obserwacja.value}
    if isinstance(wynik, Wykluczony):
        return {**wspolne, "werdykt": "wykluczony", "powod": wynik.powod}
    return {
        **wspolne,
        "werdykt": "nieustalony",
        "nierozstrzygniete": [n.kod for n in wynik.nierozstrzygniete],
    }


def _skrot_struktury(dane: object) -> str:
    kanoniczne = json.dumps(dane, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(kanoniczne.encode("utf-8")).hexdigest()
