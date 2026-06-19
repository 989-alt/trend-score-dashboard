"""KisOrderClient 단위테스트 — 네트워크 0 (httpx post/get 를 monkeypatch).

토큰 캐시·주문 본문/헤더 구성·잔고 파싱·rt_cd 오류 처리만 검증(실 KIS 호출 없음).
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
from backend.trader.kis_order import KisOrderClient, KisOrderError


def _client(*, with_token: bool = True) -> KisOrderClient:
    tmp = Path(tempfile.mkdtemp(prefix="tsd-trader-"))
    settings = Settings(
        kis_app_key="k",
        kis_app_secret="s",
        kis_account="50190719",
        kis_account_prod="01",
        trader_token_path=tmp / ".tok.json",
    )
    c = KisOrderClient(settings)
    if with_token:
        c._token = "tok"
        c._token_exp = datetime.now(tz=UTC) + timedelta(hours=1)
    return c


def _resp(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("POST", "http://test"))


def test_token_issue_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """토큰 미보유 → 발급 1회 후 메모리 캐시(2번째는 재발급 안 함)."""
    c = _client(with_token=False)
    calls = {"n": 0}

    def fake_req() -> dict[str, Any]:
        calls["n"] += 1
        return {"access_token": "abc", "expires_in": 3600}

    monkeypatch.setattr(c, "_request_token", fake_req)
    assert c._ensure_token() == "abc"
    assert c._ensure_token() == "abc"
    assert calls["n"] == 1


def test_token_missing_keys_raises() -> None:
    """키 미설정이면 KisOrderError."""
    c = KisOrderClient(Settings(kis_app_key="", kis_app_secret=""))
    with pytest.raises(KisOrderError):
        c._ensure_token()


def test_place_order_market_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    """시장가 매수 — ORD_DVSN=01·ORD_UNPR=0·매수 TR·hashkey 헤더, 응답 파싱."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_post(path: str, json: Any = None, headers: Any = None) -> httpx.Response:
        cap["path"], cap["body"], cap["headers"] = path, json, headers
        return _resp(
            {
                "rt_cd": "0",
                "msg1": "주문 전송 완료",
                "output": {"ODNO": "0001", "KRX_FWDG_ORD_ORGNO": "06010"},
            }
        )

    monkeypatch.setattr(c._client, "post", fake_post)
    monkeypatch.setattr(c, "_hashkey", lambda body: "HASH")

    r = c.place_order("005930", "buy", 10)
    assert (r.order_no, r.org_no, r.side, r.qty) == ("0001", "06010", "buy", 10)
    assert cap["body"]["PDNO"] == "005930" and cap["body"]["ORD_QTY"] == "10"
    assert cap["body"]["ORD_DVSN"] == "01" and cap["body"]["ORD_UNPR"] == "0"
    assert cap["headers"]["tr_id"] == "VTTC0802U" and cap["headers"]["hashkey"] == "HASH"


def test_place_order_limit_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    """지정가 매도 — ORD_DVSN=00·ORD_UNPR=가격·매도 TR."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_post(path: str, json: Any = None, headers: Any = None) -> httpx.Response:
        cap["body"], cap["headers"] = json, headers
        return _resp({"rt_cd": "0", "output": {"ODNO": "2", "KRX_FWDG_ORD_ORGNO": "6"}})

    monkeypatch.setattr(c._client, "post", fake_post)
    monkeypatch.setattr(c, "_hashkey", lambda body: "H")

    r = c.place_order("000660", "sell", 5, price=Decimal("180000"), market=False)
    assert r.side == "sell" and r.qty == 5
    assert cap["body"]["ORD_DVSN"] == "00" and cap["body"]["ORD_UNPR"] == "180000"
    assert cap["headers"]["tr_id"] == "VTTC0801U"


def test_place_order_validation() -> None:
    """수량≤0, 지정가 price 누락은 호출 전 KisOrderError."""
    c = _client()
    with pytest.raises(KisOrderError):
        c.place_order("005930", "buy", 0)
    with pytest.raises(KisOrderError):
        c.place_order("005930", "buy", 10, market=False)


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
        c.place_order("005930", "buy", 10)


def test_get_balance_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """잔고 — 수량>0 보유만, 현금/총평가 파싱."""
    c = _client()
    payload = {
        "rt_cd": "0",
        "output1": [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "10",
                "pchs_avg_pric": "60000",
                "prpr": "70000",
                "evlu_amt": "700000",
                "evlu_pfls_amt": "100000",
                "evlu_pfls_rt": "16.67",
            },
            {"pdno": "000660", "prdt_name": "SK하이닉스", "hldg_qty": "0", "pchs_avg_pric": "0"},
        ],
        "output2": [{"prvs_rcdl_excc_amt": "500000000", "tot_evlu_amt": "500700000"}],
    }
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: _resp(payload))
    bal = c.get_balance()
    assert bal.cash == Decimal("500000000")
    assert bal.total_eval == Decimal("500700000")
    assert len(bal.positions) == 1  # 수량 0 종목 제외
    pos = bal.positions[0]
    assert pos.ticker == "005930" and pos.qty == 10 and pos.avg_price == Decimal("60000")


def test_inquire_orders_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """일별 체결 조회 — 방향/체결수량 파싱."""
    c = _client()
    payload = {
        "rt_cd": "0",
        "output1": [
            {
                "odno": "0001",
                "pdno": "005930",
                "sll_buy_dvsn_cd": "02",
                "ord_qty": "10",
                "tot_ccld_qty": "10",
                "avg_prvs": "70000",
                "ccld_dvsn_name": "체결",
            }
        ],
    }
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: _resp(payload))
    orders = c.inquire_orders("20260622")
    assert len(orders) == 1
    o = orders[0]
    assert o.order_no == "0001" and o.side == "buy" and o.filled_qty == 10
    assert o.filled_price == Decimal("70000")
