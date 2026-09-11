#!/usr/bin/env python
"""Sonda celowana: czy powtórzone `miasto=` jest ORowane, czy ANDowane?

Po co: asystent odpowiada na zdanie „…we Wrocławiu oraz Gdańsku" kryteriami z **dwoma**
miastami, a `Criteria.to_params` wysyła je jako dwa parametry `miasto=` w jednym żądaniu.
Powtórzone `pkd=`, `nip=` i `status=` zostały zmierzone jako OR (`docs/decisions.md`);
`miasto=` nigdy. Jeśli jest ANDowane, to **żaden wpis nie leży w dwóch miastach naraz**, więc
zapytanie wraca puste — bez błędu, bez ostrzeżenia, na ścieżce, która ma być najłatwiejsza.

Wnioskowanie z `pkd` jest tu zakazane i to nie jest ostrożność: 2026-09-09 ustalono, że
rodzina pól tekstowych **nie jest jednorodna** — `nazwa` i `miasto` dopasowują fragmentem,
a co najmniej dwa z pięciu pozostałych pól nie (pozycja F12, `docs/decisions.md`). Semantyka
jednego parametru nie mówi nic o sąsiednim.

**Jedno żądanie wystarcza, i tylko dzięki asymetrii wyniku**: żaden wpis nie ma dwóch miast
działalności, więc pod ANDem zbiór jest pusty z definicji. Każdy `count > 0` wyklucza AND.
Wynik zerowy wyklucza natomiast tylko OR-a — i wtedy trzeba osobnego pomiaru na to, czy
przyczyną jest AND, czy coś trzeciego (np. odrzucenie powtórzonego parametru).

Żadnych rekordów nie pobieramy: `limit=1` daje samą liczbę trafień. Zapytanie budujemy przez
`Criteria.to_params`, czyli tym samym kodem, którym buduje je narzędzie — sonda ma mierzyć
dialekt, którego program naprawdę używa.

**Uruchomiona na produkcji 2026-09-09**: `count = 242 415`, czyli **OR**. Zdanie z dwoma
miastami działa tak, jak operator się spodziewa. Ponowny przebieg ma sens tylko przy
podejrzeniu zmiany po stronie API — wynik jest w `docs/decisions.md`.

Uruchomienie wymaga zgody właściciela w sesji — patrz CLAUDE.md.

    PYTHONUTF8=1 python scripts/ceidg_probe_repeated_city.py --env prod
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

from probe_support import no_proxy_opener, shared_gate  # noqa: E402

from ceidg_tool.apiprofile import load_profile  # noqa: E402
from ceidg_tool.criteria import Criteria  # noqa: E402

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}

# Dwa duże miasta w **różnych** województwach. Województwa celowo nie ma w kryteriach:
# dołożone, ANDowałoby się z obydwoma miastami i wynik byłby pusty niezależnie od tego,
# czego szukamy — czyli sonda mierzyłaby własny błąd.
MIASTA = ("Wrocław", "Gdańsk")


# Sufit funkcji (#158): 52 instrukcje przy sufcie 50. Jak wyżej: sonda, nie moduł produktu.
def main() -> int:  # noqa: PLR0915
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
    profil = load_profile(args.env)
    kryteria = Criteria.model_validate({"miasto": list(MIASTA)})
    pary = [*kryteria.to_params(profil), ("limit", "1")]
    url = f"{base}/firmy?" + urllib.parse.urlencode(pary)

    print(f"Sonda powtórzonego `miasto=`, środowisko {args.env}, 1 żądanie.")
    print(f"  miasta: {', '.join(MIASTA)}")
    print(f"  parametry: {pary}\n")

    opener = no_proxy_opener()
    with shared_gate(args.env, token) as gate:
        gate.before("firmy")
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "ceidg-probe-repeated-city/0.1",
            },
        )
        started = time.monotonic()
        try:
            with opener.open(req, timeout=120) as resp:
                status, data = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, data = exc.code, exc.read()
        except urllib.error.URLError as exc:
            status, data = -1, str(exc.reason).encode()
        gate.after(status)

    count: int | None = None
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
            if isinstance(body, dict) and isinstance(body.get("count"), int):
                count = body["count"]
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    print(f"  HTTP {status}  {time.monotonic() - started:5.2f}s  count={count}")

    print("\n--- wynik ---")
    if status == -1:
        print("  Brak połączenia — nie wyciągaj wniosku.")
        return 1
    if count and count > 0:
        print(f"  count={count} > 0 → powtórzone `miasto=` jest **ORowane**.")
        print("  Żaden wpis nie ma dwóch miast działalności, więc AND dałby zero z definicji.")
        print("  Zdanie z dwoma miastami działa tak, jak operator się spodziewa.")
        return 0
    print("  Pusty wynik → powtórzone `miasto=` NIE jest ORowane.")
    print("  Zdanie z dwoma miastami wraca puste, bez błędu. Asystent musi wtedy albo")
    print("  rozbić zapytanie na miasta, albo powiedzieć operatorowi, że tak się nie da.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
