"""US 매수 경로 검증 (1회용) — 1주 지정가 매수 접수→취소로 주문 TR·자금 확인.

서버에서:  uv run python scripts/verify_us_order.py
- buying_power() 로 통합증거금 매수가능액(USD) 확인.
- AAPL 1주를 **체결 불가능한 $10 지정가**로 매수 접수 → 응답(접수/거부+사유) 출력 → 즉시 취소.
- $10 매수지정가는 시장가보다 한참 낮아 절대 체결되지 않으며, 접수 후 취소하므로 포지션을
  남기지 않는다(취소 실패해도 $10 가엔 영영 체결 안 됨). 키·토큰은 출력하지 않는다.
"""

from __future__ import annotations

import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.trader.errors import KisOrderError  # noqa: E402
from backend.trader.kis_auth import token_from_settings  # noqa: E402
from backend.trader.kis_overseas import KisOverseasOrderClient  # noqa: E402

_MOCK = "https://openapivts.koreainvestment.com:29443"


def main() -> None:
    s = get_settings()
    client = KisOverseasOrderClient(s, mode="mock", token=token_from_settings(s, _MOCK))

    print("=== 1) 통합증거금 매수가능액 ===")
    try:
        print("buying_power (USD):", client.buying_power())
    except Exception as exc:
        print("buying_power 실패:", exc)

    time.sleep(1.0)  # 트레이더와 모의키 공유 → 초당거래건수 충돌 완화.
    print("\n=== 2) AAPL 1주 $10 지정가 매수 접수 시도 (체결 불가 가격) ===")
    try:
        r = client.place_order("AAPL", "buy", 1, price=Decimal("10"))
    except KisOrderError as exc:
        print("접수 거부/오류:", exc)
        print(
            "(메시지에 '초당' 있으면 rate limit — 잠시 후 재실행. '예수금/현금부족'이면 자금 문제.)"
        )
        return
    print("접수 OK — ODNO:", r.order_no, "ORG:", r.org_no, "msg:", r.message)

    time.sleep(1.0)
    print("\n=== 3) 취소 ===")
    try:
        cancel = client.cancel_order(r.order_no, r.org_no, 1)
        print("취소 OK — msg:", cancel.message)
    except KisOrderError as exc:
        print("취소 실패(수동 확인 — 단 $10 가라 체결은 안 됨):", exc)


if __name__ == "__main__":
    main()
