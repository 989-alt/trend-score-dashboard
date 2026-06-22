"""평균회귀 슬리브 — RSI2 과매도 반등(횡보·고변동 레짐용). 신호 + 이벤트드리븐 백테스트.

전략(Connors 식): **장기 상승(200일선 위) 우량주의 단기 과매도(RSI2<10) 반등**을 짧게 먹는다.
추세추종과 정반대로 **평균(MA20) 복귀에서 즉시 익절**하고, 안 되면 시간손절/하드손절로 칼청산.

이 모듈은 라이브 슬리브와 백테스트가 **같은 신호 함수**를 쓰도록 순수 함수로 둔다(드리프트 방지).
금액·비율은 코드베이스 관례대로 **Decimal 전면**(float 금지). 단일 종목 시뮬을 종목별로 돌려
거래를 풀링하면 유니버스 백테스트가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from backend.schemas import OHLCVRow

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class MeanRevParams:
    """평균회귀 파라미터(백테스트 튜닝 대상). 기본값은 설계서 권장치."""

    rsi_period: int = 2
    rsi_buy: Decimal = Decimal("10")  # 진입: RSI(2) < 10
    rsi_exit: Decimal = Decimal("50")  # 익절: RSI > 50
    ma_long: int = 200  # 품질 필터(200일선 위)
    ma_exit: int = 20  # 익절 목표(MA20 복귀)
    time_stop_days: int = 3  # 시간손절(보유 N봉 초과)
    hard_stop_pct: Decimal = Decimal("0.05")  # 하드손절 −5%
    cost_bps: Decimal = Decimal("41")  # 왕복 거래비용(한쪽 bps ×2 적용)


#: 기본 파라미터 싱글턴 — 인자 기본값에 함수호출(B008) 회피용.
_DEFAULT = MeanRevParams()


@dataclass(frozen=True)
class Trade:
    """체결 1건(단일 종목 시뮬). ``ret`` 은 왕복 비용 차감 순수익률."""

    entry_date: date
    exit_date: date
    entry: Decimal
    exit: Decimal
    ret: Decimal
    reason: str  # ma20 | rsi | time | stop | eod


def _sma(closes: list[Decimal], window: int) -> Decimal | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:], _ZERO) / Decimal(window)


def rsi(closes: list[Decimal], period: int) -> Decimal | None:
    """Wilder RSI(period). 종가 < period+1 이면 None. 무손실 구간은 100."""
    if len(closes) < period + 1:
        return None
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for prev, cur in pairwise(closes):
        ch = cur - prev
        gains.append(ch if ch > _ZERO else _ZERO)
        losses.append(-ch if ch < _ZERO else _ZERO)
    avg_gain = sum(gains[:period], _ZERO) / period
    avg_loss = sum(losses[:period], _ZERO) / period
    for g, ls in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + ls) / period
    if avg_loss == _ZERO:
        return _HUNDRED
    rs = avg_gain / avg_loss
    return _HUNDRED - _HUNDRED / (_ONE + rs)


def is_entry(rows: list[OHLCVRow], params: MeanRevParams = _DEFAULT) -> bool:
    """진입 신호 — RSI(period) < rsi_buy **AND** 종가 > MA200(장기 상승 우량주만)."""
    if len(rows) < params.ma_long:
        return False
    closes = [r.close for r in rows]
    ma = _sma(closes, params.ma_long)
    rv = rsi(closes, params.rsi_period)
    return rv is not None and ma is not None and rv < params.rsi_buy and closes[-1] > ma


def _should_exit(
    rows: list[OHLCVRow], i: int, entry: Decimal, held_days: int, params: MeanRevParams
) -> str | None:
    """i 봉 종가 기준 청산 사유(없으면 None). 우선순위: 하드손절 > 익절(MA20/RSI) > 시간손절."""
    close = rows[i].close
    if close <= entry * (_ONE - params.hard_stop_pct):
        return "stop"
    ma20 = _sma([r.close for r in rows[: i + 1]], params.ma_exit)
    if ma20 is not None and close >= ma20:
        return "ma20"
    rv = rsi([r.close for r in rows[: i + 1]], params.rsi_period)
    if rv is not None and rv > params.rsi_exit:
        return "rsi"
    if held_days >= params.time_stop_days:
        return "time"
    return None


def simulate(
    rows: list[OHLCVRow],
    params: MeanRevParams = _DEFAULT,
    *,
    allowed_dates: frozenset[date] | None = None,
) -> list[Trade]:
    """단일 종목 이벤트드리븐 시뮬(룩어헤드 0). 종가 매수/종가 청산, 한 번에 1포지션.

    신호 봉 종가에 진입 → 다음 봉부터 청산 조건 점검 → 충족 봉 종가에 청산. 데이터 끝이면 청산(eod).
    ``allowed_dates`` 지정 시 그 날짜(레짐=CHOP_VOL 등)에만 **진입**한다(청산은 무관). 라이브에선
    레짐 게이트가 같은 역할을 한다 — 평균회귀는 횡보장에서만 켜야 추세장 역행 손실을 피한다.
    """
    trades: list[Trade] = []
    n = len(rows)
    i = params.ma_long
    cost = params.cost_bps / Decimal("10000") * Decimal("2")  # 왕복
    while i < n:
        gated = allowed_dates is not None and rows[i].date not in allowed_dates
        if gated or not is_entry(rows[: i + 1], params):
            i += 1
            continue
        entry_idx = i
        entry = rows[entry_idx].close
        j = entry_idx + 1
        reason = "eod"
        while j < n:
            r = _should_exit(rows, j, entry, j - entry_idx, params)
            if r is not None:
                reason = r
                break
            j += 1
        exit_idx = min(j, n - 1)
        exit_px = rows[exit_idx].close
        gross = (exit_px - entry) / entry if entry > _ZERO else _ZERO
        trades.append(
            Trade(
                entry_date=rows[entry_idx].date,
                exit_date=rows[exit_idx].date,
                entry=entry,
                exit=exit_px,
                ret=gross - cost,
                reason=reason,
            )
        )
        i = exit_idx + 1  # 청산 다음 봉부터 재탐색
    return trades


def summarize(trades: list[Trade]) -> dict[str, Decimal | int]:
    """풀링된 거래의 요약 — 건수·승률·평균·기대값·총복리·MDD(거래순 equity)."""
    from backend.backtest import metrics

    if not trades:
        return {"n": 0, "win_rate": _ZERO, "avg_ret": _ZERO, "total_return": _ZERO, "mdd": _ZERO}
    ordered = sorted(trades, key=lambda t: t.exit_date)
    rets = [t.ret for t in ordered]
    nav = [_ONE]
    for r in rets:
        nav.append(nav[-1] * (_ONE + r))
    return {
        "n": len(trades),
        "win_rate": metrics.win_rate(rets),
        "avg_ret": sum(rets, _ZERO) / Decimal(len(rets)),
        "total_return": nav[-1] - _ONE,
        "mdd": metrics.max_drawdown(nav),
    }


__all__ = ["MeanRevParams", "Trade", "is_entry", "rsi", "simulate", "summarize"]
