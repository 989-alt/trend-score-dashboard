from __future__ import annotations

from decimal import Decimal

from backend.backtest.metrics import (
    cagr,
    max_adverse_excursion,
    max_drawdown,
    spearman_monotonicity,
    win_rate,
)


def test_spearman_perfect_monotonic() -> None:
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (-2, -1, 0, 1, 2)]
    assert spearman_monotonicity(scores, fwd) == Decimal("1")


def test_spearman_inverted() -> None:
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (5, 4, 3, 2, 1)]
    assert spearman_monotonicity(scores, fwd) == Decimal("-1")


def test_mae_is_worst_drop_within_horizon() -> None:
    path = [Decimal(x) for x in (102, 95, 110, 90, 105)]
    assert max_adverse_excursion(Decimal("100"), path) == Decimal("-0.10")


def test_max_drawdown() -> None:
    nav = [Decimal(x) for x in (100, 120, 80, 130)]
    assert max_drawdown(nav).quantize(Decimal("0.0001")) == Decimal("-0.3333")


def test_win_rate() -> None:
    fwd = [Decimal("0.1"), Decimal("-0.2"), Decimal("0.0"), Decimal("0.3")]
    assert win_rate(fwd) == Decimal("0.5")


def test_cagr_two_years_doubling() -> None:
    assert cagr(Decimal("100"), Decimal("200"), years=Decimal("2")).quantize(
        Decimal("0.0001")
    ) == Decimal("0.4142")
