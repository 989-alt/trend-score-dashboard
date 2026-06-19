"""runner.build_loop 배선 단위테스트 — 컴포넌트 타입·시장 확인. 네트워크 0(run() 미호출)."""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.store import Store
from backend.trader.kis_order import KisOrderClient
from backend.trader.loop import TraderLoop
from backend.trader.positions import PositionManager
from backend.trader.runner import build_loop
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine


def test_build_loop_wires_components(tmp_path: Path) -> None:
    """build_loop 가 올바른 타입의 컴포넌트를 주입한 TraderLoop(market=KR)을 반환한다."""
    settings = Settings(
        kis_app_key="k",
        kis_app_secret="s",
        kis_account="50190719",
        db_path=tmp_path / "dashboard.db",
        trader_db_path=tmp_path / "trading.db",
    )

    loop = build_loop(settings, "KR")

    assert isinstance(loop, TraderLoop)
    assert loop._market == "KR"
    assert isinstance(loop._oc, KisOrderClient)
    assert isinstance(loop._store, Store)
    assert isinstance(loop._ts, TradeStore)
    assert isinstance(loop._engine, StrategyEngine)
    assert isinstance(loop._pm, PositionManager)
