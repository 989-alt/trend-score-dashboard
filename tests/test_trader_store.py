"""TradeStore(SQLite) + PositionManager 단위테스트 — 네트워크 0."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.trader.models import Balance, HoldingPosition, OrderResult, OrderStatus
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore


def _sell(order_no: str, ticker: str, ts: datetime) -> OrderResult:
    return OrderResult(
        order_no=order_no,
        org_no="6",
        ticker=ticker,
        side="sell",
        qty=10,
        submitted_at=ts,
        message="모의투자 매도주문이 완료 되었습니다.",
    )


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


def test_latest_positions_empty_after_account_goes_flat(tmp_path: Path) -> None:
    """매도로 계좌가 비면(이후 빈 스냅샷) latest_positions 는 []. (유령 포지션 버그 회귀)."""
    store = TradeStore(tmp_path / "t.db")
    # 09:02 보유 1종목.
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 2, tzinfo=UTC),
        total_eval=Decimal("502760000"),
        cash=Decimal("500000000"),
        positions=[
            HoldingPosition(
                ticker="000660", name="SK하이닉스", qty=1, avg_price=Decimal("2735000")
            ).model_dump()
        ],
    )
    # 09:03 전량 매도 → 보유 0(빈 스냅샷: nav 행만, position_snap 행 없음).
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 3, tzinfo=UTC),
        total_eval=Decimal("502749000"),
        cash=Decimal("502749000"),
        positions=[],
    )
    # 최신 사이클(09:03)이 flat 이므로 빈 리스트여야 한다(09:02 스냅샷 고정 금지).
    assert store.latest_positions() == []


def test_record_order_stores_and_returns_name(tmp_path: Path) -> None:
    """주문 기록에 종목명 저장 → recent_orders 가 name 반환(없으면 코드 표시 폴백용)."""
    store = TradeStore(tmp_path / "t.db")
    store.record_order(
        _sell("1", "000660", datetime(2026, 6, 22, 9, 2, tzinfo=UTC)), name="SK하이닉스"
    )
    o = store.recent_orders()[0]
    assert o["ticker"] == "000660" and o["name"] == "SK하이닉스"


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


def test_recent_orders_default_fill_fields(tmp_path: Path) -> None:
    """접수 직후 기록은 filled_qty=0 · status='접수'(체결 아님)."""
    store = TradeStore(tmp_path / "t.db")
    store.record_order(_sell("1", "005930", datetime(2026, 6, 22, 9, 5, tzinfo=UTC)))
    o = store.recent_orders()[0]
    assert o["filled_qty"] == 0 and o["status"] == "접수"
    assert store.realized_pnl_total() is None  # 체결 전이라 실현손익 없음


def test_reconcile_fills_sets_fill_and_realized(tmp_path: Path) -> None:
    """체결 재조회 → filled_qty/상태 갱신 + 직전 평단 대비 실현손익 산정."""
    store = TradeStore(tmp_path / "t.db")
    # 직전 스냅샷: 005930 평단 70,000(실현손익 기준점).
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[
            HoldingPosition(ticker="005930", qty=10, avg_price=Decimal("70000")).model_dump()
        ],
    )
    store.record_order(_sell("1", "005930", datetime(2026, 6, 22, 9, 5, tzinfo=UTC)))
    # 75,000 에 10주 전량 체결 → (75000-70000)*10 = 50,000 실현.
    store.reconcile_fills(
        [
            OrderStatus(
                order_no="1",
                ticker="005930",
                side="sell",
                order_qty=10,
                filled_qty=10,
                filled_price=Decimal("75000"),
                status="체결",
            )
        ]
    )
    o = store.recent_orders()[0]
    assert o["filled_qty"] == 10 and o["status"] == "체결"
    assert store.realized_pnl_total() == Decimal("50000")


def test_reconcile_fills_is_idempotent(tmp_path: Path) -> None:
    """1분 루프가 같은 체결을 반복 조회해도 실현손익은 1회만 집계."""
    store = TradeStore(tmp_path / "t.db")
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[
            HoldingPosition(ticker="005930", qty=10, avg_price=Decimal("70000")).model_dump()
        ],
    )
    store.record_order(_sell("1", "005930", datetime(2026, 6, 22, 9, 5, tzinfo=UTC)))
    fill = OrderStatus(
        order_no="1",
        ticker="005930",
        side="sell",
        order_qty=10,
        filled_qty=10,
        filled_price=Decimal("75000"),
        status="체결",
    )
    store.reconcile_fills([fill])
    store.reconcile_fills([fill])  # 재호출
    assert store.realized_pnl_total() == Decimal("50000")  # 100,000 아님


def test_reconcile_unfilled_sell_stays_unrealized(tmp_path: Path) -> None:
    """접수만 되고 체결 0 이면 미체결 표시 + 실현손익 없음(접수≠체결)."""
    store = TradeStore(tmp_path / "t.db")
    store.record_snapshot(
        datetime(2026, 6, 22, 9, 0, tzinfo=UTC),
        total_eval=Decimal("1"),
        cash=Decimal("1"),
        positions=[
            HoldingPosition(ticker="000660", qty=1, avg_price=Decimal("2735000")).model_dump()
        ],
    )
    store.record_order(_sell("9", "000660", datetime(2026, 6, 22, 9, 2, tzinfo=UTC)))
    store.reconcile_fills(
        [
            OrderStatus(
                order_no="9",
                ticker="000660",
                side="sell",
                order_qty=1,
                filled_qty=0,
                filled_price=None,
                status="",
            )
        ]
    )
    o = store.recent_orders()[0]
    assert o["filled_qty"] == 0 and o["status"] == "미체결"
    assert store.realized_pnl_total() is None


def test_ensure_order_columns_migrates_legacy_orders(tmp_path: Path) -> None:
    """체결 컬럼 없는 구 orders 테이블도 ALTER 로 보강 — 기존 행 보존 + 기본값."""
    import sqlite3

    db = tmp_path / "legacy_orders.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE orders (ts TEXT, order_no TEXT, org_no TEXT, ticker TEXT, "
        "side TEXT, qty INTEGER, reason TEXT, message TEXT);"
        "INSERT INTO orders VALUES ('2026-06-22T09:01:00+09:00','1','6','005930',"
        "'sell',14,'청산:순위이탈','ok');"
    )
    conn.commit()
    conn.close()

    store = TradeStore(db)  # 보강 ALTER 수행
    o = store.recent_orders()[0]
    assert o["ticker"] == "005930" and o["filled_qty"] == 0 and o["status"] == "접수"


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
