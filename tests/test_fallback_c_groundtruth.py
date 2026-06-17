"""폴백 C 합성 그라운드트루스 — 두 메커니즘이 *실제로* 작동함을 증명한다.

합성 패널에 효과를 **명시적으로 심고**(planted), 메커니즘이 발화하는지를 먼저 확인한
뒤, 강한 방향성 단언으로 결과를 검증한다(타우톨로지 금지).

TEST 1 — 레짐 게이트가 최대낙폭(MDD)을 줄인다.
  지수(KS11)에 초기 분산일 클러스터를 심어 첫 리밸런스일에 ``is_risk_off`` 가 발화하게
  하고, 보유 종목은 그 초기 risk-off 구간에 급락시킨다. regime_on=True 면 그 구간이 현금
  보류되어 급락을 회피 → MDD 가 얕아진다(덜 음수). atr/sizing 은 끈다(레짐 토글 격리).

TEST 2 — near_52w 재가중이 진입 후 MAE 를 줄인다.
  적격 유니버스를 (a) 신고가 근접(높은 near_52w) 후 급락하는 '익스텐디드' 종목과
  (b) 낮은 near_52w 로 진입 후 상승/횡보하는 '풀백' 종목으로 구성한다. baseline(near_52w
  0.30)은 익스텐디드(고 MAE)를 top-N 으로 올리고, fallback_c(weight_52w_fallback=0.12)는
  이들을 강등 → ΔMAE > 0(개선).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.compare import compare_presets
from backend.backtest.metrics import max_drawdown
from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation
from backend.backtest.portfolio import simulate_risk_overlay
from backend.backtest.regime import is_risk_off
from backend.backtest.run import BacktestConfig, WalkForwardConfig, _score_at
from backend.config import Settings
from backend.schemas import OHLCVRow

# ---------------------------------------------------------------------------
# 공용 행 빌더
# ---------------------------------------------------------------------------


def _idx_row(d: date, close: Decimal, vol: int) -> OHLCVRow:
    """지수 봉 — 분산일 판정(종가/거래량)만 의미 있으므로 좁은 H/L."""
    return OHLCVRow(
        date=d,
        open=close,
        high=close * Decimal("1.001"),
        low=close * Decimal("0.999"),
        close=close,
        volume=Decimal(str(vol)),
    )


def _stk_row(d: date, close: Decimal, vol: int = 1_000_000) -> OHLCVRow:
    """종목 봉 — H/L 은 ±2.5% (ATR/저가 기반 평가가 동작하도록)."""
    return OHLCVRow(
        date=d,
        open=close,
        high=close * Decimal("1.025"),
        low=close * Decimal("0.975"),
        close=close,
        volume=Decimal(str(vol)),
    )


# ===========================================================================
# TEST 1 — 레짐 게이트가 최대낙폭(MDD)을 줄인다
# ===========================================================================


def _build_regime_crash_panel() -> tuple[Panel, list[date], Settings]:
    """초기 risk-off 발화 + 그 구간 보유종목 급락 패널.

    설계:
      지수 KS11 = (1) 200일선/적격 확보용 완만 상승 prefix 205봉,
                  (2) 분산일 클러스터 6봉(각 종가 ×0.99 ≤ 전일×0.998 AND 거래량↑)
                      → 직전 window=5 안에서 분산일 5개 → threshold=5 충족,
                  (3) 이후 완만 회복.
      종목 000001 = (1) 완만 상승 prefix(200일선 위 + 변동성 밴드 통과),
                    (2) 첫 리밸런스일 직후 10봉 급락(−5%/일),
                    (3) 급락 후 완만 회복(레짐-ON 이 T1 진입 시 양(+)의 구간을 갖도록).

    리밸런스일: T0 = 분산일 클러스터 마지막 봉(여기서 is_risk_off=True),
                T1 = ~8일 후(급락 이후), T2 = 그 뒤(회복 구간).
    """
    start = date(2023, 1, 2)
    n_prefix = 205
    window, threshold = 5, 5
    cluster_len = 6  # 6봉 → 평가 대상 5봉(첫 봉 전일 없음) → 분산일 5개

    # ── 지수 KS11 ──────────────────────────────────────────────────────
    idx_rows: list[OHLCVRow] = []
    ic = Decimal("2000")
    iv = 1_000_000
    for i in range(n_prefix):  # (1) 완만 상승
        idx_rows.append(_idx_row(start + timedelta(days=i), ic, iv))
        ic *= Decimal("1.001")
    for i in range(n_prefix, n_prefix + cluster_len):  # (2) 분산일 클러스터
        ic *= Decimal("0.99")  # 하락 1% (≤ ×0.998)
        iv += 100_000  # 거래량 증가
        idx_rows.append(_idx_row(start + timedelta(days=i), ic, iv))
    for i in range(n_prefix + cluster_len, n_prefix + 40):  # (3) 회복(분산일 해소)
        ic *= Decimal("1.001")
        iv = 1_000_000
        idx_rows.append(_idx_row(start + timedelta(days=i), ic, iv))

    # ── 종목 000001 ────────────────────────────────────────────────────
    stk_rows: list[OHLCVRow] = []
    for i in range(n_prefix + cluster_len):  # (1) 완만 상승 prefix
        base = Decimal("100") + Decimal("0.5") * Decimal(i)
        close = base * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
        stk_rows.append(_stk_row(start + timedelta(days=i), close))
    last = Decimal("100") + Decimal("0.5") * Decimal(n_prefix + cluster_len - 1)
    for i in range(n_prefix + cluster_len, n_prefix + cluster_len + 10):  # (2) 급락
        last *= Decimal("0.95")  # −5%/일
        stk_rows.append(_stk_row(start + timedelta(days=i), last))
    for i in range(n_prefix + cluster_len + 10, n_prefix + 40):  # (3) 완만 회복
        last *= Decimal("1.01")  # +1%/일
        stk_rows.append(_stk_row(start + timedelta(days=i), last))

    stock = TickerSeries(
        ticker="000001",
        rows=stk_rows,
        turnover_by_date={r.date: Decimal("20000000000") for r in stk_rows},
        valuation_by_date={
            r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in stk_rows
        },
    )
    panel = Panel(
        series={"000001": stock},
        fundamentals={"000001": []},
        listings={"000001": (start, None)},
        index_rows=idx_rows,
    )
    settings = Settings(regime_window=window, regime_threshold=threshold)
    # T0 = 클러스터 마지막 봉, T1 = 급락 이후, T2 = 회복 구간.
    t0 = start + timedelta(days=n_prefix + cluster_len - 1)
    t1 = start + timedelta(days=n_prefix + cluster_len + 9)
    t2 = start + timedelta(days=n_prefix + 25)
    return panel, [t0, t1, t2], settings


def test_regime_gate_reduces_max_drawdown() -> None:
    """레짐 게이트 ON 이면 초기 급락을 현금 회피 → MDD 가 strictly 얕아진다."""
    panel, dates, settings = _build_regime_crash_panel()
    t0 = dates[0]

    # (가드) 메커니즘이 실제로 발화하는지 — 효과가 심어졌음을 증명.
    assert is_risk_off(
        panel.index_rows_asof(t0),
        window=settings.regime_window,
        threshold=settings.regime_threshold,
        drop=settings.regime_drop,
    ), "심은 분산일 클러스터가 T0 에서 is_risk_off 를 발화시켜야 한다"
    # regime_off 일 때 보유될 종목이 실제로 적격·채점되는지(regime_on=False 경로에서 급락 흡수).
    ranked_t0 = _score_at(panel, t0, settings, "baseline")
    assert ranked_t0 and ranked_t0[0][0] == "000001", (
        f"T0 에서 000001 이 적격·채점되어야 regime_off 경로가 급락을 보유한다: {ranked_t0}"
    )

    base_cfg = BacktestConfig(
        start=dates[0], end=dates[-1], rebalance="weekly", top_n=1, preset="baseline"
    )
    res_on = simulate_risk_overlay(
        panel, base_cfg, settings, dates, regime_on=True, atr_on=False, sizing_on=False
    )
    res_off = simulate_risk_overlay(
        panel, base_cfg, settings, dates, regime_on=False, atr_on=False, sizing_on=False
    )

    mdd_on = max_drawdown(res_on.nav)
    mdd_off = max_drawdown(res_off.nav)

    # 강한 방향성 단언 — 레짐 ON 이 급락을 회피해 MDD 가 덜 음수.
    assert mdd_on >= mdd_off, (
        f"레짐 ON 의 MDD 가 OFF 보다 얕아야(>=) 한다: mdd_on={mdd_on}, mdd_off={mdd_off}"
    )
    assert mdd_on > mdd_off, (
        f"급락을 실제로 회피했다면 MDD 가 strictly 더 좋아야 한다: "
        f"mdd_on={mdd_on}, mdd_off={mdd_off}, nav_on={res_on.nav}, nav_off={res_off.nav}"
    )
    # risk-off 가 실제로 발화해 T0 가 현금 보류되었음을 증명.
    assert t0 in res_on.regime_off_dates, (
        f"T0({t0}) 가 regime_off_dates 에 있어야 한다: {res_on.regime_off_dates}"
    )


# ===========================================================================
# TEST 2 — near_52w 재가중이 진입 후 MAE 를 줄인다
# ===========================================================================


def _build_extension_mae_panel(
    *,
    n_extended: int = 10,
    n_pullback: int = 10,
    n_trailing: int = 265,
    n_forward: int = 70,
) -> tuple[Panel, date]:
    """near_52w 가 높은 '익스텐디드'(진입 후 급락) vs 낮은 '풀백'(진입 후 상승) 패널.

    - 익스텐디드(티커 1..n_extended): 완만 상승 후 마지막 10봉 급등(MA 이격↑, near_52w≈1)
      → 진입 후 급락(큰 음(−) MAE). 급등폭이 클수록 낙폭도 크게.
    - 풀백(티커 n_extended+1..): 완만 상승 유지(near_52w 가 익스텐디드보다 낮음)
      → 진입 후 완만 상승(작은 MAE).

    단일 리밸런스일 T(= trailing 마지막 날) 에서:
      baseline(near_52w 0.30)  → 익스텐디드를 top-N 으로 올림(고 MAE).
      fallback_c(0.12)         → near_52w 강등 + extension_guard → 익스텐디드 demote.
    """
    start = date(2023, 1, 2)
    slope = Decimal("10")
    base_price = Decimal("1000")
    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    fundamentals: dict[str, list[AsOfFundamentals]] = {}

    for i in range(n_extended + n_pullback):
        ticker = f"{i + 1:06d}"
        is_ext = i < n_extended
        t_prices: list[Decimal] = []
        fwd_prices: list[Decimal] = []

        if is_ext:
            for j in range(n_trailing - 10):  # 완만 상승 기저
                t_prices.append(base_price + slope * Decimal(j))
            last_base = t_prices[-1]
            # 급등 폭 0.25~0.50 (티커마다 증가) — MA 이격 + 신고가 근접.
            ext_frac = Decimal("0.25") + Decimal("0.25") * Decimal(i) / Decimal(
                max(n_extended - 1, 1)
            )
            for k in range(10):
                step = last_base * ext_frac / Decimal("10")
                t_prices.append(last_base + step * Decimal(k + 1))
            # 진입 후 급락 — 급등폭이 클수록 더 크게.
            drop_pct = Decimal("0.15") + ext_frac * Decimal("0.4")
            last = t_prices[-1]
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") - drop_pct / Decimal(n_forward) * Decimal(k + 1))
                )
        else:
            for j in range(n_trailing):  # 완만 상승 유지(신고가 근접도 낮음)
                t_prices.append(base_price + slope * Decimal(j))
            rise_pct = Decimal("0.15")  # 진입 후 완만 상승(결정론 — rng 미사용)
            last = t_prices[-1]
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") + rise_pct / Decimal(n_forward) * Decimal(k + 1))
                )

        rows: list[OHLCVRow] = []
        for idx, c in enumerate(t_prices + fwd_prices):
            # ±1.5% 교번 — 연환산 변동성 밴드 [0.20,0.60] 통과(적격 확보).
            close = max(c * (Decimal("1.015") if idx % 2 == 0 else Decimal("0.985")), Decimal("10"))
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
        series[ticker] = TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )
        listings[ticker] = (start, None)
        fundamentals[ticker] = [
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
    panel = Panel(
        series=series, fundamentals=fundamentals, listings=listings, index_rows=index_rows
    )
    t = start + timedelta(days=n_trailing - 1)  # trailing 마지막 날
    return panel, t


def test_near_52w_deweight_reduces_post_entry_mae() -> None:
    """fallback_c 가 익스텐디드(고 MAE)를 강등 → ΔMAE > 0(개선)."""
    n_extended, n_pullback = 10, 10
    top_n = 8
    panel, t = _build_extension_mae_panel(n_extended=n_extended, n_pullback=n_pullback)

    settings_fc = Settings(weight_52w_fallback=Decimal("0.12"))

    # (가드) baseline top-N 이 실제로 '익스텐디드(고 MAE)'를 담고, fallback_c 가 그들을
    # 빼는지 확인 — 그래야 ΔMAE 개선이 메커니즘에서 비롯됨이 증명된다.
    ranked_base = _score_at(panel, t, Settings(), "baseline")
    ranked_fc = _score_at(panel, t, settings_fc, "fallback_c")
    base_top = {tk for tk, _ in ranked_base[:top_n]}
    fc_top = {tk for tk, _ in ranked_fc[:top_n]}
    ext_tickers = {f"{i + 1:06d}" for i in range(n_extended)}
    base_top_ext = base_top & ext_tickers
    assert base_top_ext == base_top, (
        f"baseline top-{top_n} 이 전부 익스텐디드(고 MAE)여야 한다: "
        f"base_top={sorted(base_top)}, ext={sorted(base_top_ext)}"
    )
    assert not (fc_top & ext_tickers), (
        f"fallback_c 가 익스텐디드를 top-{top_n} 에서 demote 해야 한다: fc_top={sorted(fc_top)}"
    )

    cfg = BacktestConfig(
        start=t,
        end=t,
        rebalance="monthly",
        top_n=top_n,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
        bootstrap_seed=42,
    )
    wf = WalkForwardConfig(n_folds=1, holdout_frac=Decimal("0.0"))
    res = compare_presets(panel, cfg, wf, variant_preset="fallback_c", settings=settings_fc)

    h = 20  # 20일 호라이즌 — 패널에 충분한 관찰(n_forward=70).
    hc = res.horizons[h]
    assert hc.n > 0, f"호라이즌 {h} 에 관찰이 있어야 한다: n={hc.n}"
    # MAE 는 음수. 개선 컨벤션상 ΔMAE = mae_variant − mae_baseline > 0 = 더 0 에 가까움.
    assert hc.dmae > Decimal("0"), (
        f"fallback_c 의 진입 후 MAE 가 baseline 보다 개선되어야(ΔMAE>0) 한다: "
        f"dmae={hc.dmae}, mae_baseline={hc.mae_baseline}, mae_variant={hc.mae_variant}, n={hc.n}"
    )
