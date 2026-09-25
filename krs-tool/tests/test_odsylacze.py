"""Odsyłacze w dokumentach tego pod-projektu rozwiązują się do istniejących plików.

Bramka rdzenia (`tests/test_adr_numbering.py`) tego drzewa NIE sprawdza, i to jest decyzja,
nie przeoczenie: pisane prozą `docs/adr/0001_…` rozwiązywałaby od korzenia repozytorium, gdzie
numeracja ADR należy do Sufler'a i mówi o czym innym. Wykluczenie bez zastępstwa zamieniłoby
jednak obserwatora na dobre chęci, więc ten sam warunek stoi tutaj — z korzeniem przesuniętym
o jeden katalog.

Ta zamiana ma powód zmierzony: `krs-tool/CLAUDE.md` wszedł na `Main` z odsyłaczem, który
zapalił bramkę rdzenia dopiero PO scaleniu, bo PR-y tego dnia nie dostały ani jednego przebiegu
CI. Dokumentów w tym pod-projekcie jest dziewięć i cytują się nawzajem gęściej niż kod.
"""

from __future__ import annotations

import re
from pathlib import Path

KORZEN = Path(__file__).resolve().parent.parent

_LINK_MD = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
# ŚWIADOME poszerzenie wobec rdzenia, który zawęża się do `docs/adr/NNNN…`: tam szersza wersja
# zapalała się na odsyłaczach MIĘDZYREPOZYTORYJNYCH (dokumenty paczki wdrożeniowej), których
# tamto drzewo rozwiązać nie może. Ten pod-projekt takich odsyłaczy nie ma i cytuje prozą całe
# `docs/`, nie tylko ADR-y. Pierwszy odsyłacz do cudzego `docs/…` będzie musiał to zawężenie
# przywrócić — i wtedy ten komentarz jest miejscem, w którym widać, że różnica była wyborem.
_SCIEZKA_PROZA = re.compile(r"(?<![\w/.\-])(docs/[a-z0-9_/\-]+\.md)")

# Katalogi bez dokumentacji, za to z tysiącami plików cudzych — plus jeden z własnym powodem.
# `tests/golden` to oczekiwane wydruki renderera raportu, nie proza: gdyby raport kiedykolwiek
# wyemitował ścieżkę `docs/…`, ta bramka związałaby się z bramką golden i „naprawą" stałaby się
# edycja oczekiwanego wyjścia. Dziś tamte pliki nie mają ani jednego odsyłacza, więc wykluczenie
# nic nie kosztuje i zapobiega sprzężeniu, zanim powstanie.
_POZA_BRAMKA = frozenset(
    {".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "probki", "golden"}
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

    Opis idzie przez `as_posix()`, bo inaczej niesie separator systemu: bramka biegnie na ubuntu
    w CI i na Windows lokalnie, a komunikat, który różni się między nimi, nie nadaje się ani do
    asercji, ani do wklejenia w zgłoszenie.
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

    Pierwsza redakcja tej sondy zasiewała jeden martwy napis pisany prozą i jeden ŻYWY link
    markdownowy. Żywy link dopasowuje się jednak także wyrażeniem od prozy (lookbehind przepuszcza
    nawias), więc asercję spełniała sama gałąź prozą: zabicie `_LINK_MD` zostawiało wszystkie trzy
    testy zielone, a w całym pod-projekcie stał wtedy JEDEN link markdownowy, w korzeniu, gdzie
    obie bazy rozwiązywania dają ten sam wynik. Połowa strażnika nie miała obserwatora — czyli
    dokładnie ta klasa, którą ten pod-projekt nazywa u siebie defektem trzynaście razy.

    Ta wersja idzie drogą zmiany, która dług tworzy, i żąda zapalenia się KAŻDEJ formy cytowania,
    niezależnie od tego, jak zapisanej i z którego katalogu.
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


def test_bramka_ma_co_czytac() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki.

    Liczymy ODSYŁACZE, nie pliki: próg na plikach przechodzi po zmianie konwencji zapisu, czyli
    dokładnie wtedy, gdy bramka przestaje widzieć to, po co powstała. Zmierzone 2026-09-11:
    20 odsyłaczy w 9 dokumentach. Margines jest po to, żeby usunięty akapit sondy nie zapalał,
    a wycięcie połowy dokumentacji zapalało.
    """
    assert len(_odsylacze(KORZEN)) >= 18
