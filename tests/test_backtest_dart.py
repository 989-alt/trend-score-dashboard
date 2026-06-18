from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.dart_client import DartClient, _period_from_report, _ratios_from_accounts


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
        {"rcept_dt": "20230331", "report_nm": "사업보고서 (2022.12)"},
        {"rcept_dt": "20240331", "report_nm": "사업보고서 (2023.12)"},
    ]
    monkeypatch.setattr(client, "_list_filings", lambda corp, bgn, end: filings)
    picked = client.latest_filing_on_or_before("00126380", date(2023, 6, 1))
    assert picked is not None and picked["rcept_dt"] == "20230331"
    picked2 = client.latest_filing_on_or_before("00126380", date(2024, 6, 1))
    assert picked2 is not None and picked2["rcept_dt"] == "20240331"


def test_period_from_report() -> None:
    assert _period_from_report("사업보고서 (2023.12)") == ("2023", "11011")
    assert _period_from_report("반기보고서 (2023.06)") == ("2023", "11012")
    assert _period_from_report("분기보고서 (2023.03)") == ("2023", "11013")
    assert _period_from_report("[기재정정]분기보고서 (2023.09)") == ("2023", "11014")
    assert _period_from_report("주요사항보고서") is None


def test_ratios_for_filing_parses_period(monkeypatch) -> None:
    client = DartClient(api_key="TEST")
    captured: dict[str, tuple[str, str, str]] = {}

    def fake_fr(corp: str, year: str, code: str) -> dict[str, Decimal]:
        captured["args"] = (corp, year, code)
        return {"roe": Decimal("0.1")}

    monkeypatch.setattr(client, "financial_ratios", fake_fr)
    out = client.ratios_for_filing(
        "00126380", {"report_nm": "사업보고서 (2023.12)", "rcept_dt": "20240312"}
    )
    assert captured["args"] == ("00126380", "2023", "11011")
    assert out["roe"] == Decimal("0.1")
    # 정기보고서 아님 → 빈 dict(fail-open)
    assert client.ratios_for_filing("X", {"report_nm": "주요사항보고서"}) == {}


def test_all_listed_codes_returns_six_digit_codes(monkeypatch) -> None:
    from backend.backtest.dart_client import DartClient

    c = DartClient("k")
    monkeypatch.setattr(c, "_load_corp_map", lambda: {"005930": "00126380", "000660": "00164779"})
    c._corp_map = None
    codes = c.all_listed_codes()
    assert "005930" in codes and all(len(x) == 6 for x in codes)


def test_ratios_gp_computed() -> None:
    """매출액·매출원가·자산총계 있으면 gp = (매출액 - 매출원가) / 자산총계."""
    accounts = [
        {"account_nm": "당기순이익", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"},
        {"account_nm": "자본총계", "thstrm_amount": "10,000", "frmtrm_amount": "9,000"},
        {"account_nm": "영업이익", "thstrm_amount": "1,500", "frmtrm_amount": "1,300"},
        {"account_nm": "매출액", "thstrm_amount": "20,000", "frmtrm_amount": "18,000"},
        {"account_nm": "매출원가", "thstrm_amount": "12,000", "frmtrm_amount": "11,000"},
        {"account_nm": "자산총계", "thstrm_amount": "50,000", "frmtrm_amount": "45,000"},
    ]
    r = _ratios_from_accounts(accounts)
    expected = (Decimal("20000") - Decimal("12000")) / Decimal("50000")
    assert r["gp"] == expected


def test_ratios_gp_missing_cogs() -> None:
    """매출원가 없으면 gp 키 자체가 없어야 한다(fail-open, 크래시 없음)."""
    accounts = [
        {"account_nm": "매출액", "thstrm_amount": "20,000", "frmtrm_amount": "18,000"},
        {"account_nm": "자산총계", "thstrm_amount": "50,000", "frmtrm_amount": "45,000"},
    ]
    r = _ratios_from_accounts(accounts)
    assert "gp" not in r


def test_ratios_gp_missing_assets() -> None:
    """자산총계 없으면 gp 키 자체가 없어야 한다(fail-open, 크래시 없음)."""
    accounts = [
        {"account_nm": "매출액", "thstrm_amount": "20,000", "frmtrm_amount": "18,000"},
        {"account_nm": "매출원가", "thstrm_amount": "12,000", "frmtrm_amount": "11,000"},
    ]
    r = _ratios_from_accounts(accounts)
    assert "gp" not in r
