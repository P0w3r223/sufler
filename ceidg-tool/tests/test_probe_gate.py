"""Sonda i narzędzie na jednym tokenie: jedna historia żądań, jeden limiter.

Limit API jest nakładany na token, a nie na proces. Dopóki sondy miały własne liczniki
i własny `time.sleep`, obie strony były dla siebie niewidzialne, a ostrzeżenie żyło
w docstringu („Nie uruchamiaj tego w trakcie pobierania") — czyli działało tak długo,
jak długo ktoś je czytał. Te testy sprawdzają, że teraz widzą się nawzajem naprawdę.
"""

from __future__ import annotations

import socket
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

import pytest

from ceidg_tool.apiprofile import load_profile
from ceidg_tool.clock import SystemClock
from ceidg_tool.config import ENV_DATA_DIR, Settings
from ceidg_tool.ratelimit import RateLimiter
from ceidg_tool.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from probe_support import _data_dir, no_proxy_opener, shared_gate  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.podpis"


def stamps_in_log(data_dir: Path, token: str) -> int:
    """Ile żądań widzi w bazie druga strona — narzędzie czyta dokładnie to samo."""
    settings = Settings(token=token, environment="test", data_dir=data_dir)
    store = Store(settings.store_path, environment="test", clock=SystemClock())
    try:
        return len(store.history(settings.token_fp).recent(0.0))
    finally:
        store.close()


def test_probe_requests_land_in_the_log_the_tool_reads(tmp_path: Path) -> None:
    with shared_gate("test", TOKEN, data_dir=tmp_path) as gate:
        gate.before("firmy")
        gate.after(200)

    assert stamps_in_log(tmp_path, TOKEN) == 1


def test_the_tool_waits_because_of_what_the_probe_already_spent(tmp_path: Path) -> None:
    """Sedno zmiany: żądanie sondy realnie opóźnia następne żądanie narzędzia.

    Sam zapis do bazy niczego by nie dał, gdyby limiter narzędzia go nie czytał — więc
    asercja jest o czekaniu, nie o liczbie wierszy."""
    with shared_gate("test", TOKEN, data_dir=tmp_path) as gate:
        gate.before("firmy")
        gate.after(200)

    settings = Settings(token=TOKEN, environment="test", data_dir=tmp_path)
    profile = load_profile("test")
    store = Store(settings.store_path, environment="test", clock=SystemClock())
    czekania: list[float] = []

    class Recorder:
        def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
            czekania.append(seconds)

        def __getattr__(self, _name: str) -> object:
            return lambda *a, **k: None

    limiter = RateLimiter(
        windows=profile.rate.windows,
        min_spacing_s=profile.rate.min_spacing_s,
        cooldown_s=profile.rate.cooldown_s,
        clock=_NoSleepClock(),
        history=store.history(settings.token_fp),
        events=Recorder(),  # type: ignore[arg-type]
    )
    limiter.acquire("firmy")
    store.close()

    assert czekania, "narzędzie ruszyło natychmiast — nie zobaczyło żądania sondy"
    assert max(czekania) <= profile.rate.min_spacing_s


def test_a_different_token_has_its_own_budget(tmp_path: Path) -> None:
    """Historia jest kluczowana odciskiem tokenu — inny token to inny limit po stronie API."""
    with shared_gate("test", TOKEN, data_dir=tmp_path) as gate:
        gate.before("firmy")
        gate.after(200)

    inny = TOKEN.replace("podpis", "inny00")
    assert stamps_in_log(tmp_path, TOKEN) == 1
    assert stamps_in_log(tmp_path, inny) == 0


def test_the_probe_follows_the_data_dir_override(tmp_path: Path) -> None:
    """`CEIDG_DATA_DIR` przestawia bazę narzędzia. Sonda czytająca katalog domyślny
    trafiłaby do innego pliku i „wspólna historia" byłaby pustym słowem."""
    environ: Mapping[str, str] = {ENV_DATA_DIR: str(tmp_path / "gdzie_indziej")}
    assert _data_dir(environ) == tmp_path / "gdzie_indziej"
    assert _data_dir({}) != tmp_path / "gdzie_indziej"


class _NoSleepClock:
    """Zegar systemowy bez realnego snu — test nie ma czekać 3,75 s, żeby to udowodnić."""

    def __init__(self) -> None:
        self._offset = 0.0
        self._real = SystemClock()

    def monotonic(self) -> float:
        return self._real.monotonic() + self._offset

    def wall(self) -> float:
        return self._real.wall() + self._offset

    def sleep(self, seconds: float) -> None:
        self._offset += seconds


# --- polityka wyjścia w sondach ---------------------------------------------------------

# Sondy niosą ten sam token co narzędzie, a `urllib.request.urlopen` domyślnie wstawia
# `ProxyHandler()` czytający `HTTPS_PROXY` — dokładnie ta sama dziura, którą 2026-09-07
# zamknięto po stronie httpx (`ceidg_tool/httpclient.py`, UZUPELNIENIE_01 §B).


def test_the_probe_opener_ignores_a_proxy_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sonda pyta o host API, nie o pośrednika — mimo ustawionych zmiennych proxy.

    Poprawność jest tu subtelna: `ProxyHandler({})` nie rejestruje żadnej metody `*_open`,
    więc w `opener.handlers` go nie widać. Działa wyłącznie dlatego, że `build_opener`
    widzi instancję `ProxyHandler` i pomija domyślną. To jest ten rodzaj poprawności,
    który refaktor psuje bez śladu — stąd test na zachowaniu, a nie na liście handlerów.
    """
    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(variable, "http://proxy.zly.example.test:8080")
    asked: list[str] = []

    def record(host: object, *args: object, **kwargs: object) -> object:
        asked.append(str(host))
        raise socket.gaierror("zaślepka DNS: rozwiązywanie nazw wyłączone w testach")

    monkeypatch.setattr(socket, "getaddrinfo", record)
    request = urllib.request.Request(
        "https://dane.biznes.gov.pl/api/ceidg/v3/firmy?limit=1",
        headers={"Authorization": "Bearer tok"},
    )

    with pytest.raises(urllib.error.URLError):
        no_proxy_opener().open(request, timeout=1)

    assert asked == ["dane.biznes.gov.pl"]
    assert "proxy.zly.example.test" not in asked
