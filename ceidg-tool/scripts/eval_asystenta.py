#!/usr/bin/env python
"""Ewaluacja asystenta: niezmienniczość na literówkach, niedookreślenie, filtry niewykonalne.

**Zero żądań do CEIDG.** Asystent stoi przed `count`, więc ta ewaluacja kończy się na
`Criteria` — dokładnie tam, gdzie w kreatorze operator odpowiada „wróć do menu". Kosztuje
wyłącznie wywołania modelu.

Konstrukcja, i powody, dla których jest właśnie taka:

* **15 ziaren × 5 przekształceń zamiast 75 ręcznych wzorców.** Literówka, małe litery i brak
  ogonków nie zmieniają znaczenia, więc nie potrzebują własnej odpowiedzi wzorcowej — asercją
  jest zgodność z wynikiem ziarna. To jest relacja metamorficzna, nie wartość.
* **Trzy poziomy tolerancji, nie jeden.** Grupa A zmierzyła 2026-09-07, że to samo zdanie daje
  4 kody PKD, a przy powtórzeniu 15 (po poprawce promptu: 2–4). Porównanie „równe albo nie" na
  całym `Criteria` produkowałoby więc fałszywe porażki — to znany problem metryki Exact Match
  w ewaluacji text-to-SQL. Pola stabilne porównujemy dokładnie, zbiór PKD przez zawieranie.
* **pass@1 obok pass^3.** Operator nie powtarza zapytania trzy razy, żeby sprawdzić, czy program
  jest tego samego zdania; liczy się `pass^3`, a rozstęp między nimi jest miarą niestabilności.

    PYTHONUTF8=1 python scripts/eval_asystenta.py --proba      # 6 zapytań, rozgrzewka
    PYTHONUTF8=1 python scripts/eval_asystenta.py              # pełny przebieg
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ceidg_tool.assistant import AssistantResult  # noqa: E402
from ceidg_tool.assistant.caller import AnthropicCaller  # noqa: E402
from ceidg_tool.config import load_settings  # noqa: E402
from ceidg_tool.errors import CeidgError  # noqa: E402
from ceidg_tool.pkddict import load_pkd  # noqa: E402

ZESTAW = Path(__file__).resolve().parent.parent / "tests" / "eval" / "zapytania.yaml"
DZISIAJ = date(2026, 9, 9)
POWTORZENIA_ZIARNA = 3
MAKS_KODOW_PKD = 6
"""Ile kodów PKD wolno oddać, zanim uznamy odpowiedź za rozlaną.

Grupa A zmierzyła rozrzut 2–4 po poprawce promptu i blow-up do 15 przed nią. Sześć jest więc
progiem nad zmierzonym maksimum, a wyraźnie pod awarią, którą prompt naprawiał."""

# Sąsiedztwo klawiatury (QWERTY) — literówka ma być prawdopodobna, nie losowa.
SASIEDZI = {
    "a": "sq",
    "b": "vn",
    "c": "xv",
    "d": "sf",
    "e": "wr",
    "f": "dg",
    "g": "fh",
    "h": "gj",
    "i": "uo",
    "j": "hk",
    "k": "jl",
    "l": "k",
    "m": "n",
    "n": "bm",
    "o": "ip",
    "p": "o",
    "r": "et",
    "s": "ad",
    "t": "ry",
    "u": "yi",
    "w": "qe",
    "y": "tu",
    "z": "x",
}


def bez_ogonkow(tekst: str) -> str:
    """`Gnieźnie` → `Gnieznie`. Tak pisze każdy, kto nie przełączył klawiatury."""
    rozlozony = unicodedata.normalize("NFD", tekst.replace("ł", "l").replace("Ł", "L"))
    return "".join(z for z in rozlozony if unicodedata.category(z) != "Mn")


def z_literowkami(tekst: str, ziarno: int) -> str:
    """Jedna literówka na osiem znaków, deterministycznie — przebieg ma być powtarzalny."""
    rng = random.Random(ziarno)
    znaki = list(tekst)
    pozycje = [i for i, z in enumerate(znaki) if z.lower() in SASIEDZI]
    for i in rng.sample(pozycje, k=max(1, len(pozycje) // 8)):
        znaki[i] = rng.choice(SASIEDZI[znaki[i].lower()])
    return "".join(znaki)


def przestaw(tekst: str) -> str:
    """Inny szyk i interpunkcja: `X w Y` → `w Y — X`."""
    if " w " in tekst:
        glowa, _, ogon = tekst.partition(" w ")
        return f"w {ogon} — {glowa}"
    return f"{tekst}?"


PRZEKSZTALCENIA: tuple[tuple[str, Any], ...] = (
    ("P1_male_litery", lambda t, i: t.lower()),
    ("P2_bez_ogonkow", lambda t, i: bez_ogonkow(t)),
    ("P3_literowki", lambda t, i: z_literowkami(t, i)),
    ("P4_uprzejmosc", lambda t, i: f"dzień dobry, poproszę {t[0].lower() + t[1:]}"),
    ("P5_bez_ogonkow_i_literowki", lambda t, i: z_literowkami(bez_ogonkow(t).lower(), i)),
)


@dataclass
class Przypadek:
    id: str
    klasa: str
    zdanie: str
    ziarno_id: str = ""
    oczekiwane: dict[str, Any] = field(default_factory=dict)


@dataclass
class Wynik:
    przypadek: Przypadek
    proba: int
    ok: bool | None = None
    powody: list[str] = field(default_factory=list)
    kryteria: dict[str, Any] = field(default_factory=dict)
    kody_pkd: tuple[str, ...] = ()
    ograniczenia: tuple[str, ...] = ()
    puste: bool = False
    blad: str = ""
    sekundy: float = 0.0


def kody_dla_nazwy(wzorzec: str, slownik: dict[str, str]) -> tuple[str, ...]:
    trafienia = tuple(k for k, nazwa in slownik.items() if wzorzec.lower() in nazwa.lower())
    if not trafienia:
        raise SystemExit(f"wzorzec PKD {wzorzec!r} nie trafia w żaden kod — popraw zestaw")
    if len(trafienia) > 4:
        raise SystemExit(f"wzorzec PKD {wzorzec!r} trafia w {len(trafienia)} kodów — za szeroki")
    return trafienia


def zbuduj_przypadki(dane: dict[str, Any], slownik: dict[str, str]) -> list[Przypadek]:
    przypadki: list[Przypadek] = []
    for i, z in enumerate(dane["ziarna"]):
        oczek = {k: v for k, v in z.items() if k not in ("id", "zdanie", "pkd_nazwa")}
        oczek["pkd_dozwolone"] = kody_dla_nazwy(z["pkd_nazwa"], slownik)
        przypadki.append(Przypadek(z["id"], "I_ziarno", z["zdanie"], z["id"], oczek))
        for nazwa, f in PRZEKSZTALCENIA:
            przypadki.append(
                Przypadek(f"{z['id']}/{nazwa}", "I_przeksztalcenie", f(z["zdanie"], i), z["id"])
            )
    przypadki += [
        Przypadek(n["id"], "II_niedookreslone", n["zdanie"]) for n in dane["niedookreslone"]
    ]
    przypadki += [
        Przypadek(x["id"], "III_niewykonalne", x["zdanie"], "", {"ograniczenie": x["ograniczenie"]})
        for x in dane["niewykonalne"]
    ]
    return przypadki


def jako_slownik(wynik: AssistantResult) -> dict[str, Any]:
    k = wynik.kryteria
    return {
        "miasto": list(k.miasto),
        "wojewodztwo": list(k.wojewodztwo),
        "status": list(k.status),
        "pkd": list(k.pkd),
        "szczegoly": k.szczegoly,
        "data_od": str(k.data_od) if k.data_od else None,
        "data_do": str(k.data_do) if k.data_do else None,
        "nazwa": list(k.nazwa),
    }


STABILNE = ("miasto", "wojewodztwo", "status", "data_od", "data_do", "szczegoly")


def ocen_ziarno(w: Wynik) -> None:
    """Pola stabilne — równość dokładna; PKD — zawieranie i próg liczności."""
    ocz, kryt, powody = w.przypadek.oczekiwane, w.kryteria, []
    for pole in STABILNE:
        if pole not in ocz:
            continue
        chciane, dane = ocz[pole], kryt.get(pole)
        if isinstance(chciane, list) and isinstance(dane, list):
            if sorted(chciane) != sorted(dane):
                powody.append(f"{pole}: {dane} ≠ {chciane}")
        elif str(chciane) != str(dane):
            powody.append(f"{pole}: {dane!r} ≠ {chciane!r}")
    dozwolone = set(ocz.get("pkd_dozwolone", ()))
    oddane = set(kryt.get("pkd") or ())
    if dozwolone and not (dozwolone & oddane):
        powody.append(f"PKD: {sorted(oddane)} nie zawiera żadnego z {sorted(dozwolone)}")
    if len(oddane) > MAKS_KODOW_PKD:
        powody.append(f"PKD: {len(oddane)} kodów, próg {MAKS_KODOW_PKD}")
    w.powody, w.ok = powody, not powody


def ocen_niedookreslone(w: Wynik) -> None:
    w.ok = w.puste
    if not w.ok:
        w.powody = [f"kryteria niepuste: {[f'{k}={v}' for k, v in w.kryteria.items() if v]}"]


def ocen_niewykonalne(w: Wynik) -> None:
    chciane = w.przypadek.oczekiwane["ograniczenie"]
    w.ok = chciane in w.ograniczenia
    if not w.ok:
        w.powody = [f"brak ograniczenia {chciane}; oddane: {list(w.ograniczenia)}"]


def ocen_przeksztalcenia(wyniki: list[Wynik]) -> None:
    """Niezmienniczość: przekształcenie nie ma prawa zmienić odpowiedzi ziarna.

    Porównujemy z pierwszą próbą ziarna, polami stabilnymi i przecięciem zbiorów PKD —
    tą samą tolerancją co ocena ziarna, bo inaczej mierzylibyśmy dwie różne rzeczy.
    """
    ziarno_odp = {
        w.przypadek.ziarno_id: w
        for w in wyniki
        if w.przypadek.klasa == "I_ziarno" and w.proba == 1 and not w.blad
    }
    for w in wyniki:
        if w.przypadek.klasa != "I_przeksztalcenie" or w.blad:
            continue
        z = ziarno_odp.get(w.przypadek.ziarno_id)
        if z is None:
            continue
        powody = [
            f"{pole}: {w.kryteria.get(pole)} ≠ ziarno {z.kryteria.get(pole)}"
            for pole in STABILNE
            if w.kryteria.get(pole) != z.kryteria.get(pole)
        ]
        if z.kody_pkd and not (set(w.kody_pkd) & set(z.kody_pkd)):
            powody.append(f"PKD: {list(w.kody_pkd)} ∩ ziarno {list(z.kody_pkd)} = ∅")
        w.powody, w.ok = powody, not powody


def uruchom(przypadek: Przypadek, proba: int, caller: AnthropicCaller) -> Wynik:
    w = Wynik(przypadek, proba)
    start = time.monotonic()
    try:
        wynik = caller.interpret(przypadek.zdanie, dzisiaj=DZISIAJ)
        w.kryteria = jako_slownik(wynik)
        w.kody_pkd = tuple(k for k, _ in wynik.kody_pkd)
        w.ograniczenia = tuple(str(o) for o in wynik.ograniczenia)
        w.puste = wynik.kryteria.is_empty()
    except CeidgError as exc:
        w.blad = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001 — przebieg pomiarowy ma przeżyć jeden zły przypadek
        w.blad = f"{type(exc).__name__}: {exc}"
    w.sekundy = round(time.monotonic() - start, 1)
    return w


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--proba", action="store_true", help="6 zapytań na rozgrzewkę")
    ap.add_argument("--watki", type=int, default=3)
    ap.add_argument("--tylko", nargs="*", help="tylko te ziarna, po id")
    ap.add_argument("--out", type=Path, default=Path("eval_wynik.json"))
    args = ap.parse_args()

    slownik = load_pkd()
    dane = yaml.safe_load(ZESTAW.read_text(encoding="utf-8"))
    przypadki = zbuduj_przypadki(dane, slownik)

    zadania: list[tuple[Przypadek, int]] = []
    for p in przypadki:
        powtorzen = POWTORZENIA_ZIARNA if p.klasa == "I_ziarno" else 1
        zadania += [(p, i + 1) for i in range(powtorzen)]
    if args.tylko:
        chciane = set(args.tylko)
        zadania = [z for z in zadania if z[0].id.split("/")[0] in chciane]
    if args.proba:
        zadania = [z for z in zadania if z[0].id in ("S01", "N01", "X01")][:6]

    settings = load_settings(environment="test", prod_consent=False)
    if not settings.anthropic_key:
        raise SystemExit("brak klucza asystenta — `ceidg-tool sprawdz-token`")

    print(f"### zapytań unikalnych: {len(przypadki)}   wywołań modelu: {len(zadania)}")
    print("### żądań do CEIDG: 0 (asystent stoi przed `count`)")
    print(f"### wątków: {args.watki}   dzisiaj={DZISIAJ}", flush=True)

    lokalne = {}

    def caller_watku() -> AnthropicCaller:
        import threading

        klucz = threading.get_ident()
        if klucz not in lokalne:
            lokalne[klucz] = AnthropicCaller(api_key=settings.anthropic_key, slownik=slownik)
        return lokalne[klucz]

    start = time.monotonic()
    wyniki: list[Wynik] = []
    with ThreadPoolExecutor(max_workers=args.watki) as pula:
        for w in pula.map(lambda z: uruchom(z[0], z[1], caller_watku()), zadania):
            if not w.blad:
                {
                    "I_ziarno": ocen_ziarno,
                    "II_niedookreslone": ocen_niedookreslone,
                    "III_niewykonalne": ocen_niewykonalne,
                }.get(w.przypadek.klasa, lambda _: None)(w)
            wyniki.append(w)
            znak = "." if w.ok else ("!" if w.ok is False else ("E" if w.blad else "?"))
            print(znak, end="", flush=True)

    # Przekształcenia ocenia się dopiero teraz, bo ich wzorcem jest **odpowiedź ziarna**,
    # a ta powstaje w tym samym przebiegu. Zostawienie ich z `ok=None` raz już wyprodukowało
    # nieprawdę: tabela zliczyła „nieocenione" jako porażki i pokazała 0 % tam, gdzie było 80 %.
    ocen_przeksztalcenia(wyniki)
    print(f"\n### czas: {round(time.monotonic() - start)} s")

    args.out.write_text(
        json.dumps(
            [
                {
                    "id": w.przypadek.id,
                    "klasa": w.przypadek.klasa,
                    "ziarno": w.przypadek.ziarno_id,
                    "zdanie": w.przypadek.zdanie,
                    "proba": w.proba,
                    "ok": w.ok,
                    "powody": w.powody,
                    "kryteria": w.kryteria,
                    "kody_pkd": list(w.kody_pkd),
                    "ograniczenia": list(w.ograniczenia),
                    "puste": w.puste,
                    "blad": w.blad,
                    "sekundy": w.sekundy,
                }
                for w in wyniki
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"### surowe wyniki: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
