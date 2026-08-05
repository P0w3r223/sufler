"""Cichy token dostępu z CUDZEGO cache MSAL (grafik Shifts, ADR 0056) — WYŁĄCZNIE odczyt.

Cache tokenu należy do bota powiadomienia-teams i jest montowany RO. Pożyczamy z niego refresh-token
i wymieniamy go po cichu (``acquire_token_silent``) na token do Microsoft Graph z zakresem
``Schedule.Read.All``. Trzy nienaruszalne zasady:

  1. NIGDY nie zapisujemy cache (mont jest RO, a i tak ``has_state_changed`` ignorujemy) — to nie
     nasz stan; zapis nadpisałby refresh-token tamtego bota.
  2. Deserializujemy przy KAŻDYM wywołaniu — tamten proces rotuje refresh-token, a my chcemy zawsze
     świeży, nie zamrożony z chwili startu.
  3. Brak cichego tokenu (brak konta, brak zgody, wygasła sesja) → ``ScheduleReadError`` z czytelną
     podpowiedzią, NIE device-code (nie mamy tu interakcji) i NIE traceback.
"""

from __future__ import annotations

from collections.abc import Callable

from workmate.config import ScheduleSettings
from workmate.core.errors import ScheduleReadError


def build_silent_token_provider(settings: ScheduleSettings) -> Callable[[], str]:
    """Zbuduj funkcję zwracającą świeży token Graph z cudzego cache MSAL (odczyt, bez zapisu)."""

    def get_token() -> str:
        import msal

        cache = msal.SerializableTokenCache()
        try:
            cache.deserialize(settings.token_cache_path.read_text())
        except (OSError, ValueError) as exc:
            raise ScheduleReadError(
                "Grafik jest chwilowo niedostępny: nie mogę odczytać pamięci logowania "
                f"({settings.token_cache_path}). Sprawdź, czy wolumen powiadomienia-teams jest "
                "zamontowany."
            ) from exc

        app = msal.PublicClientApplication(
            settings.client_id, authority=settings.authority, token_cache=cache
        )
        accounts = app.get_accounts()
        result = (
            app.acquire_token_silent(list(settings.scopes), account=accounts[0])
            if accounts
            else None
        )
        # ŚWIADOMIE bez ``cache.serialize()`` — plik jest cudzy i RO; nasz odczyt niczego nie
        # zmienia.
        if not result or "access_token" not in result:
            raise ScheduleReadError(
                "Grafik jest chwilowo niedostępny: ciche logowanie się nie powiodło — najpewniej "
                "aplikacja nie ma zgody na odczyt grafiku (Schedule.Read.All) albo sesja bota "
                "powiadomienia-teams wygasła i trzeba go zalogować ponownie."
            )
        return str(result["access_token"])

    return get_token
