"""Sprawdzanie kodów PKD przysłanych przez model — moduł czysty (ADR-0011, decyzja 4).

Sam słownik przeprowadził się do `ceidg_tool/pkddict.py` 2026-09-23 razem z drugim konsumentem
(ADR-0026, decyzja 3): dane o klasyfikacji są faktem o rejestrze, a nie aktywem asystenta. Tutaj
zostaje to, co jest naprawdę asystenckie — pytania „czy model podał kod, który istnieje" i „jak
się ten kod nazywa" — więc reeksport nazw ze słownika jest zamierzony: importerzy sprawdzający
odpowiedź modelu mają jedno miejsce, a `pkddict` nie musi wiedzieć, że asystent istnieje.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..criteria import normalize_pkd
from ..errors import ConfigError
from ..pkddict import DEFAULT_PKD_PATH, PKD_RESOURCE, PKD_VINTAGE, load_pkd

__all__ = [
    "DEFAULT_PKD_PATH",
    "PKD_RESOURCE",
    "PKD_VINTAGE",
    "load_pkd",
    "lookup",
    "validate_codes",
]


def lookup(kod: str, slownik: Mapping[str, str]) -> str | None:
    """Nazwa kodu albo `None`. Kod jest wcześniej normalizowany, więc `62.01.Z` też trafi."""
    try:
        return slownik.get(normalize_pkd(kod))
    except ValueError:
        return None


def validate_codes(kody: Iterable[str], slownik: Mapping[str, str]) -> tuple[str, ...]:
    """Normalizuje i sprawdza istnienie każdego kodu. Rzuca `ConfigError` na pierwszym obcym.

    Kolejność jest celowa: **najpierw** słownik, potem `Criteria`. Dzięki temu operator czyta
    „kod 9999Z nie istnieje w PKD 2025", a nie komunikat o kształcie — a to jest różnica między
    informacją a zagadką dla kogoś, kto nie zna klasyfikacji.
    """
    out: list[str] = []
    for kod in kody:
        try:
            canonical = normalize_pkd(kod)
        except ValueError as exc:
            raise ConfigError(f"Asystent podał kod PKD w złym formacie: {exc}") from exc
        if canonical not in slownik:
            raise ConfigError(
                f"Asystent podał kod {canonical}, którego nie ma w klasyfikacji {PKD_VINTAGE}. "
                "Popraw opis albo podaj kod ręcznie."
            )
        if canonical not in out:
            out.append(canonical)
    return tuple(out)
