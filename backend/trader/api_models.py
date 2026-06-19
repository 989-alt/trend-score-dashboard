"""매매 현황 탭 API 응답 모델 (pydantic). 읽기전용 — TradeStore 기록을 표시한다.

Decimal 은 pydantic JSON 직렬화에서 문자열로 나가며, 프런트 파서가 number|string 을 처리한다.
모든 응답에 면책(disclaimer)을 포함한다 — 모의 매매 전진검증 기록일 뿐 투자 자문이 아니다.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from backend.schemas import DISCLAIMER

_CFG = ConfigDict(extra="forbid")


class TradingPosition(BaseModel):
    """보유 종목 1건(최신 스냅샷). 금액·손익은 소스 미제공 시 None."""

    model_config = _CFG

    ticker: str
    name: str
    qty: int
    avg_price: Decimal
    cur_price: Decimal | None = None
    eval_amount: Decimal | None = None
    pnl_amount: Decimal | None = None
    pnl_pct: Decimal | None = None


class TradingOrder(BaseModel):
    """주문 접수 기록 1건(체결이 아니라 '접수')."""

    model_config = _CFG

    ts: str
    ticker: str
    side: str
    qty: int
    reason: str
    message: str


class NavPoint(BaseModel):
    """NAV(총평가) 시계열 1점."""

    model_config = _CFG

    ts: str
    total_eval: Decimal | None = None
    cash: Decimal | None = None


class TradingStatus(BaseModel):
    """``GET /api/trading/status`` — 가동 여부 + 최신 NAV·포지션 요약 + 면책."""

    model_config = _CFG

    running: bool
    total_eval: Decimal | None = None
    cash: Decimal | None = None
    position_count: int = 0
    total_pnl: Decimal | None = None  # 보유 종목 pnl_amount 합
    as_of: str | None = None  # 최신 NAV ts
    disclaimer: str = DISCLAIMER


class TradingPositionsResponse(BaseModel):
    """``GET /api/trading/positions`` — 최신 보유 종목 + 면책."""

    model_config = _CFG

    positions: list[TradingPosition]
    disclaimer: str = DISCLAIMER


class TradingOrdersResponse(BaseModel):
    """``GET /api/trading/history`` — 최근 주문 접수 기록 + 면책."""

    model_config = _CFG

    orders: list[TradingOrder]
    disclaimer: str = DISCLAIMER


class TradingNavResponse(BaseModel):
    """``GET /api/trading/nav`` — NAV 시계열 + 면책."""

    model_config = _CFG

    nav: list[NavPoint]
    disclaimer: str = DISCLAIMER


__all__ = [
    "NavPoint",
    "TradingNavResponse",
    "TradingOrder",
    "TradingOrdersResponse",
    "TradingPosition",
    "TradingPositionsResponse",
    "TradingStatus",
]
