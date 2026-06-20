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


def test_nav_market_aware_headline_is_kr(tmp_path: Path) -> None:
    """KR(자금)·US($0) NAV 가 같은 ts 로 섞여도 KR 만 골라 헤드라인 0 오염 방지."""
    store = TradeStore(tmp_path / "trading.db")
    ts = datetime(2026, 6, 22, 9, 0, tzinfo=UTC)
    # 같은 ts 에 KR(5억)·US(0) 두 시장 기록.
    store.record_snapshot(
        ts, market="KR", total_eval=Decimal("500000000"), cash=Decimal("499000000"), positions=[]
    )
    store.record_snapshot(ts, market="US", total_eval=Decimal("0"), cash=Decimal("0"), positions=[])

    # 전체(None)는 2행, KR 만은 1행(5억).
    assert len(store.nav_series()) == 2
    kr = store.nav_series(market="KR")
    assert len(kr) == 1 and kr[0]["total_eval"] == Decimal("500000000")
    us = store.nav_series(market="US")
    assert len(us) == 1 and us[0]["total_eval"] == Decimal("0")


def test_latest_positions_merges_markets(tmp_path: Path) -> None:
    """보유 종목은 시장별 최신 스냅샷의 합집합(국장+미장 병합)."""
    store = TradeStore(tmp_path / "trading.db")
    kr_pos = HoldingPosition(ticker="005930", qty=10, avg_price=Decimal("70000"))
    us_pos = HoldingPosition(ticker="AAPL", qty=5, avg_price=Decimal("190"))
    # KR·US 가 서로 다른 ts(다른 사이클)로 기록 — 단일 MAX(ts)면 한쪽이 누락된다.
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        market="KR",
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[kr_pos.model_dump()],
    )
    store.record_snapshot(
        datetime(2026, 6, 22, 22, 30, tzinfo=UTC),
        market="US",
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[us_pos.model_dump()],
    )
    latest = store.latest_positions()
    assert {p["ticker"] for p in latest} == {"005930", "AAPL"}


def test_record_snapshot_defaults_to_kr(tmp_path: Path) -> None:
    """market 미지정 기록은 KR 로 저장(하위호환 — 기존 호출부 무수정)."""
    store = TradeStore(tmp_path / "trading.db")
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        total_eval=Decimal("100"),
        cash=Decimal("100"),
        positions=[],
    )
    assert len(store.nav_series(market="KR")) == 1
    assert store.nav_series(market="US") == []


def test_legacy_schema_db_recreated(tmp_path: Path) -> None:
    """구 스키마(market 컬럼 없는 nav) DB 를 열어도 크래시 없이 재생성된다."""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    # 구 스키마: market 컬럼 없음.
    conn.executescript(
        "CREATE TABLE nav (ts TEXT PRIMARY KEY, total_eval TEXT, cash TEXT);"
        "CREATE TABLE position_snap (ts TEXT, ticker TEXT, name TEXT, qty INTEGER, "
        "avg_price TEXT, cur_price TEXT, eval_amount TEXT, pnl_amount TEXT, pnl_pct TEXT);"
        "INSERT INTO nav VALUES ('2026-06-01T09:00:00', '999', '999');"
    )
    conn.commit()
    conn.close()

    # 구 DB 를 열면 market 없는 표시 테이블은 재생성(구 NAV 행은 버려짐 — 표시 전용).
    store = TradeStore(db)
    assert store.nav_series() == []  # 재생성으로 비워짐
    # 새 스키마로 정상 기록·조회.
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        market="KR",
        total_eval=Decimal("100"),
        cash=Decimal("100"),
        positions=[],
    )
    assert len(store.nav_series(market="KR")) == 1


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
