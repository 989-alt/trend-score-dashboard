"""공유 순수 스코어러 — rows(≤T)+w52+지수모멘텀 → Candidate.

engine._collect_raw 와 backtest 가 동일 팩터 로직을 공유하기 위한 추출.
provider I/O 는 호출 측이 담당하고, 본 함수는 순수 계산만 한다(결정론).
"""

from __future__ import annotations

from decimal import Decimal

from backend import scoring as sc
from backend.config import Settings
from backend.schemas import Market, OHLCVRow


def build_candidate(
    *,
    ticker: str,
    rows: list[OHLCVRow],
    w52_high: Decimal | None,
    index_momentum: Decimal,
    turnover: Decimal,
    min_turnover: Decimal,
    settings: Settings,
    market: Market = "KR",
) -> sc.Candidate:
    """rows(오름차순, ≤T)로부터 팩터를 계산해 Candidate 조립."""
    recent = rows[-settings.lookback_days :]
    momentum = sc.compute_momentum(recent)
    rs = momentum - index_momentum
    volatility = sc.compute_annualized_volatility(recent)
    near_52w = sc.proximity_to_52w_high(rows, high_52w=w52_high)
    has_pp = sc.pocket_pivot(rows, lookback=settings.pocket_pivot_lookback)
    above = sc.above_ma200(rows, settings.ma200_window)
    eligible = sc.passes_hard_filter(
        turnover=turnover,
        momentum=momentum,
        volatility=volatility,
        near_52w=near_52w,
        above_ma200_flag=above,
        min_turnover=min_turnover,
        settings=settings,
    )
    return sc.Candidate(
        ticker=ticker,
        turnover=turnover,
        momentum=momentum,
        rs=rs,
        volatility=volatility,
        near_52w=near_52w,
        has_pocket_pivot=has_pp,
        above_ma200=above,
        eligible=eligible,
    )


__all__ = ["build_candidate"]
