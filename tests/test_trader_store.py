"""TradeStore(SQLite) + PositionManager 단위테스트 — 네트워크 0."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.trader.models import Balance, HoldingPosition, OrderResult
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore


def test_trade_store_roundtrip(tmp_path: Path) -> None:
    """주문·NAV·포지션 기록 후 읽기(Decimal 복원)."""
    store = TradeStore(tmp_path / "trading.db")
    order = OrderResult(
        order_no="1",
        org_no="6",
        ticker="005930",
        side="buy",
        qty=10,
        submitted_at=datetime(2026, 6, 22, 9, 1, tzinfo=UTC),
        message="ok",
    )
    store.record_order(order, reason="진입:점수상위")
    pos = HoldingPosition(
        ticker="005930",
        name="삼성전자",
        qty=10,
        avg_price=Decimal("70000"),
        cur_price=Decimal("71000"),
        eval_amount=Decimal("710000"),
        pnl_amount=Decimal("10000"),
        pnl_pct=Decimal("1.43"),
    )
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 2, tzinfo=UTC),
        total_eval=Decimal("500000000"),
        cash=Decimal("499290000"),
        positions=[pos.model_dump()],
    )

    orders = store.recent_orders()
    assert len(orders) == 1
    assert orders[0]["ticker"] == "005930" and orders[0]["reason"] == "진입:점수상위"

    latest = store.latest_positions()
    assert len(latest) == 1
    assert latest[0]["ticker"] == "005930" and latest[0]["avg_price"] == Decimal("70000")

    nav = store.nav_series()
    assert len(nav) == 1 and nav[0]["total_eval"] == Decimal("500000000")


def test_trade_store_latest_only_newest_snapshot(tmp_path: Path) -> None:
    """latest_positions 는 가장 최근 ts 스냅샷만."""
    store = TradeStore(tmp_path / "t.db")
    old = HoldingPosition(ticker="005930", qty=5, avg_price=Decimal("60000"))
    new = HoldingPosition(ticker="000660", qty=3, avg_price=Decimal("180000"))
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[old.model_dump()],
    )
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 30, tzinfo=UTC),
        total_eval=Decimal("2"),
        cash=Decimal("2"),
        positions=[new.model_dump()],
    )
    latest = store.latest_positions()
    assert {p["ticker"] for p in latest} == {"000660"}


def test_position_manager_sync_and_size() -> None:
    """잔고 동기화 + 사이징(목표금액÷현재가, 호가단위 내림)."""
    pm = PositionManager()
    pm.sync(
        Balance(
            cash=Decimal("100000000"),
            total_eval=Decimal("100710000"),
            positions=[HoldingPosition(ticker="005930", qty=10, avg_price=Decimal("70000"))],
        )
    )
    assert pm.cash == Decimal("100000000")
    assert pm.total_eval == Decimal("100710000")
    assert pm.held_tickers() == {"005930"}
    assert pm.qty("005930") == 10 and pm.qty("000660") == 0
    held = pm.position("005930")
    assert held is not None and held.qty == 10

    assert PositionManager.target_qty(Decimal("1000000"), Decimal("70000")) == 14
    assert PositionManager.target_qty(Decimal("1000000"), Decimal("70000"), lot=10) == 10
    assert PositionManager.target_qty(Decimal("1000000"), Decimal("0")) == 0
