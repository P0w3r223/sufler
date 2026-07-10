"""Cennik i rzeczywiste rozliczenie tokenów rozmów (claude-sonnet-5).

Rzeczywista liczba tokenów pochodzi z pola ``usage`` odpowiedzi Claude API
(input/output + cache), nie z estymaty (Design 2). Stawki i data zmiany ceny to
stałe konfiguracyjne — jedno miejsce edycji przy zmianie cennika.

Czysta domena: bez I/O, bez SDK. ``TokenUsage`` przenosi surowe liczby z ``usage``
przez porty do rdzenia i magazynu; ``cost_usd`` wylicza koszt wg cennika z danego dnia.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Cennik claude-sonnet-5 (USD za 1M tokenów). Cena WPROWADZAJĄCA obowiązuje DO
# 2026-08-31 włącznie; od 2026-09-01 stawki standardowe.
PRICING_SWITCH_DATE = date(2026, 9, 1)
PRICE_INPUT_INTRO_PER_M = 2.0
PRICE_OUTPUT_INTRO_PER_M = 10.0
PRICE_INPUT_STD_PER_M = 3.0
PRICE_OUTPUT_STD_PER_M = 15.0
# Prompt caching: odczyt z cache = 10% ceny wejścia; zapis 5-min = 125% ceny wejścia.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_5M_MULTIPLIER = 1.25
_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class TokenUsage:
    """Rzeczywiste użycie tokenów z pola ``usage`` odpowiedzi API (jedno wywołanie).

    ``input_tokens`` to tokeny NIECACHE'OWANE (pełna cena); cache liczone osobno,
    by nie liczyć podwójnie (por. ``cost_usd``). Sumowalne (``+``) — jedna tura runtime
    to bywa wiele wywołań API (pętla tool-use).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )

    @property
    def total_tokens(self) -> int:
        """Rzeczywista suma tokenów (wejście niecache'owane + cache + wyjście)."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )


def _rates(on: date) -> tuple[float, float]:
    """(cena wejścia, cena wyjścia) za 1M wg cennika obowiązującego w dniu ``on``."""
    if on < PRICING_SWITCH_DATE:
        return PRICE_INPUT_INTRO_PER_M, PRICE_OUTPUT_INTRO_PER_M
    return PRICE_INPUT_STD_PER_M, PRICE_OUTPUT_STD_PER_M


def cost_usd(usage: TokenUsage, *, on: date) -> float:
    """Koszt w USD dla danego ``usage`` wg cennika z dnia ``on``.

    ``input_tokens`` (niecache'owane) po pełnej cenie wejścia; ``output_tokens`` po
    cenie wyjścia; cache osobno: odczyt 10% ceny wejścia, zapis 5-min 125%.
    """
    price_in, price_out = _rates(on)
    return (
        usage.input_tokens * price_in
        + usage.output_tokens * price_out
        + usage.cache_read_input_tokens * price_in * CACHE_READ_MULTIPLIER
        + usage.cache_creation_input_tokens * price_in * CACHE_WRITE_5M_MULTIPLIER
    ) / _PER_MILLION
