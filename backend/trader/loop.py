"""매매 루프 — 스냅샷 로드 → 잔고 동기화 → 결정 → 매도/매수 주문 → 기록.

한 번의 사이클이 ``run_once`` 다(스케줄링은 후속 단계). 매도를 먼저 내 현금을 확보한 뒤
매수한다. 주문은 종목 단위로 격리해(try/except) 한 종목 실패가 루프 전체를 막지 않는다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from backend.config import Settings
from backend.schemas import Market
from backend.store import Store
from backend.trader.kis_order import KisOrderClient, KisOrderError
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

logger = logging.getLogger(__name__)

_ONE = Decimal("1")


class TraderLoop:
    """모의 매매 한 사이클 실행기. 상태(보유·현금)는 매 사이클 잔고로 재동기화."""

    def __init__(
        self,
        settings: Settings,
        market: Market,
        *,
        order_client: KisOrderClient,
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

    def run_once(self, now: datetime) -> None:
        """1사이클: 스냅샷→잔고동기화→결정→매도→매수→NAV/포지션 기록."""
        snap = self._store.load_snapshot(self._market)
        if snap is None or not snap.entries:
            logger.info("스냅샷 없음 — 스킵 (market=%s)", self._market)
            return

        balance = self._oc.get_balance()
        self._pm.sync(balance)

        decisions = self._engine.decide(snap.entries, self._pm, top_n=self._s.trader_top_n)

        # 매도 먼저 — 현금 확보.
        for ticker, reason in decisions.sells:
            qty = self._pm.qty(ticker)
            if qty <= 0:
                continue
            try:
                order = self._oc.place_order(ticker, "sell", qty, market=True)
            except KisOrderError:
                logger.warning("매도 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
                continue
            self._ts.record_order(order, reason=reason)

        # 매수 — 종목당 목표금액(가용평가액 ÷ top_n).
        by_ticker = {e.ticker: e for e in snap.entries}
        target_value = (self._pm.total_eval * (_ONE - self._s.trader_cash_buffer)) / Decimal(
            self._s.trader_top_n
        )
        for ticker in decisions.buys:
            entry = by_ticker.get(ticker)
            if entry is None or entry.price <= 0:
                continue
            qty = PositionManager.target_qty(target_value, entry.price)
            if qty <= 0:
                continue
            try:
                order = self._oc.place_order(ticker, "buy", qty, market=True)
            except KisOrderError:
                logger.warning("매수 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
                continue
            self._ts.record_order(order, reason="진입:점수상위")

        self._ts.record_snapshot(
            now,
            total_eval=self._pm.total_eval,
            cash=self._pm.cash,
            positions=[p.model_dump() for p in balance.positions],
        )


__all__ = ["TraderLoop"]
