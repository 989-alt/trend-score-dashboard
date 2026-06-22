"""레짐 엔진 단위테스트 — 합성 지수 일봉으로 3대 레짐 + 경계(히스테리시스) 검증. 네트워크 0."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.regime import classify_regime
from backend.schemas import OHLCVRow


def _rows(closes: list[float]) -> list[OHLCVRow]:
    """종가 리스트 → OHLCVRow(오래된→최신). high/low 는 종가 ±1%, open=직전 종가."""
    out: list[OHLCVRow] = []
    d0 = date(2025, 1, 1)
    prev = closes[0]
    for i, c in enumerate(closes):
        cd = Decimal(str(c))
        out.append(
            OHLCVRow(
                date=d0 + timedelta(days=i),
                open=Decimal(str(prev)),
                high=cd * Decimal("1.01"),
                low=cd * Decimal("0.99"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
        prev = c
    return out


def test_uptrend_above_ma200_strong_adx_is_up_trend() -> None:
    """꾸준한 상승(고 ADX·MA200 위) → UP_TREND."""
    rows = _rows([100 + i for i in range(260)])
    r = classify_regime(rows)
    assert r.regime == "UP_TREND"
    assert r.above_ma200 is True
    assert r.adx is not None and r.adx >= Decimal("25")


def test_downtrend_below_ma200_strong_adx_is_down() -> None:
    """꾸준한 하락(고 ADX·MA200 아래) → DOWN."""
    rows = _rows([400 - i for i in range(260)])
    r = classify_regime(rows)
    assert r.regime == "DOWN"
    assert r.above_ma200 is False


def test_choppy_low_adx_is_chop_vol() -> None:
    """등락 반복(저 ADX) → CHOP_VOL."""
    rows = _rows([200 + (8 if i % 2 else -8) for i in range(260)])
    r = classify_regime(rows)
    assert r.regime == "CHOP_VOL"
    assert r.adx is not None and r.adx < Decimal("20")


def test_too_few_rows_is_unknown() -> None:
    """MA200 산정 불가(봉 부족) → UNKNOWN."""
    assert classify_regime(_rows([100 + i for i in range(50)])).regime == "UNKNOWN"


def test_boundary_keeps_previous_regime_hysteresis() -> None:
    """ADX 가 chop~trend 경계면 직전 레짐 유지(prev 없으면 CHOP_VOL)."""
    rows = _rows([200 + (8 if i % 2 else -8) for i in range(260)])
    # 임계값을 ADX 가능범위(0~100) 밖으로 벌려 ADX 를 확실히 경계 구간(chop~trend)에 둔다.
    wide = {"adx_trend": Decimal("101"), "adx_chop": Decimal("-1")}
    assert classify_regime(rows, prev="DOWN", **wide).regime == "DOWN"
    assert classify_regime(rows, prev=None, **wide).regime == "CHOP_VOL"
