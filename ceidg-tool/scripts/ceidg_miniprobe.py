#!/usr/bin/env python3
"""Mini-sonda uzupełniająca (ok. 7 żądań): rozmiar porcji `ids`, limit /zmiana,
`links.next` na ostatniej stronie, zawartość jednego raportu CSV.

Użycie:  CEIDG_TOKEN=... python scripts/ceidg_miniprobe.py --env prod
Wynik:   probe_out/miniprobe_findings.md + probe_out/mini/*.json + probe_out/raport_sample.zip
"""

from __future__ import annotations

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
from pathlib import Path

from probe_support import SharedGate, no_proxy_opener, shared_gate

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}
OUT = Path("probe_out")
SAMPLES = OUT / "mini"
findings: list[str] = []
GATE: SharedGate
# Sonda niesie ten sam token co narzędzie, więc obowiązuje ją ta sama polityka wyjścia (§B).
OPENER = no_proxy_opener()
_n = 0


def call(path: str, params: dict | list | None = None, raw: bool = False):
    global _n
    GATE.before(path.strip("/") or "firmy")
    url = f"{BASE[ARGS.env]}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {ARGS.token}",
            "Accept": "application/json, application/octet-stream",
            "User-Agent": "ceidg-miniprobe/0.1",
        },
    )
    _n += 1
    t0 = time.monotonic()
    try:
        with OPENER.open(req, timeout=120) as resp:
            status, headers, data = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        status, headers, data = e.code, dict(e.headers), e.read()
    except urllib.error.URLError as e:
        status, headers, data = -1, {}, str(e.reason).encode()
    GATE.after(status)
    print(
        f"[{_n:02d}] {status} {time.monotonic() - t0:5.2f}s  {len(url)} znaków URL  GET {url[:90]}"
    )
    if raw:
        return status, headers, data
    body = None
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"_nonjson": data[:300].decode("utf-8", "replace")}
    SAMPLES.mkdir(parents=True, exist_ok=True)
    (SAMPLES / f"{_n:02d}_{status}_{path.strip('/').replace('/', '_')}.json").write_text(
        json.dumps(
            {"url": url, "status": status, "headers": headers, "body": body},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return status, headers, body


def note(line: str = "") -> None:
    findings.append(line)
    print("   " + line if line else "")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    findings.append(f"# Mini-sonda CEIDG v3 — `{ARGS.env}` — {time.strftime('%Y-%m-%d')}\n")

    note("## 1. Rozmiar porcji ids[]")
    ids: list[str] = []
    nip_for_small_query = None
    for page in (0, 1):
        status, _, body = call("firmy", {"wojewodztwo": "PODLASKIE", "limit": 25, "page": page})
        firms = (body or {}).get("firmy", []) if isinstance(body, dict) else []
        ids += [f["id"] for f in firms]
        if firms and not nip_for_small_query:
            nip_for_small_query = (firms[0].get("wlasciciel") or {}).get("nip")
        note(f"strona {page}: status {status}, {len(firms)} rekordów (łącznie {len(ids)} id)")
    for n in (25, 50):
        chunk = ids[:n]
        if len(chunk) < n:
            note(f"ids={n}: za mało identyfikatorów ({len(chunk)})")
            continue
        status, headers, body = call("firma", {"ids": chunk})
        got = len(body.get("firma", [])) if isinstance(body, dict) else 0
        err = body if status != 200 else ""
        note(
            f"ids={n}: status {status}, zwrócono {got} firm{(' | ' + json.dumps(err, ensure_ascii=False)[:200]) if err else ''}"  # noqa: E501
        )

    note("\n## 2. /zmiana limit=500")
    status, _, body = call("zmiana", {"dataod": "2026-09-04", "datado": "2026-09-05", "limit": 500})
    got = len(body.get("identyfikatoryWpisow", [])) if isinstance(body, dict) else 0
    note(
        f"status {status}, count={body.get('count') if isinstance(body, dict) else None}, id na stronie: {got}"  # noqa: E501
        + (f" | {json.dumps(body, ensure_ascii=False)[:200]}" if status != 200 else "")
    )

    note("\n## 3. links.next na ostatniej stronie")
    if nip_for_small_query:
        status, _, body = call("firmy", {"nip": nip_for_small_query, "limit": 25})
        if isinstance(body, dict):
            links = body.get("links", {})
            note(
                f"status {status}, count={body.get('count')}, rekordów {len(body.get('firmy', []))}"
            )
            note(f"klucze links: {sorted(links.keys())}")
            note(
                f"next == self: {links.get('next') == links.get('self')} | next obecny: {'next' in links} | last == self: {links.get('last') == links.get('self')}"  # noqa: E501
            )
        else:
            note(f"status {status} (brak JSON)")
    else:
        note("brak NIP do małego zapytania")

    note("\n## 4. Zawartość raportu CSV")
    sample25 = Path("probe_out/samples/25_200_raporty.json")
    if not sample25.exists():
        note("brak probe_out/samples/25_200_raporty.json — pomijam")
    else:
        reps = json.loads(sample25.read_text(encoding="utf-8"))["body"]["raporty"]
        csvs = [
            r
            for r in reps
            if r.get("format") == ".csv"
            and r["nazwa"].startswith("Zarejestrowane")
            and "opolskie" in r["nazwa"]
        ]
        pick = sorted(csvs, key=lambda r: r.get("data-utworzenia", ""))[-1] if csvs else reps[0]
        note(f"raport: {pick['nazwa']} | {pick['format']} | {pick.get('data-utworzenia')}")
        status, headers, data = call(f"raport/{pick['id']}", raw=True)
        ctype = headers.get("Content-Type") or headers.get("content-type")
        note(
            f"pobranie: status {status}, {len(data) / 1024:.0f} KB, content-type {ctype}, content-disposition {headers.get('Content-Disposition')}"  # noqa: E501
        )
        if status == 200:
            (OUT / "raport_sample.zip").write_bytes(data)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    names = z.namelist()
                    note(f"pliki w ZIP: {names}")
                    for name in names[:2]:
                        blob = z.read(name)
                        text = None
                        for enc in ("utf-8-sig", "utf-8", "cp1250"):
                            try:
                                text = blob.decode(enc)
                                note(f"`{name}`: kodowanie {enc}, {len(blob) / 1024:.0f} KB")
                                break
                            except UnicodeDecodeError:
                                continue
                        if text is None:
                            note(f"`{name}`: binarny")
                            continue
                        head = text.splitlines()[:2]
                        delim = ";" if head and head[0].count(";") >= head[0].count(",") else ","
                        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
                        note(
                            f"separator `{delim}`, wierszy danych: {len(rows) - 1}, kolumn: {len(rows[0]) if rows else 0}"  # noqa: E501
                        )
                        note(f"nagłówek: {rows[0] if rows else []}")
                        if len(rows) > 1:
                            note(f"typy w 1. wierszu (długości): {[len(c) for c in rows[1]]}")
            except zipfile.BadZipFile:
                note("odpowiedź nie jest ZIP — sprawdź probe_out/raport_sample.zip")

    note(f"\nWykonano {_n} żądań.")
    (OUT / "miniprobe_findings.md").write_text("\n".join(findings), encoding="utf-8")
    print(f"\nRaport: {OUT / 'miniprobe_findings.md'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", choices=BASE.keys(), default="test")
    ap.add_argument("--token", default=os.environ.get("CEIDG_TOKEN", ""))
    ARGS = ap.parse_args()
    if not ARGS.token:
        sys.exit("Brak tokenu: ustaw CEIDG_TOKEN.")
    # Wspólna historia żądań: sonda i pobranie przestają być dla siebie niewidzialne.
    with shared_gate(ARGS.env, ARGS.token) as GATE:
        main()
