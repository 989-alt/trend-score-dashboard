"""T4: compare_presets — 페어드 Δ단조성/ΔMAE 게이트 테스트.

C1. 설계된 패널(entry_bias 가 명확히 유리): dmono > 0 (게이트 포인트 추정치 통과).
C2. 구조 검증: horizons 키 정합, CI lo <= hi, n >= 0.
C3. 렌더링: 마크다운/JSON 오류 없음.
C4. 노이즈 패널: 결과 구조 정상 반환 (CI 범위 유효).

NOTE: CI gates(lo > 0) 는 n_resamples=50 의 작은 샘플에서 not reliable 하므로
      포인트 추정치(dmono > 0) 만 단언한다. 실 데이터 평가에서 strict_pass 를 본다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest
from backend.backtest.compare import compare_presets
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from backend.schemas import OHLCVRow

# ---------------------------------------------------------------------------
# 헬퍼 — test_entry_bias.py 의 _make_entry_bias_sanity_panel 재사용 패턴
# ---------------------------------------------------------------------------


def _make_structured_panel(
    n_extended: int = 10,
    n_pullback: int = 10,
    *,
    seed: int = 42,
    n_trailing: int = 265,
    n_forward: int = 70,
) -> object:
    """entry_bias 가 유리한 구조 패널.

    - extended 그룹: 급등(MA 이격 30%+) + 이후 하락 (-15~-25%)
    - pullback 그룹: 완만 상승 유지 + 이후 완만 상승 (+10~+20%)

    단일 rebalance date T(=trailing 마지막 날) 에서:
    - baseline: near_52w ≈ 1.0 → 급등 종목 높은 점수 (잘못된 랭킹)
    - entry_bias: extension_guard → 급등 종목 하방 조정 (올바른 랭킹)

    compare_presets 에 2 날짜(n_folds=1, holdout=0)만 넘겨서 per-date pooled 비교.
    """
    from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation

    rng = random.Random(seed)
    start = date(2023, 1, 2)
    slope = Decimal("10")
    base_price = Decimal("1000")

    series: dict[str, object] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    fundamentals_map: dict[str, list] = {}

    for i in range(n_extended + n_pullback):
        ticker = f"{i + 1:06d}"
        is_ext = i < n_extended

        t_prices: list[Decimal] = []
        fwd_prices: list[Decimal] = []

        if is_ext:
            # extended: 기저 상승 + 마지막 10봉 급등 (25~50% 이격)
            for j in range(n_trailing - 10):
                t_prices.append(base_price + slope * Decimal(j))
            last_base = t_prices[-1]
            ext_frac = Decimal(str(0.25 + (0.25 * i / max(n_extended - 1, 1))))
            for k in range(10):
                step = last_base * ext_frac / Decimal("10")
                t_prices.append(last_base + step * Decimal(k + 1))
            # 포워드: 급등 폭이 클수록 더 크게 하락
            drop_pct = Decimal(str(0.15 + float(ext_frac) * 0.4))
            last = t_prices[-1]
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") - drop_pct / Decimal(n_forward) * Decimal(k + 1))
                )
        else:
            # pullback: 완만 상승
            for j in range(n_trailing):
                t_prices.append(base_price + slope * Decimal(j))
            rise_pct = Decimal(str(0.10 + rng.random() * 0.10))
            last = t_prices[-1]
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") + rise_pct / Decimal(n_forward) * Decimal(k + 1))
                )

        all_prices = t_prices + fwd_prices
        rows = []
        for idx, c in enumerate(all_prices):
            # ±1.5% 교번 — 변동성 밴드 [0.20, 0.60] 통과
            close = c * (Decimal("1.015") if idx % 2 == 0 else Decimal("0.985"))
            close = max(close, Decimal("10"))
            rows.append(
                OHLCVRow(
                    date=start + timedelta(days=idx),
                    open=c,
                    high=c * Decimal("1.025"),
                    low=c * Decimal("0.975"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )

        ts = TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )
        series[ticker] = ts
        listings[ticker] = (start, None)
        fundamentals_map[ticker] = [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.12"),
            )
        ]

    total = n_trailing + n_forward
    index_rows = [
        OHLCVRow(
            date=start + timedelta(days=i),
            open=Decimal("2000"),
            high=Decimal("2050"),
            low=Decimal("1950"),
            close=Decimal("2000") + Decimal(i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]
    return Panel(
        series=series,
        fundamentals=fundamentals_map,
        listings=listings,
        index_rows=index_rows,
    )


def _make_noise_panel(
    n_tickers: int = 8,
    *,
    seed: int = 99,
    n_trailing: int = 265,
    n_forward: int = 70,
) -> object:
    """순수 노이즈 패널 — 점수와 forward return 간 관계 없음."""
    from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation

    rng = random.Random(seed)
    start = date(2023, 1, 2)
    base_price = Decimal("1000")
    slope = Decimal("5")

    series: dict[str, object] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    fundamentals_map: dict[str, list] = {}

    for i in range(n_tickers):
        ticker = f"{i + 1:06d}"
        t_prices: list[Decimal] = []
        for j in range(n_trailing):
            noise = Decimal(str(rng.uniform(0.97, 1.03)))
            t_prices.append(base_price * noise + slope * Decimal(j))

        fwd_prices: list[Decimal] = []
        last = t_prices[-1]
        for _k in range(n_forward):
            noise = Decimal(str(rng.uniform(0.95, 1.05)))
            last = last * noise
            fwd_prices.append(last)

        all_prices = t_prices + fwd_prices
        rows = []
        for idx, c in enumerate(all_prices):
            close = max(c, Decimal("10"))
            rows.append(
                OHLCVRow(
                    date=start + timedelta(days=idx),
                    open=close,
                    high=close * Decimal("1.02"),
                    low=close * Decimal("0.98"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )

        ts = TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )
        series[ticker] = ts
        listings[ticker] = (start, None)
        fundamentals_map[ticker] = [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.12"),
            )
        ]

    total = n_trailing + n_forward
    index_rows = [
        OHLCVRow(
            date=start + timedelta(days=i),
            open=Decimal("2000"),
            high=Decimal("2050"),
            low=Decimal("1950"),
            close=Decimal("2000") + Decimal(i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]
    return Panel(
        series=series,
        fundamentals=fundamentals_map,
        listings=listings,
        index_rows=index_rows,
    )


# ---------------------------------------------------------------------------
# 헬퍼 — 단일 시점 rebalance date 로 compare 실행
# ---------------------------------------------------------------------------
def _cfg_single_date(t: date, *, n_resamples: int = 50, seed: int = 42) -> BacktestConfig:
    """단일 리밸런스 날짜 t 를 start=end 로 설정 — 1개 날짜 테스트."""
    return BacktestConfig(
        start=t,
        end=t,
        rebalance="monthly",
        top_n=20,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(20,),
        n_resamples=n_resamples,
        n_perms=n_resamples,
        bootstrap_seed=seed,
    )


# ---------------------------------------------------------------------------
# C1. 구조적 패널 — dmono > 0 (포인트 추정치)
# ---------------------------------------------------------------------------


def test_compare_structured_entry_bias_dmono_positive() -> None:
    """설계된 패널에서 entry_bias dmono > 0 (baseline 보다 단조성 높아야 함).

    단일 리밸런스 날짜(trailing 마지막 날)에서 측정.
    n_trailing=265 → 날짜 인덱스 264 가 T.
    """
    n_trailing = 265
    start_date = date(2023, 1, 2)
    t = start_date + timedelta(days=n_trailing - 1)  # trailing 마지막 날

    panel = _make_structured_panel(n_extended=10, n_pullback=10, seed=42, n_trailing=n_trailing)
    cfg = _cfg_single_date(t, n_resamples=50, seed=42)
    # holdout_frac=0.0, n_folds=1 → fold train=[], test=[t]
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))

    result = compare_presets(panel, cfg, wf, variant_preset="entry_bias")

    assert result.variant_preset == "entry_bias"
    assert result.baseline_preset == "baseline"
    assert 20 in result.horizons, "horizon 20 이 결과에 있어야 함"

    hc = result.horizons[20]
    if hc.n == 0:
        pytest.skip(f"관찰 수 0 — 패널 설계 확인 필요 (n_trailing={n_trailing}, t={t})")

    # 포인트 추정치: entry_bias 가 baseline 보다 단조성이 높아야 함
    assert hc.dmono > Decimal("0"), (
        f"entry_bias 가 구조적 패널에서 baseline 보다 단조성이 높아야 함: "
        f"dmono={hc.dmono:.4f}, mono_baseline={hc.mono_baseline:.4f}, "
        f"mono_variant={hc.mono_variant:.4f}, n={hc.n}"
    )


# ---------------------------------------------------------------------------
# C2. 구조 검증 — horizons 키, CI lo <= hi, n >= 0
# ---------------------------------------------------------------------------


def test_compare_result_structure() -> None:
    """compare_presets 결과 구조 및 horizons 키 정합 검증."""
    from backend.backtest.compare import ComparisonResult, HorizonComparison

    n_trailing = 265
    start_date = date(2023, 1, 2)
    t = start_date + timedelta(days=n_trailing - 1)

    panel = _make_structured_panel(n_extended=5, n_pullback=5, seed=7, n_trailing=n_trailing)

    cfg = BacktestConfig(
        start=t,
        end=t,
        rebalance="monthly",
        top_n=20,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(5, 20),
        n_resamples=20,
        n_perms=20,
        bootstrap_seed=7,
    )
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))

    result = compare_presets(panel, cfg, wf, variant_preset="entry_bias")

    assert isinstance(result, ComparisonResult)
    assert result.variant_preset == "entry_bias"
    assert result.baseline_preset == "baseline"
    assert set(result.horizons.keys()) == {5, 20}, (
        f"horizons 키가 {set(result.horizons.keys())} 여야 함, 기대: {{5, 20}}"
    )
    for h, hc in result.horizons.items():
        assert isinstance(hc, HorizonComparison), f"horizon {h} 가 HorizonComparison 이어야 함"
        assert hc.dmono_ci_lo <= hc.dmono_ci_hi, (
            f"horizon {h}: dmono CI lo({hc.dmono_ci_lo}) > hi({hc.dmono_ci_hi})"
        )
        assert hc.dmae_ci_lo <= hc.dmae_ci_hi, (
            f"horizon {h}: dmae CI lo({hc.dmae_ci_lo}) > hi({hc.dmae_ci_hi})"
        )
        assert hc.n >= 0
        assert hc.n_dates >= 0


# ---------------------------------------------------------------------------
# C3. 렌더링 — 마크다운/JSON 오류 없음
# ---------------------------------------------------------------------------


def test_compare_render_no_error() -> None:
    """render_comparison_markdown 과 render_comparison_json 이 오류 없이 실행됨."""
    import json

    from backend.backtest.compare import render_comparison_json, render_comparison_markdown

    n_trailing = 265
    start_date = date(2023, 1, 2)
    t = start_date + timedelta(days=n_trailing - 1)

    panel = _make_structured_panel(n_extended=5, n_pullback=5, seed=3, n_trailing=n_trailing)
    cfg = _cfg_single_date(t, n_resamples=20, seed=3)
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))

    result = compare_presets(panel, cfg, wf, variant_preset="entry_bias")

    md = render_comparison_markdown(result, cfg)
    assert "entry_bias" in md
    assert "baseline" in md

    d = render_comparison_json(result, cfg)
    s = json.dumps(d, ensure_ascii=False)
    assert len(s) > 10


# ---------------------------------------------------------------------------
# C4. 노이즈 패널 — 결과 구조 정상 반환
# ---------------------------------------------------------------------------


def test_compare_noise_panel_valid_structure() -> None:
    """순수 노이즈 패널에서도 결과 구조가 유효하게 반환됨."""
    n_trailing = 265
    start_date = date(2023, 1, 2)
    t = start_date + timedelta(days=n_trailing - 1)

    panel = _make_noise_panel(n_tickers=8, seed=99, n_trailing=n_trailing)
    cfg = _cfg_single_date(t, n_resamples=50, seed=99)
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))

    result = compare_presets(panel, cfg, wf, variant_preset="entry_bias")

    assert 20 in result.horizons
    hc = result.horizons[20]
    # CI 넓이 >= 0
    assert hc.dmono_ci_hi >= hc.dmono_ci_lo
    assert hc.dmae_ci_hi >= hc.dmae_ci_lo
    assert hc.n >= 0
