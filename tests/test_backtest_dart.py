from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.dart_client import DartClient, _ratios_from_accounts


def test_ratios_from_accounts() -> None:
    accounts = [
        {"account_nm": "당기순이익", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"},
        {"account_nm": "자본총계", "thstrm_amount": "10,000", "frmtrm_amount": "9,000"},
        {"account_nm": "영업이익", "thstrm_amount": "1,500", "frmtrm_amount": "1,300"},
        {"account_nm": "매출액", "thstrm_amount": "20,000", "frmtrm_amount": "18,000"},
    ]
    r = _ratios_from_accounts(accounts)
    assert r["roe"] == (Decimal("1200") / Decimal("10000"))
    assert r["op_margin"] == (Decimal("1500") / Decimal("20000"))
    assert r["rev_growth"].quantize(Decimal("0.0001")) == Decimal("0.1111")


def test_pick_latest_filing_on_or_before(monkeypatch) -> None:
    client = DartClient(api_key="TEST")
    filings = [
        {"rcept_dt": "20230331", "reprt_code": "11011", "bsns_year": "2022"},
        {"rcept_dt": "20240331", "reprt_code": "11011", "bsns_year": "2023"},
    ]
    monkeypatch.setattr(client, "_list_filings", lambda corp, bgn, end: filings)
    picked = client.latest_filing_on_or_before("00126380", date(2023, 6, 1))
    assert picked is not None and picked["rcept_dt"] == "20230331"
    picked2 = client.latest_filing_on_or_before("00126380", date(2024, 6, 1))
    assert picked2 is not None and picked2["rcept_dt"] == "20240331"
