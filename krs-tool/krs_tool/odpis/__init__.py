"""Odczyt odpisu z pliku dostarczonego przez operatora.

Ten pakiet **nie importuje biblioteki sieciowej ani bazy danych** — jego wejściem są bajty,
które ktoś już pobrał. Dzięki temu zdanie „narzędzie nie odpytuje rejestru" jest własnością
grafu importów, a nie staranności przy pisaniu (`docs/design/etap1_core.md`).
"""

from __future__ import annotations
