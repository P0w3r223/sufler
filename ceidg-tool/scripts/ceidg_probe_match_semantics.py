#!/usr/bin/env python
"""Sonda celowana: które filtry `/firmy` dopasowują **fragmentem**, a które dokładnie?

Po co: `reports.matches_criteria` odtwarza filtry `/firmy` lokalnie, na wierszach dziennego
raportu. Jeśli odtwarza je inaczej, niż robi to serwer, ścieżka raportu **cicho zwraca inny
zbiór** niż ścieżka API — a komentarz nad tą funkcją zapewnia, że zbiory są te same. Audyt
2026-09-08 nazwał to pozycją F12 i zostawił otwartą, bo siedem pól nigdy nie zostało
zmierzonych: `imie`, `nazwisko`, `miasto`, `powiat`, `gmina`, `kod`, `ulica`.
Z tej siódemki `kod` odpada bez żądania: `Criteria` waliduje go do postaci
`15-333`, więc fragment nigdy nie wychodzi z narzędzia, a przy stałej długości
sześciu znaków „zawiera" i „równa się" to jedno i to samo.

**Dwa z nich były już rozstrzygnięte, tylko nikt nie zajrzał:**

- `nazwa` — substring, case-insensitive (`adam` = `ADAM` = 82 954; `docs/decisions.md`).
- `miasto` — substring, rozstrzygnięte **za zero żądań** 2026-09-09 z bazy operatora. Run
  `eb1df3a8`, filtr `miasto=['Łomża']`, zwrócił z produkcji `Stara Łomża przy Szosie` (×2)
  i `Stara Łomża nad Rzeką` (×2). To miejscowości zawierające „Łomża", ale jej nierówne, więc
  dopasowanie jest fragmentem — i to fragmentem ze **środka**, nie prefiksem. Ten sam run
  pokazał `ŁOMŻA` wielkimi literami, co potwierdza nieczułość na wielkość liter.

Ta sonda mierzy **sześć pozostałych** pól dwoma żądaniami. Fragmenty to wycinki ze środka
prawdziwych wartości jednego rekordu z bazy (`v[1:-1]`), więc nie są ani równe oryginałowi,
ani jego prefiksem: silnik dopasowujący fragmentem musi je znaleźć, a dopasowujący dokładnie
albo prefiksem — nie.

    żądanie 1 (adres):  powiat + gmina + ulica, wszystkie jako fragmenty
    żądanie 2 (osoba):  imie + nazwisko, jako fragmenty

Parametry są ANDowane, więc `count > 0` oznacza, że **wszystkie** pola w tej grupie
dopasowują fragmentem; `count = 0` oznacza, że co najmniej jedno tego nie robi — i wtedy
grupa wymaga rozbicia, a nie wniosku o konkretnym polu. Rozdzielenie na dwie grupy zamiast
jednej sześciopolowej jest po to, żeby zero w jednej nie zatruwało drugiej.

Zapytanie budujemy przez `Criteria.to_params(load_profile(...))` — tym samym kodem, którym
buduje je narzędzie. Sonda mierzy dialekt, którego program naprawdę używa, a nie ręcznie
sklejony URL, który mógłby się od niego różnić.

Wartości pól osobowych **nie są wypisywane** ani zapisywane; na ekran idą tylko długości
fragmentów i liczby trafień. Odpowiedzi biorą `limit=1`, więc nie pobieramy rekordów.

Uruchomienie wymaga zgody właściciela w sesji — patrz CLAUDE.md.

    PYTHONUTF8=1 python scripts/ceidg_probe_match_semantics.py --env prod --fragmenty plik.json
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

from ceidg_tool.apiprofile import load_profile  # noqa: E402
from ceidg_tool.criteria import Criteria  # noqa: E402

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}

# Pola osobowe nie trafiają na ekran — nazwa grupy decyduje o tym, co wolno wypisać.
GRUPY: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # `kod` tu nie ma i to jest wynik, nie przeoczenie: `Criteria` waliduje kod pocztowy do
    # postaci `15-333`, więc narzędzie nie potrafi wysłać fragmentu. Przy wartości zawsze
    # sześcioznakowej „zawiera" i „równa się" pokrywają się, więc `_equals_ci` jest tam
    # poprawne z konstrukcji wejścia — bez wydawania żądania.
    ("adres", ("powiat", "gmina", "ulica"), True),
    ("osoba", ("imie", "nazwisko"), False),
)

GATE: SharedGate
OPENER = no_proxy_opener()


def zapytaj(base: str, token: str, kryteria: Criteria, srodowisko: str) -> tuple[int, int | None]:
    """Jedno `count` dla podanych kryteriów. Zwraca (status HTTP, count albo None)."""
    profil = load_profile(srodowisko)
    pary = [*kryteria.to_params(profil), ("limit", "1")]
    url = f"{base}/firmy?" + urllib.parse.urlencode(pary)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ceidg-probe-match-semantics/0.1",
        },
    )
    GATE.before("firmy")
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
    print(f"    HTTP {status}  {time.monotonic() - started:5.2f}s  count={count}")
    return status, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", choices=("test", "prod"), default="test")
    parser.add_argument("--token", default=None)
    parser.add_argument(
        "--fragmenty",
        required=True,
        help="JSON {pole: fragment} — wycinki ze środka wartości jednego prawdziwego rekordu",
    )
    parser.add_argument("--wojewodztwo", default="PODLASKIE")
    args = parser.parse_args()

    import os

    token = args.token or os.environ.get("CEIDG_TOKEN") or ""
    if not token:
        from dotenv import dotenv_values

        token = (dotenv_values(".env").get("CEIDG_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Brak tokenu: ustaw CEIDG_TOKEN albo podaj --token.")

    frag: dict[str, str] = json.loads(Path(args.fragmenty).read_text(encoding="utf-8"))
    base = BASE[args.env]
    print(f"Sonda semantyki dopasowania, środowisko {args.env}, {len(GRUPY)} żądania.\n")

    global GATE
    wyniki: dict[str, int | None] = {}
    with shared_gate(args.env, token) as gate:
        GATE = gate
        for nazwa_grupy, pola, jawne in GRUPY:
            brakujace = [p for p in pola if not frag.get(p)]
            if brakujace:
                print(f"  grupa {nazwa_grupy}: brak fragmentów dla {brakujace} — pomijam")
                continue
            opis = (
                ", ".join(f"{p}='{frag[p]}'" for p in pola)
                if jawne
                else ", ".join(f"{p}=<{len(frag[p])} znaków>" for p in pola)
            )
            print(f"  grupa {nazwa_grupy}: {opis}")
            kryteria = Criteria.model_validate(
                {"wojewodztwo": [args.wojewodztwo], **{p: [frag[p]] for p in pola}}
            )
            wyniki[nazwa_grupy] = zapytaj(base, token, kryteria, args.env)[1]

    print("\n--- wynik ---")
    for nazwa_grupy, pola, _ in GRUPY:
        count = wyniki.get(nazwa_grupy)
        if count is None:
            print(f"  {nazwa_grupy}: brak odpowiedzi — nie wyciągaj wniosku")
        elif count > 0:
            print(f"  {nazwa_grupy}: count={count} → WSZYSTKIE z {list(pola)}")
            print("      dopasowują fragmentem")
        else:
            print(f"  {nazwa_grupy}: count=0 → co najmniej JEDNO z {list(pola)} nie dopasowuje")
            print("      fragmentem. Która — tego to żądanie nie mówi; trzeba rozbić grupę.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
