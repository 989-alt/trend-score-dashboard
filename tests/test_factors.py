from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.config import Settings
from backend.factors import build_candidate
from backend.schemas import OHLCVRow


def _rows(closes: list[int]) -> list[OHLCVRow]:
    """closes 리스트를 받아 OHLCVRow 목록 생성.

    변동성 하드필터(vol_band_low=0.20) 통과를 위해 일별 고/저를 ±2% 로 벌린다.
    이렇게 하면 annualized vol ≈ 0.02*√252 ≈ 0.32 로 [0.20, 0.60] 밴드 안에 들어간다.
    """
    out: list[OHLCVRow] = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        # 홀짝 교번으로 당일 종가를 ±2% 흔들어 vol 을 현실적으로 만든다.
        if i % 2 == 0:
            close = cd * Decimal("1.02")
        else:
            close = cd * Decimal("0.98")
        out.append(
            OHLCVRow(
                date=date(2024, 1, 1) + timedelta(days=i),
                open=cd,
                high=cd * Decimal("1.03"),
                low=cd * Decimal("0.97"),
                close=close,
                volume=Decimal("1000000"),
            )
        )
    return out


def test_build_candidate_eligible_uptrend() -> None:
    settings = Settings(data_mode="sample")
    rows = _rows(list(range(100, 360)))
    cand = build_candidate(
        ticker="000001",
        rows=rows,
        w52_high=None,
        index_momentum=Decimal("0"),
        turnover=Decimal("20000000000"),
        min_turnover=settings.min_turnover_krw,
        settings=settings,
    )
    assert cand.ticker == "000001"
    assert cand.eligible is True
    assert cand.above_ma200 is True
    assert cand.momentum > 0
    assert Decimal("0") <= cand.near_52w <= Decimal("1")


def test_build_candidate_ineligible_low_turnover() -> None:
    settings = Settings(data_mode="sample")
    rows = _rows(list(range(100, 360)))
    cand = build_candidate(
        ticker="000002",
        rows=rows,
        w52_high=None,
        index_momentum=Decimal("0"),
        turnover=Decimal("1000000000"),
        min_turnover=settings.min_turnover_krw,
        settings=settings,
    )
    assert cand.eligible is False
