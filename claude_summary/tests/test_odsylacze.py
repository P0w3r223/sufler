"""Odsyłacze w dokumentach tego pod-projektu rozwiązują się do istniejących plików.

Bramka rdzenia (`tests/test_adr_numbering.py`) tego drzewa NIE sprawdza, i to jest decyzja,
nie przeoczenie: ten pod-projekt ma WŁASNĄ numerację ADR, więc pisane prozą `docs/adr/0003_…`
rozwiązywałoby się od korzenia repozytorium — w dokument Sufler'a o zupełnie czym innym.
Odsyłacz trafiający w niewłaściwy dokument jest gorszy niż martwy, bo nie widać, że jest zły.

Wykluczenie bez zastępstwa zamienia jednak obserwatora na dobre chęci, a tak ten pod-projekt
stał do dziś (#166). Ten plik jest tym zastępstwem, z korzeniem przesuniętym o jeden katalog.

Materiału jest mało — siedem dokumentów, dziesięć odsyłaczy — i właśnie dlatego sonda
`test_bramka_ma_co_czytac` jest tu ważniejsza niż gdzie indziej: bramka nad pustym zbiorem
przechodzi zawsze i wygląda identycznie jak bramka, która czegoś pilnuje.
"""

from __future__ import annotations

import re
from pathlib import Path

KORZEN = Path(__file__).resolve().parent.parent

_LINK_MD = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")

# Szerokie wyrażenie dla prozy (`docs/**`), jak w `krs-tool` i `ceidg-tool`, i tak samo
# ZMIERZONE: 2026-09-24 daje 10 odsyłaczy i zero martwych. Ten pod-projekt nie cytuje prozą
# dokumentów spoza własnego drzewa; jedyne cytowanie ADR-a o numerze kolidującym z rdzeniem
# (`CHANGELOG.md` → `docs/adr/0003-redaction-of-sensitive-content.md`) wskazuje na WŁASNY
# dokument tego pod-projektu i rozwiązuje się poprawnie właśnie dlatego, że korzeń jest tutaj,
# a nie w repozytorium. Pierwszy odsyłacz prozą do cudzego `docs/…` będzie musiał to
# wyrażenie zawęzić do `docs/adr/` — i wtedy ten komentarz jest miejscem, w którym widać,
# że różnica była wyborem, a nie zaniedbaniem.
_SCIEZKA_PROZA = re.compile(r"(?<![\w/.\-])(docs/[a-z0-9_/\-]+\.md)")

# Katalogi narzędziowe: tysiące cudzych plików, ani jednego dokumentu tego projektu.
_POZA_BRAMKA = frozenset({".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"})


def _dokumenty(korzen: Path) -> list[Path]:
    return [
        p
        for p in sorted(korzen.rglob("*.md"))
        if not (set(p.relative_to(korzen).parts) & _POZA_BRAMKA)
    ]


def _odsylacze(korzen: Path) -> list[tuple[str, Path]]:
    """`(opis, ścieżka_celu)` dla każdego odsyłacza w dokumentach objętych bramką.

    Link markdownowy rozwiązujemy WZGLĘDEM DOKUMENTU — tak czyta go czytelnik i tak sprawdzi go
    przeglądarka. Ścieżkę pisaną prozą — względem korzenia POD-PROJEKTU, bo tak ją czyta ktoś,
    kto szuka pliku, i dlatego właśnie bramka rdzenia nie umie jej tutaj rozwiązać.
    """
    znalezione: list[tuple[str, Path]] = []
    for dokument in _dokumenty(korzen):
        for numer, linia in enumerate(dokument.read_text(encoding="utf-8").splitlines(), 1):
            cele = [
                (cel, dokument.parent / cel)
                for cel in _LINK_MD.findall(linia)
                if not cel.startswith(("http://", "https://"))
            ]
            cele += [(cel, korzen / cel) for cel in _SCIEZKA_PROZA.findall(linia)]
            znalezione += [
                (f"{dokument.relative_to(korzen).as_posix()}:{numer} → {cel}", sciezka)
                for cel, sciezka in cele
            ]
    return znalezione


def _martwe_odsylacze(korzen: Path) -> list[str]:
    return [opis for opis, sciezka in _odsylacze(korzen) if not sciezka.is_file()]


def test_dokumenty_nie_maja_martwych_odsylaczy() -> None:
    martwe = _martwe_odsylacze(KORZEN)

    assert not martwe, f"odsyłacze do nieistniejących dokumentów: {martwe}"


def test_bramka_przechodzi_droge_przyszlej_zmiany(tmp_path: Path) -> None:
    """Sprawdzenie samej bramki — przez PRZEMIANOWANIE pliku, nie przez znane wyrażenia.

    Powód jest pożyczony z `krs-tool` razem z kształtem: tamta sonda w pierwszej redakcji
    zasiewała znany napis i przechodziła z ZABITĄ połową strażnika, bo żywy link markdownowy
    dopasowywał się również wyrażeniem od prozy. Droga przemianowania rozróżnia obie gałęzie.
    """
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0002-cel.md").write_text("# cel\n", encoding="utf-8")
    (tmp_path / "docs" / "adr" / "0001-cytujacy.md").write_text(
        "prozą: docs/adr/0002-cel.md\ngołą nazwą: [ADR 2](0002-cel.md)\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(
        "od korzenia: [ADR 2](docs/adr/0002-cel.md)\n", encoding="utf-8"
    )

    assert _martwe_odsylacze(tmp_path) == [], "przed przemianowaniem nic nie miało być martwe"

    (tmp_path / "docs" / "adr" / "0002-cel.md").rename(tmp_path / "docs" / "adr" / "0002-nowy.md")

    assert set(_martwe_odsylacze(tmp_path)) == {
        "README.md:1 → docs/adr/0002-cel.md",
        "docs/adr/0001-cytujacy.md:1 → docs/adr/0002-cel.md",
        "docs/adr/0001-cytujacy.md:2 → 0002-cel.md",
    }, "przemianowanie miało zapalić wszystkie trzy formy cytowania"


def test_bramka_ma_co_czytac() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki.

    Liczymy ODSYŁACZE, nie pliki: próg na plikach przechodzi po zmianie konwencji zapisu, czyli
    dokładnie wtedy, gdy bramka przestaje widzieć to, po co powstała. Zmierzone 2026-09-24:
    10 odsyłaczy w 7 dokumentach.
    """
    assert len(_odsylacze(KORZEN)) >= 9
