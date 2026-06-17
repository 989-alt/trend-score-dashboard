"""Task 5: 레이어1 ablation 러너 테스트."""

from decimal import Decimal

from backend.backtest.ablation import run_layer1_ablation
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from tests.fixtures.backtest_synth import make_panel


def test_layer1_ablation_returns_row_per_candidate() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date,
        end=panel.index_rows[-1].date,
        rebalance="weekly",
        top_n=2,
        n_resamples=40,
        n_perms=40,
    )
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.2"))
    rows = run_layer1_ablation(panel, cfg, wf, w52_candidates=[Decimal("0.30"), Decimal("0.12")])
    assert [r.w52 for r in rows] == [Decimal("0.30"), Decimal("0.12")]
    assert all(hasattr(r, "dmae_20") for r in rows)
