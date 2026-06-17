from decimal import Decimal

from backend.backtest.metrics import portfolio_metrics


def test_portfolio_metrics_basic() -> None:
    nav = [Decimal("1"), Decimal("1.1"), Decimal("0.99"), Decimal("1.05")]
    rets = [Decimal("0.1"), Decimal("-0.1"), Decimal("0.0606")]
    m = portfolio_metrics(nav, rets, periods_per_year=12)
    assert m["mdd"] <= 0  # 최대낙폭 ≤ 0
    assert "sharpe" in m and "calmar" in m and "cagr" in m
    assert all(isinstance(v, Decimal) for v in m.values())
