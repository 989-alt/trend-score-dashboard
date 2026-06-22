"""보유 포지션·현금 상태 + 주문 사이징.

매 루프 시작 시 KIS 잔고(``Balance``)로 ``sync`` 해 **메모리-실계좌 드리프트를 차단**한다
(유령 체결·부분 체결 흡수). 사이징은 목표금액÷현재가(호가단위 내림).
"""

from __future__ import annotations

from decimal import Decimal

from backend.trader.models import Balance, HoldingPosition

_DEC0 = Decimal("0")


class PositionManager:
    """현재 보유·현금의 단일 출처. KIS 잔고로 동기화."""

    def __init__(self) -> None:
        self._pos: dict[str, HoldingPosition] = {}
        self._cash: Decimal = _DEC0
        self._total: Decimal = _DEC0

    def sync(self, balance: Balance) -> None:
        """KIS 잔고로 상태 갱신(실계좌가 진실)."""
        self._pos = {p.ticker: p for p in balance.positions}
        self._cash = balance.cash
        self._total = balance.total_eval

    @property
    def cash(self) -> Decimal:
        """주문가능현금."""
        return self._cash

    @property
    def total_eval(self) -> Decimal:
        """총평가금액(현금+주식)."""
        return self._total

    def held_tickers(self) -> set[str]:
        return set(self._pos)

    def qty(self, ticker: str) -> int:
        pos = self._pos.get(ticker)
        return pos.qty if pos else 0

    def name(self, ticker: str) -> str:
        """보유 종목명(잔고 기준). 미보유면 빈 문자열 — 주문 기록 표시용 폴백."""
        pos = self._pos.get(ticker)
        return pos.name if pos else ""

    def position(self, ticker: str) -> HoldingPosition | None:
        return self._pos.get(ticker)

    @staticmethod
    def target_qty(target_value: Decimal, price: Decimal, *, lot: int = 1) -> int:
        """목표금액으로 살 수 있는 수량(호가단위 ``lot`` 내림). 가격·금액 ≤0 이면 0."""
        if price <= 0 or target_value <= 0 or lot < 1:
            return 0
        shares = int(target_value / price)
        return (shares // lot) * lot


__all__ = ["PositionManager"]
