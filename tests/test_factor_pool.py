"""Task 8a — 오리엔티드 팩터 풀(build_factor_pool) + 호스레이스 배선 스모크.

목표:
  - build_factor_pool() 이 15개 정확한 이름의 callable dict 를 반환.
  - 합성 패널에서 각 팩터가 기대 타입(Decimal|None)을 반환하고, 역방향 팩터
    (neg_per/neg_pbr)가 올바르게 음수화됨(방향: 높을수록 좋음).
  - 풀이 run_horserace 엔진에 end-to-end 로 꽂혀 Leaderboard(결과 15개)를 반환
    (2종목 합성이라 통계는 퇴화 — 승자 판정은 하지 않음, 배선만 증명).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.factor_pool import build_factor_pool
from backend.backtest.horserace import Leaderboard, run_horserace
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from tests.fixtures.backtest_synth import make_panel

_EXPECTED_NAMES = [
    "trend_template",
    "ma_alignment",
    "mom_12_1",
    "mom",
    "volume_surge",
    "near_52w",
    "pocket_pivot",
    "neg_atr",
    "neg_vol_dryup",
    "gp",
    "roe",
    "op_margin",
    "rev_growth",
    "neg_per",
    "neg_pbr",
]

# 000001 이 상장(2023-01-02)되어 있고 충분한 봉(~120)을 가진 시점.
_LIVE_DATE = date(2023, 5, 2)
_TICKER = "000001"

_PRICE_FACTORS = {
    "trend_template",
    "ma_alignment",
    "mom_12_1",
    "mom",
    "volume_surge",
    "near_52w",
    "pocket_pivot",
    "neg_atr",
    "neg_vol_dryup",
}


def test_pool_has_exactly_expected_keys() -> None:
    pool = build_factor_pool()
    assert set(pool) == set(_EXPECTED_NAMES), "풀 키는 정확히 15개 기대 이름이어야 함"
    assert len(pool) == 15
    # 삽입 순서(리더보드 순서) 보존.
    assert list(pool) == _EXPECTED_NAMES


def test_pool_values_are_callable() -> None:
    pool = build_factor_pool()
    for name, fn in pool.items():
        assert callable(fn), f"{name} 값은 callable 이어야 함"


def test_price_factors_return_decimal_or_none() -> None:
    pool = build_factor_pool()
    panel = make_panel()
    for name in _PRICE_FACTORS:
        v = pool[name](panel, _TICKER, _LIVE_DATE)
        # 000001 은 라이브이고 rows 존재 → 가격 팩터는 Decimal 반환(데이터 부족 조건은
        # 함수가 Decimal("0")/raw 0 으로 처리 — None 은 rows 비었을 때만).
        assert isinstance(v, Decimal), f"{name} 은 Decimal 을 반환해야 함(got {v!r})"


def test_price_factor_none_when_no_rows() -> None:
    """rows 가 없으면(미상장 시점) 가격 팩터는 None."""
    pool = build_factor_pool()
    panel = make_panel()
    # 000001 상장 전 날짜는 없으므로 미존재 티커로 rows 빈 경우를 검증.
    v = pool["mom"](panel, "999999", _LIVE_DATE)
    assert v is None


def test_fundamental_factors() -> None:
    pool = build_factor_pool()
    panel = make_panel()
    # 합성 펀더멘털: 2023-03-31 접수분이 as-of(roe=0.10, op_margin=0.08, rev_growth=0.15).
    assert pool["roe"](panel, _TICKER, _LIVE_DATE) == Decimal("0.10")
    assert pool["op_margin"](panel, _TICKER, _LIVE_DATE) == Decimal("0.08")
    assert pool["rev_growth"](panel, _TICKER, _LIVE_DATE) == Decimal("0.15")
    # gp 는 합성 펀더멘털이 설정하지 않음 → None.
    assert pool["gp"](panel, _TICKER, _LIVE_DATE) is None


def test_value_factors_negated() -> None:
    """value 팩터는 낮을수록 좋음 → 음수화(높을수록 좋음 방향 통일)."""
    pool = build_factor_pool()
    panel = make_panel()
    # make_series: per=10, pbr=1.2 → 음수화.
    neg_per = pool["neg_per"](panel, _TICKER, _LIVE_DATE)
    neg_pbr = pool["neg_pbr"](panel, _TICKER, _LIVE_DATE)
    assert neg_per == Decimal("-10")
    assert neg_pbr == Decimal("-1.2")
    # 방향 검증: per=10(양수)인 종목의 neg_per 는 음수.
    assert neg_per < Decimal("0")


def test_horserace_wiring_smoke() -> None:
    """풀이 run_horserace 에 end-to-end 로 꽂혀 결과 15개 Leaderboard 반환.

    2종목 합성이라 통계는 퇴화(승자 판정 안 함) — 배선만 증명.
    작은 n_resamples/n_perms 로 속도 확보.
    """
    panel = make_panel()
    last = max(r.date for s in panel.series.values() for r in s.rows)
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=last,
        rebalance="weekly",
        top_n=5,
        forward_horizons=(5,),
        n_resamples=30,
        n_perms=30,
    )
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.2"))
    pool = build_factor_pool()
    lb = run_horserace(panel, cfg, wf, pool, horizon=5, q=Decimal("0.10"))
    assert isinstance(lb, Leaderboard)
    assert len(lb.results) == 15, "리더보드는 풀의 모든 팩터(15개) 결과를 포함해야 함"
    assert {r.name for r in lb.results} == set(_EXPECTED_NAMES)
