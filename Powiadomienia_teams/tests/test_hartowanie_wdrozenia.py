"""Strażnik hartowania kontenera w `deploy/docker-compose.yml`.

Audyt 2026-09-08 zastał rozjazd w kierunku, którego nikt nie pilnował: na serwerze
(`/opt/teams-shifts-reminder/docker-compose.yml`) kontener biegał z `cap_drop`, limitami zasobów
i `noexec,nosuid` na `/tmp`, a plik w repozytorium **nie miał ani jednej z tych linii**.
`git log --all -S cap_drop -- Powiadomienia_teams/deploy/` nie zwracał nic: hardening dołożono
ręcznie na hoście i nigdy nie wrócił do gita.

Kierunek tego rozjazdu jest najgorszy z możliwych — odtworzenie wdrożenia z repozytorium
(nowy serwer, migracja, `git checkout` po awarii) **po cichu zdejmowało zabezpieczenia**, które
na produkcji działały od tygodni. Nic by nie padło i nic by nie zapisało w logu; kontener
wstałby z pełnym zestawem capability.

Reguła iteruje po CHRONIONYCH RZECZACH, nie po jednej znanej literówce: skasowanie dowolnej
z nich zapala test, a dołożenie kolejnej wymaga świadomego dopisania tutaj — ten sam wzorzec, co
`test_wersje._MIEJSCA` i `test_srodowiska_bramki`.

Plik `deploy/docker-compose.yml` nie wchodzi do obrazu (`Dockerfile` kopiuje z `deploy/` samo
`env.example`), więc w etapie `test` obrazu ten strażnik POMIJA SIĘ jawnie — jak `test_wersje`.
Weryfikacja prozy repozytorium należy do CI (ADR 0008).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PODPROJEKT = Path(__file__).resolve().parent.parent
_COMPOSE = _PODPROJEKT / "deploy" / "docker-compose.yml"

#: (wzorzec, co chroni). Wzorce są liniowe i tolerują odstępy, bo formatowanie YAML-a wolno
#: zmieniać — nie wolno zmieniać TREŚCI zabezpieczenia.
_HARTOWANIE = (
    (
        re.compile(r"^\s*cap_drop:\s*\[ALL\]", re.M),
        "odebranie wszystkich capability — proces robi tylko wychodzące HTTPS i zapis do wolumenu",
    ),
    (
        re.compile(r"^\s*read_only:\s*true", re.M),
        "system plików kontenera tylko do odczytu poza wolumenem stanu",
    ),
    (
        re.compile(r"^\s*-\s*no-new-privileges:true", re.M),
        "zakaz podnoszenia uprawnień przez setuid",
    ),
    (
        re.compile(r"^\s*-\s*/tmp:[^\n]*noexec[^\n]*nosuid", re.M),
        "/tmp jest JEDYNYM zapisywalnym miejscem poza wolumenem — i jedynym, w którym dałoby się "
        "cokolwiek podłożyć i uruchomić",
    ),
    (
        re.compile(r"^\s*mem_limit:\s*\S+", re.M),
        "wyciek pamięci ma wywrócić usługę, a nie serwer klienta",
    ),
    (
        re.compile(r"^\s*pids_limit:\s*\d+", re.M),
        "pętla tworząca wątki ma wywrócić usługę, a nie serwer klienta",
    ),
)


def _tresc() -> str:
    if not _COMPOSE.exists():
        pytest.skip(
            "brak deploy/docker-compose.yml — poza kontekstem repozytorium (obraz); biegnie w CI"
        )
    return _COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("wzorzec", "co_chroni"), _HARTOWANIE, ids=lambda x: getattr(x, "pattern", x)[:40]
)
def test_compose_niesie_hartowanie(wzorzec: re.Pattern[str], co_chroni: str) -> None:
    """Każda pozycja hartowania musi być W REPOZYTORIUM, nie tylko na hoście."""
    assert wzorzec.search(_tresc()), (
        f"deploy/docker-compose.yml stracił zabezpieczenie: {co_chroni}. "
        "Jeśli to zamierzone, skasuj też wpis z `_HARTOWANIE` — świadomie, nie przez przeoczenie."
    )


def test_compose_nie_wystawia_portow() -> None:
    """Usługa robi WYŁĄCZNIE połączenia wychodzące — sekcja `ports` byłaby regresją bezpieczeństwa.

    Brak `ports` jest w tym pliku decyzją opisaną komentarzem, a komentarz nikogo nie zatrzyma.
    """
    assert not re.search(r"^\s*ports:", _tresc(), re.M), (
        "compose wystawia porty — usługa nie nasłuchuje na niczym i nie ma czego wystawiać"
    )
