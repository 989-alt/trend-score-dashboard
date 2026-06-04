"""트레일링 손절 — 무상태(stateless) 매도요구 판정.

swing-bot ``src/execution/exit_manager.py`` 의 트레일링 스톱 로직을 본 대시보드에
무상태로 포팅한다. 실 주문은 없고 '매도요구' 신호만 만든다. 가상진입 영속(StopState)
없이, 매 사이클 가격이력에서 결정론으로 peak·stop 을 다시 계산한다.

판정(결정론):
- 트레일링 우선 — ``peak>0`` 이고 ``current <= peak*(1-pct/100)`` 이면 ``trailing_stop``.
- 그 다음 200일선 이탈 — ``ma200>0`` 이고 ``current < ma200`` 이면 ``ma200_break``.
- 둘 다 아니면 ``None`` (보유 유지).
"""

from __future__ import annotations

from decimal import Decimal

from backend.schemas import OHLCVRow, SellReason


def trailing_stop_price(peak: Decimal, pct: Decimal) -> Decimal:
    """트레일링 스톱 발동 임계가 = ``peak * (1 - pct/100)``."""
    return peak * (Decimal("1") - pct / Decimal("100"))


def evaluate_sell(
    *,
    current: Decimal,
    peak: Decimal | None,
    ma200: Decimal | None,
    pct: Decimal,
) -> SellReason | None:
    """매도요구 사유 판정 (트레일링 우선).

    - ``peak`` 가 양수이고 ``current <= trailing_stop_price(peak, pct)`` → ``trailing_stop``.
    - 위가 아니고 ``ma200`` 가 양수이고 ``current < ma200`` → ``ma200_break``.
    - 그 외 → ``None`` (보유 유지). ``None`` 또는 비양수 값인 검사는 건너뛴다
      (exit_manager: ``peak>0``·``ma200>0`` 가드와 정합 — 0/음수는 미관측으로 본다).
    """
    if peak is not None and peak > 0 and current <= trailing_stop_price(peak, pct):
        return "trailing_stop"
    if ma200 is not None and ma200 > 0 and current < ma200:
        return "ma200_break"
    return None


def compute_trailing_stop(
    rows: list[OHLCVRow],
    current: Decimal,
    *,
    window: int,
    pct: Decimal,
) -> tuple[Decimal, Decimal]:
    """가격이력에서 무상태로 (peak, stop) 산출.

    - ``peak`` = 최근 ``window`` 봉 종가 ∪ ``current`` 의 최대 — "진입 이후 최고가"를
      가상진입 영속 없이 가격이력만으로 결정론 근사(exit_manager 의 peak 의미).
    - ``stop`` = ``trailing_stop_price(peak, pct)``.

    ``window<=0`` 이거나 ``rows`` 가 비면 ``current`` 만으로 peak 를 잡는다.
    """
    recent = rows[-window:] if window > 0 else rows
    peak = max((row.close for row in recent), default=current)
    peak = max(peak, current)
    return peak, trailing_stop_price(peak, pct)


__all__ = ["compute_trailing_stop", "evaluate_sell", "trailing_stop_price"]
