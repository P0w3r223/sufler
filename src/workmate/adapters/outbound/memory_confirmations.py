"""Rejestr zapowiedzianych mutacji w pamięci procesu (port ``ConfirmationLedger``, ADR 0065).

W pamięci, nie na dysku — i to jest wybór, nie skrót. Zapowiedź ma żyć minuty, w obrębie jednej
rozmowy, i wygasać sama. Trwały zapis dawałby stan, który przeżywa restart i wdrożenie: pytanie
„czy potwierdzić skasowanie", zadane wczoraj, autoryzowałoby operację dzisiaj. Utrata rejestru
przy restarcie jest tu ZACHOWANIEM POŻĄDANYM — mutacja wraca wtedy do punktu wyjścia, czyli do
ponownej zapowiedzi. Fail-closed z definicji.

Zegar wstrzykiwany, bo rdzeń go nie woła, a test nie ma czekać w realnym czasie.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

# Ile żyje zapowiedź. Dość, żeby człowiek zdążył przeczytać i odpowiedzieć; za mało, żeby
# zapomniana rozmowa autoryzowała cokolwiek po przerwie na obiad.
_DEFAULT_TTL = timedelta(minutes=15)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryConfirmations:
    """Zapowiedzi z czasem życia; bez wpisu = brak zgody."""

    def __init__(
        self,
        *,
        ttl: timedelta = _DEFAULT_TTL,
        clock: Callable[[], datetime] = _utcnow,
        max_entries: int = 512,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        # Sufit wpisów jest backstopem przed rośnięciem bez końca: rejestr karmi go model, więc
        # bez limitu wystarczyłaby pętla proszenia o mutacje, żeby rozdąć pamięć procesu drzwi.
        self._max = max_entries
        # klucz → (kiedy wygasa, token tury, w której padła zapowiedź)
        self._entries: dict[str, tuple[datetime, str]] = {}
        self._lock = threading.Lock()

    def turn_of(self, key: str) -> str | None:
        with self._lock:
            self._prune()
            wpis = self._entries.get(key)
            return wpis[1] if wpis is not None else None

    def remember(self, key: str, turn_token: str) -> None:
        with self._lock:
            self._prune()
            if len(self._entries) >= self._max and key not in self._entries:
                # Przepełnienie: usuwamy NAJSTARSZY wpis. Odrzucenie nowego byłoby gorsze —
                # zamiast zapomnieć starą zapowiedź, zablokowałoby bieżącą rozmowę.
                najstarszy = min(self._entries, key=lambda k: self._entries[k][0])
                del self._entries[najstarszy]
            self._entries[key] = (self._clock() + self._ttl, turn_token)

    def forget(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def _prune(self) -> None:
        """Usuń wpisy wygasłe — wołane pod zamkiem, przy każdym dotknięciu rejestru."""
        teraz = self._clock()
        for key in [k for k, (wygasa, _tura) in self._entries.items() if wygasa <= teraz]:
            del self._entries[key]
