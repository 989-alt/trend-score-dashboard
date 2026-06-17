from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.regime import count_distribution_days, is_risk_off
from backend.schemas import OHLCVRow


def _row(d: date, close: int, vol: int) -> OHLCVRow:
    c = Decimal(close)
    return OHLCVRow(date=d, open=c, high=c, low=c, close=c, volume=Decimal(vol))


def _series(specs: list[tuple[int, int]]) -> list[OHLCVRow]:
    start = date(2023, 1, 2)
    return [_row(start + timedelta(days=i), c, v) for i, (c, v) in enumerate(specs)]


def test_count_distribution_days_detects_down_on_higher_volume() -> None:
    # 분산일 = 종가 ≤ 전일×0.998 AND 거래량 > 전일.
    rows = _series([(100, 100), (99, 120), (101, 130), (98, 140), (97, 90)])
    # day1: 99 ≤ 100*0.998=99.8 ✓ & 120>100 ✓ → 분산일
    # day2: 101 > 100.8 ✗ → 아님
    # day3: 98 ≤ 100.798 ✓ & 140>130 ✓ → 분산일
    # day4: 97 ≤ 97.804 ✓ & 90>140 ✗ → 아님(거래량 감소)
    assert count_distribution_days(rows, window=4, drop=Decimal("0.998")) == 2


def test_is_risk_off_threshold() -> None:
    rows = _series([(100, 100), (99, 120), (98, 130), (97, 140), (96, 150), (95, 160)])
    # day1..5 모두 분산일(하락+거래량증가) → 최근5일 분산일 5회
    assert is_risk_off(rows, window=5, threshold=5, drop=Decimal("0.998")) is True
    assert is_risk_off(rows, window=5, threshold=6, drop=Decimal("0.998")) is False


def test_is_risk_off_insufficient_rows_false() -> None:
    assert (
        is_risk_off(_series([(100, 100)]), window=25, threshold=5, drop=Decimal("0.998")) is False
    )
