"""Taksonomia wyjątków narzędzia.

Skopiowana z `ceidg-tool/ceidg_tool/errors.py` (kopia z 2026-09-10) i okrojona.

Kody wyjścia: 1 = błąd nieodwracalny w tym uruchomieniu, 3 = błąd konfiguracji.
**Kodu 2 („wznawialny") tu nie ma i nie ma go z powodu**: wznowienie miało sens przy
pobieraniu z rejestru, które mogło paść w połowie. Etap 1 czyta plik — albo się udaje, albo
nie. Wynik pośredni nie istnieje, więc harmonogram nie ma czego ponawiać.

Z tego samego powodu nie ma tu członów po API (`AuthError`, `BadRequestError`, `ServerError`,
`TransportError`, `RateLimitError`, `PagingRunawayError`, `UntrustedLinkError`,
`ProfileMismatchError`). Słownik po ruchu, którego się nie wykonuje, czyta się jak
twierdzenie o tym ruchu — a `docs/niezmierzone.md` mówi wprost, że żadnego takiego
twierdzenia ten projekt nie ma prawa postawić.
"""

from __future__ import annotations


class KrsError(Exception):
    """Bazowy wyjątek narzędzia. Komunikat jest przeznaczony dla użytkownika."""

    exit_code: int = 1


class ConfigError(KrsError):
    """Zła ścieżka, nieczytelna konfiguracja."""

    exit_code = 3


class OdpisNieczytelnyError(KrsError):
    """Plik nie daje się wczytać albo nie jest odpisem."""


class NieznanyKsztaltOdpisuError(KrsError):
    """Odpis wczytany, ale brakuje w nim pól, na których stoi cała reszta.

    Osobno od `OdpisNieczytelnyError`, bo to dwie różne wiadomości dla operatora: tam plik jest
    zły, tu plik jest dobry, a nasze założenia o rejestrze są niepełne. Drugie kończy się
    wpisem w `docs/niezmierzone.md`, pierwsze nie.
    """


class BrakZrodlaPublicznegoError(KrsError):
    """Podmiot nie ma wpisu w rejestrze przedsiębiorców KRS.

    To NIE jest „brak sprawozdania". Sprawozdania podmiotów spoza rejestru trafiają do Szefa
    KAS i są objęte tajemnicą skarbową, więc jedyne prawdziwe zdanie brzmi „brak źródła
    publicznego". Pomylenie tych dwóch było jednym z pierwszych ustaleń rozpoznania.
    """


class NieodtwarzalneZZachowanychError(KrsError):
    """Żądanie odtworzenia oceny, której ładunki zostały już usunięte z retencji."""
