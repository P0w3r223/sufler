"""Logowanie do Microsoft Graph przez device-code flow (delegowane, jako użytkownik).

- Pierwsze uruchomienie / po utracie tokenu: jednorazowe interaktywne logowanie
  ``powiadomienia-teams --login`` (kod + microsoft.com/devicelogin).
- W pętli usługi dostawca tokenu jest WYŁĄCZNIE CICHY (``acquire_token_silent`` z refresh-tokenu
  w cache) — NIGDY nie inicjuje interaktywnego device-flow, bo ten blokuje aż do zalogowania i
  w usłudze bez terminala zawiesiłby cały nasłuch.
- Utrata refresh-tokenu (rolling ~90 dni albo Conditional Access) → ``AuthExpiredError`` zamiast
  zawisu; orkiestracja loguje CRITICAL i zatrzymuje się czysto (patrz ``app.run_forever``).
- App MSAL i cache budowane RAZ w ``build_token_provider`` (nie co wywołanie) — token trzyma się
  w pamięci, plik czytany na starcie, zapisywany dopiero gdy refresh-token się zmieni.
- Cache tokenu jest SEKRETEM — poza repo i ``data/`` (patrz ``Settings.token_cache_path``).

Import ``msal`` jest LENIWY (w fabryce aplikacji), więc sam import modułu i testy wyższych warstw
go nie wymagają; ``app_factory`` jest wstrzykiwalny, więc testy podają atrapę bez ``msal``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from powiadomienia_teams.config import Settings

logger = logging.getLogger(__name__)


class AuthExpiredError(RuntimeError):
    """Utracono refresh-token — wymagane ponowne logowanie ``powiadomienia-teams --login``.

    Rzucany zamiast blokowania pętli usługi na interaktywnym device-code, żeby proces bez
    terminala nie zawisł na cichej próbie odświeżenia tokenu.
    """


def _load_cache(cache_path: Path) -> Any:
    import msal

    cache = msal.SerializableTokenCache()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    return cache


def _save_cache(cache: Any, cache_path: Path) -> None:
    """Zapisz cache tylko gdy MSAL zmienił stan (np. rotacja refresh-tokenu). chmod 600 (POSIX)."""
    if not getattr(cache, "has_state_changed", False):
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(cache.serialize())
    # Ogranicz dostęp do pliku (Linux/macOS; na Windows ignorowane).
    with contextlib.suppress(OSError):
        os.chmod(cache_path, 0o600)


def _default_app_and_cache(settings: Settings) -> tuple[Any, Any]:
    """Domyślna fabryka: ``PublicClientApplication`` + serializowalny cache (wymaga ``msal``)."""
    import msal

    cache = _load_cache(settings.token_cache_path)
    app = msal.PublicClientApplication(
        settings.client_id, authority=settings.authority, token_cache=cache
    )
    return app, cache


def build_token_provider(
    settings: Settings,
    *,
    app_factory: Callable[[Settings], tuple[Any, Any]] = _default_app_and_cache,
) -> Callable[[], str]:
    """Zbuduj dostawcę tokenu SILENT-ONLY (cichy refresh; utrata tokenu → ``AuthExpiredError``).

    App i cache budowane RAZ (``app_factory`` — wstrzykiwalny do testów bez ``msal``). ``get_token``
    zwraca token z cichego odświeżenia; przy braku ważnego tokenu rzuca ``AuthExpiredError`` (nie
    inicjuje device-flow — to robi tylko ``login_interactive`` ze startu).
    """
    app, cache = app_factory(settings)
    scopes = list(settings.scopes)
    cache_path = settings.token_cache_path

    def get_token() -> str:
        accounts = app.get_accounts()
        result = app.acquire_token_silent(scopes, account=accounts[0]) if accounts else None
        _save_cache(cache, cache_path)  # utrwal ewentualnie zrotowany refresh-token
        if not result or "access_token" not in result:
            raise AuthExpiredError(
                "Utracono refresh-token — zaloguj się ponownie: `powiadomienia-teams --login`."
            )
        return str(result["access_token"])

    return get_token


def login_interactive(
    settings: Settings,
    *,
    app_factory: Callable[[Settings], tuple[Any, Any]] = _default_app_and_cache,
) -> None:
    """Jednorazowe interaktywne logowanie device-code (kod + microsoft.com/devicelogin).

    Wołane WYŁĄCZNIE ze startu (``--login`` lub pierwszy start z terminalem), NIGDY z pętli usługi —
    ``acquire_token_by_device_flow`` blokuje aż do zalogowania. Po sukcesie zapisuje refresh-token
    do cache, więc dalsze działanie idzie już cichym odświeżeniem.
    """
    app, cache = app_factory(settings)
    flow = app.initiate_device_flow(scopes=list(settings.scopes))
    if "user_code" not in flow:
        raise RuntimeError(
            "Nie udało się rozpocząć device flow: " + json.dumps(flow, ensure_ascii=False)
        )
    print(flow["message"])  # „wejdź na adres i wpisz kod"
    sys.stdout.flush()
    result = app.acquire_token_by_device_flow(flow)  # blokuje aż do zalogowania
    _save_cache(cache, settings.token_cache_path)
    if not result or "access_token" not in result:
        error = (result or {}).get("error")
        description = (result or {}).get("error_description")
        raise RuntimeError(f"Logowanie nie powiodło się: {error} — {description}")
