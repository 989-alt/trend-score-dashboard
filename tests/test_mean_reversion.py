"""평균회귀 슬리브 단위테스트 — RSI·진입신호·이벤트드리븐 시뮬. 합성데이터, 네트워크 0."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.schemas import OHLCVRow
from backend.sleeves.mean_reversion import (
    is_entry,
    rsi,
    simulate,
    summarize,
)

_VALID_REASONS = {"ma20", "rsi", "time", "stop", "eod"}


def _rows(closes: list[float]) -> list[OHLCVRow]:
    out: list[OHLCVRow] = []
    d0 = date(2025, 1, 1)
    for i, c in enumerate(closes):
        cd = Decimal(str(c))
        out.append(
            OHLCVRow(
                date=d0 + timedelta(days=i),
                open=cd,
                high=cd,
                low=cd,
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    return out


def test_rsi_extremes() -> None:
    """전구간 상승 → 100, 전구간 하락 → 0."""
    up = [Decimal(100 + i) for i in range(20)]
    down = [Decimal(200 - i) for i in range(20)]
    assert rsi(up, 2) == Decimal("100")
    assert rsi(down, 2) == Decimal("0")
    assert rsi([Decimal("1")], 2) is None  # 데이터 부족


def test_is_entry_oversold_above_ma200_true() -> None:
    """장기 상승(200일선 위) 후 단기 과매도(RSI2<10) → 진입."""
    rows = _rows([100 + i for i in range(210)] + [295, 290])
    assert is_entry(rows) is True


def test_is_entry_uptrend_no_oversold_false() -> None:
    """과매도 아님(꾸준한 상승, RSI2 높음) → 진입 안 함."""
    rows = _rows([100 + i for i in range(212)])
    assert is_entry(rows) is False


def test_is_entry_below_ma200_false() -> None:
    """200일선 아래(떨어지는 칼날) → 과매도여도 진입 금지."""
    rows = _rows([400 - i for i in range(212)])  # 하락추세 → 종가 < MA200
    assert is_entry(rows) is False


def test_simulate_profit_on_bounce_exits_at_mean() -> None:
    """진입 후 평균 복귀 반등 → 익절(ma20/rsi), 순수익 > 0."""
    rows = _rows([100 + i for i in range(210)] + [295, 290, 305, 320])
    trades = simulate(rows)
    assert len(trades) >= 1
    assert all(t.reason in _VALID_REASONS for t in trades)
    assert trades[0].ret > Decimal("0")
    assert trades[0].reason in {"ma20", "rsi"}


def test_simulate_hard_stop_on_continued_drop() -> None:
    """진입 후 −5% 이탈 지속 → 하드손절(stop), 순손실 < 0."""
    rows = _rows([100 + i for i in range(210)] + [295, 290, 270, 260])
    trades = simulate(rows)
    assert len(trades) >= 1
    assert trades[0].reason == "stop"
    assert trades[0].ret < Decimal("0")


def test_simulate_no_entry_no_trades() -> None:
    """진입 신호 없으면 거래 0."""
    assert simulate(_rows([100 + i for i in range(212)])) == []


def test_summarize_pools_trades() -> None:
    """요약 — 건수·승률·총복리. 빈 입력도 안전."""
    rows = _rows([100 + i for i in range(210)] + [295, 290, 305, 320])
    s = summarize(simulate(rows))
    assert s["n"] >= 1
    assert Decimal("0") <= s["win_rate"] <= Decimal("1")
    empty = summarize([])
    assert empty["n"] == 0
