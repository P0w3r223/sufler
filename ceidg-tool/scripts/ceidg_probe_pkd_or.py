#!/usr/bin/env python
"""Sonda celowana: czy powtórzone `pkd=` działa jak OR, czy jak AND?

Pytanie jest starsze niż ADR-0012 i przez cztery dni nikt go nie zadał. Zmierzone jest
powtórzone `nip=` (2026-09-06, działa jak OR) oraz `status=`; `api_notes.md` opisuje `pkd[]`
jako parametr listowy — ale **dla `pkd` nigdy tego nie sprawdzono**. Tymczasem narzędzie już
dziś wysyła zapytania wielokodowe: asystent zwrócił cztery kody w przebiegu A1 i piętnaście
w A7. Żaden przebieg grupy A nie doszedł do pobrania, więc to zachowanie nie dotknęło jeszcze
API. Gdyby kody były AND-owane, takie zapytanie zwracałoby zero, a operator przeczytałby
„brak firm" — odpowiedź pewna siebie i całkowicie błędna.

Test jest ostry, bo oba kody należą do **rozłącznych** roczników (rejestr trzyma jeden rocznik
przy rekordzie, `docs/decisions.md`):

- `9621Z` „Działalność fryzjerska" — PKD 2025.
- `9602Z` „Fryzjerstwo i pozostałe zabiegi kosmetyczne" — PKD 2007, w PKD 2025 nie istnieje.

Zatem przy OR trzecie żądanie musi zwrócić **dokładnie sumę** dwóch pierwszych, a przy AND
około zera. Trzecia możliwość — że serwer bierze pod uwagę tylko jedno wystąpienie parametru —
też jest rozpoznawalna: wynik równy jednemu ze składników.

Trzy żądania `limit=1`, czyli same liczby trafień; żadnych danych osobowych nie pobieramy
i nie zapisujemy. Sonda dzieli limiter i historię żądań z narzędziem (`probe_support`).

Uruchomienie wymaga zgody właściciela w sesji — patrz CLAUDE.md.

    PYTHONUTF8=1 python scripts/ceidg_probe_pkd_or.py --env prod
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

NOWY = "9621Z"
STARY = "9602Z"

# (etykieta, krotka kodów, po co pytamy)
PRZYPADKI: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("A", (NOWY,), "sam kod PKD 2025 — składnik pierwszy"),
    ("B", (STARY,), "sam kod PKD 2007 — składnik drugi"),
    ("C", (NOWY, STARY), "oba naraz — to jest właściwy pomiar"),
)

GATE: SharedGate
OPENER = no_proxy_opener()
_n = 0


def call(base: str, token: str, kody: tuple[str, ...]) -> tuple[int, int | None]:
    """Jedno `count` dla podanych kodów PKD. Zwraca (status, count albo None)."""
    global _n
    GATE.before("firmy")
    pary = [("pkd", k) for k in kody] + [("limit", "1")]
    url = f"{base}/firmy?" + urllib.parse.urlencode(pary)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ceidg-probe-pkd-or/0.1",
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
    opis = "&".join(f"pkd={k}" for k in kody)
    print(f"[{_n}/{len(PRZYPADKI)}] {opis}  HTTP {status}  {czas:5.2f}s  count={count}")
    return status, count


def werdykt(a: int, b: int, c: int) -> str:
    """Nazywa zaobserwowaną semantykę. Suma jest dokładna, bo roczniki są rozłączne."""
    if c == a + b:
        return (
            "Powtórzone `pkd=` działa jak OR — trzecie żądanie zwróciło dokładnie sumę.\n"
            "ADR-0012 (warianty C/D/E) stoi na mocnym gruncie, a dzisiejsze wielokodowe\n"
            "zapytania asystenta faktycznie sumują kody, tak jak zakładaliśmy bez dowodu."
        )
    if c == 0:
        return (
            "Powtórzone `pkd=` działa jak AND — oba kody naraz dają zero, bo żaden rekord\n"
            "nie ma dwóch roczników. To POWAŻNE: dzisiejsze wielokodowe zapytania asystenta\n"
            'zwracają pustkę, a operator czyta „brak firm". Warianty C/D/E z ADR-0012 upadają.'
        )
    if c in (a, b):
        return (
            "Serwer bierze pod uwagę TYLKO JEDNO wystąpienie parametru `pkd` — wynik równa się\n"
            f"jednemu ze składników ({'pierwszemu' if c == a else 'drugiemu'}), a nie ich sumie.\n"
            "Skutek jest taki sam jak przy AND: zapytanie wielokodowe cicho gubi kody."
        )
    return (
        "Wynik nie jest ani sumą, ani zerem, ani żadnym ze składników — semantyka jest inna\n"
        f"niż wszystkie trzy hipotezy (suma byłaby {a + b}). Nie wyciągaj wniosku z tej liczby;\n"
        "zanotuj ją i zaprojektuj kolejny pomiar."
    )


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
    print(f"Sonda semantyki powtórzonego `pkd=`, środowisko {args.env}, {len(PRZYPADKI)} żądania.")
    print()

    global GATE
    wyniki: dict[str, int | None] = {}
    with shared_gate(args.env, token) as gate:
        GATE = gate
        for etykieta, kody, po_co in PRZYPADKI:
            print(f"  {etykieta}: {po_co}")
            wyniki[etykieta] = call(base, token, kody)[1]

    print("\n--- wynik ---")
    a, b, c = wyniki["A"], wyniki["B"], wyniki["C"]
    if a is None or b is None or c is None:
        print("Któreś żądanie nie zwróciło `count` — wynik nierozstrzygający, nie interpretuj go.")
        return 1
    if a == 0 or b == 0:
        print(f"Składnik zerowy ({NOWY}={a}, {STARY}={b}) — bez dwóch niepustych zbiorów")
        print("ten pomiar niczego nie odróżnia. Sprawdź kody i środowisko.")
        return 1
    print(f"{NOWY} = {a}   {STARY} = {b}   suma = {a + b}   oba razem = {c}")
    print()
    print(werdykt(a, b, c))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
