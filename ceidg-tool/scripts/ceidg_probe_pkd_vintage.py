#!/usr/bin/env python
"""Sonda celowana: który rocznik PKD indeksuje parametr `pkd` w `/firmy`?

Pytanie zostało otwarte po pomiarze z 2026-09-07 (`docs/decisions.md`): **rekordy** niosą
`rokPkd: 2025`, ale sonda fazy 1 zmierzyła wyłącznie **format** parametru `pkd` (zwarty,
`62.01.Z` daje 204 zamiast 400), nigdy jego rocznika. Słownik asystenta jest zbudowany
z PKD 2025 — jeśli parametr indeksuje 2007, asystent proponowałby kody, których filtr nie zna,
a operator czytałby „brak firm" zamiast błędu.

Rozstrzygnięcie opiera się na kodzie, który istnieje **wyłącznie w jednym** roczniku:

- `6201Z` „Działalność związana z oprogramowaniem" — PKD 2007; w PKD 2025 **nie istnieje**
  (programowanie przeniesiono na 62.10).
- `6210B` „Pozostała działalność w zakresie programowania" — PKD 2025; w PKD 2007 nie istnieje.
- `4933Z` „Transport pasażerski na żądanie pojazdem z kierowcą" — PKD 2025, kontrola
  pozytywna: ten kod widzieliśmy w prawdziwych rekordach z produkcji, więc **musi** coś zwrócić.

Trzy żądania `limit=1`, czyli same liczby trafień; żadnych danych osobowych nie pobieramy
i nie zapisujemy. Sonda dzieli limiter i historię żądań z narzędziem (`probe_support`).

**Uruchomiona na produkcji 2026-09-07**: 6201Z → 234 605, 6210B → 142 294, 4933Z → 31 402.
Filtr przyjmuje oba roczniki, więc słownik PKD 2025 jest właściwy. Ponowny przebieg ma sens
tylko wtedy, gdy podejrzewasz zmianę po stronie API — wynik jest w `docs/decisions.md`.

Uruchomienie wymaga zgody właściciela w sesji — patrz CLAUDE.md.

    PYTHONUTF8=1 python scripts/ceidg_probe_pkd_vintage.py --env prod
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from probe_support import SharedGate, no_proxy_opener, shared_gate  # noqa: E402

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}

# (kod, rocznik, w którym istnieje, po co pytamy)
PRZYPADKI = (
    ("6201Z", "2007", "istnieje tylko w PKD 2007 — programowanie sprzed zmiany"),
    ("6210B", "2025", "istnieje tylko w PKD 2025 — programowanie po zmianie"),
    ("4933Z", "2025", "kontrola pozytywna: ten kod widzieliśmy w rekordach z produkcji"),
)

GATE: SharedGate
OPENER = no_proxy_opener()
_n = 0


def call(base: str, token: str, kod: str) -> tuple[int, int | None]:
    """Jedno `count` dla podanego kodu PKD. Zwraca (status, count albo None)."""
    global _n
    GATE.before("firmy")
    url = f"{base}/firmy?" + urllib.parse.urlencode([("pkd", kod), ("limit", "1")])
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ceidg-probe-pkd-vintage/0.1",
        },
    )
    _n += 1
    started = time.monotonic()
    try:
        with OPENER.open(req, timeout=120) as resp:
            status, data = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, data = exc.code, exc.read()
    except urllib.error.URLError as exc:
        status, data = -1, str(exc.reason).encode()
    GATE.after(status)

    count: int | None = None
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
            if isinstance(body, dict) and isinstance(body.get("count"), int):
                count = body["count"]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    czas = time.monotonic() - started
    print(f"[{_n}/{len(PRZYPADKI)}] pkd={kod}  HTTP {status}  {czas:5.2f}s  count={count}")
    return status, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("test", "prod"), default="test")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()

    import os

    token = args.token or os.environ.get("CEIDG_TOKEN") or ""
    if not token:
        from dotenv import dotenv_values

        token = (dotenv_values(".env").get("CEIDG_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Brak tokenu: ustaw CEIDG_TOKEN albo podaj --token.")

    base = BASE[args.env]
    print(f"Sonda rocznika PKD, środowisko {args.env}, {len(PRZYPADKI)} żądania.\n")

    global GATE
    wyniki: dict[str, tuple[int, int | None]] = {}
    with shared_gate(args.env, token) as gate:
        GATE = gate
        for kod, rocznik, po_co in PRZYPADKI:
            print(f"  {kod} ({rocznik}): {po_co}")
            wyniki[kod] = call(base, token, kod)

    print("\n--- wynik ---")
    stare = wyniki["6201Z"][1] or 0
    nowe = wyniki["6210B"][1] or 0
    kontrola = wyniki["4933Z"][1] or 0

    if kontrola == 0:
        print("KONTROLA POZYTYWNA ZAWIODŁA: 4933Z nie zwrócił nic, choć widzieliśmy go")
        print("w prawdziwych rekordach. Nie wyciągaj wniosków z pozostałych dwóch liczb —")
        print("coś jest nie tak z zapytaniem albo ze środowiskiem.")
        return 1
    if nowe > 0 and stare == 0:
        print("Parametr `pkd` indeksuje PKD 2025. Słownik asystenta jest właściwy.")
    elif stare > 0 and nowe == 0:
        print("Parametr `pkd` indeksuje PKD 2007 — SŁOWNIK ASYSTENTA JEST ZŁY.")
    elif stare > 0 and nowe > 0:
        # Gałąź zmierzona 2026-09-07 i **rozstrzygnięta poza sondą**, bo trzy liczniki nie
        # potrafią tego rozstrzygnąć: nie odróżniają tłumaczenia po stronie API od rocznika
        # trzymanego przy rekordzie. Odpowiedzi dostarczył raport produkcyjny leżący na dysku
        # (`RokPKD` per rekord, 285 026 wierszy) — filtr dopasowuje kod tak, jak zapisano.
        # Pierwsza wersja tego komunikatu mówiła „warstwa tłumacząca po stronie API" i była
        # wnioskiem podanym jako pomiar, czyli tą samą pomyłką, która raz już w tym projekcie
        # napędziła decyzję projektową (`docs/decisions.md`).
        print("Parametr przyjmuje OBA roczniki: kody 2025 i 2007 zwracają trafienia.")
        print("Kody ze słownika 2025 działają — ale to NIE znaczy, że sięgają całego rejestru.")
        print("Rejestr jest w trakcie przejścia na PKD 2025 (do 31.12.2026), a filtr dopasowuje")
        print("kod tak, jak zapisano go w rekordzie, więc oba zbiory są ROZŁĄCZNE. Zmierzone")
        print("na 285 026 rekordach (wielkopolskie): 58,6 % nadal ma kody 2007, a 8,6 %")
        print("nie niesie żadnego kodu ze słownika 2025. Szczegóły: docs/decisions.md.")
    else:
        print("Oba kody zwróciły zero mimo działającej kontroli — wynik nierozstrzygający.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
