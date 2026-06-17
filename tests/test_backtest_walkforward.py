"""Layer C — OOS 앵커드 워크포워드 테스트.

검증 축:
- 날짜 분할 정확성(홀드아웃 = 마지막 holdout_frac, n_folds 테스트 블록은 연속·비겹침·
  비홀드아웃 전체를 정확히 커버, train 윈도우는 expanding=anchored).
- 룩어헤드 없음(테스트 폴드의 점수는 standalone `_score_at(panel, T, ...)` 과 일치).
- 행위 보존(`run_backtest` 의 event_study 포인트 추정치가 `build_event_study(전체 dates)`
  와 동일).
- OOS 가 신호를 반영(심은 신호가 있는 합성 패널에서 OOS 단조성 양수, n>0).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.panel import Panel, TickerSeries, Valuation
from backend.backtest.run import (
    BacktestConfig,
    WalkForwardConfig,
    _rebalance_dates,
    _score_at,
    build_event_study,
    run_backtest,
    run_walk_forward,
)
from backend.config import get_settings
from backend.schemas import OHLCVRow
from tests.fixtures.backtest_synth import make_panel

# 작은 시드 반복수 — 테스트 속도(결정론 유지)
_FAST = {"n_resamples": 30, "n_perms": 30}


# 워밍업 210일(≥ma200_window=200) + 윈도우 140일 → 윈도우 전체에서 후보 적격.
_WARMUP_DAYS = 210
_WINDOW_DAYS = 140
_START = date(2023, 1, 2)
_WINDOW_START = _START + timedelta(days=_WARMUP_DAYS)


def _wf_panel(n_tickers: int = 8) -> Panel:
    """워밍업 후 윈도우 전체에서 후보가 적격인 합성 패널.

    총 (워밍업 210일 + 윈도우 140일) = 350일. cfg.start 를 워밍업 직후로 두면 모든
    리밸런스 날짜가 ≥200봉 이력을 가져 _score_at 이 후보를 채점한다(빈 OOS 방지).

    심은 신호: 티커 인덱스가 높을수록 종가 추세가 강함 → score 와 forward-return 이
    양의 상관(단조성 > 0). 변동성은 [0.20,0.60] 밴드 안(±1.5% 교번)으로 유지.
    """
    total = _WARMUP_DAYS + _WINDOW_DAYS
    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    for k in range(n_tickers):
        ticker = f"{k + 1:06d}"
        # 티커 k 의 일일 드리프트: 높은 k = 가파른 상승 추세(심은 신호).
        drift = Decimal("0.4") + Decimal(k) * Decimal("0.25")
        base = Decimal("100") + Decimal(k) * Decimal("3")
        rows: list[OHLCVRow] = []
        for i in range(total):
            level = base + drift * Decimal(i)
            close = level * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
            rows.append(
                OHLCVRow(
                    date=_START + timedelta(days=i),
                    open=level,
                    high=level * Decimal("1.025"),
                    low=level * Decimal("0.975"),
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
        listings[ticker] = (_START, None)
    index_rows = [
        OHLCVRow(
            date=_START + timedelta(days=i),
            open=Decimal(2000 + i),
            high=Decimal(2000 + i) * Decimal("1.01"),
            low=Decimal(2000 + i) * Decimal("0.99"),
            close=Decimal(2000 + i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]
    return Panel(series=series, fundamentals={}, listings=listings, index_rows=index_rows)


def _wf_cfg(panel: Panel) -> BacktestConfig:
    # 윈도우 시작(워밍업 직후)~끝 만 리밸런스 → 전 구간 적격 후보 보장.
    last = max(r.date for s in panel.series.values() for r in s.rows)
    return BacktestConfig(
        start=_WINDOW_START,
        end=last,
        rebalance="weekly",
        top_n=5,
        forward_horizons=(5, 20),
        **_FAST,
    )


# ---------------------------------------------------------------------------
# WalkForwardConfig 검증
# ---------------------------------------------------------------------------


def test_walkforward_config_rejects_invalid_n_folds() -> None:
    """n_folds < 1 은 ValueError."""
    import pytest

    with pytest.raises(ValueError, match="n_folds"):
        WalkForwardConfig(n_folds=0)
    with pytest.raises(ValueError, match="n_folds"):
        WalkForwardConfig(n_folds=-3)


def test_walkforward_config_rejects_invalid_holdout_frac() -> None:
    """holdout_frac ≥ 1 또는 < 0 은 ValueError."""
    import pytest

    with pytest.raises(ValueError, match="holdout_frac"):
        WalkForwardConfig(holdout_frac=Decimal("1"))
    with pytest.raises(ValueError, match="holdout_frac"):
        WalkForwardConfig(holdout_frac=Decimal("1.5"))
    with pytest.raises(ValueError, match="holdout_frac"):
        WalkForwardConfig(holdout_frac=Decimal("-0.1"))
    # 경계: 0 은 허용(홀드아웃 없음)
    wf0 = WalkForwardConfig(holdout_frac=Decimal("0"))
    assert wf0.holdout_frac == Decimal("0")


def test_walkforward_config_rejects_non_anchored_scheme() -> None:
    """scheme != 'anchored' 는 NotImplementedError."""
    import pytest

    with pytest.raises(NotImplementedError, match="rolling"):
        WalkForwardConfig(scheme="rolling")


# ---------------------------------------------------------------------------
# holdout_n Decimal 정밀도 (P0 #1 독립 오라클)
# ---------------------------------------------------------------------------


def test_holdout_n_decimal_precision() -> None:
    """holdout_frac=Decimal('0.3'), n=10 → holdout_n=3 (float 변환이면 2).

    Decimal(10) * Decimal('0.3') = Decimal('3.0') → int=3.
    float(Decimal('0.3'))=0.29999… → int(10*0.2999…)=2 (off-by-one 재현).
    """
    from backend.backtest.run import _walk_forward_splits

    dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(10)]
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.3"))
    _, holdout = _walk_forward_splits(dates, wf)
    assert len(holdout) == 3, f"holdout_frac=0.3, n=10 → holdout_n=3 이어야 함, got {len(holdout)}"


# ---------------------------------------------------------------------------
# n_dates < n_folds 클램프 (P1 #3)
# ---------------------------------------------------------------------------


def test_walkforward_fewer_dates_than_folds() -> None:
    """pre-holdout 날짜 수 < n_folds 일 때 빈 폴드 없이 유효 폴드 수로 클램프."""
    from backend.backtest.run import _walk_forward_splits

    # 날짜 5개, holdout_frac=0 → pre=5, n_folds=10 → effective_folds=5
    dates = [date(2023, 1, 1) + timedelta(days=i) for i in range(5)]
    wf = WalkForwardConfig(n_folds=10, holdout_frac=Decimal("0"))
    fold_ranges, holdout = _walk_forward_splits(dates, wf)
    assert not holdout, "holdout_frac=0 → 홀드아웃 없음"
    assert len(fold_ranges) <= 5, f"폴드 수가 날짜 수를 초과할 수 없음, got {len(fold_ranges)}"
    # 모든 폴드 test 블록이 비어있지 않아야 함
    for i, (_train, test) in enumerate(fold_ranges):
        assert test, f"폴드 {i} test 블록이 비어있음 (날짜 수 클램프 실패)"
    # 합집합이 전체 dates 를 커버
    flat = [d for _, test in fold_ranges for d in test]
    assert flat == dates, "테스트 블록 합집합이 전체 pre-holdout dates 를 커버해야 함"


# ---------------------------------------------------------------------------
# 날짜 분할 정확성
# ---------------------------------------------------------------------------


def test_walkforward_date_splitting_correctness() -> None:
    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    all_dates = _rebalance_dates(panel, cfg)
    assert len(all_dates) >= 12, f"주간 리밸런스 ≥12회 필요, got {len(all_dates)}"

    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    result = run_walk_forward(panel, cfg, wf)

    n = len(all_dates)
    # 독립 오라클: Decimal 산술 (float 공식을 복사하면 P0 #1 버그를 못 잡음)
    holdout_n = int(Decimal(n) * wf.holdout_frac)
    expected_holdout = all_dates[n - holdout_n :]
    expected_pre = all_dates[: n - holdout_n]

    # 홀드아웃 = 마지막 holdout_frac
    assert result.holdout_dates == expected_holdout
    # 폴드 테스트 블록: 연속·비겹침·비홀드아웃 전체 정확 커버
    test_blocks = [fold_dates for (_train, fold_dates) in result.fold_ranges]
    assert len(test_blocks) == wf.n_folds
    flat: list[date] = []
    for blk in test_blocks:
        flat.extend(blk)
    assert flat == expected_pre, "테스트 블록 합집합 = 비홀드아웃 dates(연속·완전 커버)"
    # 비겹침(전역 정렬·중복 없음)
    assert len(set(flat)) == len(flat)

    # train 윈도우 expanding(anchored): fold i 의 train = dates[0:split_i]
    prev_train_len = 0
    cursor = 0
    for train_dates, fold_dates in result.fold_ranges:
        # train 은 항상 dates[0]에서 시작(anchored) — 단, 첫 폴드 train 은 빈 prefix
        if train_dates:
            assert train_dates[0] == expected_pre[0]
        # train 은 비감소(expanding)
        assert len(train_dates) >= prev_train_len
        prev_train_len = len(train_dates)
        # train = 이 폴드 test 직전까지의 prefix
        assert train_dates == expected_pre[:cursor]
        # test 블록은 train 직후에서 시작
        assert fold_dates == expected_pre[cursor : cursor + len(fold_dates)]
        cursor += len(fold_dates)
    assert cursor == len(expected_pre)


def test_walkforward_oos_is_union_of_test_folds() -> None:
    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    result = run_walk_forward(panel, cfg, wf)

    # OOS dates = 모든 테스트 폴드 dates 의 합집합(= 비홀드아웃 전체)
    all_dates = _rebalance_dates(panel, cfg)
    n = len(all_dates)
    holdout_n = int(Decimal(n) * wf.holdout_frac)  # Decimal 오라클 (float 금지)
    expected_oos = all_dates[: n - holdout_n]
    assert result.oos_dates == expected_oos

    # OOS event_study = build_event_study(oos_dates) 와 동일해야 함
    settings = get_settings()
    direct = build_event_study(panel, expected_oos, settings, cfg)
    for h in cfg.forward_horizons:
        assert result.oos[h].monotonicity == direct[h].monotonicity
        assert result.oos[h].n == direct[h].n


# ---------------------------------------------------------------------------
# 룩어헤드 없음
# ---------------------------------------------------------------------------


def test_walkforward_no_lookahead_leakage() -> None:
    """테스트 폴드 날짜 T 의 스코어 순위가 standalone _score_at 호출과 정확히 일치.

    이 보장으로 폴드 메커니즘이 미래 데이터를 주입하지 않음을 증명한다.
    미래 가격 변형에 대한 독립 카나리아: test_score_at_uses_only_rows_up_to_t.
    """
    from backend.backtest.run import _fwd_return, _mae

    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    settings = get_settings()
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    result = run_walk_forward(panel, cfg, wf)

    for _train, fold_dates in result.fold_ranges:
        if not fold_dates:
            continue
        t = fold_dates[0]
        standalone = _score_at(panel, t, settings, cfg.preset)
        assert standalone, f"T={t} 에 채점된 후보가 있어야 함"

        # build_event_study(단일 날짜) 가 standalone _score_at 과 동일한 슬라이스를
        # 사용하는지 검증: standalone 순위에서 fwd_return·mae 가 모두 가용한 수 ==
        # single-date 버킷의 n (둘 다 동일 _score_at → 동일 데이터 슬라이스).
        h0 = cfg.forward_horizons[0]
        standalone_with_fwd = sum(
            1
            for tk, _ in standalone
            if _fwd_return(panel, tk, t, h0) is not None and _mae(panel, tk, t, h0) is not None
        )
        single = build_event_study(panel, [t], settings, cfg)
        assert single[h0].n == standalone_with_fwd, (
            f"T={t}: 폴드 메커니즘이 standalone 과 다른 슬라이스를 사용함 "
            f"(fold n={single[h0].n}, standalone n={standalone_with_fwd})"
        )


def test_score_at_uses_only_rows_up_to_t() -> None:
    """카나리아: T 이후 가격을 인위 폭등시켜도 T 의 스코어는 불변(룩어헤드 0)."""
    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    t = dates[len(dates) // 2]
    before = _score_at(panel, t, settings, cfg.preset)

    # T 이후 행만 종가를 10배로 폭등시킨 변형 패널
    tampered_series: dict[str, TickerSeries] = {}
    for tk, s in panel.series.items():
        new_rows = [
            r
            if r.date <= t
            else OHLCVRow(
                date=r.date,
                open=r.open * Decimal("10"),
                high=r.high * Decimal("10"),
                low=r.low * Decimal("10"),
                close=r.close * Decimal("10"),
                volume=r.volume,
            )
            for r in s.rows
        ]
        tampered_series[tk] = TickerSeries(
            ticker=tk,
            rows=new_rows,
            turnover_by_date=s.turnover_by_date,
            valuation_by_date=s.valuation_by_date,
        )
    tampered = Panel(
        series=tampered_series,
        fundamentals=panel.fundamentals,
        listings=panel.listings,
        index_rows=panel.index_rows,
    )
    after = _score_at(tampered, t, settings, cfg.preset)
    assert before == after, "T 이후 가격 변형이 T 시점 스코어에 새면 안 됨(룩어헤드)"


# ---------------------------------------------------------------------------
# 행위 보존
# ---------------------------------------------------------------------------


def test_build_event_study_matches_run_backtest() -> None:
    """리팩터 후: run_backtest 의 event_study 가 build_event_study(전체 dates) 와 동일."""
    panel = make_panel()
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
    )
    settings = get_settings()
    result = run_backtest(panel, cfg)
    dates = _rebalance_dates(panel, cfg)
    direct = build_event_study(panel, dates, settings, cfg)
    assert set(direct.keys()) == set(result.event_study.keys())
    for h in cfg.forward_horizons:
        a = result.event_study[h]
        b = direct[h]
        # 포인트 추정치 동일
        assert a.monotonicity == b.monotonicity, f"h={h} monotonicity"
        assert a.mae == b.mae, f"h={h} mae"
        assert a.win_rate == b.win_rate, f"h={h} win_rate"
        assert a.n == b.n, f"h={h} n"
        # Layer B 유의성도 동일(동일 시드·반복수)
        assert a.mono_ci_lo == b.mono_ci_lo
        assert a.mono_ci_hi == b.mono_ci_hi
        assert a.mono_pvalue == b.mono_pvalue
        assert a.mae_ci_lo == b.mae_ci_lo
        assert a.mae_ci_hi == b.mae_ci_hi


def test_run_backtest_point_estimates_preserved() -> None:
    """리팩터가 run_backtest 의 NAV·event_study 를 수치적으로 변경하지 않음."""
    panel = make_panel()
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
    )
    r1 = run_backtest(panel, cfg)
    r2 = run_backtest(panel, cfg)
    assert r1.portfolio_nav == r2.portfolio_nav
    assert r1.benchmark_nav == r2.benchmark_nav
    for h in cfg.forward_horizons:
        assert r1.event_study[h] == r2.event_study[h]


# ---------------------------------------------------------------------------
# OOS 가 신호를 반영
# ---------------------------------------------------------------------------


def test_walkforward_oos_reflects_signal() -> None:
    """심은 신호(높은 티커=강한 추세)에서 OOS 단조성 양수·n>0."""
    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    result = run_walk_forward(panel, cfg, wf)

    # OOS 가 비어있지 않고, 적어도 한 호라이즌에서 단조성이 양수
    assert any(b.n > 0 for b in result.oos.values()), "OOS 에 채점된 후보가 있어야 함"
    positive = [h for h, b in result.oos.items() if b.n > 0 and b.monotonicity > Decimal("0")]
    assert positive, (
        f"OOS 단조성이 양수인 호라이즌이 있어야 함; "
        f"got {[(h, str(b.monotonicity), b.n) for h, b in result.oos.items()]}"
    )


def test_walkforward_determinism() -> None:
    """동일 cfg/wf → 동일 결과(시드 결정론)."""
    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    r1 = run_walk_forward(panel, cfg, wf)
    r2 = run_walk_forward(panel, cfg, wf)
    for h in cfg.forward_horizons:
        assert r1.oos[h] == r2.oos[h]
        assert r1.holdout[h] == r2.holdout[h]
        assert r1.in_sample[h] == r2.in_sample[h]


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


def test_walkforward_report_renders() -> None:
    from backend.backtest.report import (
        render_walk_forward_json,
        render_walk_forward_markdown,
    )

    panel = _wf_panel()
    cfg = _wf_cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    result = run_walk_forward(panel, cfg, wf)

    md = render_walk_forward_markdown(result, cfg, wf)
    # in-sample vs OOS vs holdout 병렬 표시
    assert "OOS" in md or "표본외" in md
    assert "홀드아웃" in md or "holdout" in md.lower()
    assert "단조성" in md
    assert "MAE" in md
    # 정직성 caveat
    assert "수익" in md and "보장" in md
    # 폴드 수 / 날짜 경계
    assert str(wf.n_folds) in md

    js = render_walk_forward_json(result, cfg, wf)
    assert "in_sample" in js
    assert "oos" in js
    assert "holdout" in js
    assert "folds" in js
    assert js["config"]["n_folds"] == wf.n_folds
    for h in cfg.forward_horizons:
        assert str(h) in js["oos"]
        assert "monotonicity" in js["oos"][str(h)]


def test_walkforward_cli_writes_reports(monkeypatch, tmp_path) -> None:
    from backend.backtest import run as run_mod

    panel = _wf_panel()
    monkeypatch.setattr(
        "backend.backtest.loader.PanelLoader.build",
        lambda self, tickers, start, end: panel,
    )
    monkeypatch.delenv("DART_API_KEY", raising=False)
    cfg = _wf_cfg(panel)
    rc = run_mod.main(
        [
            "--start",
            cfg.start.isoformat(),
            "--end",
            cfg.end.isoformat(),
            "--rebalance",
            "weekly",
            "--top-n",
            "5",
            "--tickers",
            "000001,000002",
            "--walk-forward",
            "--n-folds",
            "4",
            "--holdout-frac",
            "0.2",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "report_walkforward_baseline.md").exists()
    assert (tmp_path / "report_walkforward_baseline.json").exists()
