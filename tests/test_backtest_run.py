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
