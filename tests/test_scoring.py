"""``backend.scoring`` 단위 테스트 — 원본 screener 와의 동치성 + 엣지.

검증 전략(원칙: 결정론 — 동일 입력 → 동일 출력):

1. **동치성**: swing-bot ``src/universe/screener.py`` 의 산식
   (compute_momentum/compute_annualized_volatility/simple_moving_average/
   above_ma200/proximity_to_52w_high/pocket_pivot/volatility_fit/_min_max_norm/
   _score_trend_candidates)을 본 모듈로 정확 동치 포팅했다. 원본 환경에서 미리 계산해
   둔 **ground-truth Decimal 문자열**을 골든값으로 박아 회귀를 잡는다.
2. **엣지**: 빈 rows · rows<2 · hi==lo · 변동성 밴드 경계 · 52주 신고가 돌파(분자>분모).
3. **점수**: ``score_candidates`` 가 cross-sectional min-max(turnover/momentum) 후
   가중합(0.30/0.20/0.25/0.15/0.10)을 내고, ineligible 은 0·팩터분해 보존.
4. **하드필터**: 거래대금·모멘텀·200일선·변동성 밴드 + Gap B(신고가 근접 상한 면제).
5. **등급**: settings 임계(75/60/45) 경계.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from backend.config import Settings
from backend.schemas import Grade, OHLCVRow
from backend.scoring import (
    Candidate,
    above_ma200,
    compute_annualized_volatility,
    compute_momentum,
    grade_for_score,
    min_max_norm,
    passes_hard_filter,
    pocket_pivot,
    proximity_to_52w_high,
    score_candidates,
    simple_moving_average,
    volatility_fit,
)
from tests.conftest import make_downtrend_rows, make_rows, make_uptrend_rows

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    """기본값 Settings + 오버라이드. (.env 무시 — 테스트 결정론)."""
    base = Settings(_env_file=None)  # type: ignore[call-arg]
    if overrides:
        return base.model_copy(update=overrides)
    return base


def _exact(closes: list[object], *, volumes: list[object] | None = None) -> list[OHLCVRow]:
    """conftest ``make_rows`` 와 정확히 같은 봉 생성 (동치 골든값 검증용)."""
    return make_rows(closes, volumes=volumes)


# ---------------------------------------------------------------------------
# 1. compute_momentum
# ---------------------------------------------------------------------------


class TestComputeMomentum:
    def test_basic_return(self) -> None:
        rows = make_rows([100, 105, 110])
        assert compute_momentum(rows) == Decimal("0.1")

    def test_empty_rows(self) -> None:
        assert compute_momentum([]) == Decimal("0")

    def test_single_row(self) -> None:
        assert compute_momentum(make_rows([100])) == Decimal("0")

    def test_first_close_zero(self) -> None:
        rows = make_rows([0, 50])
        assert compute_momentum(rows) == Decimal("0")

    def test_negative_momentum(self) -> None:
        rows = make_rows([100, 80])
        assert compute_momentum(rows) == Decimal("-0.2")

    def test_golden_uptrend(self) -> None:
        # 원본 screener 에서 계산한 ground-truth (260봉 우상향, 100..229.5).
        assert compute_momentum(make_uptrend_rows()) == Decimal("1.295")

    def test_golden_downtrend(self) -> None:
        assert compute_momentum(make_downtrend_rows()) == Decimal("-0.6475")


# ---------------------------------------------------------------------------
# 2. compute_annualized_volatility
# ---------------------------------------------------------------------------


class TestComputeAnnualizedVolatility:
    def test_empty(self) -> None:
        assert compute_annualized_volatility([]) == Decimal("0")

    def test_single_row(self) -> None:
        assert compute_annualized_volatility(make_rows([100])) == Decimal("0")

    def test_constant_log_returns_zero_std(self) -> None:
        # 동일 비율 상승(100→110→121)이면 로그수익률이 같아 표준편차 0.
        rows = make_rows([100, 110, 121])
        assert compute_annualized_volatility(rows) == Decimal("0")

    def test_fewer_than_two_valid_log_returns(self) -> None:
        # 비양수 종가가 끼면 로그수익률 표본 부족 → 0.
        rows = make_rows([100, 110])
        assert compute_annualized_volatility(rows) == Decimal("0")

    def test_golden_uptrend(self) -> None:
        assert compute_annualized_volatility(make_uptrend_rows()) == Decimal(
            "0.01235172203125969511087531570"
        )

    def test_golden_noisy(self) -> None:
        noisy = [100, 102, 99, 105, 103, 108, 110, 107, 115, 112, 120, 118, 125]
        assert compute_annualized_volatility(make_rows(noisy)) == Decimal(
            "0.6245675009561442314543143739"
        )


# ---------------------------------------------------------------------------
# 3. simple_moving_average
# ---------------------------------------------------------------------------


class TestSimpleMovingAverage:
    def test_insufficient_data_returns_none(self) -> None:
        assert simple_moving_average(make_rows([100, 110]), 5) is None

    def test_zero_window_returns_none(self) -> None:
        assert simple_moving_average(make_rows([100, 110, 120]), 0) is None

    def test_last_window_only(self) -> None:
        rows = make_rows([10, 20, 30, 40, 50])
        # 최근 3봉 평균 = (30+40+50)/3 = 40.
        assert simple_moving_average(rows, 3) == Decimal("40")

    def test_golden_sma5_uptrend(self) -> None:
        assert simple_moving_average(make_uptrend_rows(), 5) == Decimal("228.5")


# ---------------------------------------------------------------------------
# 4. above_ma200
# ---------------------------------------------------------------------------


class TestAboveMa200:
    def test_uptrend_above(self) -> None:
        assert above_ma200(make_uptrend_rows(), 200) is True

    def test_downtrend_below(self) -> None:
        assert above_ma200(make_downtrend_rows(), 200) is False

    def test_insufficient_data_false(self) -> None:
        assert above_ma200(make_rows([100, 110, 120]), 200) is False

    def test_equal_not_above(self) -> None:
        # 모든 종가 동일 → MA == close, "초과(>)" 아님 → False.
        rows = make_rows([100] * 10)
        assert above_ma200(rows, 5) is False


# ---------------------------------------------------------------------------
# 5. proximity_to_52w_high
# ---------------------------------------------------------------------------


class TestProximityTo52wHigh:
    def test_empty_rows(self) -> None:
        assert proximity_to_52w_high([]) == Decimal("0")

    def test_uses_rows_high_when_no_fixed(self) -> None:
        # 마지막 종가 == 최고가 근접 → 1 근처.
        assert proximity_to_52w_high(make_uptrend_rows()) == Decimal(
            "0.9950248756218905472636815920"
        )

    def test_fixed_high(self) -> None:
        rows = make_rows([100, 200, 300])
        # current=300, denom=300 → 1.
        assert proximity_to_52w_high(rows, high_52w=Decimal("300")) == Decimal("1")

    def test_breakout_above_fixed_high_clipped_to_one(self) -> None:
        # 현재가가 분모(52주 고가)를 돌파 → ratio>1 이지만 1 로 clip.
        rows = make_rows([100, 200, 300])
        assert proximity_to_52w_high(rows, high_52w=Decimal("150")) == Decimal("1")

    def test_nonpositive_fixed_high_falls_back_to_rows(self) -> None:
        # high_52w<=0 이면 무시하고 rows 최고가 사용.
        rows = make_rows([100, 200, 300])
        result = proximity_to_52w_high(rows, high_52w=Decimal("0"))
        # rows 최고가는 300*1.005 (양봉 high) → current/denom < 1.
        assert Decimal("0") < result < Decimal("1")

    def test_below_high(self) -> None:
        rows = make_rows([100, 110, 80])
        # 음봉 후 종가 80, 최고가는 110 부근 → < 1.
        assert proximity_to_52w_high(rows) < Decimal("1")


# ---------------------------------------------------------------------------
# 6. pocket_pivot
# ---------------------------------------------------------------------------


class TestPocketPivot:
    def test_insufficient_data(self) -> None:
        # lookback+1 봉 미만 → False.
        assert pocket_pivot(make_rows([100] * 5), lookback=10) is False

    def test_today_not_bullish(self) -> None:
        # 마지막 봉이 음봉(하락)이면 False.
        closes = [100] * 10 + [90]
        assert pocket_pivot(make_rows(closes), lookback=10) is False

    def test_bullish_volume_dominates_down_days(self) -> None:
        # 직전 10봉 중 음봉 거래량보다 큰 양봉 거래량 → True.
        closes = [100, 98, 99, 97, 100, 96, 101, 95, 102, 94, 110]
        vols = [1000, 500, 1000, 500, 1000, 500, 1000, 500, 1000, 500, 2000]
        assert pocket_pivot(make_rows(closes, volumes=vols), lookback=10) is True

    def test_no_down_days_bullish_passes(self) -> None:
        # 직전 구간 전부 양봉(음봉 0) → 기준 0, 오늘 양봉이면 True.
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 120]
        assert pocket_pivot(make_rows(closes), lookback=10) is True

    def test_bullish_but_volume_too_small(self) -> None:
        # 양봉이지만 거래량이 직전 음봉 최대치 이하 → False.
        closes = [100, 98, 99, 97, 100, 96, 101, 95, 102, 94, 110]
        vols = [1000, 5000, 1000, 5000, 1000, 5000, 1000, 5000, 1000, 5000, 2000]
        assert pocket_pivot(make_rows(closes, volumes=vols), lookback=10) is False

    def test_golden_uptrend_true(self) -> None:
        assert pocket_pivot(make_uptrend_rows(), lookback=10) is True


# ---------------------------------------------------------------------------
# 7. volatility_fit — 삼각형 gradient + 밴드 경계
# ---------------------------------------------------------------------------


class TestVolatilityFit:
    LOW = Decimal("0.20")
    HIGH = Decimal("0.60")

    def test_center_is_one(self) -> None:
        assert volatility_fit(Decimal("0.40"), self.LOW, self.HIGH) == Decimal("1")

    def test_lower_edge_is_zero(self) -> None:
        assert volatility_fit(self.LOW, self.LOW, self.HIGH) == Decimal("0")

    def test_upper_edge_is_zero(self) -> None:
        assert volatility_fit(self.HIGH, self.LOW, self.HIGH) == Decimal("0")

    def test_below_band_zero(self) -> None:
        assert volatility_fit(Decimal("0.10"), self.LOW, self.HIGH) == Decimal("0")

    def test_above_band_zero(self) -> None:
        assert volatility_fit(Decimal("0.70"), self.LOW, self.HIGH) == Decimal("0")

    def test_half_point(self) -> None:
        # center=0.4, half_width=0.2, value=0.3 → 1 - 0.1/0.2 = 0.5.
        assert volatility_fit(Decimal("0.30"), self.LOW, self.HIGH) == Decimal("0.5")
        assert volatility_fit(Decimal("0.50"), self.LOW, self.HIGH) == Decimal("0.5")

    def test_zero_width_band_returns_one_inside(self) -> None:
        # low==high 면 half_width 0 → 밴드값(=경계)에서 1.
        assert volatility_fit(Decimal("0.40"), Decimal("0.40"), Decimal("0.40")) == Decimal("1")


# ---------------------------------------------------------------------------
# 8. min_max_norm — hi==lo 동등 처리 + clip
# ---------------------------------------------------------------------------


class TestMinMaxNorm:
    def test_midpoint(self) -> None:
        assert min_max_norm(Decimal("5"), Decimal("0"), Decimal("10")) == Decimal("0.5")

    def test_clip_low(self) -> None:
        assert min_max_norm(Decimal("-1"), Decimal("0"), Decimal("10")) == Decimal("0")

    def test_clip_high(self) -> None:
        assert min_max_norm(Decimal("20"), Decimal("0"), Decimal("10")) == Decimal("1")

    def test_at_low_is_zero(self) -> None:
        assert min_max_norm(Decimal("0"), Decimal("0"), Decimal("10")) == Decimal("0")

    def test_at_high_is_one(self) -> None:
        assert min_max_norm(Decimal("10"), Decimal("0"), Decimal("10")) == Decimal("1")

    def test_hi_equals_lo_value_at_or_above(self) -> None:
        # 단일 생존자/동률 — value >= lo 면 1 (0 collapse 방지).
        assert min_max_norm(Decimal("5"), Decimal("5"), Decimal("5")) == Decimal("1")

    def test_hi_equals_lo_value_below(self) -> None:
        assert min_max_norm(Decimal("4"), Decimal("5"), Decimal("5")) == Decimal("0")


# ---------------------------------------------------------------------------
# 9. passes_hard_filter — 거래대금·모멘텀·200일선·변동성 밴드 + Gap B
# ---------------------------------------------------------------------------


class TestPassesHardFilter:
    def _kw(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "turnover": Decimal("20000000000"),  # 200억 (임계 100억 위)
            "momentum": Decimal("0.1"),
            "volatility": Decimal("0.40"),  # 밴드 중심
            "near_52w": Decimal("0.5"),
            "above_ma200_flag": True,
            "min_turnover": Decimal("10000000000"),  # KR 임계(100억) — 시장별 인자
        }
        base.update(overrides)
        return base

    def test_all_pass(self) -> None:
        assert passes_hard_filter(settings=_settings(), **self._kw()) is True  # type: ignore[arg-type]

    def test_turnover_below_threshold(self) -> None:
        kw = self._kw(turnover=Decimal("9999999999"))
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_turnover_exactly_threshold_passes(self) -> None:
        kw = self._kw(turnover=Decimal("10000000000"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]

    def test_min_turnover_argument_is_market_specific(self) -> None:
        # 거래대금 임계는 인자(min_turnover)로 받는다 — US 는 USD 임계(3천만)라
        # KR(100억 KRW)과 별개. USD 임계로 비교하면 3천만 미만은 탈락, 이상은 통과.
        s = _settings()
        us_min = s.min_turnover_usd
        below = self._kw(turnover=us_min - Decimal("1"), min_turnover=us_min)
        at = self._kw(turnover=us_min, min_turnover=us_min)
        assert passes_hard_filter(settings=s, **below) is False  # type: ignore[arg-type]
        assert passes_hard_filter(settings=s, **at) is True  # type: ignore[arg-type]

    def test_momentum_below_min(self) -> None:
        kw = self._kw(momentum=Decimal("-0.01"))
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_momentum_exactly_min_passes(self) -> None:
        kw = self._kw(momentum=Decimal("0"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]

    def test_not_above_ma200(self) -> None:
        kw = self._kw(above_ma200_flag=False)
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_volatility_below_lower_band(self) -> None:
        kw = self._kw(volatility=Decimal("0.10"))
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_volatility_at_lower_band_passes(self) -> None:
        kw = self._kw(volatility=Decimal("0.20"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]

    def test_volatility_at_upper_band_passes(self) -> None:
        kw = self._kw(volatility=Decimal("0.60"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]

    def test_volatility_above_upper_band_rejected_without_gap_b(self) -> None:
        kw = self._kw(volatility=Decimal("0.80"), near_52w=Decimal("0.5"))
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_gap_b_high_volatility_leader_passes(self) -> None:
        # near_52w >= breakout_52w_min(0.90) → 변동성 상한 면제.
        kw = self._kw(volatility=Decimal("0.80"), near_52w=Decimal("0.95"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]

    def test_gap_b_does_not_exempt_lower_band(self) -> None:
        # Gap B 는 상한만 면제 — 하한 미달은 신고가 근접이어도 탈락.
        kw = self._kw(volatility=Decimal("0.10"), near_52w=Decimal("0.99"))
        assert passes_hard_filter(settings=_settings(), **kw) is False  # type: ignore[arg-type]

    def test_gap_b_boundary_near52w_exact(self) -> None:
        # near_52w 가 정확히 임계(0.90)면 면제 적용(>=).
        kw = self._kw(volatility=Decimal("0.80"), near_52w=Decimal("0.90"))
        assert passes_hard_filter(settings=_settings(), **kw) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 10. score_candidates — cross-sectional min-max + 가중합 + ineligible
# ---------------------------------------------------------------------------


class TestScoreCandidates:
    def test_empty(self) -> None:
        assert score_candidates([], _settings()) == {}

    def test_single_candidate_perfect_norms(self) -> None:
        # 단일 후보 → turnover/momentum min==max → norm 모두 1.
        cand = Candidate(
            ticker="A",
            turnover=Decimal("20000000000"),
            momentum=Decimal("0.3"),
            volatility=Decimal("0.40"),  # vol_fit 중심 → 1
            near_52w=Decimal("1"),
            has_pocket_pivot=True,
            above_ma200=True,
            eligible=True,
        )
        result = score_candidates([cand], _settings())
        score, breakdown = result["A"]
        # near_52w(1*0.30)+pp(1*0.20)+mom(1*0.25)+to(1*0.15)+vol_fit(1*0.10) = 1.0
        assert score == Decimal("1")
        assert breakdown.near_52w == Decimal("1")
        assert breakdown.pocket_pivot == Decimal("1")
        assert breakdown.momentum_norm == Decimal("1")
        assert breakdown.turnover_norm == Decimal("1")
        assert breakdown.vol_fit == Decimal("1")
        assert breakdown.momentum == Decimal("0.3")
        assert breakdown.volatility == Decimal("0.40")
        assert breakdown.above_ma200 is True  # 실제 200일선 위 여부(eligible 아님)

    def test_cross_sectional_normalization(self) -> None:
        # 두 후보: turnover/momentum 의 min/max 로 정규화되는지.
        lo = Candidate(
            ticker="LO",
            turnover=Decimal("10000000000"),
            momentum=Decimal("0.0"),
            volatility=Decimal("0.40"),
            near_52w=Decimal("0"),
            has_pocket_pivot=False,
            above_ma200=True,
            eligible=True,
        )
        hi = Candidate(
            ticker="HI",
            turnover=Decimal("30000000000"),
            momentum=Decimal("0.5"),
            volatility=Decimal("0.40"),
            near_52w=Decimal("0"),
            has_pocket_pivot=False,
            above_ma200=True,
            eligible=True,
        )
        result = score_candidates([lo, hi], _settings())
        # LO: turnover_norm=0, momentum_norm=0 → near0*0.30 + pp0 + mom0 + to0 + volfit(1)*0.10
        lo_score, lo_bd = result["LO"]
        assert lo_bd.turnover_norm == Decimal("0")
        assert lo_bd.momentum_norm == Decimal("0")
        assert lo_score == Decimal("0.10")
        # HI: turnover_norm=1, momentum_norm=1 → mom(1*0.25)+to(1*0.15)+volfit(0.10) = 0.50
        hi_score, hi_bd = result["HI"]
        assert hi_bd.turnover_norm == Decimal("1")
        assert hi_bd.momentum_norm == Decimal("1")
        assert hi_score == Decimal("0.50")

    def test_ineligible_scores_zero_but_keeps_factors(self) -> None:
        cand = Candidate(
            ticker="X",
            turnover=Decimal("20000000000"),
            momentum=Decimal("0.3"),
            volatility=Decimal("0.40"),
            near_52w=Decimal("1"),
            has_pocket_pivot=True,
            above_ma200=False,  # 200일선 아래 → 부적격
            eligible=False,
        )
        result = score_candidates([cand], _settings())
        score, breakdown = result["X"]
        assert score == Decimal("0")
        # 팩터 분해(원시값·정규화)는 보존.
        assert breakdown.near_52w == Decimal("1")
        assert breakdown.pocket_pivot == Decimal("1")
        assert breakdown.momentum == Decimal("0.3")
        assert breakdown.volatility == Decimal("0.40")
        # above_ma200 은 실제 200일선 위 여부(eligible 가 아님).
        assert breakdown.above_ma200 is False

    def test_equivalence_with_reference_score_formula(self) -> None:
        # 원본 _score_trend_candidates 의 가중합과 동치(가중치 0.30/0.20/0.25/0.15/0.10).
        # 후보 3개 — 정규화·vol_fit 까지 포함한 골든 점수.
        c1 = Candidate(
            ticker="C1",
            turnover=Decimal("10000000000"),
            momentum=Decimal("0.0"),
            volatility=Decimal("0.30"),  # vol_fit = 0.5
            near_52w=Decimal("0.5"),
            has_pocket_pivot=False,
            above_ma200=True,
            eligible=True,
        )
        c2 = Candidate(
            ticker="C2",
            turnover=Decimal("20000000000"),
            momentum=Decimal("0.2"),
            volatility=Decimal("0.40"),  # vol_fit = 1
            near_52w=Decimal("1.0"),
            has_pocket_pivot=True,
            above_ma200=True,
            eligible=True,
        )
        c3 = Candidate(
            ticker="C3",
            turnover=Decimal("30000000000"),
            momentum=Decimal("0.4"),
            volatility=Decimal("0.50"),  # vol_fit = 0.5
            near_52w=Decimal("0.8"),
            has_pocket_pivot=False,
            above_ma200=True,
            eligible=True,
        )
        result = score_candidates([c1, c2, c3], _settings())
        # 정규화: turnover lo=1e10,hi=3e10 ; momentum lo=0,hi=0.4
        # C1: to_norm=0, mom_norm=0
        #   near 0.5*0.30=0.15 + pp 0 + mom 0 + to 0 + volfit 0.5*0.10=0.05 → 0.20
        assert result["C1"][0] == Decimal("0.20")
        # C2: to_norm=0.5, mom_norm=0.5
        #   near 1.0*0.30=0.30 + pp 1*0.20=0.20 + mom 0.5*0.25=0.125
        #     + to 0.5*0.15=0.075 + volfit 1*0.10=0.10 → 0.80
        assert result["C2"][0] == Decimal("0.80")
        # C3: to_norm=1, mom_norm=1
        #   near 0.8*0.30=0.24 + pp 0 + mom 1*0.25=0.25 + to 1*0.15=0.15
        #     + volfit 0.5*0.10=0.05 → 0.69
        assert result["C3"][0] == Decimal("0.69")

    def test_score_clamped_to_unit_interval(self) -> None:
        # 모든 팩터 만점 → 1.0 을 초과하지 않음(clamp).
        cand = Candidate(
            ticker="MAX",
            turnover=Decimal("1"),
            momentum=Decimal("1"),
            volatility=Decimal("0.40"),
            near_52w=Decimal("1"),
            has_pocket_pivot=True,
            above_ma200=True,
            eligible=True,
        )
        score, _ = score_candidates([cand], _settings())["MAX"]
        assert Decimal("0") <= score <= Decimal("1")


# ---------------------------------------------------------------------------
# 11. grade_for_score — 임계 경계
# ---------------------------------------------------------------------------


class TestGradeForScore:
    def test_strong_buy_at_threshold(self) -> None:
        assert grade_for_score(Decimal("75"), _settings()) == Grade.STRONG_BUY

    def test_strong_buy_above(self) -> None:
        assert grade_for_score(Decimal("90"), _settings()) == Grade.STRONG_BUY

    def test_buy_at_threshold(self) -> None:
        assert grade_for_score(Decimal("60"), _settings()) == Grade.BUY

    def test_buy_just_below_strong(self) -> None:
        assert grade_for_score(Decimal("74.99"), _settings()) == Grade.BUY

    def test_hold_at_threshold(self) -> None:
        assert grade_for_score(Decimal("45"), _settings()) == Grade.HOLD

    def test_hold_just_below_buy(self) -> None:
        assert grade_for_score(Decimal("59.99"), _settings()) == Grade.HOLD

    def test_avoid_below_hold(self) -> None:
        assert grade_for_score(Decimal("44.99"), _settings()) == Grade.AVOID

    def test_avoid_zero(self) -> None:
        assert grade_for_score(Decimal("0"), _settings()) == Grade.AVOID

    def test_custom_thresholds(self) -> None:
        s = _settings(grade_strong_buy=Decimal("80"), grade_buy=Decimal("50"))
        assert grade_for_score(Decimal("79"), s) == Grade.BUY
        assert grade_for_score(Decimal("80"), s) == Grade.STRONG_BUY


# ---------------------------------------------------------------------------
# 12. 잡 엣지 — 빈 입력 일관성
# ---------------------------------------------------------------------------


def test_empty_rows_all_factors_safe() -> None:
    """빈 rows 가 어떤 팩터에도 예외를 내지 않는다(전부 보수적 0/None/False)."""
    empty: list[OHLCVRow] = []
    assert compute_momentum(empty) == Decimal("0")
    assert compute_annualized_volatility(empty) == Decimal("0")
    assert simple_moving_average(empty, 5) is None
    assert above_ma200(empty) is False
    assert proximity_to_52w_high(empty) == Decimal("0")
    assert pocket_pivot(empty) is False


def test_make_rows_dates_ascending() -> None:
    """conftest 헬퍼 sanity — 날짜 오름차순·길이 일치."""
    rows = make_rows([1, 2, 3])
    assert [r.date for r in rows] == [date(2024, 1, 1) + timedelta(days=i) for i in range(3)]
    assert len(rows) == 3


@pytest.mark.parametrize(
    ("closes", "expected_sign"),
    [
        ([100, 110], "pos"),
        ([100, 90], "neg"),
        ([100, 100], "zero"),
    ],
)
def test_momentum_sign(closes: list[int], expected_sign: str) -> None:
    mom = compute_momentum(_exact(closes))
    if expected_sign == "pos":
        assert mom > 0
    elif expected_sign == "neg":
        assert mom < 0
    else:
        assert mom == 0
