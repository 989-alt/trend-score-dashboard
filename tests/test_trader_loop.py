"""TraderLoop.run_once 단위테스트 — 가짜 주문클라이언트/스토어, 실제 TradeStore. 네트워크 0."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.config import Settings
from backend.schemas import Grade, Market, ScoreEntry, SellReason, Snapshot
from backend.trader.kis_order import KisOrderError
from backend.trader.loop import TraderLoop
from backend.trader.models import Balance, HoldingPosition, OrderResult, OrderSide
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

_NOW = datetime(2026, 6, 22, 9, 5, tzinfo=UTC)


def _entry(
    ticker: str,
    score: str,
    *,
    price: str = "10000",
    grade: Grade = Grade.BUY,
    eligible: bool = True,
    sell_alert: bool = False,
    sell_reason: SellReason | None = None,
) -> ScoreEntry:
    return ScoreEntry(
        ticker=ticker,
        name=ticker,
        market="KR",
        price=Decimal(price),
        score=Decimal(score),
        grade=grade,
        eligible=eligible,
        sell_alert=sell_alert,
        sell_reason=sell_reason,
    )


class FakeStore:
    """backend.store.Store 대역 — load_snapshot 만 제공."""

    def __init__(self, snap: Snapshot | None) -> None:
        self._snap = snap

    def load_snapshot(self, market: Market) -> Snapshot | None:
        return self._snap


class FakeOrderClient:
    """KisOrderClient 대역 — 잔고 고정, 주문은 기록(특정 종목은 에러 주입)."""

    def __init__(self, balance: Balance, *, fail_ticker: str | None = None) -> None:
        self._balance = balance
        self._fail_ticker = fail_ticker
        self.calls: list[tuple[str, OrderSide, int]] = []

    def get_balance(self) -> Balance:
        return self._balance

    def place_order(
        self,
        ticker: str,
        side: OrderSide,
        qty: int,
        *,
        price: Decimal | None = None,
        market: bool = True,
    ) -> OrderResult:
        if ticker == self._fail_ticker:
            raise KisOrderError(f"주입 실패: {ticker}")
        self.calls.append((ticker, side, qty))
        return OrderResult(
            order_no=f"O{len(self.calls)}",
            org_no="6",
            ticker=ticker,
            side=side,
            qty=qty,
            submitted_at=_NOW,
            message="ok",
        )


def _loop(
    *,
    snap: Snapshot | None,
    balance: Balance,
    tmp_path: Path,
    top_n: int = 20,
    fail_ticker: str | None = None,
) -> tuple[TraderLoop, FakeOrderClient, TradeStore]:
    settings = Settings(trader_top_n=top_n)
    oc = FakeOrderClient(balance, fail_ticker=fail_ticker)
    ts = TradeStore(tmp_path / "trading.db")
    loop = TraderLoop(
        settings,
        "KR",
        order_client=oc,  # type: ignore[arg-type]
        store=FakeStore(snap),  # type: ignore[arg-type]
        trade_store=ts,
        engine=StrategyEngine(settings),
        position_manager=PositionManager(),
    )
    return loop, oc, ts


def _snap(entries: list[ScoreEntry]) -> Snapshot:
    return Snapshot(market="KR", generated_at=_NOW, market_open=True, entries=entries)


def test_run_once_places_buys_with_sized_qty(tmp_path: Path) -> None:
    """매수: 목표금액 = total_eval*(1-buffer)/top_n, 가격별 수량 내림."""
    snap = _snap([_entry("A", "90", price="10000"), _entry("B", "80", price="30000")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)

    loop.run_once(_NOW)

    # target_value = 20,000,000 * 0.95 / 20 = 950,000.
    # A@10,000 → 95주, B@30,000 → 31주.
    assert oc.calls == [("A", "buy", 95), ("B", "buy", 31)]
    orders = ts.recent_orders()
    assert len(orders) == 2
    assert {o["ticker"] for o in orders} == {"A", "B"}
    assert all(o["side"] == "buy" and o["reason"] == "진입:점수상위" for o in orders)


def test_run_once_sells_before_buys(tmp_path: Path) -> None:
    """매도가 매수보다 먼저 접수(현금 확보). 보유 손절 종목 매도 + 신규 매수."""
    snap = _snap(
        [
            _entry("A", "90"),
            _entry("H", "85", sell_alert=True, sell_reason="trailing_stop"),
        ]
    )
    balance = Balance(
        cash=Decimal("20000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)

    loop.run_once(_NOW)

    assert oc.calls[0] == ("H", "sell", 7)
    assert ("A", "buy", 95) in oc.calls
    sides = [c[1] for c in oc.calls]
    assert sides.index("sell") < sides.index("buy")
    reasons = {o["ticker"]: o["reason"] for o in ts.recent_orders()}
    assert reasons["H"] == "청산:트레일링손절"


def test_run_once_records_nav_snapshot(tmp_path: Path) -> None:
    """NAV·포지션 스냅샷 기록(잔고 포지션 그대로)."""
    snap = _snap([_entry("A", "90")])
    balance = Balance(
        cash=Decimal("19000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="A", qty=5, avg_price=Decimal("10000"))],
    )
    loop, _oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)

    loop.run_once(_NOW)

    nav = ts.nav_series()
    assert len(nav) == 1
    assert nav[0]["total_eval"] == Decimal("20000000")
    assert nav[0]["cash"] == Decimal("19000000")
    latest = ts.latest_positions()
    assert {p["ticker"] for p in latest} == {"A"}


def test_run_once_isolates_failed_order(tmp_path: Path) -> None:
    """한 종목 KisOrderError 가 루프를 중단시키지 않음(나머지 정상 접수)."""
    snap = _snap([_entry("A", "90"), _entry("B", "80"), _entry("C", "70")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, fail_ticker="A")

    loop.run_once(_NOW)

    # A 는 실패 → 기록 안 됨. B, C 는 정상.
    assert {c[0] for c in oc.calls} == {"B", "C"}
    assert {o["ticker"] for o in ts.recent_orders()} == {"B", "C"}


def test_run_once_no_snapshot_no_orders(tmp_path: Path) -> None:
    """스냅샷 없음 → 주문·기록·NAV 전부 없음."""
    loop, oc, ts = _loop(
        snap=None,
        balance=Balance(cash=Decimal("1"), total_eval=Decimal("1"), positions=[]),
        tmp_path=tmp_path,
    )

    loop.run_once(_NOW)

    assert oc.calls == []
    assert ts.recent_orders() == []
    assert ts.nav_series() == []


def test_run_once_empty_entries_no_orders(tmp_path: Path) -> None:
    """스냅샷은 있으나 entries 비면 스킵."""
    loop, oc, ts = _loop(
        snap=_snap([]),
        balance=Balance(cash=Decimal("1"), total_eval=Decimal("1"), positions=[]),
        tmp_path=tmp_path,
    )

    loop.run_once(_NOW)

    assert oc.calls == []
    assert ts.nav_series() == []


def test_run_once_skips_nonpositive_price(tmp_path: Path) -> None:
    """가격 0 이하 종목은 매수 스킵(사이징 불가)."""
    snap = _snap([_entry("A", "90", price="0"), _entry("B", "80", price="10000")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, _ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)

    loop.run_once(_NOW)

    assert {c[0] for c in oc.calls} == {"B"}
