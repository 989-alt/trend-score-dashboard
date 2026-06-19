"""trend-trader 데이터 계약 — 주문·체결·잔고. Decimal 전면, pydantic v2 ``extra=forbid``.

금액·가격은 ``Decimal``(float 금지), 수량은 ``int``. 시각은 timezone-aware.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: 매매 방향.
OrderSide = Literal["buy", "sell"]

_CFG = ConfigDict(extra="forbid")


class OrderResult(BaseModel):
    """주문 접수 결과 (체결이 아니라 '접수'). ``org_no`` 는 정정/취소에 필요."""

    model_config = _CFG

    order_no: str  # ODNO (주문번호)
    org_no: str  # KRX_FWDG_ORD_ORGNO (한국거래소전송주문조직번호 — 취소/정정용)
    ticker: str
    side: OrderSide
    qty: int
    submitted_at: datetime
    message: str = ""


class HoldingPosition(BaseModel):
    """잔고 한 종목 — 보유 수량·평단·평가."""

    model_config = _CFG

    ticker: str
    name: str = ""
    qty: int
    avg_price: Decimal
    cur_price: Decimal | None = None
    eval_amount: Decimal | None = None  # 평가금액
    pnl_amount: Decimal | None = None  # 평가손익
    pnl_pct: Decimal | None = None  # 평가손익률(%)


class Balance(BaseModel):
    """계좌 잔고 요약 — 주문가능현금 + 총평가 + 보유 종목."""

    model_config = _CFG

    cash: Decimal  # 주문가능현금(예수금)
    total_eval: Decimal  # 총평가금액(현금+주식)
    positions: list[HoldingPosition]


class OrderStatus(BaseModel):
    """주문 체결 현황 (일별 체결 조회)."""

    model_config = _CFG

    order_no: str
    ticker: str
    side: OrderSide
    order_qty: int
    filled_qty: int
    filled_price: Decimal | None = None
    status: str = ""  # 접수/체결/부분체결/취소 등 (KIS 텍스트)


__all__ = ["Balance", "HoldingPosition", "OrderResult", "OrderSide", "OrderStatus"]
