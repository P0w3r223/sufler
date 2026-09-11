"""Odsyłacze w dokumentach tego pod-projektu rozwiązują się do istniejących plików.

Bramka rdzenia (`tests/test_adr_numbering.py`) tego drzewa NIE sprawdza, i to jest decyzja,
nie przeoczenie: pisane prozą `docs/adr/0001_…` rozwiązywałaby od korzenia repozytorium, gdzie
numeracja ADR należy do WorkMate'a i mówi o czym innym. Wykluczenie bez zastępstwa zamieniłoby
jednak obserwatora na dobre chęci, więc ten sam warunek stoi tutaj — z korzeniem przesuniętym
o jeden katalog.

Ta zamiana ma powód zmierzony: `krs-tool/CLAUDE.md` wszedł na `Main` z odsyłaczem, który
zapalił bramkę rdzenia dopiero PO scaleniu, bo PR-y tego dnia nie dostały ani jednego przebiegu
CI. Dokumentów w tym pod-projekcie jest siedem i cytują się nawzajem gęściej niż kod.
"""

from __future__ import annotations

import re
from pathlib import Path

KORZEN = Path(__file__).resolve().parent.parent

# Dwie formy zapisu, bo obie występują i obie potrafią umrzeć. Link markdownowy czytelnik
# klika, więc rozwiązujemy go WZGLĘDEM DOKUMENTU; ścieżkę prozą czyta człowiek szukający
# pliku, więc rozwiązujemy ją od korzenia pod-projektu.
_LINK_MD = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
_SCIEZKA_PROZA = re.compile(r"(?<![\w/.\-])(docs/[a-z0-9_/\-]+\.md)")

# Katalogi bez dokumentacji, za to z tysiącami plików cudzych.
_POZA_BRAMKA = frozenset({".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "probki"})


def _dokumenty(korzen: Path) -> list[Path]:
    return [
        p
        for p in sorted(korzen.rglob("*.md"))
        if not (set(p.relative_to(korzen).parts) & _POZA_BRAMKA)
    ]


def _martwe_odsylacze(korzen: Path) -> list[str]:
    martwe: list[str] = []
    for dokument in _dokumenty(korzen):
        for numer, linia in enumerate(dokument.read_text(encoding="utf-8").splitlines(), 1):
            cele = [
                (cel, dokument.parent / cel)
                for cel in _LINK_MD.findall(linia)
                if not cel.startswith(("http://", "https://"))
            ]
            cele += [(cel, korzen / cel) for cel in _SCIEZKA_PROZA.findall(linia)]
            martwe += [
                f"{dokument.relative_to(korzen)}:{numer} → {cel}"
                for cel, sciezka in cele
                if not sciezka.is_file()
            ]
    return martwe


def test_dokumenty_nie_maja_martwych_odsylaczy() -> None:
    martwe = _martwe_odsylacze(KORZEN)

    assert not martwe, f"odsyłacze do nieistniejących dokumentów: {martwe}"


def test_bramka_widzi_zasiane_naruszenie(tmp_path: Path) -> None:
    """Reguła bez zademonstrowanej awarii jest nieodróżnialna od zbioru pustego."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "zywy.md").write_text("treść", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text(
        "Żywy [link](docs/zywy.md), martwa proza docs/adr/0002_nie_ma_mnie.md.\n",
        encoding="utf-8",
    )

    martwe = _martwe_odsylacze(tmp_path)

    assert martwe == ["CLAUDE.md:1 → docs/adr/0002_nie_ma_mnie.md"]


def test_bramka_ma_co_czytac() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki."""
    assert len(_dokumenty(KORZEN)) >= 7
