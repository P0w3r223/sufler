"""Bramka numeracji ADR: jeden numer — jedna decyzja.

Powód istnienia jest empiryczny. Praca nad harnessem agenta i praca nad odczytem
Jiry/grafikiem Shifts szły równolegle na dwóch gałęziach; obie sięgnęły po wolne
numery `0056` i `0057`. Scalenie było bezkonfliktowe tekstowo — pliki mają różne
nazwy — więc git przepuścił kolizję bez słowa, a w drzewie zostały czterdzieści
dwa cytowania „ADR 0056/0057" wskazujące na dwie różne decyzje każde.

Test sprawdza też zgodność numeru w nagłówku z numerem w nazwie pliku. To ten
warunek łapie renumerację zrobioną w połowie: `git mv` bez poprawienia tytułu
zostawia dokument, który sam o sobie mówi co innego niż katalog.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADR_DIR = Path(__file__).resolve().parents[1] / "docs" / "adr"

# To bramka REPOZYTORIUM, nie runtime'u. Obraz kopiuje `src/` i `tests/`, ale nie `docs/`,
# więc w kontenerze katalog decyzji po prostu nie istnieje — złapane budowaniem etapu `test`,
# które wywaliło się na pustej liście plików. Pomijamy zamiast osłabiać asercję: w drzewie
# roboczym pusty `docs/adr/` nadal ma być błędem.
pytestmark = pytest.mark.skipif(
    not _ADR_DIR.is_dir(),
    reason="katalog docs/adr/ nieobecny — bramka dotyczy drzewa repozytorium, nie obrazu",
)

# `0059-teams-shifts-schedule-read.md` → numer i reszta nazwy. Podkreślenie obok
# myślnika, bo `0050_seed_corpus_document_extraction.md` jest starszy niż konwencja.
_NAZWA_PLIKU = re.compile(r"^(\d{4})[-_][a-z0-9_-]+\.md$")

# Trzy konwencje tytułu żyją w tej serii obok siebie: `# 0059. Extended…`,
# `# 0009 — Meeting-note flow…` i `# ADR 0036 — Weekly worklog…`. Bramka pilnuje
# NUMERU, nie interpunkcji — ujednolicanie 25 przyjętych dokumentów kosztowałoby
# więcej niż wnosi, a numer da się odczytać z każdej z nich.
_NAGLOWEK = re.compile(r"^#\s*(?:ADR\s+)?(\d{4})\s*[.—-]")


# Bramka NIE wylicza form zapisu — iteruje po ODSYŁACZACH. Pierwsza redakcja tego poszerzenia
# wyliczała dwie formy i przez to nie widziała trzeciej, dziś NAJLICZNIEJSZEJ: gołej nazwy pliku
# w linku względnym (`](0067-….md)`), którą piszą wszystkie ADR-y od 0061 wzwyż. Zmierzone
# 2026-09-09: 130 ścieżek od korzenia, 173 gołe nazwy — czyli 34 z 73 ADR-ów nie miało pokrycia
# ŻADNEGO, w tym `0073`, który tę właśnie klasę nazywa. Wyliczanie zabezpieczeń zamiast chodzenia
# po rzeczy chronionej (ADR 0073) trafiło więc do bramki mającej pilnować tej samej reguły.
#
# Stąd zakres: KAŻDY odsyłacz do pliku `.md`, nie tylko do ADR-a. Zmierzone — 602 odsyłacze
# w drzewie, jeden martwy (patrz wykluczenia) — więc szerszy zakres nie kosztuje nic dzisiaj,
# a jutro łapie renumerację i przeniesienie dowolnego dokumentu.
_LINK_MD = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
# Ścieżka od korzenia pisana POZA linkiem — tak wyglądają nagłówki `Related to:`. Zawężona do
# `docs/adr/`, i to zawężenie jest zmierzone, nie ostrożnościowe: szersza wersja (`docs/**`)
# zapala się na wskazaniach MIĘDZYREPOZYTORYJNYCH, których to repozytorium rozwiązać nie może
# i nie powinno — `docs/decyzje/0009-…`, `docs/przebudowa-harnessu.md`,
# `docs/pozostale-do-zrobienia.md`
# to dokumenty PACZKI wdrożeniowej, cytowane tu poprawnie i świadomie (ADR 0062:10, 0070:16).
# Bramka pilnuje więc drzewa, które ma pod ręką; cudzego nie udaje, że sprawdza.
_SCIEZKA_OD_KORZENIA = re.compile(r"(?<![\w/.\-])(docs/adr/\d{4}[-_][a-z0-9_\-]+\.md)")

# Katalogi poza bramką, każdy z powodem — bo wykluczenie bez powodu zgnije tak samo jak flaga.
_POZA_BRAMKA = frozenset(
    {
        # Podprojekty z WŁASNYMI katalogami decyzji i własną numeracją: ich odsyłacze rozwiązują
        # się względem ich korzeni, nie tego. `ceidg-tool` dołączył 2026-09-10 (ADR 0074) i jest
        # tego najostrzejszym przykładem: cytuje własne `docs/adr/0003_rate_limiter.md`, a bramka
        # rozwiązywała tę ścieżkę od korzenia repozytorium — czyli w miejsce, gdzie leży ADR 0003
        # WorkMate'a o zupełnie czym innym. Odsyłacz trafiający w niewłaściwy dokument jest gorszy
        # niż martwy, bo nie widać, że jest zły.
        "Powiadomienia_teams",
        "claude_summary",
        "ceidg-tool",
        # `krs-tool` dołączył 2026-09-11 (piąty pod-projekt, `ceidg-tool/docs/adr/0023`) i wpadł
        # w tę samą pułapkę, tylko ostrzej: jego `docs/adr/0001` rozwiązywane od korzenia trafia
        # w PUSTKĘ, bo rdzeń numeracji ADR zaczyna się wyżej. Bramka zapaliła się na `Main`
        # dopiero po scaleniu — PR-y tego dnia nie dostały ani jednego przebiegu CI (blokada
        # rozliczeń organizacji), a bramka jakości pod-projektu nie uruchamia testów rdzenia.
        "krs-tool",
        # Dzienniki sesji są zapisem TEGO, CO NAPISANO danego dnia — poprawianie w nich odsyłacza
        # jest przepisywaniem dziennika, a nie naprawą dokumentu. (Jeden martwy odsyłacz siedzi
        # dziś właśnie tam: `2026-07-16.md` cytuje ADR podprojektu ścieżką główną.)
        "sessions",
        ".git",
        ".venv",
        "node_modules",
    }
)


def _pliki_adr() -> list[Path]:
    return sorted(p for p in _ADR_DIR.glob("*.md") if p.name != "README.md")


def test_katalog_adr_nie_jest_pusty() -> None:
    """Bramka bez materiału przechodzi zawsze — to sprawdzenie samej bramki."""
    assert _pliki_adr(), f"brak plików ADR w {_ADR_DIR}"


def test_nazwy_plikow_maja_ksztalt_nnnn_slug() -> None:
    zle = [p.name for p in _pliki_adr() if not _NAZWA_PLIKU.match(p.name)]
    assert not zle, f"nazwy poza wzorcem NNNN-slug.md: {zle}"


def test_kazdy_numer_nalezy_do_jednej_decyzji() -> None:
    """Dwa pliki o tym samym numerze czynią każde cytowanie tego numeru dwuznacznym."""
    wedlug_numeru: dict[str, list[str]] = {}
    for plik in _pliki_adr():
        dopasowanie = _NAZWA_PLIKU.match(plik.name)
        if dopasowanie is None:
            continue
        wedlug_numeru.setdefault(dopasowanie.group(1), []).append(plik.name)

    kolizje = {numer: nazwy for numer, nazwy in wedlug_numeru.items() if len(nazwy) > 1}
    assert not kolizje, f"numer ADR użyty więcej niż raz: {kolizje}"


def _korzen_repo() -> Path:
    return _ADR_DIR.parents[1]


def _dokumenty_objete_bramka(korzen: Path | None = None) -> list[Path]:
    """Każdy `*.md` w drzewie poza `_POZA_BRAMKA`.

    Poprzednia redakcja miała RĘCZNĄ listę (`README.md`, `CHANGELOG.md`, `docs/**`) i pomijała
    przez to `CONTRIBIUTING.md` z pięcioma żywymi cytowaniami ADR — czyli powtarzała klasę
    z ADR 0073 o poziom wyżej: bramka chodziła po liście miejsc zamiast po drzewie.
    """
    korzen = korzen or _korzen_repo()
    return [
        p
        for p in sorted(korzen.rglob("*.md"))
        if not (set(p.relative_to(korzen).parts) & _POZA_BRAMKA)
    ]


def _odsylacze(dokument: Path, korzen: Path) -> list[tuple[int, str, Path]]:
    """`(numer_linii, tekst_odsyłacza, ścieżka_celu)` dla każdego odsyłacza do pliku `.md`.

    Link markdownowy rozwiązujemy WZGLĘDEM DOKUMENTU (tak czyta go czytelnik i tak sprawdzi go
    przeglądarka), ścieżkę pisaną prozą — względem korzenia repozytorium.
    """
    trafienia: list[tuple[int, str, Path]] = []
    for numer, linia in enumerate(dokument.read_text(encoding="utf-8").splitlines(), 1):
        for cel in _LINK_MD.findall(linia):
            if cel.startswith(("http://", "https://")):
                continue
            trafienia.append((numer, cel, dokument.parent / cel))
        for cel in _SCIEZKA_OD_KORZENIA.findall(linia):
            trafienia.append((numer, cel, korzen / cel))
    return trafienia


def test_dokumenty_nie_maja_martwych_odsylaczy() -> None:
    """Renumeracja albo przeniesienie pliku bez poprawienia cytowań daje odsyłacz w próżnię.

    Ta klasa błędu przeżyła renumerację `0056/0057` → `0059/0060`: w `README.md` poprawiony
    został wiersz o grafiku Shifts, a sąsiedni o odczycie Jiry — cytujący ten sam ADR —
    został pominięty. Pozostałe bramki tego modułu patrzą na nazwy plików i nagłówki,
    więc żadna nie mogła tego zobaczyć.

    **Zakres poszerzony 2026-09-09, dwa razy, i druga poprawka jest ważniejsza od pierwszej.**
    Bramka sprawdzała rozwiązywalność od pierwszego dnia, ale wyłącznie w `README`/`CHANGELOG`
    i wyłącznie dla ścieżki od korzenia — a odsyłacze między samymi ADR-ami omijała świadomie,
    bo niosły dług historyczny. Dług spłacono w tym samym commicie (siedem martwych nagłówków
    `Related to:` w 0034/0054/0058/0059/0060, wszystkie: numer poprawny, slug ze starej nazwy),
    więc powód wyłączenia zniknął. Pierwsza redakcja poszerzenia dołożyła jednak DRUGĄ formę
    zapisu zamiast przestać je wyliczać — i nie widziała trzeciej, najliczniejszej. Teraz bramka
    chodzi po odsyłaczach, nie po ich formach.
    """
    korzen = _korzen_repo()
    martwe = [
        f"{dokument.relative_to(korzen)}:{numer} → {tekst}"
        for dokument in _dokumenty_objete_bramka(korzen)
        for numer, tekst, cel in _odsylacze(dokument, korzen)
        if not cel.is_file()
    ]
    assert not martwe, f"odsyłacze do nieistniejących dokumentów: {martwe}"


def test_bramka_przechodzi_droge_przyszlej_zmiany(tmp_path: Path) -> None:
    """Sprawdzenie samej bramki — przez PRZEMIANOWANIE ADR-a, nie przez znane wyrażenia.

    Poprzednia sonda pytała, czy bramka widzi dwie konkretne formy zapisu, więc nie mogła
    pokazać, że trzeciej nie widzi: potwierdzała własne założenie. Ta idzie drogą zmiany,
    która ten dług tworzy — plik ADR zmienia nazwę — i żąda, żeby zapaliło się KAŻDE cytowanie,
    niezależnie od tego, jak zapisane.
    """
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "how-to").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "0002-stara-nazwa.md").write_text(
        "# 0002. Cel\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "adr" / "0001-cytujacy.md").write_text(
        "# 0001. Cytujący\n"
        "Related to: docs/adr/0002-stara-nazwa.md\n"
        "goła nazwa: [ADR 0002](0002-stara-nazwa.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "how-to" / "przewodnik.md").write_text(
        "względnie: [ADR 0002](../adr/0002-stara-nazwa.md)\n", encoding="utf-8"
    )

    zywe = [
        (str(d.relative_to(tmp_path)), t)
        for d in _dokumenty_objete_bramka(tmp_path)
        for _, t, c in _odsylacze(d, tmp_path)
        if not c.is_file()
    ]
    assert zywe == [], f"przed przemianowaniem nic nie miało być martwe: {zywe}"

    (tmp_path / "docs" / "adr" / "0002-stara-nazwa.md").rename(
        tmp_path / "docs" / "adr" / "0002-nowa-nazwa.md"
    )

    martwe = {
        t
        for d in _dokumenty_objete_bramka(tmp_path)
        for _, t, c in _odsylacze(d, tmp_path)
        if not c.is_file()
    }
    assert martwe == {
        "docs/adr/0002-stara-nazwa.md",
        "0002-stara-nazwa.md",
        "../adr/0002-stara-nazwa.md",
    }, f"przemianowanie miało zapalić WSZYSTKIE trzy formy cytowania, zapaliło: {martwe}"


def test_wykluczenia_bramki_maja_powod_a_nie_tylko_wpis() -> None:
    """Wykluczenie bez powodu zgnije — więc każdy wpis ma stać w komentarzu przy zbiorze."""
    zrodlo = Path(__file__).read_text(encoding="utf-8")
    blok = zrodlo[zrodlo.index("_POZA_BRAMKA = frozenset(") : zrodlo.index("def _pliki_adr()")]
    for katalog in _POZA_BRAMKA:
        assert f'"{katalog}"' in blok, f"{katalog} zniknął ze zbioru wykluczeń"
    assert blok.count("#") >= 3, "wykluczenia straciły uzasadnienia w komentarzach"


def test_kazdy_wykluczony_podprojekt_ma_wlasnego_straznika_odsylaczy() -> None:
    """Wykluczenie z tej bramki wolno mieć tylko temu, kto ma zastępstwo u siebie.

    Metareguła wyżej pilnuje, że każde wykluczenie ma POWÓD w komentarzu. Nie pilnowała
    natomiast, że obiecane zastępstwo ISTNIEJE — a to dwie różne rzeczy. Do 2026-09-24 dwa
    z czterech pod-projektów (`ceidg-tool`, `claude_summary`) były spod bramki wyjęte i nie
    miały w zamian niczego: razem 59 dokumentów, w tym `ceidg-tool/docs/adr/` cytujące się
    nawzajem gęściej niż kod. Jedynym śladem po obserwatorze była proza (#166).

    To ta sama klasa, która w tym repozytorium powołała `Powiadomienia_teams/tests/
    test_szew_wysylki.py`: docstring powoływał się na strażnika, którego nie było.

    Kolejność jest tu odwrotna niż zwykle i to jest świadome: reguła powstaje PO strażnikach,
    bo napisana przed nimi byłaby czerwona od pierwszego dnia i nauczyłaby tylko tego, że
    czerwień tej bramki wolno przeczekać.

    Obecność katalogu `tests/` odróżnia pod-projekt od pozostałych wykluczeń (`.git`, `.venv`,
    `node_modules`, `sessions`) — te nie są drzewami z własną dokumentacją i strażnika nie
    potrzebują. Nowy pod-projekt dopisany do `_POZA_BRAMKA` zapali tę regułę, dopóki nie
    dostanie własnego `tests/test_odsylacze.py`.
    """
    korzen = _korzen_repo()
    bez_straznika = [
        katalog
        for katalog in sorted(_POZA_BRAMKA)
        if (korzen / katalog / "tests").is_dir()
        and not (korzen / katalog / "tests" / "test_odsylacze.py").is_file()
    ]

    assert bez_straznika == [], (
        "pod-projekt wyjęty spod bramki odsyłaczy rdzenia, a bez własnego strażnika: "
        f"{bez_straznika}. Wykluczenie bez zastępstwa zamienia obserwatora na dobre chęci — "
        "skopiuj wzorzec z krs-tool/tests/test_odsylacze.py"
    )


def test_numer_w_naglowku_zgadza_sie_z_nazwa_pliku() -> None:
    rozjazdy: list[str] = []
    for plik in _pliki_adr():
        z_nazwy = _NAZWA_PLIKU.match(plik.name)
        # Pusty plik daje pustą listę linii — bez tego domyślnego bramka wywalała się
        # ``IndexError`` zamiast wskazać dokument, który jest pusty.
        linie = plik.read_text(encoding="utf-8").splitlines()
        pierwsza_linia = linie[0] if linie else ""
        z_naglowka = _NAGLOWEK.match(pierwsza_linia)
        if z_nazwy is None or z_naglowka is None:
            rozjazdy.append(f"{plik.name}: nagłówek {pierwsza_linia!r} bez numeru")
        elif z_nazwy.group(1) != z_naglowka.group(1):
            rozjazdy.append(f"{plik.name}: nagłówek mówi {z_naglowka.group(1)}")
    assert not rozjazdy, f"numer w nagłówku rozjechany z nazwą pliku: {rozjazdy}"
