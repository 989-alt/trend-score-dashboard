"""KIS 국내 모의 주문 클라이언트 (trend-trader P1).

LiveProvider 의 KIS 토큰·디스크캐시 패턴을 **독립 재구현**(시세 키와 분리된 모의계좌 키/토큰).
주문은 시세 GET 과 달리 **POST + hashkey** 가 필요하다. 모든 금액·가격은 Decimal.

⚠ TR_ID(모의): 매수 ``VTTC0802U`` / 매도 ``VTTC0801U`` / 정정취소 ``VTTC0803U`` / 잔고
``VTTC8434R`` / 일별체결 ``VTTC8001R``. (실전은 ``TTTC…`` — **라이브 가동 전 KIS 문서로 TR_ID·
필드명 최종 확인**. 본 P1 은 mock 단위테스트로 클라이언트 로직만 검증.)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings
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
#: 모의 TR (실전=TTTC…). 가동 전 KIS 문서 확인.
_TR_BUY = "VTTC0802U"
_TR_SELL = "VTTC0801U"
_TR_CANCEL = "VTTC0803U"
_TR_BALANCE = "VTTC8434R"
_TR_CCLD = "VTTC8001R"

_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
_BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"

_RETRY = retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)


class KisOrderError(RuntimeError):
    """KIS 주문/조회 실패 (HTTP·rt_cd≠0·파싱)."""


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


class KisOrderClient:
    """KIS 국내 모의 주문/조회. 시세(LiveProvider)와 분리된 키·토큰을 쓴다(스레드세이프)."""

    def __init__(self, settings: Settings, *, mode: str = "mock") -> None:
        self._s = settings
        self._base = _DOMAINS[mode]
        self._client = httpx.Client(base_url=self._base, timeout=10.0)
        self._token: str | None = None
        self._token_exp: datetime | None = None
        self._lock = threading.Lock()
        acct = settings.kis_account.strip()
        if "-" in acct:
            cano, prod = acct.split("-", 1)
            self._cano, self._prod = cano.strip(), prod.strip()
        else:
            self._cano = acct
            self._prod = (settings.kis_account_prod or "01").strip()

    # ── 토큰 (LiveProvider 패턴 재구현, 별도 토큰 파일) ──────────────────
    def _ensure_token(self) -> str:
        now = datetime.now(tz=UTC)
        if self._token and self._token_exp and now < self._token_exp:
            return self._token
        if not (self._s.kis_app_key and self._s.kis_app_secret):
            raise KisOrderError("KIS 키 미설정 (KIS_APP_KEY/KIS_APP_SECRET)")
        with self._lock:
            now = datetime.now(tz=UTC)
            if self._token and self._token_exp and now < self._token_exp:
                return self._token
            disk = self._load_token(now)
            if disk is not None:
                self._token, self._token_exp = disk
                return self._token
            try:
                payload = self._request_token()
            except httpx.HTTPError as exc:
                raise KisOrderError("KIS 토큰 발급 실패") from exc
            token = payload.get("access_token")
            if not token:
                raise KisOrderError("KIS 토큰 응답에 access_token 없음")
            ttl = int(payload.get("expires_in", 86400))
            self._token = str(token)
            self._token_exp = now + timedelta(seconds=max(ttl - 60, 60))
            self._save_token(self._token, self._token_exp)
            return self._token

    @_RETRY
    def _request_token(self) -> dict[str, Any]:
        resp = self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._s.kis_app_key,
                "appsecret": self._s.kis_app_secret,
            },
        )
        resp.raise_for_status()
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise KisOrderError("KIS 토큰 응답 JSON 파싱 실패") from exc
        return payload

    def _load_token(self, now: datetime) -> tuple[str, datetime] | None:
        try:
            data = json.loads(self._s.trader_token_path.read_text(encoding="utf-8"))
            token = data["access_token"]
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not token or expires_at.tzinfo is None or now >= expires_at - timedelta(seconds=60):
            return None
        return str(token), expires_at

    def _save_token(self, token: str, expires_at: datetime) -> None:
        path = self._s.trader_token_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"access_token": token, "expires_at": expires_at.isoformat()}),
                encoding="utf-8",
            )
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except OSError:
            logger.warning("매매봇 토큰 디스크 저장 실패 — 메모리 캐시로 계속", exc_info=True)

    # ── 공통 요청 ──────────────────────────────────────────────────────
    def _headers(self, tr_id: str, *, hashkey: str | None = None) -> dict[str, str]:
        h = {
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self._s.kis_app_key,
            "appsecret": self._s.kis_app_secret,
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
                "appkey": self._s.kis_app_key,
                "appsecret": self._s.kis_app_secret,
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
        resp.raise_for_status()
        return self._check(resp, path)

    @_RETRY
    def _get(self, path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        resp = self._client.get(path, headers=self._headers(tr_id), params=params)
        resp.raise_for_status()
        return self._check(resp, path)

    @staticmethod
    def _check(resp: httpx.Response, path: str) -> dict[str, Any]:
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise KisOrderError(f"KIS 응답 JSON 파싱 실패: {path}") from exc
        if str(data.get("rt_cd", "1")) != "0":
            raise KisOrderError(f"KIS 오류({data.get('msg_cd')}): {data.get('msg1')}")
        return data

    # ── 주문 ───────────────────────────────────────────────────────────
    def place_order(
        self,
        ticker: str,
        side: OrderSide,
        qty: int,
        *,
        price: Decimal | None = None,
        market: bool = True,
    ) -> OrderResult:
        """현금 매수/매도 주문 접수. ``market=True`` 면 시장가(ORD_DVSN=01), 아니면 지정가(00).

        시장가는 ``ORD_UNPR=0``. 지정가는 ``price``(원, 정수 호가) 필수.
        """
        if qty <= 0:
            raise KisOrderError("주문 수량은 양수여야 함")
        if not market and (price is None or price <= 0):
            raise KisOrderError("지정가 주문은 양수 price 필요")
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "PDNO": ticker,
            "ORD_DVSN": "01" if market else "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0" if market else str(int(price)),  # type: ignore[arg-type]
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
        """미체결 주문 전량 취소(RVSE_CNCL_DVSN_CD=02)."""
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "KRX_FWDG_ORD_ORGNO": org_no,
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",  # 잔량 전부
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
        """계좌 잔고 — 주문가능현금 + 총평가 + 보유 종목(수량>0)."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._get(_BALANCE_PATH, tr_id=_TR_BALANCE, params=params)
        positions: list[HoldingPosition] = []
        for r in data.get("output1") or []:
            if _i(r.get("hldg_qty")) <= 0:
                continue
            positions.append(
                HoldingPosition(
                    ticker=str(r.get("pdno", "")),
                    name=str(r.get("prdt_name", "")),
                    qty=_i(r.get("hldg_qty")),
                    avg_price=_dec(r.get("pchs_avg_pric")),
                    cur_price=_dec(r.get("prpr")),
                    eval_amount=_dec(r.get("evlu_amt")),
                    pnl_amount=_dec(r.get("evlu_pfls_amt")),
                    pnl_pct=_dec(r.get("evlu_pfls_rt")),
                )
            )
        summ = (data.get("output2") or [{}])[0]
        cash = _dec(summ.get("prvs_rcdl_excc_amt")) or _dec(summ.get("dnca_tot_amt"))
        return Balance(cash=cash, total_eval=_dec(summ.get("tot_evlu_amt")), positions=positions)

    # ── 체결 조회 ──────────────────────────────────────────────────────
    def inquire_orders(self, query_date: str) -> list[OrderStatus]:
        """일별 주문체결 조회. ``query_date`` = YYYYMMDD."""
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._prod,
            "INQR_STRT_DT": query_date,
            "INQR_END_DT": query_date,
            "SLL_BUY_DVSN_CD": "00",  # 00=전체
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = self._get(_CCLD_PATH, tr_id=_TR_CCLD, params=params)
        out: list[OrderStatus] = []
        for r in data.get("output1") or []:
            out.append(
                OrderStatus(
                    order_no=str(r.get("odno", "")),
                    ticker=str(r.get("pdno", "")),
                    side="buy" if str(r.get("sll_buy_dvsn_cd", "")) == "02" else "sell",
                    order_qty=_i(r.get("ord_qty")),
                    filled_qty=_i(r.get("tot_ccld_qty")),
                    filled_price=_dec(r.get("avg_prvs")) or None,
                    status=str(r.get("ccld_dvsn_name", "") or r.get("ord_dvsn_name", "")),
                )
            )
        return out


__all__ = ["KisOrderClient", "KisOrderError"]
