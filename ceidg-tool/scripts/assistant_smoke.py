#!/usr/bin/env python
"""Grupa A z `docs/test-runs-phase4.md`: czy asystent rozumie polskie zdania.

**Zero żądań do CEIDG.** Asystent stoi przed `count`, więc ta sonda kończy się na interpretacji —
dokładnie tam, gdzie w kreatorze operator odpowiada „wróć do menu". Kosztuje wyłącznie tokeny
modelu, kilka groszy za cały przebieg.

Sonda, a nie test: kosztuje pieniądze i wymaga sieci, więc uruchamia ją człowiek świadomie —
jak trzy sondy CEIDG obok. To zarazem jedyny sposób, żeby po zmianie promptu albo modelu sprawdzić
rzecz, której atrapa nie pokaże: co model **naprawdę** odpowiada.

    PYTHONUTF8=1 python scripts/assistant_smoke.py
    PYTHONUTF8=1 python scripts/assistant_smoke.py --tylko A1 A5
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ceidg_tool.assistant.caller import AnthropicCaller  # noqa: E402
from ceidg_tool.assistant.pkd import load_pkd  # noqa: E402
from ceidg_tool.config import load_settings  # noqa: E402
from ceidg_tool.errors import CeidgError  # noqa: E402
from ceidg_tool.ui import texts  # noqa: E402

# (id, zdanie, czego szukamy — wprost z runbooka)
PRZEBIEGI: tuple[tuple[str, str, str], ...] = (
    ("A1", "firmy budowlane w Białymstoku założone w zeszłym roku", "przypadek zwykły"),
    (
        "A2",
        "salony fryzjerskie w Łomży, potrzebuję telefonów",
        "czy prośba o kontakt daje szczegóły",
    ),
    ("A3", "firmy budowlane z przychodem powyżej miliona", "czy NIE wymyśli filtra finansowego"),
    ("A4", "spółki z o.o. w Warszawie", "czy nie udaje, że odpowiada o inny rejestr"),
    ("A5", "firmy z PKD 62.01.Z", "granica roczników PKD"),
    ("A6", "kilka firm fryzjerskich w Krakowie", "czy potrafi ustawić limit rekordów (nie może)"),
    ("A7", "firmy budowlane w Białymstoku założone w zeszłym roku", "powtórka A1 — cache promptu"),
    ("A8", "chcę wszystko", "zdanie bez użytecznego filtra"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tylko", nargs="*", default=None, help="identyfikatory przebiegów")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.anthropic_key:
        raise SystemExit("Brak klucza asystenta — patrz `ceidg-tool sprawdz-token`.")
    slownik = load_pkd()
    rozmowa = AnthropicCaller(api_key=settings.anthropic_key, slownik=slownik)
    dzisiaj = datetime.now(tz=UTC).date()

    wybrane = [p for p in PRZEBIEGI if args.tylko is None or p[0] in args.tylko]
    print(f"Grupa A: {len(wybrane)} przebiegów, 0 żądań CEIDG, dzisiaj = {dzisiaj}\n")

    niepowodzenia = 0
    for ident, zdanie, po_co in wybrane:
        print("=" * 78)
        print(f"{ident}  {po_co}")
        print(f'    zdanie: „{zdanie}"')
        start = time.monotonic()
        try:
            wynik = rozmowa.interpret(zdanie, dzisiaj=dzisiaj)
        except CeidgError as exc:
            print(f"    ODMOWA po {time.monotonic() - start:.1f}s: {exc}")
            print()
            continue
        czas = time.monotonic() - start
        cache = rozmowa.ostatni_cache_read
        blok = texts.interpretation(
            zdanie,
            wynik.kryteria.describe(),
            wynik.kody_pkd,
            [str(k) for k in wynik.ograniczenia],
        )
        print(f"    czas: {czas:.1f}s   cache odczytany: {cache} tokenów")
        print(f"    pusty zestaw kryteriów: {wynik.kryteria.is_empty()}")
        print(f"    limit rekordów: {wynik.kryteria.max_rekordow}")
        for linia in blok.as_text().splitlines():
            print(f"    | {linia}")
        print()
    return 1 if niepowodzenia else 0


if __name__ == "__main__":
    raise SystemExit(main())
