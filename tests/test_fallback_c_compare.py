"""Task 4: fallback_c 프리셋의 compare_presets 연동 게이트 테스트."""

from decimal import Decimal

from backend.backtest.compare import compare_presets
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from tests.fixtures.backtest_synth import make_panel


def test_fallback_c_compare_runs_and_reports_dmae() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date, end=panel.index_rows[-1].date,
        rebalance="weekly", top_n=2, n_resamples=50, n_perms=50,
    )
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.2"))
    result = compare_presets(panel, cfg, wf, variant_preset="fallback_c")
    assert result.variant_preset == "fallback_c"
    for hc in result.horizons.values():
        assert hasattr(hc, "dmae") and hasattr(hc, "dmae_ci_lo")
