"""Ten pod-projekt jest widoczny dla bramki repozytorium.

Bez tego testu piąty najemca mógłby żyć w drzewie miesiącami, mając wszystkie cztery kroki
jakości zielone lokalnie i nie biegnąc w CI ani razu. To ta sama klasa usterki, co bramka,
która przechodzi, bo część suity się nie zebrała.
"""

from __future__ import annotations

from pathlib import Path

import yaml

KORZEN_REPO = Path(__file__).resolve().parent.parent.parent
WORKFLOW = KORZEN_REPO / ".github" / "workflows" / "ci.yml"


def _wpis_krs_tool() -> dict[str, str]:
    dane = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for zadanie in dane["jobs"].values():
        wpisy = zadanie.get("strategy", {}).get("matrix", {}).get("include", [])
        for wpis in wpisy:
            if wpis.get("name") == "krs-tool":
                return dict(wpis)
    raise AssertionError("brak wpisu `krs-tool` w macierzy bramki repozytorium")


def test_krs_tool_ma_wpis_w_macierzy() -> None:
    wpis = _wpis_krs_tool()

    assert wpis["dir"] == "krs-tool"


def test_wpis_instaluje_narzedzia_bramki() -> None:
    """Bez `dev` `uv sync` postawiłby sam produkt i każdy krok padłby na braku polecenia."""
    assert "--extra dev" in _wpis_krs_tool()["sync_args"]


def test_wpis_wymienia_katalogi_z_nazwy() -> None:
    """Układ płaski: `.` wciągnęłoby do lintera wszystko, co leży w korzeniu pod-projektu."""
    sciezki = _wpis_krs_tool()["lint_paths"].split()

    assert sciezki == ["krs_tool", "tests"]
