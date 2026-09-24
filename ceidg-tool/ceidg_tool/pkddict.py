"""Słownik PKD 2025 — moduł czysty, jeden spakowany plik.

Mieszkał w `assistant/pkd.py` do 2026-09-23 i wyprowadził go **drugi konsument** (ADR-0026,
decyzja 3): `szukaj-pkd` czyta ten sam plik, a nazwa modułu jest twierdzeniem o własności i przy
dwóch konsumentach to twierdzenie przestawało być prawdziwe. Słownik jest faktem o rejestrze, nie
aktywem asystenta — różnica robi się nośna dokładnie wtedy, gdy asystenta da się wyłączyć
(ADR-0025), bo dziś ktoś mógłby usunąć `assistant/` razem z danymi, których używa reszta programu.

Dlaczego słownik jest nośny, a nie ozdobny: sonda z 2026-09-05 zmierzyła (`docs/decisions.md`),
że API odpowiada na nieznany `pkd` **kodem 204, nie 400**. Kod zmyślony, ale o poprawnym
kształcie, daje więc `count = 0` i zdanie „Brak firm spełniających kryteria" — nie do odróżnienia
od pustego rejestru przez operatora, który z założenia nie zna API. Słownik jest jedyną rzeczą
stojącą między tym zdaniem a prawdą.

Wczytywanie naśladuje `apiprofile.load_profile`: czysta funkcja z podmienialną ścieżką, żeby
testy mogły podstawić słownik pięcioelementowy zamiast pełnego.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from .criteria import normalize_pkd
from .errors import ConfigError

# Zasób pakietu, nie ścieżka względem pliku — tak samo jak profile API. Ścieżka po `__file__`
# działa przy `pip install -e .` i przestaje działać przy instalacji z koła, a różnicę widać
# dopiero wtedy, gdy narzędzie jest już u kogoś.
PKD_RESOURCE = resources.files("ceidg_tool").joinpath("data", "pkd2025.yaml")
DEFAULT_PKD_PATH = Path(str(PKD_RESOURCE))

# Rocznik klasyfikacji. Trafia do komunikatu o odrzuceniu, bo odrzucenie kodu z **innego**
# rocznika wygląda dla operatora dokładnie jak błąd modelu — a nie jest nim.
#
# 2025, a nie 2007: wszystkie `rokPkd` w prawdziwych odpowiedziach API mówią 2025 (11 wystąpień,
# 3 pliki — patrz `docs/decisions.md`). Pierwotny wybór 2007 opierał się wyłącznie na ręcznie
# napisanej atrapie w `tests/conftest.py`, czyli na dowodzie, który sami sfabrykowaliśmy.
PKD_VINTAGE = "PKD 2025"


def load_pkd(path: Path | None = None) -> dict[str, str]:
    """Wczytuje mapę `kod -> nazwa`. Każdy klucz musi być już w postaci kanonicznej.

    Sprawdzenie kluczy przy wczytaniu, a nie przy użyciu: uszkodzony słownik ma paść od razu
    i głośno, a nie objawić się jako nietrafiona podpowiedź trzy ekrany później.

    `DEFAULT_PKD_PATH` czytane jest **przy wywołaniu**, nie jako wartość domyślna argumentu —
    dzięki temu test podmieniający globalną w module dostaje podmienioną ścieżkę.
    """
    source = path or DEFAULT_PKD_PATH
    if not source.is_file():
        # Brak słownika wyłącza asystenta tak samo jak brak klucza — z komunikatem, co zrobić.
        # Milcząca praca bez słownika byłaby gorsza: model mógłby podać kod spoza klasyfikacji,
        # API odpowiedziałoby 204, a operator przeczytałby „brak firm" zamiast „zły kod".
        raise ConfigError(
            f"Brak słownika {PKD_VINTAGE} ({source}). Zbuduj go z oficjalnego pliku GUS: "
            "PYTHONUTF8=1 python scripts/build_pkd.py <plik.csv> "
            "(źródło: https://klasyfikacje.stat.gov.pl/Pkd2025)."
        )
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Nie można wczytać słownika PKD {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Słownik PKD {source} musi być mapą kod: nazwa.")

    out: dict[str, str] = {}
    for key, value in raw.items():
        kod = str(key).strip()
        # `normalize_pkd` rzuca `ValueError` na czymkolwiek, co nie jest podklasą — a sekcja `J`
        # albo klasa `62.01` to najbardziej prawdopodobne uszkodzenie tego pliku. Wywołanie go
        # wprost w warunku przepuszczało ten wyjątek na zewnątrz: `ValueError` nie jest
        # `CeidgError`, więc `cli.py` go nie łapie i operator dostawał ślad stosu zamiast zdania.
        try:
            canonical = normalize_pkd(kod)
        except ValueError as exc:
            raise ConfigError(
                f"Słownik PKD {source}: klucz {kod!r} nie jest kodem podklasy PKD ({exc})."
            ) from exc
        if canonical != kod:
            raise ConfigError(
                f"Słownik PKD {source}: klucz {kod!r} nie jest w postaci kanonicznej "
                f"(oczekiwano {canonical!r})."
            )
        nazwa = str(value).strip()
        if not nazwa:
            raise ConfigError(f"Słownik PKD {source}: kod {kod} nie ma nazwy.")
        out[kod] = nazwa
    return out
