"""KisOverseasOrderClient 단위테스트 — 네트워크 0 (httpx post/get 를 monkeypatch).

해외 주문 본문(거래소·종목·수량·지정가·TR)·잔고 파싱(output1+USD output2)·지정가 강제·
rt_cd 오류·비-2xx 본문 표면화만 검증(실 KIS 호출 없음).
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import Settings
from backend.trader.errors import KisOrderError
from backend.trader.kis_overseas import KisOverseasOrderClient


def _client() -> KisOverseasOrderClient:
    tmp = Path(tempfile.mkdtemp(prefix="tsd-overseas-"))
    settings = Settings(
        kis_appkey="k",
        kis_appsecret="s",
        kis_account="50190719",
        kis_account_prod="01",
        trader_token_path=tmp / ".tok.json",
    )
    c = KisOverseasOrderClient(settings)
    # KisToken 메모리 캐시를 미리 채워 _headers 가 발급을 트리거하지 않게 한다.
    c._token._token = "tok"
    c._token._token_exp = datetime.now(tz=UTC) + timedelta(hours=1)
    return c


def _resp(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "http://test"))


def test_place_order_limit_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """지정가 매수 — 거래소 NASD·종목·수량·OVRS_ORD_UNPR·ORD_DVSN=00·매수 TR·hashkey."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_post(path: str, json: Any = None, headers: Any = None) -> httpx.Response:
        cap["path"], cap["body"], cap["headers"] = path, json, headers
        return _resp(
            {
                "rt_cd": "0",
                "msg1": "주문 전송 완료",
                "output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "0810"},
            }
        )

    monkeypatch.setattr(c._client, "post", fake_post)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")

    r = c.place_order("AAPL", "buy", 3, price=Decimal("190.25"))
    assert (r.order_no, r.org_no, r.side, r.qty) == ("0001", "0810", "buy", 3)
    assert "overseas-stock/v1/trading/order" in cap["path"]
    assert cap["body"]["OVRS_EXCG_CD"] == "NASD" and cap["body"]["PDNO"] == "AAPL"
    assert cap["body"]["ORD_QTY"] == "3" and cap["body"]["OVRS_ORD_UNPR"] == "190.25"
    assert cap["body"]["ORD_DVSN"] == "00" and cap["body"]["ORD_SVR_DVSN_CD"] == "0"
    assert cap["headers"]["tr_id"] == "VTTT1002U" and cap["headers"]["hashkey"] == "HASH"


def test_place_order_limit_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """지정가 매도 — 매도 TR(VTTT1001U)·지정가 본문."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_post(path: str, json: Any = None, headers: Any = None) -> httpx.Response:
        cap["body"], cap["headers"] = json, headers
        return _resp({"rt_cd": "0", "output": {"ODNO": "2", "KRX_FWDG_ORD_ORGNO": "9"}})

    monkeypatch.setattr(c._client, "post", fake_post)
    monkeypatch.setattr(c, "_hashkey", lambda body: "H")

    r = c.place_order("MSFT", "sell", 2, price=Decimal("410.5"), market=True)
    assert r.side == "sell" and r.qty == 2
    assert cap["body"]["OVRS_ORD_UNPR"] == "410.5" and cap["body"]["ORD_DVSN"] == "00"
    assert cap["headers"]["tr_id"] == "VTTT1001U"


def test_place_order_requires_limit_price() -> None:
    """미장은 지정가 전용 — price 누락/0/음수, 수량≤0 은 호출 전 KisOrderError."""
    c = _client()
    with pytest.raises(KisOrderError):
        c.place_order("AAPL", "buy", 3)  # price 없음
    with pytest.raises(KisOrderError):
        c.place_order("AAPL", "buy", 3, price=Decimal("0"))
    with pytest.raises(KisOrderError):
        c.place_order("AAPL", "buy", 0, price=Decimal("100"))


def test_place_order_ignores_market_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """market=True 여도 시장가 없음 → 지정가(ORD_DVSN=00)로 전송."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_post(path: str, json: Any = None, headers: Any = None) -> httpx.Response:
        cap["body"] = json
        return _resp({"rt_cd": "0", "output": {"ODNO": "1", "KRX_FWDG_ORD_ORGNO": "1"}})

    monkeypatch.setattr(c._client, "post", fake_post)
    monkeypatch.setattr(c, "_hashkey", lambda body: "H")

    c.place_order("AAPL", "buy", 1, price=Decimal("100"), market=True)
    assert cap["body"]["ORD_DVSN"] == "00"


def test_get_balance_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """잔고 — output1 보유(수량>0)·output2 USD 현금. 총평가=현금+Σ(수량×현재가)."""
    c = _client()
    payload = {
        "rt_cd": "0",
        "output1": [
            {
                "ovrs_pdno": "AAPL",
                "ovrs_item_name": "APPLE INC",
                "ovrs_cblc_qty": "10",
                "pchs_avg_pric": "150.00",
                "now_pric2": "190.00",
                "ovrs_stck_evlu_amt": "1900.00",
                "frcr_evlu_pfls_amt": "400.00",
                "evlu_pfls_rt": "26.67",
            },
            {"ovrs_pdno": "TSLA", "ovrs_cblc_qty": "0", "pchs_avg_pric": "0"},
        ],
        "output2": [
            {"crcy_cd": "KRW", "frcr_dncl_amt_2": "0"},
            {"crcy_cd": "USD", "frcr_dncl_amt_2": "5000.00"},
        ],
    }
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: _resp(payload))
    bal = c.get_balance()
    assert bal.cash == Decimal("5000.00")
    # 총평가 = 5000 + 10*190 = 6900.
    assert bal.total_eval == Decimal("6900.00")
    assert len(bal.positions) == 1  # 수량 0 종목 제외
    pos = bal.positions[0]
    assert pos.ticker == "AAPL" and pos.qty == 10
    assert pos.avg_price == Decimal("150.00") and pos.cur_price == Decimal("190.00")


def test_get_balance_output2_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """output2 가 list 가 아니라 dict(단일 통화)인 경우도 USD 현금을 파싱."""
    c = _client()
    payload = {
        "rt_cd": "0",
        "output1": [],
        "output2": {"crcy_cd": "USD", "frcr_dncl_amt1": "1234.50"},
    }
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: _resp(payload))
    bal = c.get_balance()
    assert bal.cash == Decimal("1234.50")
    assert bal.total_eval == Decimal("1234.50")
    assert bal.positions == []


def test_inquire_orders_unfilled(monkeypatch: pytest.MonkeyPatch) -> None:
    """미체결 조회 — output 의 종목/잔여수량을 OrderStatus(order_qty>filled_qty)로."""
    c = _client()
    payload = {
        "rt_cd": "0",
        "output": [
            {
                "odno": "0001",
                "pdno": "AAPL",
                "sll_buy_dvsn_cd": "02",
                "nccs_qty": "5",
                "prcs_stat_name": "접수",
            }
        ],
    }
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: _resp(payload))
    orders = c.inquire_orders("20260622")
    assert len(orders) == 1
    o = orders[0]
    assert o.order_no == "0001" and o.ticker == "AAPL" and o.side == "buy"
    assert o.order_qty == 5 and o.filled_qty == 0  # 멱등 가드가 미체결로 인식


def test_rt_cd_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """rt_cd≠0 이면 KisOrderError(msg 포함)."""
    c = _client()
    monkeypatch.setattr(
        c._client,
        "post",
        lambda path, json=None, headers=None: _resp(
            {"rt_cd": "1", "msg_cd": "40310000", "msg1": "모의투자 장종료"}
        ),
    )
    monkeypatch.setattr(c, "_hashkey", lambda body: "H")
    with pytest.raises(KisOrderError):
        c.place_order("AAPL", "buy", 1, price=Decimal("100"))


def test_http_error_surfaces_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """비-2xx 응답은 KIS 에러 본문을 KisOrderError 메시지에 담는다(진단용)."""
    c = _client()
    body = '{"rt_cd":"1","msg_cd":"40310000","msg1":"모의투자 미지원 TR"}'
    resp = httpx.Response(500, text=body, request=httpx.Request("GET", "http://test"))
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: resp)
    with pytest.raises(KisOrderError) as ei:
        c.get_balance()
    msg = str(ei.value)
    assert "500" in msg and "미지원" in msg
