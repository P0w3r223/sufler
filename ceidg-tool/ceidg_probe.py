#!/usr/bin/env python3
"""
ceidg_probe.py — sonda API v3 Hurtowni Danych CEIDG (środowisko testowe lub produkcyjne).

Odpowiada na pytania, których nie rozstrzyga dokumentacja, i zapisuje raport
do katalogu probe_out/ (findings.md + surowe odpowiedzi JSON + pobrany raport ZIP).

Użycie:
    export CEIDG_TOKEN="eyJ..."            # token JWT z biznes.gov.pl
    python ceidg_probe.py                  # domyślnie środowisko testowe
    python ceidg_probe.py --env prod       # produkcja (uwaga na limity!)
    python ceidg_probe.py --skip-raport    # bez pobierania pliku raportu

Skrypt wykonuje ok. 30 żądań z odstępem 3,7 s, więc trwa ~2 minuty i nie zbliża
się do limitu 50 żądań / 3 min. Nie wywołuje celowo kodu 429.
Wymaga tylko biblioteki standardowej (Python 3.9+).
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}
SPACING_S = 3.7          # dokumentacja: optymalnie 3,6 s między zapytaniami
OUT = Path("probe_out")
SAMPLES = OUT / "samples"

findings: list[str] = []      # linie raportu markdown
_last_call = 0.0
_call_no = 0


# ----------------------------------------------------------------------------- HTTP

def call(path: str, params: dict | list | None = None, raw: bool = False):
    """GET z tokenem, stałym odstępem i zapisem surowej odpowiedzi.

    Zwraca (status, headers, body) gdzie body to dict/list (JSON), bytes (raw)
    albo None (204 / brak treści). Nigdy nie rzuca — błędy HTTP wracają jako status.
    """
    global _last_call, _call_no
    wait = SPACING_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)

    url = f"{BASE[ARGS.env]}/{path.lstrip('/')}"
    if params:
        # doseq=True → nip=1&nip=2 dla list, zgodnie z dokumentacją
        url += "?" + urllib.parse.urlencode(params, doseq=True)

    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {ARGS.token}",
        "Accept": "application/json, application/octet-stream",
        "User-Agent": "ceidg-probe/0.1",
    })
    _call_no += 1
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, headers, data = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        status, headers, data = e.code, dict(e.headers), e.read()
    except urllib.error.URLError as e:
        status, headers, data = -1, {}, str(e.reason).encode()
    dt = time.monotonic() - t0
    _last_call = time.monotonic()

    print(f"[{_call_no:02d}] {status} {dt:5.2f}s  GET {url[:110]}")

    if raw:
        return status, headers, data

    body = None
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"_nonjson": data[:500].decode("utf-8", "replace")}

    # zapis surowej odpowiedzi do analizy po fakcie
    SAMPLES.mkdir(parents=True, exist_ok=True)
    fname = SAMPLES / f"{_call_no:02d}_{status}_{path.strip('/').replace('/', '_')}.json"
    fname.write_text(json.dumps({
        "url": url, "status": status, "headers": headers, "elapsed_s": round(dt, 3),
        "body": body,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return status, headers, body


def note(line: str = ""):
    findings.append(line)
    print("   " + line if line else "")


def section(title: str):
    findings.append(f"\n## {title}\n")
    print(f"\n=== {title} ===")


def firmy_of(body) -> list:
    return (body or {}).get("firmy", []) if isinstance(body, dict) else []


# ----------------------------------------------------------------------------- sondy

def probe_auth():
    section("1. Autoryzacja i nagłówki")
    status, headers, body = call("firmy", {"limit": 1})
    if status == 401:
        note("**Token odrzucony (401).** Sprawdź, czy token jest z właściwego środowiska "
             f"({ARGS.env}) i czy nie wygasł. Dalsze sondy nie mają sensu.")
        return False
    if status == -1:
        note(f"**Brak połączenia:** {body}")
        return False
    note(f"Status pierwszego zapytania: `{status}`.")
    rl = {k: v for k, v in headers.items() if "limit" in k.lower() or "retry" in k.lower()}
    note(f"Nagłówki związane z limitami: `{rl or 'brak'}` "
         "(jeśli brak, klient musi liczyć żądania sam).")
    if isinstance(body, dict):
        note(f"Klucze odpowiedzi `/firmy`: `{sorted(body.keys())}`.")
        note(f"Pole `count` przy limit=1: `{body.get('count')}` "
             "(jeśli >1, to `count` = liczba wszystkich trafień, nie pozycji na stronie).")
        links = body.get("links", {})
        note(f"Przykładowy `links.first`: `{links.get('first', '')[:160]}`")
    return True


def probe_paging():
    section("2. Numeracja stron i maksymalny limit")
    ids = {}
    for page in (0, 1, 2):
        status, _, body = call("firmy", {"limit": 5, "page": page})
        f = firmy_of(body)
        ids[page] = [x.get("id") for x in f]
        note(f"page={page}: status `{status}`, zwrócono {len(f)} rekordów.")
    if ids[0] and ids[1]:
        same = ids[0] == ids[1]
        note("**Wniosek:** page=0 i page=1 zwracają "
             + ("**te same** rekordy → numeracja zaczyna się od 1 (page=0 to alias 1)."
                if same else "**różne** rekordy → numeracja zaczyna się od 0 (**klient musi startować od 0**)."))
    for lim in (25, 50, 100, 500):
        status, _, body = call("firmy", {"limit": lim, "page": 1})
        f = firmy_of(body)
        note(f"limit={lim}: status `{status}`, zwrócono {len(f)} rekordów.")
    note("Maksymalny sensowny `limit` = największa wartość, dla której liczba rekordów "
         "rośnie razem z parametrem (albo ostatnia bez 400).")


def probe_filters():
    section("3. Semantyka filtrów")
    tests = [
        ("nazwa fragment małe litery", {"nazwa": "adam", "limit": 3}),
        ("nazwa fragment WIELKIE", {"nazwa": "ADAM", "limit": 3}),
        ("nazwa bez trafienia (204?)", {"nazwa": "xqzv-nie-istnieje-9981", "limit": 3}),
        ("wojewodztwo małe litery", {"wojewodztwo": "podlaskie", "limit": 3}),
        ("wojewodztwo WIELKIE", {"wojewodztwo": "PODLASKIE", "limit": 3}),
        ("pkd bez kropki", {"pkd": "6201Z", "limit": 3}),
        ("pkd z kropkami", {"pkd": "62.01.Z", "limit": 3}),
        ("pkd małe z", {"pkd": "6201z", "limit": 3}),
        ("status jeden", {"status": "AKTYWNY", "limit": 3}),
        ("status lista", {"status": ["AKTYWNY", "ZAWIESZONY"], "limit": 3}),
        ("data rozpoczęcia 2014", {"dataod": "2014-01-01", "datado": "2014-12-31", "limit": 3}),
        ("data zły format", {"dataod": "01.01.2014", "limit": 3}),
        ("miasto lista", {"miasto": ["Białystok", "Warszawa"], "limit": 3}),
    ]
    for label, params in tests:
        status, _, body = call("firmy", params)
        f = firmy_of(body)
        cnt = body.get("count") if isinstance(body, dict) else None
        extra = ""
        if f and "nazwa" in params:
            extra = " / nazwy: " + "; ".join(x.get("nazwa", "")[:40] for x in f[:2])
        if f and "wojewodztwo" in params:
            extra = " / woj.: " + ", ".join(x.get("adresDzialalnosci", {}).get("wojewodztwo", "") for x in f[:2])
        if status == 204:
            extra = " / **pusta odpowiedź — klient musi obsłużyć 204 bez .json()**"
        note(f"{label}: status `{status}`, count=`{cnt}`, rekordów {len(f)}{extra}")
    note("Porównaj pary (małe/WIELKIE, z kropką/bez): identyczny `count` = parametr "
         "niewrażliwy na wariant; 400 lub 204 = wariant odrzucony.")


def probe_details():
    section("4. Szczegóły firmy i wypełnienie pól")
    status, _, body = call("firmy", {"limit": 25, "page": 1})
    lst = firmy_of(body)
    if not lst:
        note("Brak listy firm do pobrania szczegółów.")
        return
    # 1) po ścieżce /firma/{id}
    fid = lst[0]["id"]
    status, _, det = call(f"firma/{fid}")
    note(f"`/firma/{{id}}`: status `{status}`, klucz główny: `{list(det.keys()) if isinstance(det, dict) else det}`")
    # 2) po parametrze ids[] — czy jedno zapytanie zwróci wiele firm?
    ids = [x["id"] for x in lst[:5]]
    status, _, multi = call("firma", {"ids": ids})
    got = len(multi.get("firma", [])) if isinstance(multi, dict) else 0
    note(f"`/firma?ids=…` z {len(ids)} identyfikatorami: status `{status}`, zwrócono {got} firm. "
         + ("**Batchowanie działa → szczegóły dla N firm kosztują 1 zapytanie, nie N.**" if got > 1
            else "Batchowanie nie działa lub zwraca 1 → szczegóły kosztują 1 zapytanie na firmę."))
    # 3) inwentaryzacja pól na próbce
    sample = multi.get("firma", []) if isinstance(multi, dict) and got else ([det["firma"][0]] if isinstance(det, dict) and det.get("firma") else [])
    if sample:
        keys = sorted({k for rec in sample for k in rec.keys()})
        note(f"Pola obecne w próbce ({len(sample)} rekordów): `{keys}`")
        for fld in ("email", "telefon", "www", "pkd", "pkdGlowny", "adresKorespondencyjny", "dataZawieszenia"):
            filled = sum(1 for r in sample if r.get(fld))
            note(f"- `{fld}`: wypełnione w {filled}/{len(sample)}")
        note("Na środowisku testowym dane są sztuczne; procent wypełnienia kontaktów "
             "zweryfikuj na produkcji na próbce ~100 rekordów.")


def probe_raporty():
    section("5. Raporty gotowe (/raporty)")
    status, _, body = call("raporty")
    reps = body.get("raporty", []) if isinstance(body, dict) else []
    note(f"status `{status}`, liczba raportów: {len(reps)}")
    for r in reps[:30]:
        note(f"- {r.get('nazwa')} | {r.get('format')} | {r.get('data-utworzenia') or r.get('dataUtworzenia')}")
    if len(reps) > 30:
        note(f"- … i {len(reps) - 30} więcej (pełna lista w samples/)")
    if not reps or ARGS.skip_raport:
        return
    # pobierz pierwszy raport i zajrzyj do środka
    r = reps[0]
    rid = r.get("id")
    status, headers, data = call(f"raport/{rid}", raw=True)
    note(f"Pobranie `{r.get('nazwa')}`: status `{status}`, {len(data)/1024:.0f} KB, "
         f"content-type `{headers.get('Content-Type') or headers.get('content-type')}`")
    if status != 200:
        return
    OUT.mkdir(exist_ok=True)
    zpath = OUT / "raport_sample.zip"
    zpath.write_bytes(data)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            note(f"Pliki w ZIP: `{names}`")
            for n in names[:2]:
                blob = z.read(n)
                text = None
                for enc in ("utf-8-sig", "utf-8", "cp1250"):
                    try:
                        text = blob.decode(enc)
                        note(f"`{n}`: kodowanie `{enc}`, {len(blob)/1024:.0f} KB")
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:
                    note(f"`{n}`: nie jest tekstem (binarny)")
                    continue
                head = text.splitlines()[:3]
                delim = ";" if head and head[0].count(";") > head[0].count(",") else ","
                rows = list(csv.reader(io.StringIO(text), delimiter=delim))
                note(f"separator `{delim}`, wierszy: {len(rows) - 1}, kolumn: {len(rows[0]) if rows else 0}")
                note(f"nagłówek: `{rows[0] if rows else []}`")
                if len(rows) > 1:
                    note(f"pierwszy wiersz: `{[c[:30] for c in rows[1]]}`")
    except zipfile.BadZipFile:
        note("Odpowiedź nie jest poprawnym ZIP — sprawdź plik raport_sample.zip ręcznie.")


def probe_zmiana():
    section("6. Zmiany (/zmiana)")
    d_to = date.today()
    d_from = d_to - timedelta(days=3)
    status, _, body = call("zmiana", {"dataod": d_from.isoformat(), "datado": d_to.isoformat(), "limit": 100})
    cnt = body.get("count") if isinstance(body, dict) else None
    n = len(body.get("identyfikatoryWpisow", [])) if isinstance(body, dict) else 0
    note(f"ostatnie 3 dni: status `{status}`, count=`{cnt}`, identyfikatorów na stronie: {n}")
    note("Jeśli count > 0 na środowisku testowym, przyrostowa aktualizacja bazy jest wykonalna; "
         "na produkcji spodziewaj się tysięcy zmian dziennie.")


# ----------------------------------------------------------------------------- main

def main():
    OUT.mkdir(exist_ok=True)
    findings.append(f"# Sonda API CEIDG v3 — środowisko `{ARGS.env}` — {date.today().isoformat()}\n")
    findings.append(f"Bazowy URL: `{BASE[ARGS.env]}`. Odstęp między zapytaniami: {SPACING_S} s.")
    t0 = time.monotonic()
    ok = probe_auth()
    if ok:
        probe_paging()
        probe_filters()
        probe_details()
        probe_raporty()
        probe_zmiana()
    section("Podsumowanie")
    note(f"Wykonano {_call_no} żądań w {time.monotonic() - t0:.0f} s. "
         f"Surowe odpowiedzi: `{SAMPLES}/`.")
    note("Na podstawie sekcji 2–5 ustal: początek numeracji stron, maksymalny limit, "
         "obsługę 204, czy `ids[]` batchuje szczegóły, oraz czy raporty CSV pokrywają "
         "typowe zapytania (region + okres).")
    (OUT / "findings.md").write_text("\n".join(findings), encoding="utf-8")
    print(f"\nRaport zapisany: {OUT / 'findings.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", choices=BASE.keys(), default="test")
    ap.add_argument("--token", default=os.environ.get("CEIDG_TOKEN", ""))
    ap.add_argument("--skip-raport", action="store_true", help="nie pobieraj pliku raportu (ZIP)")
    ARGS = ap.parse_args()
    if not ARGS.token:
        sys.exit("Brak tokenu: ustaw zmienną CEIDG_TOKEN albo podaj --token.")
    main()
