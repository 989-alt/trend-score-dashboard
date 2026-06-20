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
from backend.trader.errors import KisOrderError, KisTokenExpiredError
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


def _route_get(*, balance: dict[str, Any], psamount: dict[str, Any]) -> Any:
    """경로별 GET 라우터 — 잔고 GET 과 매수가능금액(psamount) GET 을 분기해 응답한다.

    get_balance() 는 이제 ①잔고 ②매수가능금액(통합증거금 buying power) 두 GET 을 호출한다.
    """

    def _get(path: str, headers: Any = None, params: Any = None) -> httpx.Response:
        if "inquire-psamount" in path:
            return _resp(psamount)
        return _resp(balance)

    return _get


def test_get_balance_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """잔고 — output1 보유(수량>0)·cash=통합증거금 매수가능금액. 총평가=현금+Σ(수량×현재가)."""
    c = _client()
    balance = {
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
        # 외화예수금 USD 는 $0(원화만 보유) — cash 는 이 값이 아니라 매수가능금액을 써야 한다.
        "output2": [
            {"crcy_cd": "KRW", "frcr_dncl_amt_2": "0"},
            {"crcy_cd": "USD", "frcr_dncl_amt_2": "0"},
        ],
    }
    psamount = {"rt_cd": "0", "output": {"ord_psbl_frcr_amt": "100000.00"}}
    monkeypatch.setattr(c._client, "get", _route_get(balance=balance, psamount=psamount))
    bal = c.get_balance()
    # cash = 통합증거금 매수가능금액(100000), 외화예수금 $0 이 아님.
    assert bal.cash == Decimal("100000.00")
    # 총평가 = 100000 + 10*190 = 101900.
    assert bal.total_eval == Decimal("101900.00")
    assert len(bal.positions) == 1  # 수량 0 종목 제외
    pos = bal.positions[0]
    assert pos.ticker == "AAPL" and pos.qty == 10
    assert pos.avg_price == Decimal("150.00") and pos.cur_price == Decimal("190.00")


def test_buying_power_parses_ord_psbl_frcr_amt(monkeypatch: pytest.MonkeyPatch) -> None:
    """매수가능금액 — output.ord_psbl_frcr_amt → Decimal(통합증거금 buying power)."""
    c = _client()
    cap: dict[str, Any] = {}

    def fake_get(path: str, headers: Any = None, params: Any = None) -> httpx.Response:
        cap["path"], cap["params"], cap["headers"] = path, params, headers
        return _resp(
            {
                "rt_cd": "0",
                "output": {
                    "ord_psbl_frcr_amt": "100000.00",
                    "ovrs_ord_psbl_amt": "100000.00",
                    "max_ord_psbl_qty": "495",
                },
            }
        )

    monkeypatch.setattr(c._client, "get", fake_get)
    assert c.buying_power() == Decimal("100000.00")
    assert "inquire-psamount" in cap["path"]
    # 계좌단위 금액이라 기준종목·단가로 호출.
    assert cap["params"]["ITEM_CD"] == "AAPL" and cap["params"]["OVRS_ORD_UNPR"] == "100"
    assert cap["params"]["OVRS_EXCG_CD"] == "NASD"
    assert cap["headers"]["tr_id"] == "VTTS3007R"


def test_buying_power_falls_back_to_ovrs_ord_psbl_amt(monkeypatch: pytest.MonkeyPatch) -> None:
    """ord_psbl_frcr_amt 누락/0 이면 ovrs_ord_psbl_amt 로 폴백."""
    c = _client()
    monkeypatch.setattr(
        c._client,
        "get",
        lambda path, headers=None, params=None: _resp(
            {"rt_cd": "0", "output": {"ovrs_ord_psbl_amt": "777.00"}}
        ),
    )
    assert c.buying_power() == Decimal("777.00")


def test_buying_power_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """초당 거래건수 초과(HTTP 500) → time.sleep 후 재시도해 금액 반환(raise 안 함)."""
    c = _client()
    monkeypatch.setattr("backend.trader.kis_overseas.time.sleep", lambda _s: None)
    rate_limited = httpx.Response(
        500,
        text='{"rt_cd":"1","msg_cd":"EGW00201","msg1":"초당 거래건수를 초과하였습니다."}',
        request=httpx.Request("GET", "http://test"),
    )
    ok = _resp({"rt_cd": "0", "output": {"ord_psbl_frcr_amt": "100000.00"}})
    calls = {"n": 0}

    def fake_get(path: str, headers: Any = None, params: Any = None) -> httpx.Response:
        calls["n"] += 1
        return rate_limited if calls["n"] == 1 else ok

    monkeypatch.setattr(c._client, "get", fake_get)
    assert c.buying_power() == Decimal("100000.00")  # raise 하지 않고 재시도 성공
    assert calls["n"] == 2


def test_buying_power_persistent_failure_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """매수가능금액 조회가 계속 실패해도 raise 없이 Decimal('0') (페일세이프)."""
    c = _client()
    monkeypatch.setattr("backend.trader.kis_overseas.time.sleep", lambda _s: None)
    rate_limited = httpx.Response(
        500,
        text='{"rt_cd":"1","msg_cd":"EGW00201","msg1":"초당 거래건수를 초과하였습니다."}',
        request=httpx.Request("GET", "http://test"),
    )
    monkeypatch.setattr(c._client, "get", lambda path, headers=None, params=None: rate_limited)
    assert c.buying_power() == Decimal("0")


def test_get_balance_psamount_failure_cash_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """매수가능금액 실패 시 cash=0(US skip)·총평가=보유평가만 — 크래시 없음(페일세이프)."""
    c = _client()
    balance = {
        "rt_cd": "0",
        "output1": [
            {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "10", "now_pric2": "190.00"},
        ],
        "output2": [{"crcy_cd": "USD", "frcr_dncl_amt_2": "0"}],
    }
    # psamount 는 비-2xx(서버 오류) — buying_power 가 0 으로 페일세이프.
    psamount_err = httpx.Response(
        500,
        text='{"rt_cd":"1","msg_cd":"40310000","msg1":"일시 오류"}',
        request=httpx.Request("GET", "http://test"),
    )

    def fake_get(path: str, headers: Any = None, params: Any = None) -> httpx.Response:
        return psamount_err if "inquire-psamount" in path else _resp(balance)

    monkeypatch.setattr(c._client, "get", fake_get)
    bal = c.get_balance()
    assert bal.cash == Decimal("0")  # US 이번 사이클 skip
    assert bal.total_eval == Decimal("1900.00")  # 보유평가 10*190 만
    assert len(bal.positions) == 1


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


def test_check_token_expired_on_egw00123() -> None:
    """해외도 EGW00123(HTTP 500 본문)은 KisTokenExpired 로 구분."""
    body = '{"rt_cd":"1","msg_cd":"EGW00123","msg1":"기간이 만료된 token 입니다."}'
    resp = httpx.Response(500, text=body, request=httpx.Request("GET", "http://test"))
    with pytest.raises(KisTokenExpiredError):
        KisOverseasOrderClient._check(resp, "/x")


def test_get_self_heals_on_token_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """해외 조회 중 EGW00123 → 토큰 재발급 후 1회 재시도해 성공."""
    c = _client()
    refreshed = {"n": 0}

    def fake_refresh() -> str:
        refreshed["n"] += 1
        return "newtok"

    monkeypatch.setattr(c._token, "refresh", fake_refresh)
    expired = httpx.Response(
        500, text='{"msg_cd":"EGW00123"}', request=httpx.Request("GET", "http://test")
    )
    balance_ok = _resp({"rt_cd": "0", "output1": [], "output2": []})
    psamount_ok = _resp({"rt_cd": "0", "output": {"ord_psbl_frcr_amt": "100"}})
    # 잔고 GET 1회차는 토큰만료 → 재발급 후 2회차 성공. 이후 매수가능금액 GET 은 정상.
    balance_calls = {"n": 0}

    def fake_get(path: str, headers: Any = None, params: Any = None) -> httpx.Response:
        if "inquire-psamount" in path:
            return psamount_ok
        balance_calls["n"] += 1
        return expired if balance_calls["n"] == 1 else balance_ok

    monkeypatch.setattr(c._client, "get", fake_get)
    bal = c.get_balance()
    assert balance_calls["n"] == 2 and refreshed["n"] == 1
    assert bal.cash == Decimal("100")
