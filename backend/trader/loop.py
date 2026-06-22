"""매매 루프 — 스냅샷 로드 → 잔고 동기화 → 결정 → 매도/매수 주문 → 기록.

한 번의 사이클이 ``run_once`` 다(스케줄링은 후속 단계). 매도를 먼저 내 현금을 확보한 뒤
매수한다. 주문은 종목 단위로 격리해(try/except) 한 종목 실패가 루프 전체를 막지 않는다.

리스크 가드 3종:
- 장중 가드: 장 마감이면 NAV 스냅샷만 기록하고 주문은 스킵(NAV 연속성 유지).
- 킬스위치: ``trader_kill_switch`` 또는 halt 파일 존재 시 **신규 매수만** 중단(매도는 계속).
- 멱등: 당일 미체결/부분체결 종목은 재주문하지 않음(1분 루프 중복주문 방지).
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from backend.config import Settings
from backend.market_hours import is_market_open
from backend.schemas import Market, ScoreEntry
from backend.store import Store
from backend.trader.errors import KisOrderError
from backend.trader.kis_order import KisOrderClient
from backend.trader.kis_overseas import KisOverseasOrderClient
from backend.trader.models import OrderResult
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

#: 주문 클라이언트 — 국내(시장가) 또는 해외(지정가). 둘 다 동일 메서드 시그니처.
OrderClient = KisOrderClient | KisOverseasOrderClient

logger = logging.getLogger(__name__)

_ONE = Decimal("1")


class TraderLoop:
    """모의 매매 한 사이클 실행기. 상태(보유·현금)는 매 사이클 잔고로 재동기화."""

    def __init__(
        self,
        settings: Settings,
        market: Market,
        *,
        order_client: OrderClient,
        store: Store,
        trade_store: TradeStore,
        engine: StrategyEngine,
        position_manager: PositionManager,
    ) -> None:
        self._s = settings
        self._market = market
        self._oc = order_client
        self._store = store
        self._ts = trade_store
        self._engine = engine
        self._pm = position_manager

    def _buys_halted(self) -> bool:
        """신규 매수 중단 여부 — 킬스위치(설정) 또는 halt 파일 존재."""
        return self._s.trader_kill_switch or self._s.trader_halt_file.exists()

    def _pending_tickers(self, now: datetime) -> set[str]:
        """당일 미체결/부분체결 종목(재주문 금지 대상). 조회 실패 시 fail-open(빈 집합)."""
        try:
            orders = self._oc.inquire_orders(now.strftime("%Y%m%d"))
        except KisOrderError:
            logger.warning("당일 주문 조회 실패 — 멱등 가드 우회(fail-open)", exc_info=True)
            return set()
        return {o.ticker for o in orders if o.order_qty > o.filled_qty}

    def _reconcile_fills(self, now: datetime) -> None:
        """당일 체결 조회 → 접수 기록에 실제 체결 수량·체결가·실현손익 반영(fail-open).

        표시 정합성 보강일 뿐 — 실패해도 매매에 영향이 없어야 하므로 모든 예외를 흡수한다.
        (KIS 모의는 접수를 '완료'로 응답해, 이 재조회 없이는 접수와 실제 체결이 어긋난다.)
        """
        try:
            statuses = self._oc.inquire_orders(now.strftime("%Y%m%d"))
            self._ts.reconcile_fills(statuses)
        except Exception:
            logger.warning("체결 재조회 실패 — 무시(fail-open)", exc_info=True)

    def run_once(self, now: datetime) -> None:
        """1사이클: 스냅샷→잔고동기화→(장중)결정→매도→매수→NAV/포지션 기록."""
        snap = self._store.load_snapshot(self._market)
        if snap is None or not snap.entries:
            logger.info("스냅샷 없음 — 스킵 (market=%s)", self._market)
            return

        balance = self._oc.get_balance()
        self._pm.sync(balance)

        def _record_nav() -> None:
            self._ts.record_snapshot(
                now,
                market=self._market,
                total_eval=self._pm.total_eval,
                cash=self._pm.cash,
                positions=[p.model_dump() for p in balance.positions],
            )

        # 장중 가드 — 장 마감이면 NAV 만 기록하고 주문은 스킵(NAV 연속성 유지).
        if not is_market_open(self._market, now):
            logger.info("장 마감 — 주문 스킵 (market=%s)", self._market)
            _record_nav()
            self._reconcile_fills(now)  # 마감 후 당일 체결 확정값 반영
            return

        decisions = self._engine.decide(snap.entries, self._pm, top_n=self._s.trader_top_n)
        pending = self._pending_tickers(now)
        halted = self._buys_halted()
        by_ticker = {e.ticker: e for e in snap.entries}

        # 매도 먼저 — 현금 확보. (당일 미체결 종목은 재주문 스킵.)
        for ticker, reason in decisions.sells:
            qty = self._pm.qty(ticker)
            if qty <= 0:
                continue
            if ticker in pending:
                logger.info("미체결 잔존 — 매도 재주문 스킵 (ticker=%s)", ticker)
                continue
            order = self._place_sell(ticker, qty, by_ticker.get(ticker))
            if order is not None:
                self._ts.record_order(order, reason=reason)

        # 매수 — 종목당 목표금액(가용평가액 ÷ top_n). 킬스위치/미체결 시 스킵.
        if halted:
            logger.warning("킬스위치 — 신규 매수 중단 (market=%s)", self._market)
        else:
            target_value = (self._pm.total_eval * (_ONE - self._s.trader_cash_buffer)) / Decimal(
                self._s.trader_top_n
            )
            for ticker in decisions.buys:
                if ticker in pending:
                    logger.info("미체결 잔존 — 매수 재주문 스킵 (ticker=%s)", ticker)
                    continue
                entry = by_ticker.get(ticker)
                if entry is None or entry.price <= 0:
                    continue
                qty = PositionManager.target_qty(target_value, entry.price)
                if qty <= 0:
                    continue
                order = self._place_buy(ticker, qty, entry.price)
                if order is not None:
                    self._ts.record_order(order, reason="진입:점수상위")

        _record_nav()
        self._reconcile_fills(now)  # 직전 사이클들의 접수 → 실제 체결 반영(멱등)

    def _is_us(self) -> bool:
        """미장 여부 — 미장은 지정가(LIMIT) 전용(시장가 없음), 국장은 시장가."""
        return self._market == "US"

    def _place_buy(self, ticker: str, qty: int, snap_price: Decimal) -> OrderResult | None:
        """매수 접수. 미장=스냅샷가 지정가, 국장=시장가. 실패 시 스킵(None 반환)."""
        try:
            if self._is_us():
                return self._oc.place_order(ticker, "buy", qty, price=snap_price, market=False)
            return self._oc.place_order(ticker, "buy", qty, market=True)
        except KisOrderError:
            logger.warning("매수 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
            return None

    def _place_sell(self, ticker: str, qty: int, entry: ScoreEntry | None) -> OrderResult | None:
        """매도 접수. 국장=시장가. 미장=현재가(없으면 스냅샷가) 지정가, 둘 다 없으면 스킵."""
        try:
            if not self._is_us():
                return self._oc.place_order(ticker, "sell", qty, market=True)
            limit = self._sell_limit(ticker, entry)
            if limit is None:
                logger.warning("미장 매도 지정가 미확보 — 스킵 (ticker=%s)", ticker)
                return None
            return self._oc.place_order(ticker, "sell", qty, price=limit, market=False)
        except KisOrderError:
            logger.warning("매도 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
            return None

    def _sell_limit(self, ticker: str, entry: ScoreEntry | None) -> Decimal | None:
        """미장 매도 지정가 — 보유 현재가 우선, 없으면 스냅샷 가격. 둘 다 없으면 None."""
        pos = self._pm.position(ticker)
        if pos is not None and pos.cur_price is not None and pos.cur_price > 0:
            return pos.cur_price
        if entry is not None and entry.price > 0:
            return entry.price
        return None


__all__ = ["TraderLoop"]
