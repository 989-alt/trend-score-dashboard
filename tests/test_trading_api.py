"""매매 현황 API(``/api/trading/*``) 테스트 — 읽기전용, 네트워크 0.

``TestClient`` 로 lifespan 을 구동하고, ``trader_db_path`` 를 tmp DB 로 돌린 뒤 TradeStore
기록(주문·NAV·포지션)을 미리 넣어 라우트가 그대로 표시하는지 검증한다. 빈 DB(봇 미가동)
케이스도 200 + 빈 리스트/None 으로 안전한지 확인한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from backend.app import create_app
from backend.config import Settings
from backend.schemas import DISCLAIMER
from backend.trader.models import HoldingPosition, OrderResult
from backend.trader.store import TradeStore
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_mode="sample",
        db_path=tmp_path / "dash.db",
        news_db_path=tmp_path / "news.db",
        trader_db_path=tmp_path / "trading.db",
    )
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    app.state.initial_thread.join(timeout=30)
    return client


@pytest.fixture
def populated_client(tmp_path: Path) -> Iterator[TestClient]:
    """주문·NAV·포지션을 미리 기록한 TradeStore 를 가진 클라이언트."""
    store = TradeStore(tmp_path / "trading.db")
    store.record_order(
        OrderResult(
            order_no="1",
            org_no="6",
            ticker="005930",
            side="buy",
            qty=10,
            submitted_at=datetime(2026, 6, 22, 9, 1, tzinfo=UTC),
            message="ok",
        ),
        reason="진입:점수상위",
    )
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
    # NAV ts 를 '지금'으로 둬서 running=True 가 되도록(가동 윈도 10분 이내).
    now = datetime.now(tz=UTC)
    store.record_snapshot(
        now,
        total_eval=Decimal("500000000"),
        cash=Decimal("499290000"),
        positions=[pos.model_dump()],
    )
    client = _make_client(tmp_path)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture
def empty_client(tmp_path: Path) -> Iterator[TestClient]:
    """봇 미가동 — 빈 TradeStore(테이블만 생성)."""
    client = _make_client(tmp_path)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


def test_positions_returns_recorded(populated_client: TestClient) -> None:
    """보유 종목 200 · 기록한 포지션(평단/손익) 반환 · disclaimer 포함."""
    resp = populated_client.get("/api/trading/positions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disclaimer"] == DISCLAIMER
    assert len(body["positions"]) == 1
    pos = body["positions"][0]
    assert pos["ticker"] == "005930"
    assert pos["name"] == "삼성전자"
    assert Decimal(pos["avg_price"]) == Decimal("70000")
    assert Decimal(pos["pnl_amount"]) == Decimal("10000")


def test_history_returns_recorded_order(populated_client: TestClient) -> None:
    """주문 기록 200 · ticker·reason 보존 · disclaimer 포함."""
    resp = populated_client.get("/api/trading/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disclaimer"] == DISCLAIMER
    assert len(body["orders"]) == 1
    order = body["orders"][0]
    assert order["ticker"] == "005930"
    assert order["side"] == "buy"
    assert order["reason"] == "진입:점수상위"


def test_history_limit_capped(populated_client: TestClient) -> None:
    """limit 상한 초과는 422(Query le=200)."""
    resp = populated_client.get("/api/trading/history", params={"limit": 999})
    assert resp.status_code == 422


def test_nav_returns_recorded_point(populated_client: TestClient) -> None:
    """NAV 200 · 기록한 total_eval 반환 · disclaimer 포함."""
    resp = populated_client.get("/api/trading/nav")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disclaimer"] == DISCLAIMER
    assert len(body["nav"]) == 1
    assert Decimal(body["nav"][0]["total_eval"]) == Decimal("500000000")
    assert Decimal(body["nav"][0]["cash"]) == Decimal("499290000")


def test_status_summary(populated_client: TestClient) -> None:
    """status 200 · position_count·total_eval·total_pnl 집계 · running · disclaimer."""
    resp = populated_client.get("/api/trading/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disclaimer"] == DISCLAIMER
    assert body["position_count"] == 1
    assert Decimal(body["total_eval"]) == Decimal("500000000")
    assert Decimal(body["total_pnl"]) == Decimal("10000")
    assert body["running"] is True  # 최신 NAV ts 가 지금 → 가동 중
    assert body["as_of"] is not None


def test_empty_db_endpoints_ok(empty_client: TestClient) -> None:
    """봇 미가동(빈 DB) — 전 엔드포인트 200 · 빈 리스트/None · 크래시 없음."""
    positions = empty_client.get("/api/trading/positions")
    assert positions.status_code == 200
    assert positions.json()["positions"] == []

    history = empty_client.get("/api/trading/history")
    assert history.status_code == 200
    assert history.json()["orders"] == []

    nav = empty_client.get("/api/trading/nav")
    assert nav.status_code == 200
    assert nav.json()["nav"] == []

    status = empty_client.get("/api/trading/status")
    assert status.status_code == 200
    body = status.json()
    assert body["running"] is False
    assert body["position_count"] == 0
    assert body["total_eval"] is None
    assert body["total_pnl"] is None
    assert body["as_of"] is None
    assert body["disclaimer"] == DISCLAIMER
