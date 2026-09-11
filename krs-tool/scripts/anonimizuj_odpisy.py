"""Cienka nakładka wiersza poleceń na `krs_tool.anonimizacja`.

Logika stoi w pakiecie, bo wytwarza materiał dowodowy i ma podlegać regułom pakietu. Tutaj
zostaje wyłącznie wczytanie, zapis i obsługa argumentów.

    python scripts/anonimizuj_odpisy.py probki tests/fixtures/odpisy
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from krs_tool.anonimizacja import anonimizuj


def main() -> int:
    parser = argparse.ArgumentParser(description="Anonimizacja odpisów przed commitem.")
    parser.add_argument("zrodlo", type=Path, help="Katalog z odpisami dostarczonymi ręcznie.")
    parser.add_argument("cel", type=Path, help="Katalog, do którego trafią kopie anonimowe.")
    argumenty = parser.parse_args()

    argumenty.cel.mkdir(parents=True, exist_ok=True)
    for plik in sorted(argumenty.zrodlo.glob("*.json")):
        dane = json.loads(plik.read_text(encoding="utf-8"))
        (argumenty.cel / plik.name).write_text(
            json.dumps(anonimizuj(dane), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
