"""Taksonomia wyjątków narzędzia.

Kody wyjścia: 1 = błąd nieodwracalny w tym uruchomieniu, 2 = błąd wznawialny
(harmonogram może ponowić), 3 = błąd konfiguracji lub autoryzacji.
Szczegóły: docs/design/phase2_core.md.
"""

from __future__ import annotations


class CeidgError(Exception):
    """Bazowy wyjątek narzędzia. Komunikat jest przeznaczony dla użytkownika."""

    exit_code: int = 1


class ConfigError(CeidgError):
    """Brak tokenu, zła ścieżka, niepoprawny profil."""

    exit_code = 3


class ProdWithoutConsentError(ConfigError):
    """Żądanie środowiska produkcyjnego bez jawnej zgody użytkownika."""


class AuthError(CeidgError):
    """401 / 403 — token odrzucony; bez ponawiania."""

    exit_code = 3


class BadRequestError(CeidgError):
    """400 — niepoprawnie skonstruowane zapytanie; bez ponawiania."""


class NotFoundError(CeidgError):
    """404 — zasób nie istnieje."""


class UntrustedLinkError(CeidgError):
    """`links.next` wskazuje na obcy host — nie wysyłamy tam tokenu."""


class PagingRunawayError(CeidgError):
    """Przekroczony twardy limit stron — ochrona przed nieskończoną pętlą."""


class ProfileMismatchError(CeidgError):
    """Wznowienie na innym profilu API lub innych kryteriach niż pobranie."""


class StoreError(CeidgError):
    """Niespójność bazy lokalnej (brak runu, brak `id` w rekordzie)."""


class StoreLockedError(StoreError):
    """Inny proces pobiera na tym samym środowisku."""


class ResumableError(CeidgError):
    """Błąd, po którym warto wznowić (`ceidg-tool wznow`)."""

    exit_code = 2


class ServerError(ResumableError):
    """5xx po wyczerpaniu prób."""


class TransportError(ResumableError):
    """Timeout, DNS, zerwane połączenie."""


class RateLimitError(ResumableError):
    """429 mimo limitera — po odczekaniu pełnej blokady nadal odrzucane."""


class ExportError(CeidgError):
    """Nie da się zapisać skoroszytu (np. przekroczony limit wierszy Excela)."""
