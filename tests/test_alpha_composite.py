"""Task 7: alpha_composite 프리셋 — 승자 팩터 rank-z 등가중 합성 테스트.

설계:
  - alpha_composite 는 eligible(=baseline 과 동일 유니버스) 종목을 주입된 승자 팩터들의
    횡단면 rank → z-score → 등가중 평균으로 재점수화하는 post-processor.
  - rank-z 방향: 큰 팩터값 → 큰 rank → 큰 z (positive-mono "높을수록 좋음" 보존).
  - std 는 compute_annualized_volatility 패턴(모집단 분산 ÷ n, float sqrt → Decimal).

손으로 검증한 수치(2 종목, 1 팩터):
  - _rank([작은값, 큰값]) = [1, 2] (오름차순 — 큰 값이 높은 rank).
  - ranks [1,2] → mean 1.5, var(÷n) 0.25, std 0.5 → z = [-1, +1].
  - 따라서 팩터값이 큰 종목 = +1, 작은 종목 = -1.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from backend.backtest.panel import Panel
from backend.backtest.run import (
    BacktestConfig,
    WalkForwardConfig,
    _score_at,
)
from backend.config import get_settings
from tests.fixtures.backtest_synth import make_series

# 두 종목 모두 ≥200봉(적격), 동일 시작일 → 횡단면 비교 가능.
# 000001 은 더 높은 종가대(100..359), 000002 는 더 낮은 종가대(50..309).
_START = date(2023, 1, 2)
_T = _START + timedelta(days=259)  # 마지막 봉(둘 다 적격)


def _two_ticker_panel() -> Panel:
    a = make_series("000001", _START, list(range(100, 360)))  # 260봉, 높은 종가
    b = make_series("000002", _START, list(range(50, 310)))  # 260봉, 낮은 종가
    index_rows = make_series("KS11", _START, list(range(2000, 2260))).rows
    return Panel(
        series={"000001": a, "000002": b},
        fundamentals={"000001": [], "000002": []},
        listings={"000001": (_START, None), "000002": (_START, None)},
        index_rows=index_rows,
    )


def _last_close(panel: Panel, tk: str, t: date) -> Decimal | None:
    rows = [r for r in panel.series[tk].rows if r.date <= t]
    return rows[-1].close if rows else None


# ---------------------------------------------------------------------------
# 1. 크리스프 ±1 검증 (1 팩터, 2 종목) — 손 계산 그대로
# ---------------------------------------------------------------------------
def test_single_factor_two_tickers_z_is_plus_minus_one() -> None:
    """1 팩터 · 2 종목 → 큰 값 z=+1, 작은 값 z=-1 (정확히)."""
    panel = _two_ticker_panel()
    settings = get_settings()

    # f1 = 마지막 종가. 000001(353.6) > 000002(304.4).
    factors = {"last_close": _last_close}
    ranked = _score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors)
    scores = dict(ranked)

    assert set(scores.keys()) == {"000001", "000002"}, "eligible 2종목이 모두 점수화돼야 함"
    # 큰 팩터값(000001) → z=+1, 작은 값(000002) → z=-1
    assert scores["000001"] == Decimal("1"), f"000001 z 기대 +1, 실제 {scores['000001']}"
    assert scores["000002"] == Decimal("-1"), f"000002 z 기대 -1, 실제 {scores['000002']}"
    # 정렬: 큰 점수 우선
    assert ranked[0][0] == "000001"
    assert ranked[1][0] == "000002"


# ---------------------------------------------------------------------------
# 2. 두 팩터 등가중 평균 — 방향 일치 / 상쇄 (값→종목 pairing 검증)
# ---------------------------------------------------------------------------
def test_two_factors_agree_average_is_plus_minus_one() -> None:
    """두 팩터가 같은 방향(000001 우위) → 합성 000001=+1, 000002=-1."""
    panel = _two_ticker_panel()
    settings = get_settings()

    def f_high(p: Panel, tk: str, t: date) -> Decimal | None:
        # 000001 에 더 큰 값을 부여(임의의 상수 매핑)
        return Decimal("100") if tk == "000001" else Decimal("10")

    factors = {"last_close": _last_close, "f_high": f_high}
    scores = dict(_score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors))
    # 두 팩터 모두 000001 을 큰 값으로 → 각 z=+1, 평균 +1
    assert scores["000001"] == Decimal("1")
    assert scores["000002"] == Decimal("-1")


def test_two_factors_oppose_average_cancels() -> None:
    """두 팩터가 반대 방향 → z 가 상쇄돼 합성 0 (pairing 정확성 검증)."""
    panel = _two_ticker_panel()
    settings = get_settings()

    def f_favors_2(p: Panel, tk: str, t: date) -> Decimal | None:
        # 000002 에 더 큰 값 → 000002 z=+1, 000001 z=-1
        return Decimal("100") if tk == "000002" else Decimal("10")

    # last_close 는 000001 우위(z: 000001=+1, 000002=-1).
    # f_favors_2 는 000002 우위(z: 000002=+1, 000001=-1).
    # 합성: 000001=(+1-1)/2=0, 000002=(-1+1)/2=0.
    factors = {"last_close": _last_close, "f_favors_2": f_favors_2}
    scores = dict(_score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors))
    assert scores["000001"] == Decimal("0"), f"상쇄로 0 기대, 실제 {scores['000001']}"
    assert scores["000002"] == Decimal("0"), f"상쇄로 0 기대, 실제 {scores['000002']}"


# ---------------------------------------------------------------------------
# 3. baseline 과 다름
# ---------------------------------------------------------------------------
def test_alpha_composite_differs_from_baseline() -> None:
    """alpha_composite 점수가 baseline 과 달라야 함(no-op 아님)."""
    panel = _two_ticker_panel()
    settings = get_settings()

    factors = {"last_close": _last_close}
    base = _score_at(panel, _T, settings, preset="baseline")
    alpha = _score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors)
    assert base and alpha, "두 프리셋 모두 채점된 후보가 있어야 함"
    assert base != alpha, "alpha_composite 가 baseline 과 점수가 달라야 함"


# ---------------------------------------------------------------------------
# 4. 결측 팩터 처리 — None 종목은 그 팩터에서 제외, 나머지 팩터로 평균
# ---------------------------------------------------------------------------
def test_missing_factor_value_averages_over_remaining() -> None:
    """한 팩터가 특정 종목에 None → 그 종목은 나머지 가용 팩터로 평균(크래시 없음)."""
    panel = _two_ticker_panel()
    settings = get_settings()

    def f_none_for_2(p: Panel, tk: str, t: date) -> Decimal | None:
        # 000002 에는 None → 이 팩터는 present 1개(<2) 라 스킵됨
        return Decimal("50") if tk == "000001" else None

    # last_close: 둘 다 present → z(000001=+1, 000002=-1).
    # f_none_for_2: present 1개(000001) <2 → 전체 스킵.
    # 따라서 합성 = last_close z 만 → 000001=+1, 000002=-1.
    factors = {"last_close": _last_close, "f_none_for_2": f_none_for_2}
    scores = dict(_score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors))
    assert scores["000001"] == Decimal("1")
    assert scores["000002"] == Decimal("-1")


def test_factor_present_for_fewer_than_two_is_skipped() -> None:
    """모든 팩터가 <2 종목에만 present → 모든 종목 점수 0(평균할 z 없음)."""
    panel = _two_ticker_panel()
    settings = get_settings()

    def f_only_1(p: Panel, tk: str, t: date) -> Decimal | None:
        return Decimal("50") if tk == "000001" else None

    factors = {"only_1": f_only_1}
    scores = dict(_score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=factors))
    # present <2 → 스킵 → z 없음 → Decimal("0")
    assert scores["000001"] == Decimal("0")
    assert scores["000002"] == Decimal("0")


# ---------------------------------------------------------------------------
# 5. 가드 — alpha_factors 누락 시 ValueError
# ---------------------------------------------------------------------------
def test_alpha_composite_without_factors_raises() -> None:
    """alpha_factors=None 으로 alpha_composite 호출 시 ValueError."""
    panel = _two_ticker_panel()
    settings = get_settings()
    with pytest.raises(ValueError, match="alpha_composite"):
        _score_at(panel, _T, settings, preset="alpha_composite", alpha_factors=None)


def test_alpha_composite_with_empty_factors_raises() -> None:
    """alpha_factors={} (빈 dict) 도 ValueError(주입 필요)."""
    panel = _two_ticker_panel()
    settings = get_settings()
    with pytest.raises(ValueError, match="alpha_composite"):
        _score_at(panel, _T, settings, preset="alpha_composite", alpha_factors={})


# ---------------------------------------------------------------------------
# 6. compare 경로 스모크 — variant=alpha_composite 가 오류 없이 ComparisonResult 반환
# ---------------------------------------------------------------------------
def test_compare_presets_alpha_composite_smoke() -> None:
    """compare_presets(variant=alpha_composite, alpha_factors=...) 가 오류 없이 동작."""
    from backend.backtest.compare import ComparisonResult, compare_presets

    panel = _two_ticker_panel()
    factors = {"last_close": _last_close}
    cfg = BacktestConfig(
        start=_T,
        end=_T,
        rebalance="monthly",
        top_n=2,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(5,),
        n_resamples=20,
        n_perms=20,
        bootstrap_seed=42,
    )
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))
    result = compare_presets(
        panel, cfg, wf, variant_preset="alpha_composite", alpha_factors=factors
    )
    assert isinstance(result, ComparisonResult)
    assert result.variant_preset == "alpha_composite"
    assert result.baseline_preset == "baseline"
