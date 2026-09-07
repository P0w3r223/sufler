"""Strażnik martwych odsyłaczy: plik cytowany w grawisach ma istnieć.

Powód jest policzony, nie wyobrażony. 2026-09-07 w `src/` żyło JEDENAŚCIE odsyłaczy do plików,
których nigdy nie było w repozytorium — w tym cztery do strażników obiecanych w docstringach
(test_obieg_stan, test_zaleznosci, test_kontrakty, test_wzorzec) i cały plan rozwoju, cytowany
jako uzasadnienie czterdziestu decyzji. Jeden taki odsyłacz kosztował już usterkę: wysyłka
powoływała się na strażnika szwu, którego nie było, więc pominięta bramka przeżyła wydanie.
(Nazwy stoją tu bez grawisów świadomie — reguła czyta grawisy, a to są przykłady, nie odsyłacze.)

Reguła iteruje po ODSYŁACZACH, nie po plikach docelowych: nowy martwy odsyłacz wpada pod nią bez
niczyjej pamięci. Kierunek odwrotny (plik, do którego nikt nie odsyła) świadomie nie jest
sprawdzany — nie jest defektem.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PODPROJEKT = Path(__file__).resolve().parent.parent
_KORZEN_REPO = _PODPROJEKT.parent

#: Skanujemy prozę ŻYWĄ. `CHANGELOG.md` i `PLAN.md` są poza zakresem świadomie: opisują stany
#: MINIONE i wolno im wymieniać pliki skasowane od tamtej pory (np. `roster.py`).
_SKANOWANE = ("src/**/*.py", "tests/**/*.py", "README.md", "deploy/*.md", "docs/**/*.md")

#: Rozszerzenia plików ŹRÓDŁOWYCH. Świadomie bez `.json` i `.bin`: pod tymi nazwami cytuje się
#: artefakty RUNTIME'u (`powiadomienia_state.json`, `teams_token_cache.bin`), których w repo nie ma
#: i być nie powinno.
# Po rozszerzeniu MUSI stać coś, co nie jest dalszym ciągiem identyfikatora — inaczej
# `lifecycle.should_expire` czyta się jako plik `lifecycle.sh`, a `msvcrt.locking` jako
# `msvcrt.lock`. Oba zdarzyły się przy pierwszym uruchomieniu tej reguły.
_ODSYLACZ = re.compile(
    r"`([A-Za-z0-9_./-]+\.(?:py|md|toml|yml|sh|example|lock|cfg))(?![A-Za-z0-9_])"
)

#: Gdzie wolno szukać celu. Kolejność bez znaczenia — wystarczy jedno trafienie.
_BAZY = (
    _PODPROJEKT,
    _PODPROJEKT / "src" / "powiadomienia_teams",
    _PODPROJEKT / "tests",
    _PODPROJEKT / "deploy",
    _PODPROJEKT / "docs",
    _PODPROJEKT / "scripts",
    _KORZEN_REPO,
    _KORZEN_REPO / ".github" / "workflows",
)

#: Wyjątki JAWNE, parami (plik cytujący, cel) i każdy z powodem. Para, nie sama nazwa celu:
#: nowy martwy odsyłacz do tej samej nazwy z innego pliku nadal zapala regułę.
_HISTORYCZNE = {
    ("test_cisza.py", "test_send_window.py"): "plik wprost mówi, że ZASTĘPUJE tamten",
    (
        "__init__.py",
        "tools/check_versions.py",
    ): "zdanie mówi wprost, że narzędzie nigdy nie istniało",
    ("interpreter.py", "docs/architektura.md"): "zdanie mówi wprost, że dokumentu nigdy nie było",
    ("interpreter.py", "tools/sprawdz_odsylacze.py"): "jw. — strażnik zastąpiony tym plikiem",
    ("wzorzec.py", "test_wzorzec.py"): "moduł niepodłączony (D1); rozstrzyga się razem z nim",
    (
        "0001-multi-team-shifts-support.md",
        "roster.py",
    ): "moduł skasowany świadomie przy czyszczeniu 0.2.0 — ADR jest zapisem stanu sprzed",
    (
        "0005-send-window-for-bot-initiated-messages.md",
        "scheduler/send_window.py",
    ): "ADR stwierdza NIEOBECNOŚĆ tego modułu w 0.2.19 — cytat jest twierdzeniem o braku",
    ("0005-send-window-for-bot-initiated-messages.md", "send_window.py"): "jw.",
    ("0005-send-window-for-bot-initiated-messages.md", "tests/test_send_window.py"): "jw.",
}


def _pliki() -> list[Path]:
    """Proza do przeskanowania — bez tego pliku.

    Reguła jest O odsyłaczach, więc z natury wymienia w komentarzach nazwy plików, których nie ma
    (`roster.py` skasowany w 0.2.0, przykłady fałszywych trafień). Wyjęcie jej spod własnego
    pomiaru jest tańsze niż pisanie o odsyłaczach bez wymieniania odsyłaczy — i jest jedynym
    wyjątkiem POD PLIK, a nie pod parę (plik, cel).
    """
    return [
        p
        for wzorzec in _SKANOWANE
        for p in sorted(_PODPROJEKT.glob(wzorzec))
        if p.is_file() and p.name != Path(__file__).name
    ]


def _istnieje(cel: str) -> bool:
    if any((baza / cel).exists() for baza in _BAZY):
        return True
    nazwa = Path(cel).name
    return any(_PODPROJEKT.rglob(nazwa)) or any(_KORZEN_REPO.glob(nazwa))


def test_kazdy_cytowany_plik_istnieje():
    # Sam fakt, że `src/` i `tests/` są na miejscu, NIE wystarcza: obraz niesie właśnie je i nic
    # więcej, więc reguła miałaby czym skanować, a nie miałaby gdzie rozwiązywać celów — każdy
    # odsyłacz do `deploy/` czy `docs/` wyszedłby jako martwy. Wykryła to sonda `>100` przy
    # pierwszym biegu w obrazie (80 odsyłaczy zamiast ponad stu) i to jest dokładnie ta rola,
    # dla której sonda istnieje: pomiar na okrojonym przedmiocie nie jest pomiarem.
    if not ((_PODPROJEKT / "docs").is_dir() and (_PODPROJEKT / "deploy").is_dir()):
        pytest.skip("strażnik odsyłaczy — poza kontekstem repozytorium (obraz); biegnie w CI")
    pliki = _pliki()
    assert pliki, "brak plików prozy do skanowania mimo obecnego drzewa repozytorium"

    martwe: list[str] = []
    wszystkich = 0
    for plik in pliki:
        for nr, wiersz in enumerate(plik.read_text(encoding="utf-8").splitlines(), 1):
            for cel in _ODSYLACZ.findall(wiersz):
                wszystkich += 1
                if _HISTORYCZNE.get((plik.name, cel)) or _istnieje(cel):
                    continue
                martwe.append(f"{plik.relative_to(_PODPROJEKT)}:{nr} → {cel}")

    assert wszystkich > 100, (
        f"znaleziono tylko {wszystkich} odsyłaczy — czy konwencja zapisu w grawisach się zmieniła? "
        f"reguła, która nic nie widzi, jest zielona z niewłaściwego powodu"
    )
    assert martwe == [], martwe
