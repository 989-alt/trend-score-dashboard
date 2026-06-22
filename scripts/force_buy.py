"""스모크 테스트 강제 매수 (1회용) — LLM 의사결정과 무관하게 지정 종목 1주 매수.

실제 체결이 일어나는지 확인하려고 장 시작 시 무조건 1주를 산다(전략·등급 무시).
    uv run python scripts/force_buy.py --market KR   # SK하이닉스 000660, 시장가
    uv run python scripts/force_buy.py --market US   # 알파벳A GOOGL, 마켓터블 지정가

- KR: 시장가(ORD_DVSN=01) → 개장 시 즉시 체결.
- US: 지정가 전용이라 **현재가×1.05 지정가**(매수호가 위 = 마켓터블 → 시장가처럼 즉시 체결).
키·토큰은 출력하지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import Settings, get_settings  # noqa: E402
from backend.market_data import get_provider  # noqa: E402
from backend.trader.errors import KisOrderError  # noqa: E402
from backend.trader.kis_auth import token_from_settings  # noqa: E402
from backend.trader.kis_order import KisOrderClient  # noqa: E402
from backend.trader.kis_overseas import KisOverseasOrderClient  # noqa: E402
from backend.trader.models import OrderResult  # noqa: E402
from backend.trader.store import TradeStore  # noqa: E402

_MOCK = "https://openapivts.koreainvestment.com:29443"
_KR_TICKER = "000660"  # SK하이닉스
_KR_NAME = "SK하이닉스"
_US_TICKER = "GOOGL"  # 알파벳 A
_US_NAME = "알파벳A"
_REASON = "강제매수:스모크"  # 매매 현황에 출처가 남도록(트레이더 매매와 구분)


def _log(settings: Settings, order: OrderResult, name: str) -> None:
    """강제매수를 TradeStore 에 접수 기록 — 매매 현황 history 에 출처가 남게 한다.

    이 기록이 없으면 강제매수로 생긴 보유가 history 에 안 보여 '매수 없이 보유'처럼 보인다.
    체결 수량/실현손익은 트레이더 루프의 체결 재조회가 채운다(같은 order_no).
    """
    TradeStore(settings.trader_db_path).record_order(order, reason=_REASON, name=name)


def main() -> None:
    parser = argparse.ArgumentParser(description="스모크 테스트 강제 매수 1주")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    market = parser.parse_args().market

    settings = get_settings()
    token = token_from_settings(settings, _MOCK)

    if market == "KR":
        client = KisOrderClient(settings, mode="mock", token=token)
        print(f"강제 매수: KR {_KR_TICKER}(SK하이닉스) 1주 시장가")
        try:
            r = client.place_order(_KR_TICKER, "buy", 1, market=True)
            _log(settings, r, _KR_NAME)
            print("결과 — ODNO:", r.order_no, "msg:", r.message)
        except KisOrderError as exc:
            print("주문 실패:", exc)
        return

    # US — 지정가 전용 → 현재가×1.05 마켓터블 지정가로 체결 보장.
    try:
        price = get_provider(settings).get_quote(_US_TICKER, "US").price
    except Exception as exc:
        print(f"현재가 조회 실패 → 주문 중단: {exc}")
        return
    if price <= 0:
        print(f"현재가 비정상(${price}) → 주문 중단")
        return
    limit = (price * Decimal("1.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    print(f"강제 매수: US {_US_TICKER}(알파벳A) 1주 지정가 ${limit} (현재가 ${price}×1.05)")
    overseas = KisOverseasOrderClient(settings, mode="mock", token=token)
    try:
        r = overseas.place_order(_US_TICKER, "buy", 1, price=limit)
        _log(settings, r, _US_NAME)
        print("결과 — ODNO:", r.order_no, "msg:", r.message)
    except KisOrderError as exc:
        print("주문 실패:", exc)


if __name__ == "__main__":
    main()
