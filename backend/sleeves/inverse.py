"""인버스 슬리브 — 하락(DOWN) 레짐에서 인버스 ETF 롱(KR=KODEX 인버스, US=SH). 신호 + 백테스트.

KR 개인·모의계좌는 개별주 공매도 불가 → "역베팅"을 **인버스 ETF 매수**로 구현(숏 메커니즘 없음).
인버스 ETF 의 일일 수익률 ≈ −(지수 일일 수익률)×레버리지. 그래서 백테스트는 지수 일봉으로
DOWN 레짐 구간을 잡아 "인버스 보유"의 복리수익을 시뮬한다(실 ETF 데이터 없이 1X 근사).

DOWN 진입은 레짐 확정(MA200 아래 + ADX≥trend) 후 → 하락 추세 확인 시만. 레짐 이탈/트레일링
손절로 청산(변동성 드래그·휩쏘 방지). 금액·비율 **Decimal 전면**.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.regime import Regime, classify_regime
from backend.schemas import OHLCVRow
from backend.sleeves.mean_reversion import Trade

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class InverseParams:
    """인버스 파라미터. leverage=1(1X 기본; 2X 는 횡보 드래그로 위험)."""

    leverage: Decimal = _ONE
    trail_stop: Decimal = Decimal("0.07")  # 인버스 equity 고점 대비 −7% 트레일링
    cost_bps: Decimal = Decimal("10")  # 한쪽 bps(왕복 ×2)
    ma_window: int = 200
    adx_period: int = 14
    adx_trend: Decimal = Decimal("25")
    adx_chop: Decimal = Decimal("20")


_DEFAULT = InverseParams()


def _regimes(index_rows: list[OHLCVRow], params: InverseParams) -> list[Regime]:
    """지수 일봉의 봉별 레짐 시퀀스(히스테리시스 반영)."""
    out: list[Regime] = []
    prev: Regime | None = None
    for k in range(len(index_rows)):
        r = classify_regime(
            index_rows[: k + 1],
            ma_window=params.ma_window,
            adx_period=params.adx_period,
            adx_trend=params.adx_trend,
            adx_chop=params.adx_chop,
            prev=prev,
        )
        prev = r.regime
        out.append(r.regime)
    return out


def simulate_inverse(index_rows: list[OHLCVRow], params: InverseParams = _DEFAULT) -> list[Trade]:
    """지수 일봉으로 인버스 보유 시뮬 — DOWN 레짐 구간 진입, 레짐이탈/트레일링손절 청산.

    인버스 일일수익 = (1 − leverage × 지수일일수익). 한 번에 1포지션. 룩어헤드 0(레짐은 당봉까지).
    """
    regimes = _regimes(index_rows, params)
    trades: list[Trade] = []
    n = len(index_rows)
    cost = params.cost_bps / Decimal("10000") * Decimal("2")
    i = 0
    while i < n:
        if regimes[i] != "DOWN":
            i += 1
            continue
        entry_idx = i
        equity = _ONE
        peak = _ONE
        reason = "eod"
        j = entry_idx + 1
        while j < n:
            prev_c = index_rows[j - 1].close
            r_idx = (index_rows[j].close - prev_c) / prev_c if prev_c > _ZERO else _ZERO
            equity *= _ONE - params.leverage * r_idx  # 인버스 일일
            peak = max(peak, equity)
            if regimes[j] != "DOWN":
                reason = "regime"
                break
            if equity <= peak * (_ONE - params.trail_stop):
                reason = "stop"
                break
            j += 1
        exit_idx = min(j, n - 1)
        trades.append(
            Trade(
                entry_date=index_rows[entry_idx].date,
                exit_date=index_rows[exit_idx].date,
                entry=index_rows[entry_idx].close,
                exit=index_rows[exit_idx].close,
                ret=equity - _ONE - cost,
                reason=reason,
            )
        )
        i = exit_idx + 1
    return trades


__all__ = ["InverseParams", "simulate_inverse"]
