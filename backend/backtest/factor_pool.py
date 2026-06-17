"""실전 후보 팩터 풀 (Task 8a) — 호스레이스 엔진 입력용 오리엔티드 팩터 dict.

각 팩터는 **높을수록 좋음**(높은 값 = 기대 forward-return 큼) 방향으로 정렬한다.
호스레이스 엔진이 *양의* 단조성 승자만 추대하므로, value 류(per/pbr — 낮을수록 좋음)와
변동성 류(atr/vol_dryup — 낮을수록 좋음)는 음수화해 방향을 통일한다.

Task-4 결정론 팩터(`backend.scoring`) + 패널 펀더멘털/밸류에이션 as-of 접근자를
`FactorFn = Callable[[Panel, str, date], Decimal | None]` 시그니처로 래핑한다.

룩어헤드 0: 모든 접근자(rows_asof/fundamentals_asof/valuation_asof)가 ≤t 만 반환하므로
팩터 fn 은 그 가드를 그대로 계승한다.

배선만 담당하는 leaf 모듈(run 핸들러만 import) — 순환 import 없음.
삽입 순서 = 리더보드 순서.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from backend.backtest.horserace import FactorFn
from backend.backtest.panel import Panel
from backend.schemas import OHLCVRow
from backend.scoring import (
    atr20_over_price,
    compute_momentum,
    ma_alignment,
    mom_12_1,
    pocket_pivot,
    proximity_to_52w_high,
    trend_template,
    vol_dryup,
    volume_surge,
)


def _price_factor(fn: Callable[[list[OHLCVRow]], Decimal]) -> FactorFn:
    """≤t rows 에 가격 팩터 fn 을 적용. rows 비었으면 None."""

    def factor(panel: Panel, tk: str, t: date) -> Decimal | None:
        rows = panel.rows_asof(tk, t)
        return fn(rows) if rows else None

    return factor


def _fund_factor(attr: str) -> FactorFn:
    """as-of 펀더멘털의 attr(roe/op_margin/rev_growth/gp). 미존재/미설정이면 None."""

    def factor(panel: Panel, tk: str, t: date) -> Decimal | None:
        f = panel.fundamentals_asof(tk, t)
        return getattr(f, attr) if f is not None else None

    return factor


def _neg_value_factor(attr: str) -> FactorFn:
    """value 팩터(per/pbr) — 낮을수록 좋음 → 음수화해 '높을수록 좋음'으로 통일."""

    def factor(panel: Panel, tk: str, t: date) -> Decimal | None:
        v = panel.valuation_asof(tk, t)
        raw = getattr(v, attr) if v is not None else None
        return -raw if raw is not None else None

    return factor


def build_factor_pool() -> dict[str, FactorFn]:
    """오리엔티드 후보 팩터 풀(높을수록 좋음). 삽입 순서 = 리더보드 순서.

    - 추세/모멘텀/거래량/신고가/포켓피봇: 원시 방향(높을수록 좋음).
    - neg_atr / neg_vol_dryup: 변동성·거래량 마름은 낮을수록 좋음 → 음수화.
    - 퀄리티(gp/roe/op_margin/rev_growth): 높을수록 좋음(원시).
    - neg_per / neg_pbr: 밸류는 낮을수록 좋음 → 음수화.
    """
    return {
        "trend_template": _price_factor(trend_template),
        "ma_alignment": _price_factor(ma_alignment),
        "mom_12_1": _price_factor(mom_12_1),
        "mom": _price_factor(compute_momentum),
        "volume_surge": _price_factor(volume_surge),
        "near_52w": _price_factor(proximity_to_52w_high),
        "pocket_pivot": _price_factor(
            lambda rows: Decimal("1") if pocket_pivot(rows) else Decimal("0")
        ),
        "neg_atr": _price_factor(lambda rows: -atr20_over_price(rows)),
        "neg_vol_dryup": _price_factor(lambda rows: -vol_dryup(rows)),
        "gp": _fund_factor("gp"),
        "roe": _fund_factor("roe"),
        "op_margin": _fund_factor("op_margin"),
        "rev_growth": _fund_factor("rev_growth"),
        "neg_per": _neg_value_factor("per"),
        "neg_pbr": _neg_value_factor("pbr"),
    }


__all__ = ["build_factor_pool"]
