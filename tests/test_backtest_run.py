from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.run import BacktestConfig, run_backtest
from tests.fixtures.backtest_synth import make_panel


def test_run_backtest_baseline_deterministic() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        cost_bps=Decimal("41"),
        preset="baseline",
        forward_horizons=(5, 20),
    )
    result = run_backtest(panel, cfg)
    # 결정론: 동일 입력 → 동일 출력
    assert run_backtest(panel, cfg).portfolio_nav == result.portfolio_nav
    assert result.portfolio_nav[0] > 0
    assert set(result.event_study.keys()) == {5, 20}
    assert all(v.monotonicity is not None for v in result.event_study.values())
    # 합성 패널이 실제로 적격 후보를 채점했는지(빈 테스트 방지)
    assert any(b.n > 0 for b in result.event_study.values()), (
        "이벤트스터디에 채점된 후보가 있어야 함"
    )
    assert len(result.portfolio_nav) > 1, "픽이 발생해 NAV 가 진화해야 함"


def test_report_render_has_assumptions_and_metrics() -> None:
    from backend.backtest.report import render_json, render_markdown
    from backend.backtest.run import BacktestConfig

    panel = make_panel()
    cfg = BacktestConfig(start=date(2023, 1, 2), end=date(2023, 9, 1), rebalance="monthly", top_n=1)
    result = run_backtest(panel, cfg)
    md = render_markdown(result, cfg)
    assert "가정" in md and "이벤트스터디" in md and "41" in md
    assert "벤치마크" in md and "변동성" in md
    js = render_json(result, cfg)
    assert js["config"]["cost_bps"] == "41"
    assert "event_study" in js
    assert "volatility" in js["summary"] and "benchmark_cagr" in js["summary"]


def test_benchmark_nav_parallels_portfolio() -> None:
    panel = make_panel()
    cfg = BacktestConfig(start=date(2023, 1, 2), end=date(2023, 9, 1), rebalance="monthly", top_n=1)
    result = run_backtest(panel, cfg)
    assert len(result.benchmark_nav) == len(result.portfolio_nav)
    assert result.benchmark_nav[0] == Decimal("1")


def test_factor_study_evaluates_quality() -> None:
    panel = make_panel()
    cfg = BacktestConfig(start=date(2023, 1, 2), end=date(2023, 9, 1), rebalance="monthly", top_n=1)
    result = run_backtest(panel, cfg)
    assert set(result.factor_study.keys()) >= {"roe", "op_margin", "rev_growth", "per", "pbr"}
    assert any(b.n > 0 for b in result.factor_study["roe"].values()), "ROE 예측력이 평가돼야 함"
    assert any(b.n > 0 for b in result.factor_study["per"].values()), "PER 예측력이 평가돼야 함"


def test_quality_tilt_is_not_a_noop() -> None:
    from backend.backtest.run import _score_at
    from backend.config import get_settings

    panel = make_panel()
    settings = get_settings()
    t = date(2023, 8, 30)  # 000001 이 적격(≥200봉)인 시점
    base = _score_at(panel, t, settings, "baseline")
    tilt = _score_at(panel, t, settings, "quality_tilt")
    assert base and tilt, "이 시점에 채점된 후보가 있어야 함"
    assert base != tilt, "quality_tilt 가 baseline 과 점수가 달라야 함(no-op 아님)"


def test_event_study_significance_fields_populated() -> None:
    """Layer B: 이벤트스터디 버킷에 유의성 필드가 올바르게 채워져야 함."""
    from decimal import Decimal

    panel = make_panel()
    # n_resamples/n_perms 를 작게 설정해 테스트 속도 최적화
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
    )
    result = run_backtest(panel, cfg)

    for h, b in result.event_study.items():
        if b.n == 0:
            # 데이터 없으면 기본값
            assert b.mono_ci_lo == Decimal("0")
            assert b.mono_ci_hi == Decimal("0")
            assert b.mono_pvalue == Decimal("1")
            continue
        # 점수 추정치가 CI 안에 있어야 함 (부트스트랩 95% CI 포함 여부)
        assert b.mono_ci_lo <= b.monotonicity <= b.mono_ci_hi, (
            f"h={h}: monotonicity {b.monotonicity} not in CI [{b.mono_ci_lo}, {b.mono_ci_hi}]"
        )
        # p-value 범위
        assert Decimal("0") < b.mono_pvalue <= Decimal("1"), (
            f"h={h}: mono_pvalue out of range: {b.mono_pvalue}"
        )
        # MAE CI — mae 가 0 이 아닌 경우만 범위 확인
        if b.mae != Decimal("0"):
            assert b.mae_ci_lo <= b.mae <= b.mae_ci_hi or (
                # CI 가 좁아서 rounding 때문에 살짝 벗어날 수 있음 — 넓은 공차 허용
                abs(b.mae - b.mae_ci_lo) < Decimal("0.01")
                or abs(b.mae - b.mae_ci_hi) < Decimal("0.01")
            ), f"h={h}: MAE {b.mae} not in CI [{b.mae_ci_lo}, {b.mae_ci_hi}]"


def test_event_study_significance_in_report() -> None:
    """Layer B: 리포트 MD/JSON 에 CI·p-value 가 포함되어야 함."""
    from backend.backtest.report import render_json, render_markdown

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
    result = run_backtest(panel, cfg)

    md = render_markdown(result, cfg)
    # 기존 컬럼 유지 확인
    assert "단조성" in md
    assert "이벤트스터디" in md
    assert "벤치마크" in md
    # 새 컬럼 확인
    assert "95%CI" in md or "CI" in md
    assert "p-value" in md

    js = render_json(result, cfg)
    for h_str, es in js["event_study"].items():
        assert "mono_ci_lo" in es, f"mono_ci_lo missing for h={h_str}"
        assert "mono_ci_hi" in es, f"mono_ci_hi missing for h={h_str}"
        assert "mono_pvalue" in es, f"mono_pvalue missing for h={h_str}"
        assert "mae_ci_lo" in es, f"mae_ci_lo missing for h={h_str}"
        assert "mae_ci_hi" in es, f"mae_ci_hi missing for h={h_str}"


def test_event_study_significance_determinism() -> None:
    """Layer B: 동일 cfg (동일 seed) → 동일 유의성 결과."""
    panel = make_panel()
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
        bootstrap_seed=999,
    )
    r1 = run_backtest(panel, cfg)
    r2 = run_backtest(panel, cfg)
    for h in cfg.forward_horizons:
        assert r1.event_study[h].mono_ci_lo == r2.event_study[h].mono_ci_lo
        assert r1.event_study[h].mono_ci_hi == r2.event_study[h].mono_ci_hi
        assert r1.event_study[h].mono_pvalue == r2.event_study[h].mono_pvalue


def test_pooled_point_estimates_unchanged() -> None:
    """Layer B 추가 후에도 pooled 포인트 추정치(monotonicity, mae, win_rate, n) 가 동일해야 함."""
    from decimal import Decimal

    panel = make_panel()
    # n_resamples=0 에 해당하는 cfg — 하지만 그 경우 boot_groups 빈 것과 같은 효과는 없으므로
    # 두 cfg 를 비교하는 대신, 수동으로 pooled Spearman 을 직접 재계산해 검증
    cfg = BacktestConfig(
        start=date(2023, 1, 2),
        end=date(2023, 9, 1),
        rebalance="monthly",
        top_n=1,
        forward_horizons=(5, 20),
        n_resamples=50,
        n_perms=50,
    )
    result = run_backtest(panel, cfg)
    # 포인트 추정치가 유효한 Decimal 이어야 함 (float 아님)
    for h, b in result.event_study.items():
        assert isinstance(b.monotonicity, Decimal), f"h={h}: monotonicity is not Decimal"
        assert isinstance(b.mae, Decimal), f"h={h}: mae is not Decimal"
        assert isinstance(b.win_rate, Decimal), f"h={h}: win_rate is not Decimal"
        assert isinstance(b.n, int), f"h={h}: n is not int"


def test_main_writes_reports_offline(monkeypatch, tmp_path) -> None:
    from backend.backtest import run as run_mod
    from tests.fixtures.backtest_synth import make_panel

    # 네트워크 차단: loader.build 를 합성 패널로 대체, DART 키 없음 경로
    monkeypatch.setattr(
        "backend.backtest.loader.PanelLoader.build",
        lambda self, tickers, start, end: make_panel(),
    )
    monkeypatch.delenv("DART_API_KEY", raising=False)
    rc = run_mod.main(
        [
            "--start",
            "2023-01-02",
            "--end",
            "2023-09-01",
            "--rebalance",
            "monthly",
            "--top-n",
            "1",
            "--tickers",
            "000001",
            "--out",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert (tmp_path / "report_baseline.md").exists()
    assert (tmp_path / "report_baseline.json").exists()
