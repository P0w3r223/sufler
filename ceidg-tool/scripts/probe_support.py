"""Wspólna historia żądań dla sond — ta sama, z której korzysta limiter narzędzia.

Limit API jest nakładany na **token**, a nie na proces. Dopóki każda sonda miała własny
licznik i własny `time.sleep`, sonda i pobranie były dla siebie niewidzialne: dwa procesy
na jednym tokenie to scenariusz, który kończy się 429 i pauzą 180 s. Docstringi sond
ostrzegały przed tym słowami („Nie uruchamiaj tego w trakcie pobierania"), co działa
dokładnie tak długo, jak długo ktoś je czyta.

Po podpięciu tutaj obie strony widzą siebie nawzajem, bo jedne i drugie żądania trafiają
do `request_log` tego samego środowiska, kluczowanego odciskiem tokenu. Sonda odczekuje
przed żądaniami pobrania, pobranie odczekuje przed żądaniami sondy, a hamulec na
`X-Rate-Limit-Remaining` działa w obu.

Użycie w sondzie:

    from probe_support import shared_gate

    with shared_gate(args.env, args.token) as gate:
        gate.before("firmy")          # zamiast time.sleep(SPACING_S)
        ...żądanie...
        gate.after(status)            # 429 uruchamia pełną blokadę
"""

from __future__ import annotations

import os
import sys
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

# Sonda bywa uruchamiana jako `python scripts/ceidg_probe.py` z katalogu głównego, więc
# katalog pakietu nie musi być na ścieżce. Import pakietu jest wtedy jedyną rzeczą, która
# nie zadziała — a bez niego cały sens tego modułu znika.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ceidg_tool.apiprofile import load_profile  # noqa: E402
from ceidg_tool.clock import SystemClock  # noqa: E402
from ceidg_tool.config import ENV_DATA_DIR, Settings, default_data_dir  # noqa: E402
from ceidg_tool.ratelimit import RateLimiter  # noqa: E402
from ceidg_tool.store import Store  # noqa: E402


def no_proxy_opener() -> urllib.request.OpenerDirector:
    """Otwieracz ignorujący proxy ze środowiska — ta sama polityka co `ceidg_tool/httpclient.py`.

    `urllib.request.urlopen` domyślnie wstawia `ProxyHandler()` czytający `HTTPS_PROXY`,
    dokładnie tak samo, jak robił to `httpx` w narzędziu do 2026-09-07. Sondy niosą ten sam
    token co narzędzie, a token niesie PESEL, więc obowiązuje je ten sam zakaz wychodzenia
    przez pośrednika (uzupelnienie-01.md §B). Pusty słownik wyłącza wykrywanie proxy zamiast
    tylko podmieniać jego adres.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SharedGate:
    """Bramka sondy: ten sam limiter i ta sama historia, co w narzędziu."""

    def __init__(self, limiter: RateLimiter, store: Store) -> None:
        self._limiter = limiter
        self._store = store

    def before(self, endpoint: str) -> None:
        """Blokuje do chwili, gdy żądanie mieści się w limitach, i zapisuje je w historii."""
        self._limiter.acquire(endpoint)

    def after(self, status: int) -> None:
        """Zapisuje status; 429 uruchamia pełną blokadę, tak samo jak w narzędziu."""
        self._limiter.note_response(status)

    def close(self) -> None:
        self._store.close()


def _data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Ten sam katalog danych, co narzędzie — inaczej „wspólna historia" jest pustym słowem.

    `CEIDG_DATA_DIR` przestawia bazę narzędzia; sonda pisząca do katalogu domyślnego
    trafiłaby wtedy do innego pliku i obie strony dalej by się nie widziały, tyle że
    z komentarzem twierdzącym coś przeciwnego."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_DATA_DIR)
    return Path(raw) if raw else default_data_dir()


@contextmanager
def shared_gate(
    environment: str, token: str, *, data_dir: Path | None = None
) -> Iterator[SharedGate]:
    """Otwiera bazę środowiska tylko po to, by dzielić `request_log`.

    Nie bierze blokady pobierania: blokada jest po to, żeby dwa **pobrania** nie deptały
    sobie po checkpointach, a sonda niczego nie zapisuje poza własnymi znacznikami żądań.
    Współbieżne pobranie nie jest już problemem — limiter zobaczy jego żądania w historii
    i odczeka, zamiast dokładać się do tego samego okna."""
    settings = Settings(token=token, environment=environment, data_dir=data_dir or _data_dir())
    profile = load_profile(environment)
    clock = SystemClock()
    store = Store(settings.store_path, environment=environment, clock=clock)
    store.trim_request_log()
    limiter = RateLimiter(
        windows=profile.rate.windows,
        min_spacing_s=profile.rate.min_spacing_s,
        cooldown_s=profile.rate.cooldown_s,
        clock=clock,
        history=store.history(settings.token_fp),
    )
    gate = SharedGate(limiter, store)
    try:
        yield gate
    finally:
        gate.close()
