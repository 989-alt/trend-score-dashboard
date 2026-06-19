"""KIS 해외주식(미장) 모의 주문 클라이언트 (trend-trader P8).

국내(``kis_order.KisOrderClient``)와 동일 모의 도메인·동일 ``KisToken``(같은 앱키 → 토큰 공유)을
쓰되, 해외주식 전용 TR_ID·경로·파라미터를 사용한다. 반환 모델은 국내와 **동일**(``Balance``/
``OrderResult``/``OrderStatus``)이라 ``TraderLoop`` 가 시장 구분 없이 동작한다.

**미장은 지정가(LIMIT) 전용** — 시장가 주문이 없다(``place_order`` 는 ``price`` 필수). 거래소는
나스닥(``NASD``) 고정(유니버스가 나스닥 화이트리스트). 모든 금액·가격은 Decimal.

⚠ TR_ID/경로/파라미터(모의)는 swing-bot 검증값을 사용하되 일부는 **라이브 가동 전 KIS 문서 확인**
필요(아래 주석 표기). 첫 미장 라이브 사이클 + 본문 로깅으로 최종 검증한다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings
from backend.trader.errors import KisOrderError
from backend.trader.kis_auth import KisToken
from backend.trader.models import (
    Balance,
    HoldingPosition,
    OrderResult,
    OrderSide,
    OrderStatus,
)

logger = logging.getLogger(__name__)

_DOMAINS = {
    "mock": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}

#: 거래소 코드 — 나스닥 고정(US 유니버스=나스닥 화이트리스트).
_EXCG = "NASD"

#: 모의 TR (실전=TTT…). **라이브 가동 전 KIS 문서로 TR_ID·필드명 최종 확인.**
_TR_BUY = "VTTT1002U"  # 해외 매수(모의). 실전 TTTT1002U.
_TR_SELL = "VTTT1001U"  # 해외 매도(모의). 실전 TTTT1006U.
_TR_BALANCE = "VTRP6504R"  # 해외 잔고(모의). 실전 TTTS3012R.
_TR_NCCS = "VTTS3018R"  # 해외 미체결(모의). 라이브 가동 전 확인.
_TR_CANCEL = "VTTT1004U"  # 해외 정정취소(모의). 라이브 가동 전 확인.

_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"
_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/overseas-stock/v1/trading/inquire-balance"
_NCCS_PATH = "/uapi/overseas-stock/v1/trading/inquire-nccs"

_RETRY = retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)


def _i(value: Any) -> int:
    """KIS 수치 문자열 → int(빈값/None=0). 소수 표기도 흡수."""
    if value in (None, ""):
        return 0
    return int(float(str(value).strip()))


def _dec(value: Any) -> Decimal:
    """KIS 수치 문자열 → Decimal(빈값/None=0)."""
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).strip())


class KisOverseasOrderClient:
    """KIS 해외주식 모의 주문/조회. 국내 클라이언트와 동일 ``KisToken`` 공유(같은 앱키)."""

    def __init__(
        self, settings: Settings, *, mode: str = "mock", token: KisToken | None = None
    ) -> None:
        self._s = settings
        self._base = _DOMAINS[mode]
        self._client = httpx.Client(base_url=self._base, timeout=10.0)
        # 토큰은 국내와 공유(같은 모의 앱키 → KIS 는 앱키당 1토큰). 미주입 시 자체 생성.
        self._token = token or KisToken(
            settings.kis_appkey, settings.kis_appsecret, self._base, settings.trader_token_path
        )
        acct = settings.kis_account.strip()
        if "-" in acct:
            cano, prod = acct.split("-", 1)
            self._cano, self._prod = cano.strip(), prod.strip()
        else:
            self._cano = acct
            self._prod = (settings.kis_account_prod or "01").strip()

    # ── 공통 요청 (kis_order 와 동일 패턴) ──────────────────────────────
    def _headers(self, tr_id: str, *, hashkey: str | None = None) -> dict[str, str]:
        h = {
            "authorization": f"Bearer {self._token.get()}",
            "appkey": self._s.kis_appkey,
            "appsecret": self._s.kis_appsecret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if hashkey is not None:
            h["hashkey"] = hashkey
        return h

    @_RETRY
    def _hashkey(self, body: dict[str, str]) -> str:
        """주문 POST 본문 해시(KIS 위변조 방지). 주문 전 필수."""
        resp = self._client.post(
            "/uapi/hashkey",
            json=body,
            headers={
                "appkey": self._s.kis_appkey,
                "appsecret": self._s.kis_appsecret,
                "content-type": "application/json; charset=utf-8",
            },
        )
        resp.raise_for_status()
        try:
            return str(resp.json()["HASH"])
        except (ValueError, KeyError) as exc:
            raise KisOrderError("hashkey 응답 파싱 실패") from exc

    @_RETRY
    def _post(self, path: str, *, tr_id: str, body: dict[str, str]) -> dict[str, Any]:
        resp = self._client.post(
            path, json=body, headers=self._headers(tr_id, hashkey=self._hashkey(body))
        )
        return self._check(resp, path)

    @_RETRY
    def _get(self, path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        resp = self._client.get(path, headers=self._headers(tr_id), params=params)
        return self._check(resp, path)

    @staticmethod
    def _check(resp: httpx.Response, path: str) -> dict[str, Any]:
        # 비-2xx 는 KIS 에러 본문(msg_cd/msg1)을 메시지에 실어 던진다 — 진단에 필수.
        if resp.status_code >= 400:
            raise KisOrderError(f"KIS HTTP {resp.status_code} ({path}): {resp.text[:300]}")
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise KisOrderError(f"KIS 응답 JSON 파싱 실패: {path}") from exc
        if str(data.get("rt_cd", "1")) != "0":
            raise KisOrderError(f"KIS 오류({data.get('msg_cd')}): {data.get('msg1')}")
        return data

    # ── 주문 (LIMIT 전용) ──────────────────────────────────────────────
    def place_order(
        self,
        ticker: str,
        side: OrderSide,
        qty: int,
        *,
        price: Decimal | None = None,
        market: bool = True,
    ) -> OrderResult:
        """해외 지정가 매수/매도 접수. **미장은 시장가가 없어 ``market`` 인자를 무시**(항상 지정가).

        ``price``(USD, 지정가 단가)는 필수 — 없거나 ≤0 이면 ``KisOrderError``.
        """
        if qty <= 0:
            raise KisOrderError("주문 수량은 양수여야 함")
        if price is None or price <= 0:
            raise KisOrderError("해외주식은 지정가 전용 — 양수 price 필요")
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "OVRS_EXCG_CD": _EXCG,
            "PDNO": ticker,
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",  # 00=지정가(해외는 지정가만).
        }
        data = self._post(_ORDER_PATH, tr_id=_TR_BUY if side == "buy" else _TR_SELL, body=body)
        out = data.get("output") or {}
        return OrderResult(
            order_no=str(out.get("ODNO", "")),
            org_no=str(out.get("KRX_FWDG_ORD_ORGNO", "")),
            ticker=ticker,
            side=side,
            qty=qty,
            submitted_at=datetime.now(tz=UTC),
            message=str(data.get("msg1", "")),
        )

    def cancel_order(self, order_no: str, org_no: str, qty: int) -> OrderResult:
        """해외 미체결 취소(RVSE_CNCL_DVSN_CD=02). **라이브 전 KIS 문서 확인**(본문 미검증)."""
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "OVRS_EXCG_CD": _EXCG,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(int(qty)),
            "OVRS_ORD_UNPR": "0",
            "ORD_SVR_DVSN_CD": "0",
        }
        data = self._post(_CANCEL_PATH, tr_id=_TR_CANCEL, body=body)
        out = data.get("output") or {}
        return OrderResult(
            order_no=str(out.get("ODNO", order_no)),
            org_no=str(out.get("KRX_FWDG_ORD_ORGNO", org_no)),
            ticker="",
            side="sell",
            qty=qty,
            submitted_at=datetime.now(tz=UTC),
            message=str(data.get("msg1", "")),
        )

    # ── 잔고 ───────────────────────────────────────────────────────────
    def get_balance(self) -> Balance:
        """해외 잔고 — USD 주문가능현금 + 총평가(현금+평가) + 보유 종목(수량>0)."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "OVRS_EXCG_CD": _EXCG,
            "TR_CRCY_CD": "USD",
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "01",
            "INQR_DVSN_CD": "00",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        data = self._get(_BALANCE_PATH, tr_id=_TR_BALANCE, params=params)
        positions: list[HoldingPosition] = []
        holdings_eval = Decimal("0")
        for r in data.get("output1") or []:
            qty = _i(r.get("ovrs_cblc_qty"))
            if qty <= 0:
                continue
            cur = _dec(r.get("now_pric2")) or _dec(r.get("ovrs_now_pric1"))
            positions.append(
                HoldingPosition(
                    ticker=str(r.get("ovrs_pdno", "")),
                    name=str(r.get("ovrs_item_name", "")),
                    qty=qty,
                    avg_price=_dec(r.get("pchs_avg_pric")),
                    cur_price=cur or None,
                    eval_amount=_dec(r.get("ovrs_stck_evlu_amt")) or None,
                    pnl_amount=_dec(r.get("frcr_evlu_pfls_amt")) or None,
                    pnl_pct=_dec(r.get("evlu_pfls_rt")) or None,
                )
            )
            holdings_eval += qty * cur
        cash = self._parse_cash(data.get("output2"))
        return Balance(cash=cash, total_eval=cash + holdings_eval, positions=positions)

    @staticmethod
    def _parse_cash(output2: Any) -> Decimal:
        """output2(통화별 예수금) → USD 현금. dict(단일) 또는 list 둘 다 처리."""
        rows: list[dict[str, Any]]
        if isinstance(output2, dict):
            rows = [output2]
        elif isinstance(output2, list):
            rows = [r for r in output2 if isinstance(r, dict)]
        else:
            return Decimal("0")
        for r in rows:
            if str(r.get("crcy_cd", "")).upper() == "USD":
                return _dec(r.get("frcr_dncl_amt_2")) or _dec(r.get("frcr_dncl_amt1"))
        return Decimal("0")

    # ── 미체결 조회 (멱등 가드용) ──────────────────────────────────────
    def inquire_orders(self, query_date: str) -> list[OrderStatus]:
        """해외 미체결 조회 → ``OrderStatus`` 목록. ``query_date`` 는 시그니처 호환용(미사용).

        멱등 가드는 '잔여수량 있는 종목'만 필요하므로 미체결(nccs)을 그대로 반환한다.
        잔여수량은 ``order_qty`` 에, ``filled_qty=0`` 으로 둬 루프의 ``order_qty>filled_qty``
        판정을 통과시킨다. **일부 파라미터/필드는 라이브 가동 전 KIS 문서 확인.**
        """
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "OVRS_EXCG_CD": _EXCG,
            "SORT_SQN_DVSN": "01",  # 라이브 가동 전 확인.
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        data = self._get(_NCCS_PATH, tr_id=_TR_NCCS, params=params)
        out: list[OrderStatus] = []
        for r in data.get("output") or []:
            ticker = str(r.get("pdno", "") or r.get("ovrs_pdno", ""))
            # 미체결 잔여수량 — 필드명이 응답마다 다를 수 있어 후보를 순차 시도.
            remain = (
                _i(r.get("nccs_qty"))
                or _i(r.get("ord_psbl_qty"))
                or _i(r.get("ft_ord_qty"))
                or _i(r.get("ord_qty"))
            )
            out.append(
                OrderStatus(
                    order_no=str(r.get("odno", "")),
                    ticker=ticker,
                    side="buy" if str(r.get("sll_buy_dvsn_cd", "")) == "02" else "sell",
                    order_qty=remain,
                    filled_qty=0,
                    status=str(r.get("prcs_stat_name", "") or "미체결"),
                )
            )
        return out


__all__ = ["KisOverseasOrderClient"]
