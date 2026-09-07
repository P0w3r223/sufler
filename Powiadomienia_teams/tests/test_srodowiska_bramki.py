"""Strażnik zgodności DWÓCH środowisk bramki: CI i etapu `test` w obrazie (ADR 0008).

Ten sam zestaw testów biegnie w dwóch miejscach. Do 2026-09-07 biegał w nich z RÓŻNYMI
zależnościami: CI robił `uv sync` bez extras, obraz `uv sync --extra agent`. Rozjazd nie miał jak
wyjść, dopóki jakiś test nie tknąłby `anthropic` — a wtedy wyszedłby w najgorszym możliwym
miejscu, czyli przy budowaniu obrazu na serwerze.

Reguła iteruje po WIERSZACH `uv sync` w `Dockerfile`, czyli po rzeczach chronionych: nowy etap
z własną synchronizacją wpada pod nią bez dopisywania czegokolwiek tutaj.

Test wymaga drzewa repozytorium (`Dockerfile` jest w kontekście obrazu, ale `.github/` już nie),
więc w obrazie POMIJA SIĘ — jawnie, z powodem widocznym dzięki `-rs` w `addopts`. To jest
dokładnie ten podział, który zapisuje ADR 0008: weryfikacja prozy repozytorium należy do CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PODPROJEKT = Path(__file__).resolve().parent.parent
_KORZEN_REPO = _PODPROJEKT.parent
_DOCKERFILE = _PODPROJEKT / "Dockerfile"
_SCRIPTS = _PODPROJEKT / "scripts"
_CI = _KORZEN_REPO / ".github" / "workflows" / "ci.yml"

#: Wpis macierzy CI, którego dotyczy ta reguła.
_WPIS_CI = "Powiadomienia_teams"

_EXTRA = re.compile(r"--extra\s+([A-Za-z0-9_.-]+)")


def _wiersze_uv_sync() -> list[str]:
    """Wiersze `Dockerfile` uruchamiające `uv sync` — po jednym na etap, który synchronizuje."""
    return [
        wiersz.strip()
        for wiersz in _DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if "uv sync" in wiersz and not wiersz.lstrip().startswith("#")
    ]


def _extras_obrazu() -> set[str]:
    """Suma extras ze WSZYSTKICH `uv sync` obrazu — bo każdy etap buduje to samo środowisko."""
    return {nazwa for wiersz in _wiersze_uv_sync() for nazwa in _EXTRA.findall(wiersz)}


def _sync_args_ci() -> str | None:
    """`sync_args` wpisu macierzy o `dir: "Powiadomienia_teams"`; ``None``, gdy wpisu nie ma.

    Czytamy tekstem, nie parserem YAML: `pyyaml` wypadło z zależności przy czyszczeniu 0.2.0
    (jedyny konsument zniknął razem z modułem roster), a dokładanie go z powrotem dla jednego testu
    byłoby drożej niż dwadzieścia linii regexa.
    """
    tresc = _CI.read_text(encoding="utf-8")
    wpisy = re.split(r"\n\s*- name:", tresc)
    for wpis in wpisy:
        if f'dir: "{_WPIS_CI}"' not in wpis:
            continue
        if dopasowanie := re.search(r'sync_args:\s*"([^"]*)"', wpis):
            return dopasowanie.group(1)
        return ""
    return None


def _wymagaj(*sciezki: Path) -> None:
    """Pomiń test, gdy brakuje któregokolwiek z plików, które ma czytać — z ich nazwami w powodzie.

    `Dockerfile` jest w KONTEKŚCIE budowania, ale do obrazu NIE jest kopiowany, więc reguła o
    `--locked` też musi tędy przejść. Pierwsza wersja tego pliku pomijała się wyłącznie po braku
    `.github/`, a `Dockerfile` czytała bezwarunkowo — i wywróciła etap `test` przy pierwszym
    biegu nowego jobu (`FileNotFoundError: /app/Dockerfile`). Warunek pomijania ma dotyczyć
    KAŻDEGO pliku, którego test dotyka, a nie jednego wybranego jako reprezentant.
    """
    brakuje = [str(s.name) for s in sciezki if not s.exists()]
    if brakuje:
        pytest.skip(
            f"strażnik środowisk bramki — brak {', '.join(brakuje)} "
            f"(poza kontekstem repozytorium, np. w obrazie); biegnie w CI"
        )


def test_ci_i_obraz_instaluja_te_same_extras():
    """Rozjazd extras znaczy: ten sam zestaw testów, dwa różne środowiska, jeden fałszywy dowód."""
    _wymagaj(_CI, _DOCKERFILE)
    wiersze = _wiersze_uv_sync()
    assert wiersze, "brak wierszy `uv sync` w Dockerfile — reguła straciła przedmiot ochrony"

    args = _sync_args_ci()
    assert args is not None, (
        f'nie znalazłem w ci.yml wpisu macierzy o dir: "{_WPIS_CI}" — reguła mierzy pustkę'
    )
    assert _EXTRA.findall(args) or not _extras_obrazu(), (
        f"CI synchronizuje bez extras ({args!r}), a obraz z {sorted(_extras_obrazu())}"
    )
    assert set(_EXTRA.findall(args)) == _extras_obrazu(), (
        f"extras w CI {sorted(set(_EXTRA.findall(args)))} != extras w obrazie "
        f"{sorted(_extras_obrazu())} — ADR 0008: obraz jest wzorcem dla zależności"
    )


def test_kazdy_uv_sync_w_obrazie_jest_locked():
    """`--locked` w obrazie jest własnością bezpieczeństwa, nie wygodą.

    Bez niego `uv` wolno rozwiązać zależności inaczej niż `uv.lock`, więc obraz przestaje być
    odtwarzalny z commita — a to jedyna droga, jaką da się ustalić, co właściwie działa u klienta.
    """
    _wymagaj(_DOCKERFILE)
    wiersze = _wiersze_uv_sync()
    assert wiersze, "brak wierszy `uv sync` w Dockerfile — reguła straciła przedmiot ochrony"
    bez_locked = [w for w in wiersze if "--locked" not in w]
    assert bez_locked == [], bez_locked


def test_skrypt_obiecujacy_uruchomienie_na_serwerze_jest_w_obrazie():
    """Skrypt, którego docstring mówi „Na serwerze", MUSI być skopiowany do obrazu.

    Inaczej jedyną drogą uruchomienia go jest zamontowanie pliku z hosta — czyli wykonanie kodu
    SPOZA obrazu na sesji Graph bota, wbrew całej konstrukcji tego wdrożenia. Tak właśnie wyszło
    2026-09-07: `scripts/zbierz_historie.py` obiecywał wariant serwerowy, a `Dockerfile` kopiował
    wyłącznie `lista_czlonkow.py`.

    Reguła iteruje po SKRYPTACH, więc nowy diagnostyk z instrukcją serwerową wpada pod nią bez
    dopisywania czegokolwiek tutaj.
    """
    _wymagaj(_DOCKERFILE)
    if not _SCRIPTS.is_dir():
        pytest.skip("brak katalogu scripts/ — poza kontekstem repozytorium; biegnie w CI")
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    obiecujace = [
        p
        for p in sorted(_SCRIPTS.glob("*.py"))
        if "Na serwerze" in p.read_text(encoding="utf-8")[:4000]
    ]
    assert obiecujace, "żaden skrypt nie obiecuje uruchomienia na serwerze — reguła mierzy pustkę"
    brak = [p.name for p in obiecujace if f"scripts/{p.name}" not in dockerfile]
    assert brak == [], (
        f"skrypty obiecujące uruchomienie na serwerze, a nieskopiowane do obrazu: {brak}"
    )
