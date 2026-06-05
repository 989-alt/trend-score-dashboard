"""추세추종(맛동산) 결정론 산식 — 하드필터·팩터·점수·등급.

swing-bot ``src/universe/screener.py`` 의 추세추종 변형(``KRTrendScreener``)을 본
대시보드 계약(``OHLCVRow``/``FactorBreakdown``/``Grade``)에 맞춰 포팅한다.

산식 요약(결정론 — 동일 입력 → 동일 출력):
- 하드필터: 거래대금 ≥ 임계 · 모멘텀 ≥ 임계 · 200일선 위 · 변동성 밴드 안.
  + Gap B: 52주 신고가 근접(``near_52w >= breakout_52w_min``)이면 변동성 상한 면제.
- 점수: cross-sectional min-max 정규화 후 가중합(0~1). ineligible 은 0.
  ``score = w52*near_52w + w_pp*pp + w_mom*mom_norm + w_rs*rs_norm + w_to*to_norm + w_vf*vol_fit``.
  RS(지수대비 상대강도) = 종목모멘텀 − 지수모멘텀 (cross-sectional min-max 정규화).

원칙: 금액·수량·비율은 ``Decimal`` (float 금지). ``math`` 보조 변환은 문자열 경유로
정밀도 손실 최소화.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from backend.config import Settings
from backend.schemas import FactorBreakdown, Grade, OHLCVRow

# ---------------------------------------------------------------------------
# 결정론 팩터
# ---------------------------------------------------------------------------


def compute_momentum(rows: list[OHLCVRow]) -> Decimal:
    """첫→마지막 종가 수익률. rows<2 또는 첫 종가 0 이면 ``Decimal("0")``."""
    if len(rows) < 2:
        return Decimal("0")
    first = rows[0].close
    last = rows[-1].close
    if first == 0:
        return Decimal("0")
    return (last - first) / first


def compute_annualized_volatility(rows: list[OHLCVRow]) -> Decimal:
    """일별 로그수익률 표준편차 × √252 (연환산). rows<2 면 ``Decimal("0")``.

    ``Decimal`` 만 사용. ``math.log``/``math.sqrt`` 은 float 이므로 결과를 즉시
    문자열 변환 후 ``Decimal`` 로 복귀(정밀도 손실 최소화).
    """
    if len(rows) < 2:
        return Decimal("0")
    log_returns: list[Decimal] = []
    for prev, curr in pairwise(rows):
        if prev.close <= 0 or curr.close <= 0:
            continue
        ratio = curr.close / prev.close
        # ln(ratio) 는 Decimal 에 없음 → float 경유 후 즉시 Decimal 화.
        log_returns.append(Decimal(str(math.log(float(ratio)))))
    if len(log_returns) < 2:
        return Decimal("0")
    n = Decimal(len(log_returns))
    mean = sum(log_returns, Decimal("0")) / n
    variance = sum(((x - mean) ** 2 for x in log_returns), Decimal("0")) / n
    std = Decimal(str(math.sqrt(float(variance))))
    annualized = std * Decimal(str(math.sqrt(252)))
    return annualized


def simple_moving_average(rows: list[OHLCVRow], window: int) -> Decimal | None:
    """최근 ``window`` 일 종가 단순이동평균. 데이터 부족(rows<window) 이면 ``None``."""
    if window <= 0 or len(rows) < window:
        return None
    closes = [r.close for r in rows[-window:]]
    return sum(closes, Decimal("0")) / Decimal(window)


def above_ma200(rows: list[OHLCVRow], window: int = 200) -> bool:
    """현재 종가가 ``window`` 일 이동평균 위인가. 데이터 부족이면 ``False``."""
    ma = simple_moving_average(rows, window)
    if ma is None:
        return False
    return rows[-1].close > ma


def proximity_to_52w_high(rows: list[OHLCVRow], *, high_52w: Decimal | None = None) -> Decimal:
    """52주 신고가 근접도(0~1, 1=도달/돌파).

    ``high_52w`` 가 주어지면 그것을, 없으면 ``rows`` 최고 고가를 분모로 사용한다.
    분모 ≤ 0 또는 rows 비었으면 ``Decimal("0")``.
    """
    if not rows:
        return Decimal("0")
    denom = high_52w if (high_52w is not None and high_52w > 0) else max(r.high for r in rows)
    if denom <= 0:
        return Decimal("0")
    current = rows[-1].close
    ratio = current / denom
    if ratio <= 0:
        return Decimal("0")
    return min(Decimal("1"), ratio)


def pocket_pivot(rows: list[OHLCVRow], *, lookback: int = 10) -> bool:
    """포켓피봇 — 오늘 양봉 거래량이 직전 ``lookback`` 일 음봉 최대거래량을 압도.

    - 오늘 봉이 양봉(close > open) **그리고**
    - 오늘 거래량 > 직전 ``lookback`` 일 음봉(close < open) 최대 거래량.
    - 직전 구간에 음봉이 없으면 0 을 기준으로 비교 → 양봉이면 ``True``.
    - 데이터가 ``lookback + 1`` 봉 미만이면 ``False``.
    """
    if len(rows) < lookback + 1:
        return False
    today = rows[-1]
    if today.close <= today.open:
        return False
    prior = rows[-(lookback + 1) : -1]
    down_volumes = [r.volume for r in prior if r.close < r.open]
    max_down_vol = max(down_volumes) if down_volumes else Decimal("0")
    return today.volume > max_down_vol


def volatility_fit(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """변동성 밴드 적합도 — 삼각형 gradient. 밴드 중심 1.0, 양 끝/밖 0.0."""
    if value < low or value > high:
        return Decimal("0")
    center = (low + high) / Decimal("2")
    half_width = (high - low) / Decimal("2")
    if half_width == 0:
        return Decimal("1")
    distance = abs(value - center)
    return max(Decimal("0"), Decimal("1") - distance / half_width)


def min_max_norm(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    """min-max 정규화 → 0~1. ``hi == lo`` 면 ``value >= lo`` 일 때 1.0(동등 처리)."""
    if hi == lo:
        # 단일 생존자 또는 동률 — 모두 만점(밴드 내 동등). 0 collapse 방지.
        return Decimal("1") if value >= lo else Decimal("0")
    if value <= lo:
        return Decimal("0")
    if value >= hi:
        return Decimal("1")
    return (value - lo) / (hi - lo)


# ---------------------------------------------------------------------------
# 후보 + 점수
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """점수 산출 전 후보 — raw factor 값 + 200일선 위 여부 + 하드필터 통과 여부."""

    ticker: str
    turnover: Decimal
    momentum: Decimal
    rs: Decimal  # 지수대비 상대수익률 (종목모멘텀 − 지수모멘텀)
    volatility: Decimal
    near_52w: Decimal
    has_pocket_pivot: bool
    above_ma200: bool
    eligible: bool


def passes_hard_filter(
    *,
    turnover: Decimal,
    momentum: Decimal,
    volatility: Decimal,
    near_52w: Decimal,
    above_ma200_flag: bool,
    min_turnover: Decimal,
    settings: Settings,
) -> bool:
    """하드필터 통과 여부 (모두 만족해야 ``True``).

    1. 거래대금 ≥ ``min_turnover`` (시장별 임계 — KR=KRW, US=USD).
    2. 모멘텀 ≥ ``settings.momentum_min``.
    3. 200일선 위 (``above_ma200_flag``).
    4. 변동성 밴드 ``[vol_band_low, vol_band_high]`` 안.
       단 Gap B: ``near_52w >= settings.breakout_52w_min`` 이면 변동성 **상한** 면제
       (주도주는 신고가 영역의 고변동을 허용).
    """
    if turnover < min_turnover:
        return False
    if momentum < settings.momentum_min:
        return False
    if not above_ma200_flag:
        return False
    # 변동성 하한은 항상 강제. 상한은 Gap B(신고가 근접 주도주)면 면제.
    if volatility < settings.vol_band_low:
        return False
    near_high = near_52w >= settings.breakout_52w_min
    # 변동성 상한 초과는 Gap B(신고가 근접 주도주)일 때만 허용.
    return not (volatility > settings.vol_band_high and not near_high)


def score_candidates(
    candidates: list[Candidate],
    settings: Settings,
) -> dict[str, tuple[Decimal, FactorBreakdown]]:
    """후보군 → ticker 별 (점수 0~1, 팩터 분해).

    - 거래대금·모멘텀은 입력 후보군의 cross-sectional min-max 정규화.
    - 52주 근접·vol_fit 은 이미 0~1, 포켓피봇은 0/1.
    - ``eligible`` 이 ``False`` 인 후보는 점수 0(팩터 분해는 원시값 보존).
    """
    result: dict[str, tuple[Decimal, FactorBreakdown]] = {}
    if not candidates:
        return result

    turnovers = [c.turnover for c in candidates]
    momentums = [c.momentum for c in candidates]
    rss = [c.rs for c in candidates]
    t_lo, t_hi = min(turnovers), max(turnovers)
    m_lo, m_hi = min(momentums), max(momentums)
    rs_lo, rs_hi = min(rss), max(rss)

    for cand in candidates:
        turnover_norm = min_max_norm(cand.turnover, t_lo, t_hi)
        momentum_norm = min_max_norm(cand.momentum, m_lo, m_hi)
        rs_norm = min_max_norm(cand.rs, rs_lo, rs_hi)
        vol_fit = volatility_fit(cand.volatility, settings.vol_band_low, settings.vol_band_high)
        pp_score = Decimal("1") if cand.has_pocket_pivot else Decimal("0")
        if cand.eligible:
            score = (
                cand.near_52w * settings.weight_52w
                + pp_score * settings.weight_pocket_pivot
                + momentum_norm * settings.weight_momentum
                + rs_norm * settings.weight_rs
                + turnover_norm * settings.weight_turnover
                + vol_fit * settings.weight_vol_fit
            )
            score = max(Decimal("0"), min(Decimal("1"), score))
        else:
            # 하드필터 미통과 — 점수 0 (팩터 분해는 원시값·정규화값 보존).
            score = Decimal("0")
        breakdown = FactorBreakdown(
            near_52w=cand.near_52w,
            pocket_pivot=pp_score,
            momentum_norm=momentum_norm,
            rs_norm=rs_norm,
            turnover_norm=turnover_norm,
            vol_fit=vol_fit,
            momentum=cand.momentum,
            rs=cand.rs,
            volatility=cand.volatility,
            above_ma200=cand.above_ma200,
        )
        result[cand.ticker] = (score, breakdown)
    return result


def grade_for_score(score_100: Decimal, settings: Settings) -> Grade:
    """0~100 점수 → 등급.

    - ≥ ``grade_strong_buy`` (75) → ``STRONG_BUY``.
    - ≥ ``grade_buy`` (60) → ``BUY``.
    - ≥ ``grade_hold`` (45) → ``HOLD``.
    - 그 외 → ``AVOID``.

    (``SELL`` 은 손절 발동 시 ``stops`` 가 오버라이드하므로 여기서 반환하지 않는다.)
    """
    if score_100 >= settings.grade_strong_buy:
        return Grade.STRONG_BUY
    if score_100 >= settings.grade_buy:
        return Grade.BUY
    if score_100 >= settings.grade_hold:
        return Grade.HOLD
    return Grade.AVOID


__all__ = [
    "Candidate",
    "above_ma200",
    "compute_annualized_volatility",
    "compute_momentum",
    "grade_for_score",
    "min_max_norm",
    "passes_hard_filter",
    "pocket_pivot",
    "proximity_to_52w_high",
    "score_candidates",
    "simple_moving_average",
    "volatility_fit",
]
