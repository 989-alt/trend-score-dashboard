"""TraderLoop.run_once 단위테스트 — 가짜 주문클라이언트/스토어, 실제 TradeStore. 네트워크 0."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.config import Settings
from backend.schemas import Grade, Market, ScoreEntry, SellReason, Snapshot
from backend.trader.kis_order import KisOrderError
from backend.trader.loop import TraderLoop
from backend.trader.models import Balance, HoldingPosition, OrderResult, OrderSide, OrderStatus
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

#: 2026-06-22(월) 10:00 KST = 01:00 UTC — KR 정규장 개장 중(주문 가드 통과용).
_NOW = datetime(2026, 6, 22, 1, 0, tzinfo=UTC)
#: 장 마감 시각 — 같은 영업일 18:05 KST = 09:05 UTC(KR 마감 15:30 이후).
_CLOSED = datetime(2026, 6, 22, 9, 5, tzinfo=UTC)
#: 2026-06-22(월) 10:00 EDT = 14:00 UTC — US 정규장(09:30–16:00 ET) 개장 중.
_NOW_US = datetime(2026, 6, 22, 14, 0, tzinfo=UTC)


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

    def __init__(
        self,
        balance: Balance,
        *,
        fail_ticker: str | None = None,
        pending: list[OrderStatus] | None = None,
        inquire_raises: bool = False,
    ) -> None:
        self._balance = balance
        self._fail_ticker = fail_ticker
        self._pending = pending or []
        self._inquire_raises = inquire_raises
        self.calls: list[tuple[str, OrderSide, int]] = []
        #: 주문별 (price, market) 캡처 — 지정가/시장가 검증용.
        self.placed: list[dict[str, object]] = []

    def get_balance(self) -> Balance:
        return self._balance

    def inquire_orders(self, query_date: str) -> list[OrderStatus]:
        if self._inquire_raises:
            raise KisOrderError(f"주입 실패: 조회 {query_date}")
        return self._pending

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
        self.placed.append({"ticker": ticker, "side": side, "price": price, "market": market})
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
    settings: Settings | None = None,
    pending: list[OrderStatus] | None = None,
    inquire_raises: bool = False,
    market: Market = "KR",
    quote_fn: Callable[[str], Decimal] | None = None,
) -> tuple[TraderLoop, FakeOrderClient, TradeStore]:
    settings = settings or Settings(trader_top_n=top_n)
    oc = FakeOrderClient(
        balance, fail_ticker=fail_ticker, pending=pending, inquire_raises=inquire_raises
    )
    ts = TradeStore(tmp_path / "trading.db")
    loop = TraderLoop(
        settings,
        market,
        order_client=oc,  # type: ignore[arg-type]
        store=FakeStore(snap),  # type: ignore[arg-type]
        trade_store=ts,
        engine=StrategyEngine(settings),
        position_manager=PositionManager(),
        quote_fn=quote_fn,
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
    # KR 은 시장가(market=True, price 미지정) — 미장 지정가와 대비.
    assert all(p["market"] is True and p["price"] is None for p in oc.placed)
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


# ── 리스크 가드 (P4) ──────────────────────────────────────────────────────


def _pending(ticker: str, *, side: OrderSide = "buy", order_qty: int = 10) -> OrderStatus:
    """미체결(주문>체결) 주문 1건."""
    return OrderStatus(
        order_no="P1", ticker=ticker, side=side, order_qty=order_qty, filled_qty=0, status="접수"
    )


def test_run_once_market_closed_records_nav_no_orders(tmp_path: Path) -> None:
    """장 마감: 주문은 전부 스킵하되 NAV 스냅샷은 기록(연속성 유지)."""
    snap = _snap(
        [_entry("A", "90"), _entry("H", "85", sell_alert=True, sell_reason="trailing_stop")]
    )
    balance = Balance(
        cash=Decimal("20000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)

    loop.run_once(_CLOSED)

    assert oc.calls == []
    assert ts.recent_orders() == []
    nav = ts.nav_series()
    assert len(nav) == 1
    assert nav[0]["total_eval"] == Decimal("20000000")


def test_run_once_kill_switch_skips_buys_keeps_sells(tmp_path: Path) -> None:
    """킬스위치 ON: 신규 매수는 스킵, 매도(손절)는 계속 접수."""
    snap = _snap(
        [_entry("A", "90"), _entry("H", "85", sell_alert=True, sell_reason="trailing_stop")]
    )
    balance = Balance(
        cash=Decimal("20000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    settings = Settings(trader_top_n=20, trader_kill_switch=True)
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, settings=settings)

    loop.run_once(_NOW)

    assert oc.calls == [("H", "sell", 7)]
    assert {o["ticker"] for o in ts.recent_orders()} == {"H"}


def test_run_once_halt_file_skips_buys(tmp_path: Path) -> None:
    """halt 파일 존재: 신규 매수 스킵(매도는 계속)."""
    halt = tmp_path / ".trader_halt"
    halt.write_text("stop", encoding="utf-8")
    snap = _snap(
        [_entry("A", "90"), _entry("H", "85", sell_alert=True, sell_reason="trailing_stop")]
    )
    balance = Balance(
        cash=Decimal("20000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    settings = Settings(trader_top_n=20, trader_halt_file=halt)
    loop, oc, _ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, settings=settings)

    loop.run_once(_NOW)

    assert oc.calls == [("H", "sell", 7)]


def test_run_once_skips_pending_ticker(tmp_path: Path) -> None:
    """당일 미체결 종목은 재주문 스킵(다른 종목은 정상 접수)."""
    snap = _snap([_entry("A", "90"), _entry("B", "80")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, ts = _loop(
        snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, pending=[_pending("A")]
    )

    loop.run_once(_NOW)

    assert {c[0] for c in oc.calls} == {"B"}
    assert {o["ticker"] for o in ts.recent_orders()} == {"B"}


def test_run_once_inquire_error_fails_open(tmp_path: Path) -> None:
    """당일 주문 조회 실패 → fail-open(멱등 가드 우회, 주문은 계속)."""
    snap = _snap([_entry("A", "90"), _entry("B", "80")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, _ts = _loop(
        snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, inquire_raises=True
    )

    loop.run_once(_NOW)

    assert {c[0] for c in oc.calls} == {"A", "B"}


def test_run_once_reconciles_fills_and_realized(tmp_path: Path) -> None:
    """사이클 끝 체결 재조회: 접수 매도가 체결로 갱신 + 실현손익 산정.

    매도 H(평단 10,000)가 12,000 에 7주 전량 체결 → (12000-10000)*7 = 14,000 실현.
    체결 상태라 멱등 가드의 '미체결'엔 안 걸린다(order_qty==filled_qty).
    """
    snap = _snap([_entry("H", "85", sell_alert=True, sell_reason="trailing_stop")])
    balance = Balance(
        cash=Decimal("20000000"),
        total_eval=Decimal("20000000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    # 매도 H 의 order_no 는 첫 주문이라 "O1" — 같은 번호로 체결 결과를 돌려준다.
    filled = OrderStatus(
        order_no="O1",
        ticker="H",
        side="sell",
        order_qty=7,
        filled_qty=7,
        filled_price=Decimal("12000"),
        status="체결",
    )
    loop, _oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, pending=[filled])

    loop.run_once(_NOW)

    order = next(o for o in ts.recent_orders() if o["ticker"] == "H")
    assert order["filled_qty"] == 7 and order["status"] == "체결"
    assert ts.realized_pnl_total() == Decimal("14000")


# ── 미장 지정가(LIMIT) 처리 (P8) ──────────────────────────────────────────


def test_run_once_us_buys_use_marketable_limit(tmp_path: Path) -> None:
    """미장 매수: 마케터블 지정가(market=False). 현재가 조회자 미주입 → 스냅샷가×(1+1%) 폴백."""
    snap = _snap([_entry("AAPL", "90", price="190"), _entry("MSFT", "80", price="400")])
    balance = Balance(cash=Decimal("20000"), total_eval=Decimal("20000"), positions=[])
    loop, oc, _ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, market="US")

    loop.run_once(_NOW_US)

    assert {c[0] for c in oc.calls} == {"AAPL", "MSFT"}
    placed = {p["ticker"]: p for p in oc.placed}
    # 비마케터블(스냅샷가 그대로)이면 영영 미체결 → +1% 마케터블 지정가로 체결되게.
    assert placed["AAPL"]["market"] is False and placed["AAPL"]["price"] == Decimal("191.90")
    assert placed["MSFT"]["market"] is False and placed["MSFT"]["price"] == Decimal("404.00")


def test_run_once_us_buys_use_fresh_quote(tmp_path: Path) -> None:
    """미장 매수: 주입된 현재가 조회자가 있으면 스냅샷가가 아닌 신선 현재가×(1+1%)로 지정가."""
    snap = _snap([_entry("AAPL", "90", price="190")])
    balance = Balance(cash=Decimal("20000"), total_eval=Decimal("20000"), positions=[])
    # 신선 현재가 = 200(스냅샷 190 과 다름). 지정가 = 200×1.01 = 202.00 이어야 함.
    loop, oc, _ts = _loop(
        snap=snap,
        balance=balance,
        tmp_path=tmp_path,
        top_n=20,
        market="US",
        quote_fn=lambda _t: Decimal("200"),
    )

    loop.run_once(_NOW_US)

    placed = {p["ticker"]: p for p in oc.placed}
    assert placed["AAPL"]["market"] is False and placed["AAPL"]["price"] == Decimal("202.00")


def test_run_once_us_sell_uses_cur_price_limit(tmp_path: Path) -> None:
    """미장 매도: 보유 현재가(cur_price)를 지정가로(market=False)."""
    snap = _snap(
        [
            _entry("AAPL", "90", price="190"),
            _entry("TSLA", "85", price="250", sell_alert=True, sell_reason="trailing_stop"),
        ]
    )
    balance = Balance(
        cash=Decimal("20000"),
        total_eval=Decimal("20000"),
        positions=[
            HoldingPosition(
                ticker="TSLA", qty=4, avg_price=Decimal("300"), cur_price=Decimal("248.50")
            )
        ],
    )
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, market="US")

    loop.run_once(_NOW_US)

    # 매도가 먼저, 현재가 248.50 을 지정가로.
    assert oc.calls[0] == ("TSLA", "sell", 4)
    sell = next(p for p in oc.placed if p["ticker"] == "TSLA")
    assert sell["side"] == "sell" and sell["market"] is False
    assert sell["price"] == Decimal("248.50")
    assert {o["ticker"]: o["reason"] for o in ts.recent_orders()}["TSLA"] == "청산:트레일링손절"


def test_run_once_us_sell_falls_back_to_snapshot_price(tmp_path: Path) -> None:
    """미장 매도: 보유 현재가 없으면 스냅샷 가격을 지정가로."""
    snap = _snap([_entry("TSLA", "85", price="250", sell_alert=True, sell_reason="trailing_stop")])
    balance = Balance(
        cash=Decimal("20000"),
        total_eval=Decimal("20000"),
        # cur_price 미지정(None) → 스냅샷 가격(250) 폴백.
        positions=[HoldingPosition(ticker="TSLA", qty=4, avg_price=Decimal("300"))],
    )
    loop, oc, _ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, market="US")

    loop.run_once(_NOW_US)

    sell = next(p for p in oc.placed if p["ticker"] == "TSLA")
    assert sell["market"] is False and sell["price"] == Decimal("250")


def test_run_once_us_sell_skipped_without_limit(tmp_path: Path) -> None:
    """미장 매도: 현재가·스냅샷가 둘 다 없으면(스냅샷 이탈) 지정가 미확보 → 스킵."""
    # 보유 종목이 스냅샷에 없음 → 청산:스냅샷이탈인데 지정가 출처가 없음.
    snap = _snap([_entry("AAPL", "90", price="190")])
    balance = Balance(
        cash=Decimal("20000"),
        total_eval=Decimal("20000"),
        positions=[HoldingPosition(ticker="ZZZZ", qty=4, avg_price=Decimal("300"))],
    )
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20, market="US")

    loop.run_once(_NOW_US)

    # ZZZZ 매도는 지정가 미확보로 스킵, AAPL 매수만 접수.
    assert all(c[1] != "sell" for c in oc.calls)
    assert {o["ticker"] for o in ts.recent_orders()} == {"AAPL"}


# ── 당일 재매수 금지 · 일손실 킬스위치 (리스크 가드 단계4) ─────────────────────


def test_run_once_blocks_same_day_rebuy(tmp_path: Path) -> None:
    """당일(차단창 내) 이미 매수 접수한 종목은 재매수 스킵 — 1분 무한 재주문 차단(과매매 억제)."""
    snap = _snap([_entry("A", "90"), _entry("B", "80")])
    balance = Balance(cash=Decimal("20000000"), total_eval=Decimal("20000000"), positions=[])
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)
    # A 를 '오늘'(같은 _NOW) 이미 매수 접수한 것으로 선기록 → 재매수 차단 대상.
    ts.record_order(
        OrderResult(order_no="X", org_no="6", ticker="A", side="buy", qty=1, submitted_at=_NOW),
        reason="진입:점수상위",
        name="A",
    )

    loop.run_once(_NOW)

    assert {c[0] for c in oc.calls} == {"B"}  # A 는 당일 재매수 금지로 스킵
    assert ("A", "buy", 95) not in oc.calls


def test_run_once_daily_loss_kill_switch_halts_buys(tmp_path: Path) -> None:
    """당일 첫 NAV 대비 −3% 이상 빠지면 신규 매수 중단, 매도(손절)는 계속."""
    snap = _snap(
        [_entry("A", "90"), _entry("H", "85", sell_alert=True, sell_reason="trailing_stop")]
    )
    # 현재 총평가 970만 = 당일 첫 NAV 1000만 대비 정확히 −3%(임계 도달).
    balance = Balance(
        cash=Decimal("9700000"),
        total_eval=Decimal("9700000"),
        positions=[HoldingPosition(ticker="H", qty=7, avg_price=Decimal("10000"))],
    )
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)
    # 당일 첫 NAV = 1000만 선기록(같은 날·KR). 의사결정 시 이 값이 기준이 된다.
    ts.record_snapshot(
        _NOW,
        market="KR",
        total_eval=Decimal("10000000"),
        cash=Decimal("10000000"),
        positions=[],
    )

    loop.run_once(_NOW)

    # 신규 매수 없음(킬스위치), 손절 매도 H 만 접수.
    assert oc.calls == [("H", "sell", 7)]


def test_run_once_daily_loss_inactive_above_threshold(tmp_path: Path) -> None:
    """낙폭이 임계 미만(−2%)이면 킬스위치 미발동 — 신규 매수 정상."""
    snap = _snap([_entry("A", "90")])
    balance = Balance(
        cash=Decimal("9800000"), total_eval=Decimal("9800000"), positions=[]
    )  # 첫 NAV 1000만 대비 −2%
    loop, oc, ts = _loop(snap=snap, balance=balance, tmp_path=tmp_path, top_n=20)
    ts.record_snapshot(
        _NOW, market="KR", total_eval=Decimal("10000000"), cash=Decimal("10000000"), positions=[]
    )

    loop.run_once(_NOW)

    # 9,800,000*0.95/20 = 465,500 ÷ 10,000 = 46주 매수 발생(킬스위치 미발동).
    assert ("A", "buy", 46) in oc.calls
