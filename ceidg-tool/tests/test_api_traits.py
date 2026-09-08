"""Fixtures muszą zgadzać się ze zmierzonymi własnościami API (`tests/fixtures/api_traits.yaml`).

Po co osobny plik z twierdzeniami, skoro fixtures są kopią prawdziwych odpowiedzi: bo kopia
przechodzi przez `scripts/anonymize_samples.py`, a anonimizator może własność skasować —
i skasował. Podnosił każdy identyfikator do wielkich liter, więc `/zmiana` w fixture wyglądał
jak `/firma`, cała suita offline zgadzała się co do świata, którego nie ma, a na produkcji
`aktualizuj` zapisywał każdą zmienioną firmę dwa razy i nie pokazywał żadnej (ADR-0013).

Twierdzenie zapisane osobno od próbki zamyka pętlę: fixture nie może zostać wygenerowany do
zgodności z samym sobą, bo musi zgodzić się z pomiarem. Audyt na dole konfrontuje ten pomiar
z surowymi próbkami, gdy są pod ręką.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ceidg_tool.recordid import GUID_WPISU, kanoniczny_id

FIXTURES = Path(__file__).parent / "fixtures"
PROBKI = Path(__file__).resolve().parents[1] / "probe_out" / "samples"
TRAITS = yaml.safe_load((FIXTURES / "api_traits.yaml").read_text(encoding="utf-8"))
KSZTALT_GUID = re.compile(
    r"^[0-9A-Za-z]{8}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{4}-[0-9A-Za-z]{12}$"
)

IDENTYFIKATORY: list[dict[str, Any]] = TRAITS["identyfikatory"]
ID_PARAMS = [pytest.param(t, id=str(t["endpoint"])) for t in IDENTYFIKATORY]


def _ids(body: Any, sciezka: list[str]) -> list[str]:
    """Identyfikatory spod ścieżki: `[klucz]` — lista napisów, `[klucz, pole]` — lista rekordów."""
    raw = body[sciezka[0]]
    if len(sciezka) == 1:
        return [str(v) for v in raw]
    return [str(r[sciezka[1]]) for r in raw]


def _wielkosc(value: str) -> str:
    litery = [c for c in value if c.isalpha()]
    if not litery:
        return "bez_liter"
    if all(c.isupper() for c in litery):
        return "upper"
    if all(c.islower() for c in litery):
        return "lower"
    return "mieszana"


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_fixture_zgadza_sie_ze_zmierzona_wlasnoscia(trait: dict[str, Any]) -> None:
    body = json.loads((FIXTURES / trait["fixture"]).read_text(encoding="utf-8"))["body"]
    ids = _ids(body, list(trait["sciezka"]))
    assert ids, f"fixture {trait['fixture']} nie niesie żadnego identyfikatora"
    for rid in ids:
        assert KSZTALT_GUID.fullmatch(rid), (
            f"{trait['endpoint']}: {rid!r} nie ma kształtu 8-4-4-4-12"
        )
        czy_hex = GUID_WPISU.fullmatch(rid) is not None
        assert czy_hex == (trait["ksztalt"] == "guid_hex"), (
            f"{trait['endpoint']}: {rid!r} — szesnastkowość niezgodna z pomiarem"
        )
    if trait["wielkosc_liter"] != "mieszana":
        zmierzone = {_wielkosc(rid) for rid in ids} - {"bez_liter"}
        assert zmierzone == {trait["wielkosc_liter"]}, (
            f"{trait['endpoint']}: fixture niesie pisownię {zmierzone}, "
            f"pomiar mówi {trait['wielkosc_liter']!r} — czy anonimizator jej nie ujednolicił?"
        )


def test_pomiar_opisuje_obie_pisownie() -> None:
    """Detektor spłaszczenia: gdyby wszystkie endpointy niosły tę samą pisownię, cały ten
    plik przestałby cokolwiek chronić, a defekt wróciłby niezauważony."""
    pisownie = {t["wielkosc_liter"] for t in IDENTYFIKATORY}
    assert {"upper", "lower"} <= pisownie
    assert {t["endpoint"] for t in IDENTYFIKATORY} == {"firmy", "firma", "zmiana", "raporty"}


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_kanonizacja_rusza_tylko_identyfikatory_wpisow(trait: dict[str, Any]) -> None:
    """GUID wpisu idzie do wielkich liter, identyfikator raportu przechodzi nietknięty."""
    body = json.loads((FIXTURES / trait["fixture"]).read_text(encoding="utf-8"))["body"]
    for rid in _ids(body, list(trait["sciezka"])):
        if trait["ksztalt"] == "guid_hex":
            assert kanoniczny_id(rid) == rid.upper()
        else:
            assert kanoniczny_id(rid) == rid


@pytest.mark.parametrize("trait", ID_PARAMS)
def test_audyt_pomiaru_na_surowych_probkach(trait: dict[str, Any]) -> None:
    """Konfrontacja twierdzenia z produkcją — pomijana uczciwie, gdy `probe_out/` nie ma.

    `probe_out/` jest poza repozytorium (niesie dane osobowe), więc w CI ten test się pomija.
    Lokalnie, gdzie próbki są, sprawdza to, czego fixture z definicji nie udowodni: że pomiar
    opisuje odpowiedź rejestru, a nie tylko naszą jej przeróbkę."""
    probka = PROBKI / str(trait["probka"])
    if not probka.exists():
        pytest.skip(f"brak surowej próbki {probka.name} — audyt wymaga probe_out/")
    body = json.loads(probka.read_text(encoding="utf-8"))["body"]
    ids = _ids(body, list(trait["sciezka"]))
    assert ids
    for rid in ids:
        assert (GUID_WPISU.fullmatch(rid) is not None) == (trait["ksztalt"] == "guid_hex")
    if trait["wielkosc_liter"] != "mieszana":
        assert {_wielkosc(rid) for rid in ids} - {"bez_liter"} == {trait["wielkosc_liter"]}
