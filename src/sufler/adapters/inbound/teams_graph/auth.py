"""Logowanie do Microsoft Graph przez device-code flow (delegowane, jako użytkownik).

- Pierwsze uruchomienie: logujesz się RAZ w przeglądarce (kod + microsoft.com/devicelogin).
- Kolejne: ``acquire_token_silent`` odświeża token po cichu z refresh tokenu z cache.
- Cache tokenu jest SEKRETEM — trzymany poza repo i ``data/`` (patrz ``TeamsGraphSettings``).

Import ``msal`` jest LENIWY (w ``build_token_provider``), więc sam import modułu i testy
wyższych warstw nie wymagają extra ``teams-graph``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    import msal  # msal nie dostarcza py.typed/stubów (patrz [[tool.mypy.overrides]] w pyproject)

    class TokenProviderSettings(Protocol):
        """Strukturalny kontrakt konfiguracji dostawcy tokenu MSAL (delegowany, single-tenant).

        Spełniają go ``TeamsGraphSettings`` (drzwi kanałowe) i ``TeamsPushSettings`` (push, ADR
        0022) — jeden builder tokenu obsługuje oba. Pola jako read-only ``@property``, bo settings
        to zamrożone dataklasy (atrybuty tylko do odczytu).
        """

        @property
        def client_id(self) -> str: ...

        @property
        def scopes(self) -> tuple[str, ...]: ...

        @property
        def token_cache_path(self) -> Path: ...

        @property
        def authority(self) -> str: ...


logger = logging.getLogger(__name__)


def build_token_provider(settings: TokenProviderSettings) -> Callable[[], str]:
    """Zbuduj dostawcę tokenu: cichy refresh z cache, device-code przy pierwszym użyciu.

    Zwraca synchroniczną funkcję ``() -> str`` (MSAL jest synchroniczny) — warstwa Graph
    woła ją w puli wątków, żeby nie blokować pętli async.
    """
    import msal

    cache_path = settings.token_cache_path
    scopes = list(settings.scopes)

    def _load_cache() -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        if cache_path.exists():
            cache.deserialize(cache_path.read_text())
        return cache

    def _save_cache(cache: msal.SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Atomowy zapis (temp + os.replace, wzorem github/state.py — R1): docker stop w trakcie
        # write_text zostawiał ucięty cache, który MSAL nie potrafi deserializować. Uprawnienia
        # ograniczone NA TYMCZASOWYM pliku, PRZED podmianą — plik nigdy nie leży pod docelową
        # nazwą z szerszymi uprawnieniami niż docelowe, nawet przez chwilę.
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        tmp.write_text(cache.serialize())
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)  # Linux/macOS; na Windows ignorowane.
        os.replace(tmp, cache_path)

    def get_token() -> str:
        cache = _load_cache()
        app = msal.PublicClientApplication(
            settings.client_id, authority=settings.authority, token_cache=cache
        )

        result: dict[str, Any] | None = None
        accounts = app.get_accounts()
        if accounts:
            # Znane konto → MSAL odświeży token po cichu bez pytania.
            result = app.acquire_token_silent(scopes, account=accounts[0])

        if not result:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise RuntimeError(
                    "Nie udało się rozpocząć device flow: " + json.dumps(flow, ensure_ascii=False)
                )
            print(flow["message"])  # „wejdź na adres i wpisz kod"
            sys.stdout.flush()
            result = app.acquire_token_by_device_flow(flow)  # blokuje aż do zalogowania

        _save_cache(cache)

        if not result or "access_token" not in result:
            error = (result or {}).get("error")
            description = (result or {}).get("error_description")
            raise RuntimeError(f"Logowanie nie powiodło się: {error} — {description}")
        return str(result["access_token"])

    return get_token
