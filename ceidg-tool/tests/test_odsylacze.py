"""Odsyłacze w dokumentach tego pod-projektu rozwiązują się do istniejących plików.

Bramka rdzenia (`tests/test_adr_numbering.py`) tego drzewa NIE sprawdza, i to jest decyzja,
nie przeoczenie: `ceidg-tool` cytuje własne `docs/adr/0003_rate_limiter.md`, a tamta bramka
rozwiązywałaby tę ścieżkę od korzenia repozytorium — czyli w miejsce, gdzie leży ADR 0003
WorkMate'a o zupełnie czym innym. Odsyłacz trafiający w NIEWŁAŚCIWY dokument jest gorszy niż
martwy, bo nie widać, że jest zły.

Wykluczenie bez zastępstwa zamienia jednak obserwatora na dobre chęci — i dokładnie tak ten
pod-projekt stał od dołączenia do `Main` (2026-09-10, ADR 0074) do dziś: **52 dokumenty**,
w tym `docs/adr/` cytujące się nawzajem gęściej niż kod, bez ani jednego strażnika (#166).
Ten plik jest tym zastępstwem, z korzeniem przesuniętym o jeden katalog.
"""

from __future__ import annotations

import re
from pathlib import Path

KORZEN = Path(__file__).resolve().parent.parent

_LINK_MD = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")

# ŚWIADOME poszerzenie wobec rdzenia, który zawęża się do `docs/adr/NNNN…`, i poszerzenie
# ZMIERZONE, nie ostrożnościowe. Zgłoszenie #166 zakładało, że szeroka forma tutaj się zapali,
# bo ten pod-projekt cytuje dokumenty spoza własnego drzewa. Pomiar 2026-09-24 mówi co innego:
# 230 odsyłaczy, ZERO martwych. Powód jest mechaniczny — jedyne cytowanie spoza drzewa
# (`README.md` → ADR 0074 rdzenia) jest zapisane WZGLĘDNIE, jako `../docs/adr/0074-…`, a
# lookbehind niżej odrzuca ścieżkę poprzedzoną ukośnikiem. Poszerzenie nie widzi więc formy,
# która jako jedyna mogłaby je wysadzić.
#
# Warunek, pod którym to przestaje obowiązywać: pierwsze cytowanie cudzego `docs/…` pisane
# PROZĄ (bez `../`) rozwiąże się od korzenia tego pod-projektu i zapali bramkę fałszywie.
# Wtedy — i dopiero wtedy — to wyrażenie trzeba zawęzić do `docs/adr/`, tak jak w rdzeniu.
_SCIEZKA_PROZA = re.compile(r"(?<![\w/.\-])(docs/[a-z0-9_/\-]+\.md)")

# Katalogi poza bramką, każdy z powodem — wykluczenie bez powodu zgnije tak samo jak flaga.
# Katalogi narzędziowe (`.venv`, cache'e, `__pycache__`, `*.egg-info`) niosą tysiące cudzych
# plików i ani jednego dokumentu tego projektu.
#
# `sessions` z własnym powodem, przeniesionym z rdzenia: dzienniki w `.claude/sessions/` są
# zapisem TEGO, CO NAPISANO danego dnia. Poprawianie w nich odsyłacza jest przepisywaniem
# dziennika, a nie naprawą dokumentu — więc bramka, która by tego żądała, uczyłaby złego ruchu.
_POZA_BRAMKA = frozenset(
    {
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "ceidg_tool.egg-info",
        "sessions",
    }
)


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

    Opis idzie przez `as_posix()`, bo inaczej niesie separator systemu: ten pod-projekt ma
    bramkę biegnącą także na Windows (`.github/workflows/ceidg-tool.yml`), a komunikat różny
    między systemami nie nadaje się ani do asercji, ani do wklejenia w zgłoszenie.
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

    Kolejność jest tu pożyczona z `krs-tool`, razem z powodem: tamta sonda w pierwszej redakcji
    zasiewała jeden martwy napis prozą i jeden ŻYWY link markdownowy. Żywy link dopasowuje się
    jednak także wyrażeniem od prozy, więc asercję spełniała sama gałąź prozą — zabicie
    `_LINK_MD` zostawiało wszystkie testy zielone. Połowa strażnika nie miała obserwatora.

    Ta wersja idzie drogą zmiany, która dług TWORZY, i żąda zapalenia się każdej formy
    cytowania, niezależnie od tego, jak zapisanej i z którego katalogu.
    """
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "design").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0002_cel.md").write_text("# cel\n", encoding="utf-8")
    (tmp_path / "docs" / "adr" / "0001_cytujacy.md").write_text(
        "prozą: docs/adr/0002_cel.md\ngołą nazwą: [ADR 2](0002_cel.md)\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "design" / "rdzen.md").write_text(
        "względnie: [ADR 2](../adr/0002_cel.md)\n", encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text(
        "od korzenia: [ADR 2](docs/adr/0002_cel.md)\n", encoding="utf-8"
    )

    assert _martwe_odsylacze(tmp_path) == [], "przed przemianowaniem nic nie miało być martwe"

    (tmp_path / "docs" / "adr" / "0002_cel.md").rename(tmp_path / "docs" / "adr" / "0002_nowy.md")

    assert set(_martwe_odsylacze(tmp_path)) == {
        "CLAUDE.md:1 → docs/adr/0002_cel.md",
        "docs/adr/0001_cytujacy.md:1 → docs/adr/0002_cel.md",
        "docs/adr/0001_cytujacy.md:2 → 0002_cel.md",
        "docs/design/rdzen.md:1 → ../adr/0002_cel.md",
    }, "przemianowanie miało zapalić wszystkie cztery formy cytowania"


def test_odsylacz_spoza_drzewa_pisany_wzglednie_nie_zapala_bramki(tmp_path: Path) -> None:
    """Warunek, na którym stoi decyzja o SZEROKIM wyrażeniu dla prozy — jako sonda, nie proza.

    `README.md` tego pod-projektu cytuje ADR 0074 **rdzenia** jako `../docs/adr/0074-…`. Gdyby
    wyrażenie od prozy łapało ścieżki poprzedzone ukośnikiem, bramka rozwiązywałaby ten cel od
    korzenia pod-projektu i zapalała się na odsyłaczu, który jest POPRAWNY. Komentarz przy
    `_SCIEZKA_PROZA` mówi, że tak nie jest; ten test to sprawdza — bo komentarz, który sam
    siebie potwierdza, jest tą samą klasą, co wykluczenie bez zastępstwa.
    """
    (tmp_path / "pod-projekt" / "docs").mkdir(parents=True)
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0074-cudzy.md").write_text("# cudzy\n", encoding="utf-8")
    (tmp_path / "pod-projekt" / "README.md").write_text(
        "mieszka tu ([ADR 0074](../docs/adr/0074-cudzy.md))\n", encoding="utf-8"
    )

    assert _martwe_odsylacze(tmp_path / "pod-projekt") == [], (
        "odsyłacz spoza drzewa pisany względnie ma się rozwiązać względem DOKUMENTU, "
        "a nie zostać złapany jako ścieżka prozą od korzenia pod-projektu"
    )


def test_bramka_ma_co_czytac() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki.

    Liczymy ODSYŁACZE, nie pliki: próg na plikach przechodzi po zmianie konwencji zapisu, czyli
    dokładnie wtedy, gdy bramka przestaje widzieć to, po co powstała. Zmierzone 2026-09-24:
    230 odsyłaczy w 45 dokumentach objętych bramką. Margines jest po to, żeby usunięty akapit
    nie zapalał, a wycięcie połowy dokumentacji zapalało.
    """
    assert len(_odsylacze(KORZEN)) >= 200
