from __future__ import annotations

from decimal import Decimal

from backend.backtest.universe import top_by_turnover


def test_top_by_turnover_ranks_and_caps():
    codes = ["A", "B", "C", "D"]
    turn = {"A": Decimal("10"), "B": Decimal("40"), "C": Decimal("30"), "D": Decimal("0")}
    out = top_by_turnover(codes, lambda c: turn[c], top_n=2)
    assert out == ["B", "C"]  # 거래대금 내림차순, 0/결측 제외, 상위 2
