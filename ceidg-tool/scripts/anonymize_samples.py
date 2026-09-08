#!/usr/bin/env python3
"""Buduje zanonimizowane fixtures testowe z surowych próbek sondy.

Próbki z produkcji zawierają dane osobowe JDG, więc do `tests/fixtures/` trafia kopia,
w której identyfikatory, nazwiska, NIP/REGON, adresy i kontakty są podmienione na
wartości syntetyczne (deterministycznie, ziarno 42). Struktura JSON, statusy, daty,
kody PKD, `count`, `links` i nagłówki limitów zostają — to one są przedmiotem testów.

Użycie:
    python scripts/anonymize_samples.py            # probe_out/samples -> tests/fixtures
    python scripts/anonymize_samples.py --src X --dst Y
"""

from __future__ import annotations

import argparse
import json
import random
import re
import uuid
from pathlib import Path
from typing import Any

FIXTURES: dict[str, str] = {
    "01_200_firmy.json": "firmy_limit1.json",
    "02_200_firmy.json": "firmy_page0_limit5.json",
    "03_200_firmy.json": "firmy_page1_limit5.json",
    "04_200_firmy.json": "firmy_page2_limit5.json",
    "05_200_firmy.json": "firmy_limit25.json",
    "06_400_firmy.json": "firmy_400_limit50.json",
    "11_204_firmy.json": "firmy_204_nazwa.json",
    "12_200_firmy.json": "firmy_wojewodztwo.json",
    "24_200_firma.json": "firma_by_ids.json",
    "25_200_raporty.json": "raporty.json",
    "26_200_zmiana.json": "zmiana.json",
}
PROD_HOST = "https://dane.biznes.gov.pl"
TEST_HOST = "https://test-dane.biznes.gov.pl"
DROP_HEADERS = {"Set-Cookie", "X-Gravitee-Transaction-Id", "X-Correlation-ID", "Date", "Expires"}
IMIONA = ["Testomir", "Próbomira", "Fikcjan", "Wzorcysława", "Syntetyk", "Przykładzia"]
NAZWISKA = ["Testowa", "Próbny", "Fikcyjna", "Przykładowy", "Syntetyczna", "Wzorcowy"]
ULICE = ["Testowa", "Fikcyjna", "Przykładowa", "Syntetyczna", "Wzorcowa"]
NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
REGON_WEIGHTS = (8, 9, 2, 3, 4, 5, 6, 7)
GUID_RE = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")
# Litery spoza zestawu szesnastkowego — identyfikator `/raporty` ma kształt GUID-a, ale nim
# nie jest, i tylko taki znak odróżnia jedno od drugiego (patrz `_RAPORT_GROUPS`).
NIE_HEX = "ghijklmnopqrstuvwxyzGHIJKLMNOPQRSTUVWXYZ"
_RAPORT_GROUPS = (8, 4, 4, 4, 12)


def case_shape(value: str) -> str | None:
    """`upper`, `lower` albo `None`, gdy z samego napisu nie da się tego rozstrzygnąć.

    Wielkość liter identyfikatora jest własnością API, nie ozdobą: `/zmiana` zwraca je
    małymi, `/firmy` i `/firma` wielkimi. Anonimizacja, która to ujednolica, kasuje
    dokładnie tę różnicę, na której program się wywrócił (ADR-0013)."""
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return None
    if all(c.isupper() for c in letters):
        return "upper"
    if all(c.islower() for c in letters):
        return "lower"
    return None


class Anonymizer:
    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        self.ids: dict[str, str] = {}
        self.people: dict[str, dict[str, str]] = {}

    # --- generatory ------------------------------------------------------------
    def guid(self, real: str) -> str:
        """Podstawia identyfikator, zachowując **kształt zapisu** wystąpienia.

        Klucz mapowania jest wielkimi literami, bo jeden prawdziwy wpis ma mieć jeden
        zamiennik niezależnie od tego, który endpoint go zwrócił. Zwracana wartość idzie
        natomiast w pisowni oryginału — inaczej fixture twierdzi o API coś, czego API nie
        robi, a suita offline nie ma jak zobaczyć różnicy między `/zmiana` a `/firma`."""
        key = real.upper()
        if key not in self.ids:
            self.ids[key] = str(uuid.UUID(int=self.rng.getrandbits(128), version=4)).upper()
        fake = self.ids[key]
        return fake.lower() if case_shape(real) == "lower" else fake

    def raport_id(self) -> str:
        """Identyfikator `/raporty`: kształt 8-4-4-4-12, ale **nie** szesnastkowy.

        Produkcja zwraca tu `F3Aj3APe-9rud-A9rR-IId9-jMe3FedeR3e9` — z wyglądu GUID,
        w treści nie, i istotny co do wielkości liter, bo wchodzi do adresu pobrania.
        Podmiana na `RAPORT-000` gubiła ten kształt, więc żaden test nie mógł pokazać,
        że kanonizacja identyfikatorów wpisów zostawia raporty w spokoju."""
        groups = []
        for size in _RAPORT_GROUPS:
            body = "".join(self.rng.choice(NIE_HEX + "0123456789") for _ in range(size - 1))
            groups.append(self.rng.choice(NIE_HEX) + body)
        return "-".join(groups)

    def nip(self) -> str:
        while True:
            digits = [self.rng.randint(1, 9)] + [self.rng.randint(0, 9) for _ in range(8)]
            control = sum(w * d for w, d in zip(NIP_WEIGHTS, digits, strict=True)) % 11
            if control != 10:
                return "".join(map(str, digits + [control]))

    def regon(self) -> str:
        digits = [self.rng.randint(0, 9) for _ in range(8)]
        control = sum(w * d for w, d in zip(REGON_WEIGHTS, digits, strict=True)) % 11
        return "".join(map(str, digits + [0 if control == 10 else control]))

    def person(self, real_key: str) -> dict[str, str]:
        if real_key not in self.people:
            self.people[real_key] = {
                "imie": self.rng.choice(IMIONA),
                "nazwisko": self.rng.choice(NAZWISKA),
                "nip": self.nip(),
                "regon": self.regon(),
            }
        return self.people[real_key]

    # --- rekordy ---------------------------------------------------------------
    def address(self, adres: Any) -> Any:
        if not isinstance(adres, dict) or not adres:
            return adres
        out = dict(adres)
        if "ulica" in out:
            out["ulica"] = f"ul. {self.rng.choice(ULICE)}"
        if "budynek" in out:
            out["budynek"] = str(self.rng.randint(1, 99))
        if "lokal" in out:
            out["lokal"] = str(self.rng.randint(1, 20))
        if "kod" in out and isinstance(out["kod"], str) and len(out["kod"]) == 6:
            out["kod"] = f"{out['kod'][:2]}-{self.rng.randint(100, 999)}"
        if "ulic" in out:
            out["ulic"] = f"{self.rng.randint(10000, 99999)}"
        if out.get("adresat"):
            out["adresat"] = f"{self.rng.choice(IMIONA)} {self.rng.choice(NAZWISKA)}"
        if out.get("skrytkaPocztowa"):
            out["skrytkaPocztowa"] = str(self.rng.randint(1, 999))
        if out.get("opisNietypowegoMiejsca"):
            out["opisNietypowegoMiejsca"] = "opis testowy"
        return out

    def firm(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = dict(rec)
        owner = rec.get("wlasciciel") or {}
        key = str(owner.get("nip") or owner.get("regon") or rec.get("id"))
        fake = self.person(key)
        if "id" in out:
            out["id"] = self.guid(str(rec["id"]))
        if "wlasciciel" in out:
            out["wlasciciel"] = {k: fake.get(k, v) for k, v in owner.items()}
        if "nazwa" in out:
            out["nazwa"] = f"{fake['imie']} {fake['nazwisko']} FIRMA TESTOWA"
        for field in ("adresDzialalnosci", "adresKorespondencyjny"):
            if field in out:
                out[field] = self.address(out[field])
        if "adresyDzialalnosciDodatkowe" in out and isinstance(
            out["adresyDzialalnosciDodatkowe"], list
        ):
            out["adresyDzialalnosciDodatkowe"] = [
                self.address(a) for a in out["adresyDzialalnosciDodatkowe"]
            ]
        if out.get("email"):
            out["email"] = f"{fake['imie'].lower()}.{fake['nazwisko'].lower()}@example.test"
        if out.get("telefon"):
            out["telefon"] = f"+48 600 {self.rng.randint(100, 999)} {self.rng.randint(100, 999)}"
        if out.get("www"):
            out["www"] = "https://example.test"
        if out.get("adresDoreczenElektronicznych"):
            out["adresDoreczenElektronicznych"] = "AE:PL-00000-00000-TEST0-00"
        if "link" in out and isinstance(out["link"], str):
            out["link"] = self.url(out["link"])
        if isinstance(out.get("spolki"), list):
            out["spolki"] = [
                {**s, "nip": self.nip(), "regon": self.regon()} if isinstance(s, dict) else s
                for s in out["spolki"]
            ]
        return out

    def url(self, value: str) -> str:
        value = value.replace(PROD_HOST, TEST_HOST)
        return GUID_RE.sub(lambda m: self.guid(m.group(0)), value)

    def body(self, body: Any) -> Any:
        if not isinstance(body, dict):
            return body
        out = dict(body)
        for key in ("firmy", "firma"):
            if isinstance(out.get(key), list):
                out[key] = [self.firm(r) if isinstance(r, dict) else r for r in out[key]]
        if isinstance(out.get("links"), dict):
            out["links"] = {
                k: self.url(v) if isinstance(v, str) else v for k, v in out["links"].items()
            }
        if isinstance(out.get("identyfikatoryWpisow"), list):
            ids = out["identyfikatoryWpisow"][:10]
            out["identyfikatoryWpisow"] = [self.guid(i) for i in ids]
        if isinstance(out.get("raporty"), list):
            fake_ids = [self.raport_id() for _ in out["raporty"][:12]]
            out["raporty"] = [
                {
                    **r,
                    "id": rid,
                    "raport": self.url(r["raport"]).rsplit("/", 1)[0] + f"/{rid}",
                }
                for r, rid in zip(out["raporty"][:12], fake_ids, strict=True)
            ]
        return out

    def sample(self, sample: dict[str, Any]) -> dict[str, Any]:
        headers = {k: v for k, v in sample.get("headers", {}).items() if k not in DROP_HEADERS}
        return {
            "url": self.url(sample["url"]),
            "status": sample["status"],
            "headers": headers,
            "elapsed_s": sample.get("elapsed_s"),
            "body": self.body(sample.get("body")),
        }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--src", default="probe_out/samples", type=Path)
    ap.add_argument("--dst", default="tests/fixtures", type=Path)
    args = ap.parse_args()
    anon = Anonymizer()
    args.dst.mkdir(parents=True, exist_ok=True)
    written = 0
    for src_name, dst_name in FIXTURES.items():
        src = args.src / src_name
        if not src.exists():
            print(f"pomijam (brak): {src}")
            continue
        data = json.loads(src.read_text(encoding="utf-8"))
        (args.dst / dst_name).write_text(
            json.dumps(anon.sample(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written += 1
        print(f"{src_name} -> {dst_name}")
    print(f"zapisano {written} fixtures do {args.dst}")


if __name__ == "__main__":
    main()
