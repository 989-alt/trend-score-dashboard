"""결정론 합성 패널 — 외부 API 없이 가드 단위검증용."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.panel import (
    AsOfFundamentals,
    Panel,
    TickerSeries,
)
from backend.schemas import OHLCVRow


def make_series(ticker: str, start: date, closes: list[int]) -> TickerSeries:
    rows = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        # 홀짝 ±1.5% 교번으로 종가를 흔들어 연환산 변동성을 [0.20,0.60] 밴드(≈0.48) 안에 둔다
        # → 합성 종목이 하드필터(변동성)를 통과해 적격 후보가 된다(빈 백테스트 방지).
        close = cd * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
        rows.append(
            OHLCVRow(
                date=start + timedelta(days=i),
                open=cd,
                high=cd * Decimal("1.025"),
                low=cd * Decimal("0.975"),
                close=close,
                volume=Decimal("1000000"),
            )
        )
    return TickerSeries(
        ticker=ticker,
        rows=rows,
        turnover_by_date={r.date: Decimal("20000000000") for r in rows},
    )


def make_panel() -> Panel:
    start = date(2023, 1, 2)
    a = make_series("000001", start, list(range(100, 360)))
    b = make_series("000002", start + timedelta(days=100), list(range(50, 210)))
    fundamentals = {
        "000001": [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                eps_growth=Decimal("0.15"),
            ),
            AsOfFundamentals(
                rcept_date=date(2024, 3, 31),
                roe=Decimal("0.12"),
                op_margin=Decimal("0.09"),
                eps_growth=Decimal("0.20"),
            ),
        ],
        "000002": [],
    }
    listings = {
        "000001": (start, None),
        "000002": (start + timedelta(days=100), None),
    }
    index_rows = make_series("KS11", start, list(range(2000, 2260))).rows
    return Panel(
        series={"000001": a, "000002": b},
        fundamentals=fundamentals,
        listings=listings,
        index_rows=index_rows,
    )
