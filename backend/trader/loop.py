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
                total_eval=self._pm.total_eval,
                cash=self._pm.cash,
                positions=[p.model_dump() for p in balance.positions],
            )

        # 장중 가드 — 장 마감이면 NAV 만 기록하고 주문은 스킵(NAV 연속성 유지).
        if not is_market_open(self._market, now):
            logger.info("장 마감 — 주문 스킵 (market=%s)", self._market)
            _record_nav()
            return

        decisions = self._engine.decide(snap.entries, self._pm, top_n=self._s.trader_top_n)
        pending = self._pending_tickers(now)
        halted = self._buys_halted()

        # 매도 먼저 — 현금 확보. (당일 미체결 종목은 재주문 스킵.)
        for ticker, reason in decisions.sells:
            qty = self._pm.qty(ticker)
            if qty <= 0:
                continue
            if ticker in pending:
                logger.info("미체결 잔존 — 매도 재주문 스킵 (ticker=%s)", ticker)
                continue
            try:
                order = self._oc.place_order(ticker, "sell", qty, market=True)
            except KisOrderError:
                logger.warning("매도 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
                continue
            self._ts.record_order(order, reason=reason)

        # 매수 — 종목당 목표금액(가용평가액 ÷ top_n). 킬스위치/미체결 시 스킵.
        if halted:
            logger.warning("킬스위치 — 신규 매수 중단 (market=%s)", self._market)
        else:
            by_ticker = {e.ticker: e for e in snap.entries}
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
                try:
                    order = self._oc.place_order(ticker, "buy", qty, market=True)
                except KisOrderError:
                    logger.warning("매수 주문 실패 — 스킵 (ticker=%s)", ticker, exc_info=True)
                    continue
                self._ts.record_order(order, reason="진입:점수상위")

        _record_nav()


__all__ = ["TraderLoop"]
