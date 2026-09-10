#!/usr/bin/env python3
"""Sonda celowana (3 żądania): czy powtórzone `nip=` działa jak OR i ile `ids` przyjmuje `/firma`.

Dwa pytania zostawione otwarte w `docs/decisions.md` i w ADR-0008 (decyzja 5, wariant B):

1. **Powtórzone `nip=`.** Jeśli API łączy je alternatywą, jednym żądaniem da się odpytać do 25
   numerów naraz — to odblokowuje `link_ceidg` dla rekordów z raportu (raport nie ma
   identyfikatora wpisu, ale ma NIP) i tanieje sprawdzanie wielu firm.
2. **Rozmiar porcji `ids`.** Zweryfikowane jest 5; 25 i 50 odbijają się o 400. Jeśli działa 20,
   pobieranie szczegółów tysiąca firm spada z 200 żądań do 50.

Identyfikatory i NIP-y bierzemy z próbek zapisanych wcześniej w `probe_out/` — dzięki temu
sonda nie wydaje żadnego żądania na zdobycie danych wejściowych i mieści się w trzech.

Uruchomienie wymaga jawnej zgody, bo środowisko testowe jest z tej sieci nieosiągalne
i jedyne działające jest produkcyjne, z prawdziwymi danymi osobowymi:

    PYTHONUTF8=1 python scripts/ceidg_probe_nip_ids.py --env prod --produkcja

Wynik: `probe_out/nip_ids_findings.md` (bez danych osobowych) i surowe odpowiedzi
w `probe_out/nip_ids/` (katalog jest w .gitignore).

Sonda przechodzi przez ten sam limiter i tę samą historię żądań (`request_log`), co narzędzie,
więc równoległe pobieranie nie jest już problemem: obie strony widzą swoje żądania i odczekują
zamiast dokładać się do jednego okna. Limit API jest nakładany na token, a nie na proces.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from probe_support import SharedGate, no_proxy_opener, shared_gate

BASE = {
    "test": "https://test-dane.biznes.gov.pl/api/ceidg/v3",
    "prod": "https://dane.biznes.gov.pl/api/ceidg/v3",
}
OUT = Path("probe_out")
SAMPLES = OUT / "nip_ids"
IDS_SIZES = (10, 20)

findings: list[str] = []
GATE: SharedGate
# Sonda niesie ten sam token co narzędzie, więc obowiązuje ją ta sama polityka wyjścia (§B).
OPENER = no_proxy_opener()
_n = 0


def note(line: str = "") -> None:
    findings.append(line)
    print("   " + line if line else "")


def call(path: str, params: list[tuple[str, str]]) -> tuple[int, object]:
    """GET przez wspólną bramkę. Zapytanie **nie** trafia na ekran — niesie NIP-y."""
    global _n
    GATE.before(path.strip("/") or "firmy")
    url = f"{ARGS.base}/{path.lstrip('/')}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {ARGS.token}",
            "Accept": "application/json",
            "User-Agent": "ceidg-probe-nip-ids/0.1",
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
    # Na ekran idzie ścieżka i długość URL-a, nigdy parametry: te niosą NIP-y.
    print(f"[{_n}/3] {status}  {time.monotonic() - started:5.2f}s  {len(url)} znaków URL  /{path}")
    body: object = None
    if data:
        try:
            body = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"_nonjson": data[:300].decode("utf-8", "replace")}
    SAMPLES.mkdir(parents=True, exist_ok=True)
    (SAMPLES / f"{_n}_{status}_{path.strip('/')}.json").write_text(
        json.dumps({"url": url, "status": status, "body": body}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return status, body


def harvest() -> tuple[list[str], list[str]]:
    """Identyfikatory i NIP-y z wcześniejszych próbek — zero żądań."""
    ids: list[str] = []
    nips: list[str] = []
    for path in sorted(glob.glob("probe_out/**/*.json", recursive=True)):
        try:
            sample = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        body = sample.get("body") if isinstance(sample, dict) else None
        if not isinstance(body, dict):
            continue
        for record in (body.get("firmy") or []) + (body.get("firma") or []):
            if record.get("id") and record["id"] not in ids:
                ids.append(record["id"])
            nip = (record.get("wlasciciel") or {}).get("nip")
            if nip and nip not in nips:
                nips.append(nip)
    return ids, nips


def probe_repeated_nip(nips: list[str]) -> None:
    """Dwa różne NIP-y w jednym zapytaniu: OR ⇒ `count` małe i ≥ 2, AND ⇒ 0 (nikt nie ma obu),
    parametr zignorowany ⇒ `count` rzędu całego rejestru."""
    note("## 1. Powtórzone `nip=` — alternatywa czy koniunkcja?")
    pair = nips[:2]
    status, body = call("firmy", [("nip", pair[0]), ("nip", pair[1]), ("limit", "25")])
    count = body.get("count") if isinstance(body, dict) else None
    returned = len(body.get("firmy", [])) if isinstance(body, dict) else 0
    note(f"status {status}, count={count}, rekordów na stronie: {returned}")
    # Górna granica jest tu tak samo ważna jak dolna: gdyby API zignorowało powtórzony
    # parametr zamiast go użyć, `count` poszłoby w setki tysięcy — a sam warunek `>= 2`
    # ogłosiłby wtedy „OR" na podstawie zapytania bez żadnego filtra.
    if status == 200 and isinstance(count, int) and 2 <= count <= len(pair) * 3:
        note("**Wniosek: OR.** Oba numery trafione jednym żądaniem — do 25 NIP-ów na zapytanie.")
    elif status == 200 and isinstance(count, int) and count > len(pair) * 3:
        note(
            f"**Wniosek: parametr zignorowany.** `count`={count} odpowiada zapytaniu bez filtra, "
            "a nie alternatywie dwóch numerów."
        )
    elif status in (204, 200) and (count in (0, None) or returned == 0):
        note("**Wniosek: AND** (albo brak wsparcia). Każdy NIP wymaga osobnego żądania.")
    else:
        note("**Wniosek niejednoznaczny** — patrz surowa odpowiedź w probe_out/nip_ids/.")
    note()


def probe_ids_batch(ids: list[str]) -> None:
    """Ile identyfikatorów `/firma` przyjmuje naraz. 5 jest zweryfikowane, 25 i 50 dają 400."""
    note("## 2. Rozmiar porcji `ids`")
    for size in IDS_SIZES:
        chunk = ids[:size]
        if len(chunk) < size:
            note(f"ids={size}: pominięte, w próbkach jest tylko {len(chunk)} identyfikatorów")
            continue
        status, body = call("firma", [("ids", value) for value in chunk])
        got = len(body.get("firma", [])) if isinstance(body, dict) else 0
        code = body.get("code") if isinstance(body, dict) else None
        note(f"ids={size}: status {status}, zwrócono {got} firm{f', kod {code}' if code else ''}")
    note()
    note("Wartość do `ids_batch_size` w profilu: największy rozmiar, który dał status 200.")
    note()


def load_token() -> str:
    token = os.environ.get("CEIDG_TOKEN", "")
    if token:
        return token
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "CEIDG_TOKEN":
                return value.strip().strip("\"'")
    return ""


def main() -> None:
    if ARGS.env == "prod" and not ARGS.produkcja:
        sys.exit("Produkcja to prawdziwe dane osobowe — potwierdź flagą --produkcja.")
    if not ARGS.token:
        sys.exit("Brak tokenu: ustaw CEIDG_TOKEN albo wpisz go do .env.")
    ids, nips = harvest()
    print(f"Z próbek: {len(ids)} identyfikatorów, {len(nips)} NIP-ów — zero żądań na wejście.")
    if len(nips) < 2:
        sys.exit("Za mało NIP-ów w probe_out/ — uruchom najpierw scripts/ceidg_probe.py.")

    findings.append(f"# Sonda celowana `{ARGS.env}` — {time.strftime('%Y-%m-%d')}\n")
    findings.append(
        "Trzy żądania na produkcji za zgodą właściciela. Dane wejściowe (identyfikatory "
        "i NIP-y) pochodzą z próbek z poprzedniej sondy, więc nie kosztowały żadnego żądania.\n"
    )
    probe_repeated_nip(nips)
    probe_ids_batch(ids)
    findings.append(f"\nŁącznie żądań: {_n}.")

    OUT.mkdir(exist_ok=True)
    report = OUT / "nip_ids_findings.md"
    report.write_text("\n".join(findings) + "\n", encoding="utf-8")
    print(f"\nRaport: {report}  (żądań: {_n})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sonda celowana: powtórzone nip= i rozmiar ids.")
    parser.add_argument("--env", choices=sorted(BASE), default="test")
    parser.add_argument("--produkcja", action="store_true", help="zgoda na środowisko produkcyjne")
    parser.add_argument("--token", default="")
    ARGS = parser.parse_args()
    ARGS.token = ARGS.token or load_token()
    ARGS.base = BASE[ARGS.env]
    # Wspólna historia żądań: sonda i pobranie przestają być dla siebie niewidzialne.
    with shared_gate(ARGS.env, ARGS.token) as GATE:
        main()
