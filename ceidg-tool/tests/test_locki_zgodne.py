"""Dwa pliki lock opisują JEDNO rozwiązanie zależności — bramka, nie obietnica.

Ten pod-projekt jest instalowany dwiema drogami: matryca korzenia bierze `uv.lock` (`uv sync`),
oś systemów bierze `requirements.lock` (`pip install -r`). Dopóki oś instalowała nieprzypięte
z zakresów w `pyproject.toml`, rozjazd nie miał gdzie się objawić; §B `UZUPELNIENIE_01.md`
wymaga jednak przypięcia („Zależności przypięte w pliku lock"), a przypięcie do DRUGIEGO
rozwiązania znaczy, że bramka na Windows sprawdza inny program niż bramka na ubuntu.

Rozjazd nie był hipotetyczny: przed wprowadzeniem tej sondy pliki różniły się co najmniej
`anyio` (4.15.0 wobec 4.15.1) i `ast-serialize` (0.9.0 wobec 0.11.1). Nikt tego nie zauważył,
bo nic nie czytało tego pierwszego.

`requirements.lock` powstaje WYŁĄCZNIE tak — ręczna edycja jest tym, co ta sonda ma złapać:

    uv export --frozen --no-hashes --all-extras --no-emit-project -o requirements.lock
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

KORZEN = Path(__file__).resolve().parents[1]
UV_LOCK = KORZEN / "uv.lock"
PIP_LOCK = KORZEN / "requirements.lock"

# `nazwa==wersja` z opcjonalnym markerem środowiska po średniku. Wiersze komentarza (`# via ...`)
# i puste odpadają same, bo nie pasują do początku wzorca.
WPIS = re.compile(r"^(?P<nazwa>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<wersja>[^\s;]+)")


def _znormalizuj(nazwa: str) -> str:
    """PEP 503: `mypy_extensions`, `Mypy-Extensions` i `mypy-extensions` to ten sam pakiet."""
    return re.sub(r"[-_.]+", "-", nazwa).lower()


def _wersje_z_uv_locka() -> dict[str, str]:
    dane = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return {_znormalizuj(p["name"]): p["version"] for p in dane["package"]}


def _wersje_z_pip_locka() -> dict[str, str]:
    wersje: dict[str, str] = {}
    for linia in PIP_LOCK.read_text(encoding="utf-8").splitlines():
        trafienie = WPIS.match(linia)
        if trafienie:
            wersje[_znormalizuj(trafienie.group("nazwa"))] = trafienie.group("wersja")
    return wersje


def test_requirements_lock_nie_jest_pusty_ani_nieczytany() -> None:
    """Kontrola sensu sondy: gdyby wzorzec przestał pasować, reszta przechodziłaby w ciszy."""
    wersje = _wersje_z_pip_locka()
    assert len(wersje) > 40, (
        f"`requirements.lock` dał {len(wersje)} pakietów — sonda straciła przedmiot"
    )


def test_oba_locki_pinuja_te_same_wersje() -> None:
    """Każdy pakiet z `requirements.lock` stoi w `uv.lock` na tej samej wersji."""
    uv = _wersje_z_uv_locka()
    pip = _wersje_z_pip_locka()

    brakujace = sorted(set(pip) - set(uv))
    assert not brakujace, (
        f"pakiety z `requirements.lock` nieobecne w `uv.lock`: {brakujace}. "
        "Locki przestały opisywać to samo rozwiązanie — przegeneruj `uv export` (patrz docstring)."
    )

    rozjazdy = {nazwa: (pip[nazwa], uv[nazwa]) for nazwa in pip if pip[nazwa] != uv[nazwa]}
    assert not rozjazdy, (
        f"różne wersje w dwóch lockach (requirements.lock, uv.lock): {rozjazdy}. "
        "Bramka na Windows sprawdzałaby inny program niż bramka na ubuntu."
    )


def test_pip_lock_pokrywa_caly_uv_lock_poza_samym_projektem() -> None:
    """Druga strona: `uv.lock` nie może nieść pakietu, którego oś systemów nie zainstaluje.

    Wyjątkiem jest sam pakiet — oś instaluje go osobno (`pip install -e . --no-deps`), więc
    eksport powstaje z `--no-emit-project` i tego wpisu w `requirements.lock` nie ma.
    """
    uv = _wersje_z_uv_locka()
    pip = _wersje_z_pip_locka()
    brakujace = sorted(set(uv) - set(pip) - {"ceidg-tool"})
    assert not brakujace, (
        f"pakiety z `uv.lock` nieobecne w `requirements.lock`: {brakujace}. "
        "Oś systemów instalowałaby uboższe środowisko niż matryca korzenia."
    )
