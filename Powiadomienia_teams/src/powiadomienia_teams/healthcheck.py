"""Kontrola stanu zdrowia dla ``HEALTHCHECK`` obrazu: `python -m powiadomienia_teams.healthcheck`.

Bez tego `docker compose ps` pokazuje „Up" także dla procesu, który stoi — a to najgorszy stan
dla usługi bezobsługowej, bo wygląda jak zdrowie. Sprawdzamy wiek pliku pulsu, który pętla
odświeża przy każdej pobudce (``odswiez_puls`` niżej — pisze i czyta go ten sam moduł).

Świadomie jako moduł, a nie jednolinijkowiec w ``Dockerfile``: ścieżka i próg wyprowadzają się
z tej samej konfiguracji co reszta programu, więc nie mogą się z nią rozjechać, a całość daje się
przetestować jednostkowo.

Uczciwe zastrzeżenie: Docker sam NIE restartuje kontenera „unhealthy" (robi to dopiero Swarm).
Ten moduł daje widoczność, nie samoleczenie — przed zawieszeniem chronią jawne timeouty we
WSZYSTKICH trzech klientach HTTP procesu: Graph (`httpx.Client(timeout=30)`), Anthropic
(`_TIMEOUT_S`) oraz MSAL (`_MSAL_TIMEOUT_S`). Ten ostatni był długo pominięty, przez co samo
zdanie powyżej bywało nieprawdziwe — bez niego blackhole sieciowy wieszał pętlę bezterminowo.
"""

from __future__ import annotations

import logging
import sys
import time

from powiadomienia_teams.config import Settings

logger = logging.getLogger(__name__)


def odswiez_puls(settings: Settings) -> None:
    """Odśwież znacznik czasu pliku pulsu — źródło prawdy dla HEALTHCHECK obrazu.

    Wołane przy KAŻDEJ pobudce pętli, więc wiek pliku odpowiada temu, jak dawno usługa naprawdę
    coś robiła. Bez tego `docker compose ps` pokazuje „Up" także dla procesu, który stoi.

    Mieszka w tym module razem z ``zdrowy``, bo obie funkcje mówią o TYM SAMYM pliku: jedna go
    pisze, druga czyta i orzeka na jego podstawie. Rozdzielone (puls w orkiestracji, próg tutaj)
    dawały dwa miejsca, w których dało się niezależnie zmienić ścieżkę albo znaczenie znacznika.
    """
    try:
        sciezka = settings.heartbeat_path
        sciezka.parent.mkdir(parents=True, exist_ok=True)
        sciezka.touch()
    except OSError:
        # Puls jest diagnostyką, nie funkcją — jego awaria nie może zatrzymać powiadomień.
        logger.warning("Nie udało się odświeżyć pliku pulsu", exc_info=True)


def zdrowy(*, now: float | None = None) -> tuple[bool, str]:
    """Czy usługa żyje? Zwraca werdykt i czytelne uzasadnienie (czysto, ``now`` wstrzykiwalne).

    Próg pochodzi z ``health_max_age_s`` i jest NIEZALEŻNY od sufitu nasłuchu. Wcześniej był
    z niego wyprowadzany, więc podniesienie sufitu do godziny przesunęło wykrywanie stojącej
    pętli na dwie godziny. Puls bije co minutę niezależnie od tempa odpytywania Graph
    (``runtime.service.spij_z_pulsem``), więc próg może być krótki.
    """
    settings = Settings.from_env()
    sciezka = settings.heartbeat_path
    prog = settings.health_max_age_s

    if not sciezka.exists():
        return False, f"brak pliku pulsu {sciezka} — pętla jeszcze nie wystartowała albo padła"
    wiek = (now if now is not None else time.time()) - sciezka.stat().st_mtime
    if wiek > prog:
        return False, f"puls sprzed {wiek:.0f}s (próg {prog}s) — pętla stoi"
    return True, f"puls sprzed {wiek:.0f}s (próg {prog}s)"


def main() -> int:
    try:
        ok, powod = zdrowy()
    except Exception as blad:  # konfiguracja nie do odczytania = stan niezdrowy, nie wyjątek
        print(f"niezdrowy: {blad}", file=sys.stderr)
        return 1
    print(("zdrowy: " if ok else "niezdrowy: ") + powod)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
