"""알파 가격 팩터 단위검증 — trend_template·ma_alignment·mom_12_1·volume_surge·
atr20_over_price·vol_dryup.

순수·결정론 산식. Decimal 전용(float 금지). 강한 추세 합성은
``tests.fixtures.backtest_synth.make_series`` 를, 정밀 수치 단언은 명시적
``OHLCVRow`` 리스트를 직접 구성해 손계산값과 대조한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.schemas import OHLCVRow
from backend.scoring import (
    atr20_over_price,
    ma_alignment,
    mom_12_1,
    trend_template,
    vol_dryup,
    volume_surge,
)
from tests.fixtures.backtest_synth import make_series

_START = date(2022, 1, 3)


def _rows_with_volumes(volumes: list[int]) -> list[OHLCVRow]:
    """거래량 시퀀스만 다른 평탄 가격 행 — volume 계열 팩터 검증용."""
    out: list[OHLCVRow] = []
    for i, v in enumerate(volumes):
        out.append(
            OHLCVRow(
                date=_START + timedelta(days=i),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal(v),
            )
        )
    return out


# ---------------------------------------------------------------------------
# trend_template
# ---------------------------------------------------------------------------


def test_trend_template_strong_uptrend() -> None:
    rows = make_series("X", _START, list(range(100, 460))).rows
    assert trend_template(rows) >= Decimal("6") / Decimal("8")


def test_trend_template_short_data_is_zero() -> None:
    rows = make_series("X", _START, list(range(100, 250))).rows  # < 200
    assert len(rows) < 200
    assert trend_template(rows) == Decimal("0")


# ---------------------------------------------------------------------------
# ma_alignment
# ---------------------------------------------------------------------------


def test_ma_alignment_full_uptrend_is_one() -> None:
    rows = make_series("X", _START, list(range(100, 460))).rows
    assert ma_alignment(rows) == Decimal("1")


def test_ma_alignment_insufficient_data_partial() -> None:
    rows = make_series("X", _START, list(range(100, 130))).rows  # 30 rows
    result = ma_alignment(rows)
    # ma50/ma150/ma200 모두 None → 그 세 조건 False. p>ma50 도 None → False.
    # 단 크래시 없이 0~1 범위의 결정론 값.
    assert Decimal("0") <= result <= Decimal("1")
    assert result == Decimal("0")


# ---------------------------------------------------------------------------
# mom_12_1
# ---------------------------------------------------------------------------


def test_mom_12_1_rising_is_positive() -> None:
    rows = make_series("X", _START, list(range(100, 460))).rows
    assert mom_12_1(rows) > Decimal("0")


def test_mom_12_1_short_data_is_zero() -> None:
    rows = make_series("X", _START, list(range(100, 300))).rows  # < 252
    assert len(rows) < 252
    assert mom_12_1(rows) == Decimal("0")


def test_mom_12_1_exact_value() -> None:
    # 평탄 OHLC, close 만 제어해 c_recent / c_then - 1 을 정확히 단언.
    closes = [100] * 300
    closes[-21] = 120  # c_recent
    closes[-252] = 100  # c_then
    rows: list[OHLCVRow] = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        rows.append(
            OHLCVRow(
                date=_START + timedelta(days=i),
                open=cd,
                high=cd,
                low=cd,
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    # 120 / 100 - 1 = 0.2
    assert mom_12_1(rows) == Decimal("0.2")


# ---------------------------------------------------------------------------
# volume_surge
# ---------------------------------------------------------------------------


def test_volume_surge_triple_average_is_one() -> None:
    # SMA(vol,20)=최근 20봉(마지막 포함) 평균. 마지막 봉이 그 평균의 정확히 3배가
    # 되도록 구성: 앞 19봉=17, 마지막=57 → 평균=(19*17+57)/20=380/20=19, 57/19=3배,
    # (3-1)/2 = 1.0.
    volumes = [17] * 19 + [57]
    rows = _rows_with_volumes(volumes)
    assert volume_surge(rows) == Decimal("1")


def test_volume_surge_flat_is_zero() -> None:
    rows = _rows_with_volumes([100] * 25)
    assert volume_surge(rows) == Decimal("0")


def test_volume_surge_insufficient_data_is_zero() -> None:
    rows = _rows_with_volumes([100] * 19)  # < 20
    assert volume_surge(rows) == Decimal("0")


def test_volume_surge_clamped_upper() -> None:
    # 마지막 봉이 평균의 ~19.6배 → (19.6-1)/2 ≈ 9.3 → 상한 1.0 으로 클램프.
    volumes = [1] * 19 + [1000]
    rows = _rows_with_volumes(volumes)
    assert volume_surge(rows) == Decimal("1")


def test_volume_surge_partial_value() -> None:
    # 마지막 봉이 평균의 정확히 2배: 앞 19봉=9, 마지막=19 → 평균=(19*9+19)/20=190/20=9.5,
    # 19/9.5=2배, (2-1)/2 = 0.5.
    volumes = [9] * 19 + [19]
    rows = _rows_with_volumes(volumes)
    assert volume_surge(rows) == Decimal("0.5")


# ---------------------------------------------------------------------------
# atr20_over_price — 손계산 명시 단언
# ---------------------------------------------------------------------------


def test_atr20_over_price_exact() -> None:
    # 21봉: close = 100,101,...,120. high=close+2, low=close-2.
    # 각 봉 i>=1 의 TR = max(high-low=4, |high-close_prev|=3, |low-close_prev|=1) = 4.
    # ATR20 = 20개 TR 평균 = 4. price = 120. atr20/price = 4/120 = 1/30.
    rows: list[OHLCVRow] = []
    for i in range(21):
        cd = Decimal(100 + i)
        rows.append(
            OHLCVRow(
                date=_START + timedelta(days=i),
                open=cd,
                high=cd + Decimal("2"),
                low=cd - Decimal("2"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    expected = Decimal("4") / Decimal("120")
    assert atr20_over_price(rows) == expected


def test_atr20_over_price_insufficient_data_is_zero() -> None:
    rows: list[OHLCVRow] = []
    for i in range(20):  # < 21 → 20개 TR 불가
        cd = Decimal(100 + i)
        rows.append(
            OHLCVRow(
                date=_START + timedelta(days=i),
                open=cd,
                high=cd + Decimal("2"),
                low=cd - Decimal("2"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    assert atr20_over_price(rows) == Decimal("0")


# ---------------------------------------------------------------------------
# vol_dryup
# ---------------------------------------------------------------------------


def test_vol_dryup_declining_below_one() -> None:
    # 앞 45봉 거래량 1000, 최근 5봉 200 → sma5=200, sma50=(45*1000+5*200)/50=920 → <1
    volumes = [1000] * 45 + [200] * 5
    rows = _rows_with_volumes(volumes)
    assert vol_dryup(rows) < Decimal("1")


def test_vol_dryup_insufficient_data_is_zero() -> None:
    rows = _rows_with_volumes([1000] * 49)  # < 50
    assert vol_dryup(rows) == Decimal("0")


def test_vol_dryup_exact_value() -> None:
    # sma5 = mean(last 5) = 200; sma50 = (45*1000 + 5*200)/50 = 46000/50 = 920.
    volumes = [1000] * 45 + [200] * 5
    rows = _rows_with_volumes(volumes)
    assert vol_dryup(rows) == Decimal("200") / Decimal("920")
