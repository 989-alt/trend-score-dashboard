"""OpenDART 접수일(rcept_dt) 기준 as-of 재무.

corpCode(zip) → corp_code 매핑, list.json → 접수일 목록, fnlttSinglAcntAll.json
→ 전체재무제표 계정. ROE/영업이익률/성장을 결정론 산출. 키는 .env(DART_API_KEY).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

_BASE = "https://opendart.fss.or.kr/api"


def _amt(value: str | None) -> Decimal | None:
    if value is None or value in ("", "-"):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _account(accounts: list[dict[str, Any]], name: str, field: str) -> Decimal | None:
    for a in accounts:
        if a.get("account_nm") == name:
            return _amt(a.get(field))
    return None


def _ratios_from_accounts(accounts: list[dict[str, Any]]) -> dict[str, Decimal]:
    """계정 리스트 → roe·op_margin·rev_growth(가능한 것만)."""
    out: dict[str, Decimal] = {}
    ni = _account(accounts, "당기순이익", "thstrm_amount")
    eq = _account(accounts, "자본총계", "thstrm_amount")
    op = _account(accounts, "영업이익", "thstrm_amount")
    rev = _account(accounts, "매출액", "thstrm_amount")
    rev_prev = _account(accounts, "매출액", "frmtrm_amount")
    if ni is not None and eq and eq != 0:
        out["roe"] = ni / eq
    if op is not None and rev and rev != 0:
        out["op_margin"] = op / rev
    if rev is not None and rev_prev and rev_prev != 0:
        out["rev_growth"] = rev / rev_prev - Decimal("1")
    return out


class DartClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._http = client or httpx.Client(timeout=20.0)
        self._corp_map: dict[str, str] | None = None

    def corp_code(self, ticker: str) -> str | None:
        if self._corp_map is None:
            self._corp_map = self._load_corp_map()
        return self._corp_map.get(ticker)

    def _load_corp_map(self) -> dict[str, str]:
        r = self._http.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": self._key})
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8")
        import xml.etree.ElementTree as ET

        mapping: dict[str, str] = {}
        for el in ET.fromstring(xml).iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            corp = (el.findtext("corp_code") or "").strip()
            if len(stock) == 6 and corp:
                mapping[stock] = corp
        return mapping

    def _list_filings(self, corp_code: str, bgn: str, end: str) -> list[dict[str, Any]]:
        """정기보고서(pblntf_ty=A) 목록 — 접수일 포함."""
        r = self._http.get(
            f"{_BASE}/list.json",
            params={
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bgn_de": bgn,
                "end_de": end,
                "pblntf_ty": "A",
                "page_count": "100",
            },
        )
        r.raise_for_status()
        data = r.json()
        return list(data.get("list", [])) if data.get("status") == "000" else []

    def latest_filing_on_or_before(self, corp_code: str, t: date) -> dict[str, Any] | None:
        end = t.strftime("%Y%m%d")
        bgn = f"{t.year - 2}0101"
        filings = [
            f for f in self._list_filings(corp_code, bgn, end) if f.get("rcept_dt", "") <= end
        ]
        if not filings:
            return None
        return max(filings, key=lambda f: f["rcept_dt"])

    def financial_ratios(
        self, corp_code: str, bsns_year: str, reprt_code: str
    ) -> dict[str, Decimal]:
        r = self._http.get(
            f"{_BASE}/fnlttSinglAcntAll.json",
            params={
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": "CFS",
            },
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "000":
            return {}
        return _ratios_from_accounts(list(data.get("list", [])))


__all__ = ["DartClient", "_ratios_from_accounts"]
