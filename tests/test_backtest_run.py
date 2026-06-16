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
