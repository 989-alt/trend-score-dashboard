"""인버스 슬리브 단위테스트 — 합성 지수로 DOWN 진입·하락수익·트레일링손절. 네트워크 0."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.schemas import OHLCVRow
from backend.sleeves.inverse import InverseParams, simulate_inverse

_VALID = {"regime", "stop", "eod"}


def _rows(closes: list[float]) -> list[OHLCVRow]:
    out: list[OHLCVRow] = []
    d0 = date(2025, 1, 1)
    for i, c in enumerate(closes):
        cd = Decimal(str(c))
        out.append(
            OHLCVRow(
                date=d0 + timedelta(days=i),
                open=cd,
                high=cd * Decimal("1.01"),
                low=cd * Decimal("0.99"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    return out


def test_inverse_profits_in_downtrend() -> None:
    """추세 하락(DOWN 레짐)에서 인버스 보유 → 양의 수익."""
    rows = _rows([400 - i for i in range(260)])
    trades = simulate_inverse(rows)
    assert len(trades) >= 1
    assert all(t.reason in _VALID for t in trades)
    assert trades[0].ret > Decimal("0")


def test_inverse_no_trade_in_uptrend() -> None:
    """상승 추세엔 DOWN 레짐이 없어 인버스 진입 0."""
    assert simulate_inverse(_rows([100 + i for i in range(260)])) == []


def test_inverse_trailing_stop_on_sharp_bounce() -> None:
    """하락 보유 중 지수 급반등 → 인버스 equity 고점대비 급락 → 트레일링 손절."""
    rows = _rows([400 - i for i in range(232)] + [400 - 231 + x for x in (40, 60)])
    trades = simulate_inverse(rows, InverseParams(trail_stop=Decimal("0.07")))
    assert any(t.reason == "stop" for t in trades)
