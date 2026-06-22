"""레짐(장세) 엔진 — 지수 일봉으로 UP_TREND / CHOP_VOL / DOWN 판정 (읽기전용, 1단계).

올웨더 매매 설계의 backbone. 지수(KR=KOSPI, US=S&P)의 **방향(MA200) × 강도(ADX14)** 로 3대
레짐을 정한다. 1단계에선 시황 표시 전용이며, 매매 슬리브 전환(2·3단계)에서 이 판정을 소비한다.

- UP_TREND: 지수가 MA200 위 **&&** ADX≥trend(추세 상승)
- DOWN:     지수가 MA200 아래 **&&** ADX≥trend(추세 하락)
- CHOP_VOL: ADX<chop(횡보·방향 불명)
- 경계(MA200 근처 + ADX chop~trend): 직전 레짐 유지(히스테리시스). prev 없으면 CHOP_VOL.

금액 아닌 지표지만 코드베이스 관례대로 **Decimal 전면**(float 금지). 입력 부족·오류는 UNKNOWN.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from backend.schemas import OHLCVRow
from backend.scoring import simple_moving_average

if TYPE_CHECKING:  # 런타임 의존 회피(market_data 는 무거움) — 타입 주석 전용.
    from backend.config import Settings
    from backend.market_data import MarketDataProvider
    from backend.schemas import Market

#: 레짐 라벨. UNKNOWN = 데이터 부족/오류(판정 보류).
Regime = Literal["UP_TREND", "CHOP_VOL", "DOWN", "UNKNOWN"]

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class RegimeResult:
    """레짐 판정 결과 + 진단(표시·디버그용). 값은 Decimal, 미산정은 None."""

    regime: Regime
    index_close: Decimal | None = None
    ma200: Decimal | None = None
    adx: Decimal | None = None
    above_ma200: bool | None = None


def _adx(rows: list[OHLCVRow], period: int = 14) -> Decimal | None:
    """Wilder ADX(period). 봉이 2·period+1 미만이면 None. 전 구간 Decimal."""
    if len(rows) < 2 * period + 1:
        return None
    trs: list[Decimal] = []
    plus_dms: list[Decimal] = []
    minus_dms: list[Decimal] = []
    for prev, cur in pairwise(rows):
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        up = cur.high - prev.high
        down = prev.low - cur.low
        plus_dms.append(up if (up > down and up > _ZERO) else _ZERO)
        minus_dms.append(down if (down > up and down > _ZERO) else _ZERO)
        trs.append(tr)

    def _wilder(vals: list[Decimal]) -> list[Decimal]:
        """Wilder 평활: 첫 값=초기 period 합, 이후 s = s − s/period + v."""
        out = [sum(vals[:period], _ZERO)]
        for v in vals[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    atr = _wilder(trs)
    pdm = _wilder(plus_dms)
    mdm = _wilder(minus_dms)

    dxs: list[Decimal] = []
    for a, p, m in zip(atr, pdm, mdm, strict=True):
        if a == _ZERO:
            continue
        plus_di = _HUNDRED * p / a
        minus_di = _HUNDRED * m / a
        denom = plus_di + minus_di
        dxs.append(_ZERO if denom == _ZERO else _HUNDRED * abs(plus_di - minus_di) / denom)
    if len(dxs) < period:
        return None
    adx = sum(dxs[:period], _ZERO) / period
    for dx in dxs[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


def classify_regime(
    rows: list[OHLCVRow],
    *,
    ma_window: int = 200,
    adx_period: int = 14,
    adx_trend: Decimal = Decimal("25"),
    adx_chop: Decimal = Decimal("20"),
    prev: Regime | None = None,
) -> RegimeResult:
    """지수 일봉(rows, 오래된→최신)으로 레짐 판정. 데이터 부족이면 UNKNOWN."""
    ma200 = simple_moving_average(rows, ma_window)
    if not rows or ma200 is None:
        return RegimeResult("UNKNOWN")
    last_close = rows[-1].close
    above = last_close > ma200
    adx = _adx(rows, adx_period)
    trending = adx is not None and adx >= adx_trend
    choppy = adx is not None and adx < adx_chop

    regime: Regime
    if trending and above:
        regime = "UP_TREND"
    elif trending and not above:
        regime = "DOWN"
    elif choppy:
        regime = "CHOP_VOL"
    elif prev is not None and prev != "UNKNOWN":
        regime = prev  # 경계(chop~trend): 직전 유지(히스테리시스)
    else:
        regime = "CHOP_VOL"
    return RegimeResult(regime, last_close, ma200, adx, above)


def assess_regime(
    provider: MarketDataProvider,
    market: Market,
    settings: Settings,
    *,
    prev: Regime | None = None,
) -> RegimeResult:
    """provider 의 지수 일봉으로 레짐 판정(리실리언트). 조회 실패는 UNKNOWN 흡수.

    지수 일봉은 일1회 캐시(get_index_ohlcv)라 폴링마다 재호출하지 않는다. MA200+ADX 를 위해
    넉넉히(ma_window + adx_period 여유) 요청한다.
    """
    need = settings.regime_ma_window + settings.regime_adx_period + 30
    try:
        rows = provider.get_index_ohlcv(market, need)
    except Exception:
        return RegimeResult("UNKNOWN")
    return classify_regime(
        rows,
        ma_window=settings.regime_ma_window,
        adx_period=settings.regime_adx_period,
        adx_trend=settings.regime_adx_trend,
        adx_chop=settings.regime_adx_chop,
        prev=prev,
    )


__all__ = ["Regime", "RegimeResult", "assess_regime", "classify_regime"]
