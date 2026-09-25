"""Puls żywotności pollerów mostu (R5) — plik na wolumenie stanu + checker dla healthchecku.

Pulsu nie bije sama pętla — bije go ten, kto uznał rundę za PRODUKTYWNĄ, a każde drzwi mają na to
własną regułę i własny plik:

* **Poller GitHub** — po UDANEJ rundzie (``poller.py`` gałąź ``else``). Runda zakończona wyjątkiem
  NIE odświeża pliku, więc jałowa pętla („w kółko ten sam błąd") starzeje puls.
* **Poller Teams** — po każdej obsłużonej wiadomości, po każdym kanale i po domkniętej rundzie,
  także po kanale, którego polling rzucił wyjątkiem (błąd jednego kanału nie kładzie pętli, więc
  proces nadal żyje i pracuje na pozostałych). Ta częstotliwość jest celowa: runda zawiera pełne
  tury agenta na każdym kanale, więc jej długość mierzy RUCH, nie żywotność — przy ``--max-age
  180`` kontener bywał ``unhealthy`` na ścieżce SZCZĘŚLIWEJ, a wtedy sygnał przestaje być czytany.
  Dwa wyjątki bijące w drugą stronę: jałowa pętla odświeżania tokenu (``refresh_auth`` pada →
  pętla wraca na początek) pulsu NIE bije, i nie bije go też runda, w której nie dało się utrwalić
  stanu — wolumen odmawiający zapisu zatrzymuje obsługę wiadomości, więc „proces żyje" byłoby
  wtedy prawdą bezużyteczną.
* **Notifier** — po rundzie z postępem, własnym plikiem; patrz ``notifier_heartbeat_path``.

Wspólny mianownik: puls świadczy o ŻYCIU procesu i o tym, że pętla się kręci, a nie o tym, że praca
się udaje. Powtarzalny błąd JEDNEGO kanału Teams starzeje puls tylko wtedy, gdy zabierze cały
proces; inaczej widać go w logu i — od ADR 0069 — w kwarantannie porzuconych wiadomości. Tego,
czego ``docker ps`` (kontener „up") nie widzi, puls dopełnia: proces stoi albo zawiesił się na I/O.

Ścieżka pulsu jest SIOSTRĄ pliku stanu drzwi (``github_state.json`` → ``github_state.heartbeat``),
więc leży na tym samym wolumenie ``state`` i wskazuje ją ta sama zmienna ``SUFLER_*_STATE``, którą
operator już ustawia — writer i healthcheck (``--file``) trafiają w to samo miejsce bez nowej
konfiguracji.

``main`` jest entrypointem healthchecku kontenera (``sufler-heartbeat-check``): zwraca 0, gdy plik
jest świeższy niż ``--max-age`` sekund, inaczej 1 (brak pliku = niezdrowy). Logika świeżości
(``is_fresh``) jest czysta i wstrzykuje zegar — testowana jednostkowo.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def heartbeat_path(state_path: Path | str) -> Path:
    """Ścieżka pulsu = plik stanu z rozszerzeniem ``.heartbeat`` (siostra na wolumenie stanu)."""
    return Path(state_path).with_suffix(".heartbeat")


def notifier_heartbeat_path(state_path: Path | str) -> Path:
    """Puls NOTIFIERA — odrębny plik (``.notify.heartbeat``) obok pulsu pollera (ADR 0067 §2).

    Poller i notifier to różne pętle w tym samym procesie: notifier zablokowany na trwale
    niewysyłalnym zdarzeniu (kursor stoi) NIE zdradza się pulsem pollera, który dalej bije. Osobny
    plik pozwala healthcheckowi sprawdzić OBIE pętle. Notifier bije go po rundzie produktywnej
    (dostarczono / dead-letter / pusta kolejka); runda utknięta na ponowieniu pulsu NIE odświeża.
    """
    return Path(state_path).with_suffix(".notify.heartbeat")


def write_heartbeat(path: Path | str, *, now: float | None = None) -> None:
    """Zapisz znacznik żywotności, aktualizując ``mtime`` (zapis atomowy: tmp + ``os.replace``).

    Treść (epoch) jest tylko dla człowieka czytającego plik — checker patrzy na ``mtime``. Zapis
    przez plik tymczasowy i ``os.replace`` sprawia, że healthcheck nigdy nie trafi na obcięty plik.
    """
    stamp = time.time() if now is None else now
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(f"{stamp:.3f}\n", encoding="ascii")
    os.replace(tmp, target)


def is_fresh(path: Path | str, max_age_s: float, *, now: float | None = None) -> bool:
    """True, gdy plik istnieje i jego ``mtime`` jest młodszy niż ``max_age_s`` sekund.

    Nieczytelny plik → False: brak (poller nigdy nie domknął cyklu albo skasowano stan), ale też
    ``PermissionError`` czy błąd I/O na wolumenie. To ma być WERDYKT healthchecku, nie traceback —
    a każda z tych sytuacji znaczy „nie mam dowodu, że pętla żyje", czyli dokładnie „niezdrowy".
    ``now`` jest wstrzykiwalny, żeby test sterował upływem czasu bez czekania.
    """
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return False
    current = time.time() if now is None else now
    return (current - mtime) < max_age_s


def main(argv: list[str] | None = None) -> int:
    """Healthcheck kontenera: 0 = WSZYSTKIE pulsy świeże, 1 = którykolwiek nieświeży/brak.

    ``--file`` można podać WIELE razy (poller + notifier, ADR 0067 §2): kontener jest zdrowy tylko,
    gdy każda pętla domknęła świeżą rundę. Jeden ``--file`` zachowuje dawne zachowanie.
    """
    parser = argparse.ArgumentParser(
        description="Sprawdź wiek pulsów Sufler (healthcheck kontenera)."
    )
    parser.add_argument(
        "--file",
        action="append",
        required=True,
        help="ścieżka pliku pulsu na wolumenie stanu (można podać wiele: poller + notifier)",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        required=True,
        help="maksymalny dopuszczalny wiek pulsu w sekundach (interwał pollingu + zapas)",
    )
    args = parser.parse_args(argv)
    stale = [path for path in args.file if not is_fresh(path, args.max_age)]
    if not stale:
        return 0
    for path in stale:
        print(
            f"puls nieświeży lub brak: {path} starszy niż {args.max_age:g}s",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":  # pragma: no cover - cienki wrapper CLI
    sys.exit(main())
