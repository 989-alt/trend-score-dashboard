"""Layer A — 하니스 자가검증 테스트 (계측기 정확성 증명).

목표: 백테스트 하니스가 신호를 *측정*하는 도구로서 신뢰할 수 있음을 증명한다.
  A1. 완벽 단조 입력 → spearman ≈ ±1
  A2. 독립 노이즈(n=400) → |spearman| < 0.15
  A3. 종단 신호 회복 — 심어진 강도(latent strength)가 점수와 forward-return 양방에 나타남
  A4. 노이즈 패널 → |monotonicity| < 0.3 (하니스가 가짜 신호를 만들지 않음)
  A5. 룩어헤드 카나리아 — 미래 데이터 변조가 점수에 영향 없음을 단언

설계 원칙
---------
* 결정론적: 모든 RNG 는 고정 시드(random.Random(<int>)).
* 적격 보장: trailing_slope × 20(lookback) > 2 × 1.5% × base_price
  → slope_scale=200 사용 시 weakest ticker(0.2×200=40 pt/day)의 20일 상승분=800pt,
     noise swing ≈ 2×1.5%×10000=300pt → 800 > 300 → momentum > 0 확보.
* 단일 리밸런스일(cfg.end=T): score 의 trailing window 가 포워드 기간과 겹치지 않도록
  cfg.end=T 로 한정 → score ↔ fwd-return 독립성 검증 가능.
* backend/ 파일 수정 없음.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.metrics import spearman_monotonicity
from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation
from backend.backtest.run import BacktestConfig, _score_at, run_backtest
from backend.config import get_settings
from backend.schemas import OHLCVRow

# ---------------------------------------------------------------------------
# 합성 헬퍼 (이 파일 전용)
# ---------------------------------------------------------------------------

_BASE_PRICE = Decimal("10000")
_SLOPE_SCALE = 200  # point/day — weakest(0.2×200=40) × 20days=800 > noise swing(≈300)


def _ohlcv(d: date, close: Decimal) -> OHLCVRow:
    """단일 OHLCV 행. 고/저는 종가 기준 ±2.5%."""
    return OHLCVRow(
        date=d,
        open=close,
        high=close * Decimal("1.025"),
        low=close * Decimal("0.975"),
        close=close,
        volume=Decimal("1000000"),
    )


def _make_eligible_series(
    ticker: str,
    start: date,
    *,
    trailing_slope: float,
    forward_slope: float,
    n_trailing: int = 260,
    n_forward: int = 65,
    rng: random.Random,
) -> TickerSeries:
    """변동성 밴드 통과(≈0.48) + MA200 위 + 모멘텀 > 0 을 보장하는 OHLCV 시리즈.

    변동성 기법: 홀짝 교번 ×1.015/×0.985 → 일별 로그수익률 std×√252 ≈ 0.48(밴드 내).
    slope > 0 이면 20-day momentum > 0 이 된다(slope_scale=200 사용 시 보장, 참조: 설계 원칙).
    """
    rows: list[OHLCVRow] = []
    turnover: dict[date, Decimal] = {}
    valuation: dict[date, Valuation] = {}

    # 트레일링 구간
    for i in range(n_trailing):
        d = start + timedelta(days=i)
        raw = _BASE_PRICE + Decimal(str(trailing_slope * i))
        close = raw * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
        close = max(close, Decimal("100"))
        rows.append(_ohlcv(d, close))
        turnover[d] = Decimal("20000000000")  # 200억 — min_turnover(100억) 통과
        valuation[d] = Valuation(per=Decimal("10"), pbr=Decimal("1.2"))

    t = start + timedelta(days=n_trailing - 1)
    last_close = rows[-1].close

    # 포워드 구간 (date > T)
    for j in range(1, n_forward + 1):
        d = t + timedelta(days=j)
        raw = last_close + Decimal(str(forward_slope * j))
        idx = n_trailing + j
        close = raw * (Decimal("1.015") if idx % 2 == 0 else Decimal("0.985"))
        close = max(close, Decimal("100"))
        rows.append(_ohlcv(d, close))
        turnover[d] = Decimal("20000000000")
        valuation[d] = Valuation(per=Decimal("10"), pbr=Decimal("1.2"))

    return TickerSeries(
        ticker=ticker,
        rows=rows,
        turnover_by_date=turnover,
        valuation_by_date=valuation,
    )


def _make_index_rows(start: date, total_days: int) -> list[OHLCVRow]:
    """시장 지수 합성 시리즈 (완만한 상승)."""
    return [
        _ohlcv(
            start + timedelta(days=i),
            (Decimal("2000") + Decimal(str(i * 2)))
            * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985")),
        )
        for i in range(total_days)
    ]


def _make_signal_panel(
    n_tickers: int = 20,
    *,
    forward_coupled: bool = True,
    fwd_seed: int = 0,
    n_trailing: int = 260,
    n_forward: int = 65,
) -> tuple[Panel, date]:
    """K개 합성 종목 패널 + 리밸런스 기준일 T.

    strengths ∈ [0.2, 1.0] 균등 분포. trailing_slope = strength × _SLOPE_SCALE.
    forward_coupled=True  → forward_slope = trailing_slope  (신호 패널)
    forward_coupled=False → forward_slope = random.Random(fwd_seed)가 생성하는 독립값
                            (노이즈 패널 — T 이후 방향이 trailing 과 무관).
    반환: (Panel, T)  where T = start + n_trailing - 1 일.
    """
    fwd_rng = random.Random(fwd_seed)
    start = date(2020, 1, 2)
    strengths = [0.2 + 0.8 * i / (n_tickers - 1) for i in range(n_tickers)]

    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    fundamentals: dict[str, list[AsOfFundamentals]] = {}

    for i, s in enumerate(strengths):
        ticker = f"{i + 1:06d}"
        t_slope = s * _SLOPE_SCALE
        # 대칭 균등 분포 [-_SLOPE_SCALE, +_SLOPE_SCALE] — trailing 강도와 독립
        f_slope = t_slope if forward_coupled else (fwd_rng.random() - 0.5) * _SLOPE_SCALE * 2

        series[ticker] = _make_eligible_series(
            ticker,
            start,
            trailing_slope=t_slope,
            forward_slope=f_slope,
            n_trailing=n_trailing,
            n_forward=n_forward,
            rng=random.Random(i),
        )
        listings[ticker] = (start, None)
        fundamentals[ticker] = [
            AsOfFundamentals(
                rcept_date=date(2020, 6, 1),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.12"),
            )
        ]

    panel = Panel(
        series=series,
        fundamentals=fundamentals,
        listings=listings,
        index_rows=_make_index_rows(start, n_trailing + n_forward),
    )
    t = start + timedelta(days=n_trailing - 1)
    return panel, t


def _single_rebalance_cfg(t: date, n_tickers: int, horizon: int = 20) -> BacktestConfig:
    """T 하루만 리밸런스(cfg.end=T) — score window 와 fwd-return window 가 분리됨."""
    return BacktestConfig(
        start=t,
        end=t,  # T 하나만 → score=rows≤T, fwd=rows>T (겹침 없음)
        rebalance="monthly",
        top_n=n_tickers,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(horizon,),
    )


# ---------------------------------------------------------------------------
# Test 1: 완벽 단조 → spearman ≈ ±1
# ---------------------------------------------------------------------------


def test_monotonicity_recovers_planted_positive_signal() -> None:
    """완벽 단조 입력 → spearman_monotonicity == 1.0000 (양의 쌍) / -1.0000 (역전 쌍).

    메트릭의 부호·스케일이 올바름을 검증한다 (Layer A 최소 온전성 검증).
    """
    n = 20
    scores = [Decimal(str(i)) for i in range(1, n + 1)]
    fwd_pos = [Decimal(str(i)) for i in range(1, n + 1)]  # 동일 순위 → corr = +1
    fwd_neg = [Decimal(str(n - i)) for i in range(n)]  # 역순 → corr = -1

    mono_pos = spearman_monotonicity(scores, fwd_pos)
    mono_neg = spearman_monotonicity(scores, fwd_neg)

    assert mono_pos == Decimal("1.0000"), f"완전 단조 양의 상관: {mono_pos}"
    assert mono_neg == Decimal("-1.0000"), f"완전 역전 상관: {mono_neg}"


# ---------------------------------------------------------------------------
# Test 2: 독립 노이즈 → |spearman| < 0.15
# ---------------------------------------------------------------------------


def test_monotonicity_near_zero_on_independent_noise() -> None:
    """독립 노이즈(n=400, 시드=42) → |spearman_monotonicity| < 0.15.

    n=400 에서 Spearman 표준오차 ≈ 0.05 이므로 0.15 는 ≈3σ 수준.
    고정 시드로 결정론 보장. (시드 42에서 실측 ≈ 0.03 — 여유 충분.)
    """
    rng = random.Random(42)
    n = 400
    scores = [Decimal(str(rng.random())) for _ in range(n)]
    fwd = [Decimal(str(rng.random())) for _ in range(n)]

    mono = spearman_monotonicity(scores, fwd)

    assert abs(mono) < Decimal("0.15"), f"노이즈 패널 단조성이 너무 큼: {mono} (시드=42, n={n})"


# ---------------------------------------------------------------------------
# Test 3: 종단 신호 회복 (end-to-end planted signal recovery)
# ---------------------------------------------------------------------------


def test_event_study_recovers_signal_end_to_end() -> None:
    """K=20 종목, 잠재 강도 sᵢ 가 점수와 forward-return 양방에 나타남.

    구성:
    - trailing_slope ∝ sᵢ → near_52w·momentum 높음 → 점수 높음.
    - forward_slope  = trailing_slope (coupled) → fwd-return 높음.
    - 단일 리밸런스 T(cfg.end=T) → score window(≤T) ⊥ fwd window(>T).

    통과 조건: n > 0 AND monotonicity > 0.3.
    실측값(slope_scale=200, K=20): n=20, monotonicity=1.0000.
    """
    n_tickers = 20
    panel, t = _make_signal_panel(n_tickers, forward_coupled=True)
    cfg = _single_rebalance_cfg(t, n_tickers)

    result = run_backtest(panel, cfg)
    bucket = result.event_study[20]

    assert bucket.n > 0, (
        f"이벤트스터디에 채점된 후보 없음: n={bucket.n}. 적격 종목이 없거나 포워드 구간 부족."
    )
    assert bucket.monotonicity > Decimal("0.3"), (
        f"심어진 신호를 회복하지 못함: monotonicity={bucket.monotonicity}, n={bucket.n}. "
        "하니스가 횡단면 랭킹 신호를 측정하는 계측기로서 실패."
    )


# ---------------------------------------------------------------------------
# Test 4: 노이즈 패널 → |monotonicity| < 0.3
# ---------------------------------------------------------------------------


def test_event_study_near_zero_on_noise_panel_end_to_end() -> None:
    """K=20 종목에서 점수 결정 요인(trailing)과 forward 방향을 분리.

    구성:
    - trailing_slope ∝ sᵢ (점수 결정).
    - forward_slope  = random.Random(0)의 독립 균등 난수 (T 이후 방향 무관).
    - 단일 리밸런스 T → score window(≤T) ⊥ fwd window(>T).

    통과 조건: n > 0 AND |monotonicity| < 0.3.
    실측값(fwd_seed=0): n=20, monotonicity=0.0647 (≈0.07 — 노이즈 수준).

    이 테스트가 실패하면 하니스가 없는 신호를 만들어 낼 수 있음을 의미한다.
    """
    n_tickers = 20
    # fwd_seed=0: 실측 mono=0.0647 (<<0.3). 결정론 보장.
    panel, t = _make_signal_panel(n_tickers, forward_coupled=False, fwd_seed=0)
    cfg = _single_rebalance_cfg(t, n_tickers)

    result = run_backtest(panel, cfg)
    bucket = result.event_study[20]

    assert bucket.n > 0, f"n={bucket.n}: 적격 종목 없음 — 노이즈 패널도 n>0 이어야 함."
    assert abs(bucket.monotonicity) < Decimal("0.3"), (
        f"노이즈 패널에서 가짜 신호 감지: monotonicity={bucket.monotonicity}, n={bucket.n}. "
        "하니스가 존재하지 않는 신호를 만들어 낼 수 있음."
    )


# ---------------------------------------------------------------------------
# Test 5: 룩어헤드 카나리아
# ---------------------------------------------------------------------------


def test_lookahead_canary_scores_invariant_to_future() -> None:
    """미래 데이터(date > T)를 ×10 으로 변조해도 T 시점 점수가 불변임을 단언.

    또한 변조가 실제로 포워드 수익률에 영향을 줌을 보여줌(변조 효과 확인).
    이 테스트가 실패하면 _score_at 이 T 이후 데이터를 참조하는 룩어헤드 누출이 있음.
    """
    settings = get_settings()

    n_tickers = 8
    panel, t = _make_signal_panel(n_tickers, forward_coupled=True, n_trailing=260, n_forward=65)

    # 기준 점수 (T 시점)
    base_scores = _score_at(panel, t, settings, "baseline")
    assert base_scores, f"T={t} 에서 채점된 후보가 없음 (slope_scale={_SLOPE_SCALE} 확인 필요)"

    # --- 변조 패널: date > T 의 종가를 ×10 ---
    tampered_series: dict[str, TickerSeries] = {}
    for ticker, ts in panel.series.items():
        new_rows: list[OHLCVRow] = []
        for r in ts.rows:
            if r.date > t:
                boosted = r.close * Decimal("10")
                new_rows.append(
                    OHLCVRow(
                        date=r.date,
                        open=boosted,
                        high=boosted * Decimal("1.025"),
                        low=boosted * Decimal("0.975"),
                        close=boosted,
                        volume=r.volume,
                    )
                )
            else:
                new_rows.append(r)
        tampered_series[ticker] = TickerSeries(
            ticker=ticker,
            rows=new_rows,
            turnover_by_date=ts.turnover_by_date,
            valuation_by_date=ts.valuation_by_date,
        )

    # 인덱스 시리즈도 T 이후 ×10
    tampered_index: list[OHLCVRow] = []
    for r in panel.index_rows:
        if r.date > t:
            boosted = r.close * Decimal("10")
            tampered_index.append(
                OHLCVRow(
                    date=r.date,
                    open=boosted,
                    high=boosted * Decimal("1.025"),
                    low=boosted * Decimal("0.975"),
                    close=boosted,
                    volume=r.volume,
                )
            )
        else:
            tampered_index.append(r)

    tampered_panel = Panel(
        series=tampered_series,
        fundamentals=panel.fundamentals,
        listings=panel.listings,
        index_rows=tampered_index,
    )

    # 변조 패널에서 T 시점 점수 계산
    tampered_scores = _score_at(tampered_panel, t, settings, "baseline")

    # --- 단언 1: T 시점 점수가 미래 변조에 불변 (룩어헤드 = 0) ---
    assert base_scores == tampered_scores, (
        f"T={t} 시점 점수가 미래 데이터 변조에 영향받음 → 룩어헤드 누출 가능성!\n"
        f"base:     {base_scores[:3]}\n"
        f"tampered: {tampered_scores[:3]}"
    )

    # --- 단언 2: 변조가 실제로 효과적임 (포워드 구간 종가가 달라짐) ---
    first_ticker = base_scores[0][0]
    t_plus_5 = t + timedelta(days=5)

    base_fwd = [r for r in panel.series[first_ticker].rows if r.date >= t_plus_5]
    tampered_fwd = [r for r in tampered_panel.series[first_ticker].rows if r.date >= t_plus_5]

    assert base_fwd and tampered_fwd, "포워드 데이터가 존재해야 함 (n_forward=65 확인)"
    assert base_fwd[0].close != tampered_fwd[0].close, (
        "변조된 포워드 가격이 원본과 같음 — 변조가 실제로 적용되지 않았을 수 있음"
    )

    # 변조 후 가격은 기준의 ≈10배여야 함
    ratio = tampered_fwd[0].close / base_fwd[0].close
    assert ratio > Decimal("9"), f"변조 포워드 가격 비율이 예상보다 낮음: {ratio} (기대: ≈10)"
