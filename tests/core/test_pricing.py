"""Testy cennika i rozliczenia realnych tokenów (Design 2, ``core/domain/pricing.py``).

Czyste funkcje — bez I/O. Sprawdzamy sumowanie usage, przełączenie cennika po dacie
oraz mnożniki cache (read 10% wejścia, write 5-min 125%) i brak podwójnego liczenia.
"""
from __future__ import annotations

from datetime import date

import pytest

from workmate.core.domain.pricing import PRICING_SWITCH_DATE, TokenUsage, cost_usd


def test_token_usage_total_tokens():
    u = TokenUsage(
        input_tokens=100, output_tokens=20, cache_read_input_tokens=5, cache_creation_input_tokens=3
    )
    assert u.total_tokens == 128  # wszystko: 100 + 20 + 5 + 3


def test_token_usage_is_summable():
    s = TokenUsage(input_tokens=100, output_tokens=20) + TokenUsage(
        input_tokens=1, cache_read_input_tokens=2
    )
    assert s == TokenUsage(input_tokens=101, output_tokens=20, cache_read_input_tokens=2)


def test_cost_intro_pricing_before_switch_date():
    # Do 2026-08-31 włącznie: wejście $2/M, wyjście $10/M.
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd(u, on=date(2026, 8, 31)) == pytest.approx(12.0)  # 2 + 10


def test_cost_standard_pricing_from_switch_date():
    # Od 2026-09-01: wejście $3/M, wyjście $15/M.
    u = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd(u, on=PRICING_SWITCH_DATE) == pytest.approx(18.0)  # 3 + 15
    assert cost_usd(u, on=date(2026, 12, 31)) == pytest.approx(18.0)


def test_cost_cache_read_10pct_and_write_5min_125pct_of_input():
    # Przy cenie wprowadzającej ($2/M wejścia): read = 2*0.10, write = 2*1.25 (per 1M).
    u = TokenUsage(cache_read_input_tokens=1_000_000, cache_creation_input_tokens=1_000_000)
    # read: 1M*2*0.10 = 0.20 ; write 5-min: 1M*2*1.25 = 2.50 ; razem 2.70
    assert cost_usd(u, on=date(2026, 8, 1)) == pytest.approx(2.70)


def test_cost_does_not_double_count_input_and_cache():
    # ``input_tokens`` to tokeny NIECACHE'OWANE — cache liczone osobno (nie podwójnie).
    u = TokenUsage(input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    # input: 1M*2 = 2.00 ; cache read: 0.20 ; razem 2.20
    assert cost_usd(u, on=date(2026, 8, 1)) == pytest.approx(2.20)


def test_cost_of_empty_usage_is_zero():
    assert cost_usd(TokenUsage(), on=date(2026, 8, 1)) == 0.0
