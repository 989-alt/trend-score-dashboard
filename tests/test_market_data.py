"""market_data 단위 테스트 — SampleProvider 결정론·계약, LiveProvider 파싱(mock).

원칙:
- 네트워크 0. LiveProvider 는 httpx/yfinance 를 monkeypatch 로 대체해 파싱만 검증.
- SampleProvider 는 외부 의존이 없으므로 직접 호출.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backend.config import Settings
from backend.market_data import (
    Fundamentals,
    LiveProvider,
    LiveProviderError,
    MarketDataProvider,
    Quote,
    SampleProvider,
    get_provider,
)
from backend.schemas import InvestorFlow, OHLCVRow

_TRAILING_PCT = Decimal("8")
_SERIES = SampleProvider._SERIES_LEN


# ---------------------------------------------------------------------------
# SampleProvider — 결정론·계약
# ---------------------------------------------------------------------------


def test_get_provider_sample_branch() -> None:
    """data_mode=sample → SampleProvider, 그 외 → LiveProvider."""
    sample = get_provider(Settings(data_mode="sample"))
    assert isinstance(sample, SampleProvider)
    live = get_provider(Settings(data_mode="live", kis_app_key="k", kis_app_secret="s"))
    assert isinstance(live, LiveProvider)
    # Protocol 준수 (runtime_checkable).
    assert isinstance(sample, MarketDataProvider)


def test_sample_universe_dedup_and_market_split() -> None:
    """유니버스는 중복 없음 + KR/US 형태 구분(6자리 vs 심볼)."""
    p = SampleProvider()
    kr = p.list_universe("KR")
    us = p.list_universe("US")
    assert kr and us
    assert len(kr) == len(set(kr))  # 중복 제거
    assert len(us) == len(set(us))
    assert all(len(t) == 6 and t.isdigit() for t in kr)  # KR=6자리 코드
    assert "005930" in kr  # themes.yml 종목 포함
    assert "AAPL" in us  # 시장별 대형주 합집합
    assert "NVDA" in us


def test_sample_ohlcv_deterministic() -> None:
    """동일 ticker → 동일 OHLCV (값까지 완전 일치)."""
    p = SampleProvider()
    a = p.get_daily_ohlcv("005930", "KR", _SERIES)
    b = p.get_daily_ohlcv("005930", "KR", _SERIES)
    assert a == b
    assert len(a) == _SERIES
    # 날짜 오름차순.
    assert all(a[i].date < a[i + 1].date for i in range(len(a) - 1))
    # OHLC 불변식.
    for r in a:
        assert r.high >= max(r.open, r.close)
        assert r.low <= min(r.open, r.close)
        assert r.volume > 0


def test_sample_ohlcv_days_slicing() -> None:
    """days < 전체 길이면 최근 days 봉만 반환."""
    p = SampleProvider()
    rows = p.get_daily_ohlcv("000660", "KR", 30)
    full = p.get_daily_ohlcv("000660", "KR", _SERIES)
    assert len(rows) == 30
    assert rows == full[-30:]


def test_sample_fundamentals_deterministic() -> None:
    """펀더멘털 결정론 + 52주 고저 = 시계열 최고/최저."""
    p = SampleProvider()
    f1 = p.get_fundamentals("005930", "KR")
    f2 = p.get_fundamentals("005930", "KR")
    assert f1 == f2
    rows = p.get_daily_ohlcv("005930", "KR", _SERIES)
    closes = [r.close for r in rows]
    assert f1.w52_high == max(closes)
    assert f1.w52_low == min(closes)
    assert f1.market_cap is not None and f1.market_cap > 0
    assert f1.per is not None and f1.per > 0


def test_sample_quote_deterministic_except_asof() -> None:
    """시세는 asof(타임스탬프) 외 결정론. 현재가 ≈ 마지막 종가."""
    p = SampleProvider()
    q1 = p.get_quote("005930", "KR")
    q2 = p.get_quote("005930", "KR")
    assert q1.price == q2.price
    assert q1.open == q2.open
    assert q1.turnover == q2.turnover
    rows = p.get_daily_ohlcv("005930", "KR", _SERIES)
    assert q1.price == rows[-1].close
    assert q1.turnover == rows[-1].close * (q1.volume or Decimal("0"))
    # FIX-E: quote.open == 마지막 일봉의 실제 시가(_build_rows 규칙과 일치).
    assert q1.open == rows[-1].open
    # 장시작 대비(open)와 전일 대비(prev_close)가 구분되도록 open != prev_close.
    assert q1.open != q1.prev_close


def test_sample_investor_flow_kr_not_none_us_none() -> None:
    """KR 6자리 코드 → InvestorFlow(매수/매도 거래대금·순매수). US 심볼 → None."""
    p = SampleProvider()
    flow = p.get_investor_flow("005930")
    assert isinstance(flow, InvestorFlow)
    # 시장 항등식: 외국인+기관+개인 순매수 합 = 0.
    assert flow.foreign_net + flow.institution_net + flow.individual_net == 0
    # FIX-A: 매수/매도 거래대금(KRW)이 채워지고, net = buy − sell 정합.
    for buy, sell, net in (
        (flow.foreign_buy, flow.foreign_sell, flow.foreign_net),
        (flow.institution_buy, flow.institution_sell, flow.institution_net),
        (flow.individual_buy, flow.individual_sell, flow.individual_net),
    ):
        assert buy is not None and sell is not None
        # 금액 규모(수십~수천억) — 50억 이상.
        assert buy >= Decimal("5000000000")
        assert sell >= Decimal("5000000000")
        assert net == buy - sell
    # 결정론.
    assert p.get_investor_flow("005930") == flow
    # US 형태 심볼은 None.
    assert p.get_investor_flow("NVDA") is None
    assert p.get_investor_flow("AAPL") is None


def test_sample_name_mapping() -> None:
    """KR 매핑 코드는 한글명, US 는 심볼, 미등록 KR 은 기본 라벨."""
    p = SampleProvider()
    assert p.get_name("005930", "KR") == "삼성전자"
    assert p.get_name("NVDA", "US") == "NVDA"
    assert p.get_name("999999", "KR").endswith("999999")


def _recent_drop_pct(rows: list[OHLCVRow], tail: int = 5) -> Decimal:
    """꼬리 직전 구간 최고 종가 대비 마지막 종가의 하락률(%)."""
    closes = [r.close for r in rows]
    peak = max(closes[:-tail])
    last = closes[-1]
    return (peak - last) / peak * Decimal("100")


def _above_ma200(rows: list[OHLCVRow]) -> bool:
    closes = [r.close for r in rows]
    ma200 = sum(closes[-200:], Decimal("0")) / Decimal("200")
    return closes[-1] > ma200


@pytest.mark.parametrize("market", ["KR", "US"])
def test_sample_has_sell_alert_cases(market: str) -> None:
    """시장마다 '가상진입 후 트레일링 이탈'(고점 대비 8%+ 하락, 200일선 위) 종목이
    최소 2개 이상 존재한다 — 매도요구 발동 케이스 보장.
    """
    p = SampleProvider()
    sell_cases = []
    for ticker in p.list_universe(market):  # type: ignore[arg-type]
        rows = p.get_daily_ohlcv(ticker, market, _SERIES)  # type: ignore[arg-type]
        if _recent_drop_pct(rows) > _TRAILING_PCT and _above_ma200(rows):
            sell_cases.append(ticker)
    assert len(sell_cases) >= 2, f"{market}: 매도요구 케이스 {len(sell_cases)}개 (<2)"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_sample_has_both_trends(market: str) -> None:
    """상승추세(200일선 위)와 하락추세(200일선 아래)가 모두 존재한다."""
    p = SampleProvider()
    above = below = 0
    for ticker in p.list_universe(market):  # type: ignore[arg-type]
        rows = p.get_daily_ohlcv(ticker, market, _SERIES)  # type: ignore[arg-type]
        if _above_ma200(rows):
            above += 1
        else:
            below += 1
    assert above >= 1
    assert below >= 1
    # '대부분 상승추세' — 절반 초과.
    assert above > below


# ---------------------------------------------------------------------------
# LiveProvider — mock(네트워크 0) 파싱
# ---------------------------------------------------------------------------


def _live() -> LiveProvider:
    return LiveProvider(Settings(data_mode="live", kis_app_key="k", kis_app_secret="s"))


def test_live_kis_quote_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """KIS inquire-price 응답 → Quote 파싱."""
    lp = _live()
    captured: dict[str, Any] = {}

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        captured["path"] = path
        captured["tr_id"] = tr_id
        return {
            "rt_cd": "0",
            "output": {
                "stck_prpr": "70000",
                "stck_oprc": "69500",
                "stck_sdpr": "69000",
                "acml_vol": "1234567",
                "acml_tr_pbmn": "86000000000",
            },
        }

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    q = lp.get_quote("005930", "KR")
    assert isinstance(q, Quote)
    assert q.price == Decimal("70000")
    assert q.open == Decimal("69500")
    assert q.prev_close == Decimal("69000")
    assert q.volume == Decimal("1234567")
    assert q.turnover == Decimal("86000000000")
    assert "inquire-price" in captured["path"]


def test_live_kis_quote_glitch_guard_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """현재가가 전일 대비 ±50% 밖이면 1회 재조회(글리치 가드)."""
    lp = _live()
    calls = {"n": 0}

    def fake_once(ticker: str) -> Quote:
        calls["n"] += 1
        if calls["n"] == 1:
            # 전일 종가 100 대비 10배 = 글리치.
            return Quote(price=Decimal("1000"), prev_close=Decimal("100"), asof=_now())
        return Quote(price=Decimal("101"), prev_close=Decimal("100"), asof=_now())

    monkeypatch.setattr(lp, "_kis_quote_once", fake_once)
    q = lp.get_quote("005930", "KR")
    assert calls["n"] == 2  # 글리치 → 재조회
    assert q.price == Decimal("101")


def test_live_kis_ohlcv_parsing_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    """KIS 일봉(최신순 응답) → 오름차순 OHLCVRow."""
    lp = _live()

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        return {
            "rt_cd": "0",
            "output2": [
                # 최신순(내림차순)으로 옴 — 파서가 오름차순 정렬해야 함.
                _kis_bar("20250103", "120"),
                _kis_bar("20250102", "110"),
                _kis_bar("20250101", "100"),
            ],
        }

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    rows = lp.get_daily_ohlcv("005930", "KR", 10)
    assert [str(r.close) for r in rows] == ["100", "110", "120"]
    assert all(rows[i].date < rows[i + 1].date for i in range(len(rows) - 1))


def test_live_kis_investor_flow_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-A: KIS 투자자별 매수/매도 거래대금 → InvestorFlow(net=매수−매도).

    수량(``*_ntby_qty``)이 아니라 거래대금(``*_tr_pbmn``)을 읽고, net 은 매수금−매도금.
    """
    lp = _live()

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        return {
            "rt_cd": "0",
            "output": [
                {
                    "stck_bsop_date": "20250103",
                    "frgn_shnu_tr_pbmn": "300000000000",  # 외국인 매수 3,000억
                    "frgn_seln_tr_pbmn": "200000000000",  # 외국인 매도 2,000억
                    "orgn_shnu_tr_pbmn": "150000000000",
                    "orgn_seln_tr_pbmn": "180000000000",
                    "prsn_shnu_tr_pbmn": "100000000000",
                    "prsn_seln_tr_pbmn": "170000000000",
                }
            ],
        }

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    flow = lp.get_investor_flow("005930")
    assert isinstance(flow, InvestorFlow)
    assert flow.foreign_buy == Decimal("300000000000")
    assert flow.foreign_sell == Decimal("200000000000")
    assert flow.foreign_net == Decimal("100000000000")  # 매수 − 매도
    assert flow.institution_net == Decimal("-30000000000")
    assert flow.individual_net == Decimal("-70000000000")
    # US 형태 심볼은 KIS 호출 전에 None.
    assert lp.get_investor_flow("NVDA") is None


def test_live_kis_investor_flow_missing_amount_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-A: 거래대금 필드 부재 시 LiveProviderError(무음 0 금지)."""
    lp = _live()

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        # 수량 필드만 있고 거래대금 필드는 부재.
        return {"rt_cd": "0", "output": [{"stck_bsop_date": "20250103", "frgn_ntby_qty": "5000"}]}

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    with pytest.raises(LiveProviderError):
        lp.get_investor_flow("005930")


def test_live_us_quote_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance fast_info → Quote (US)."""
    lp = _live()
    monkeypatch.setattr(
        lp,
        "_yf_fast_info",
        lambda ticker: {
            "last_price": 150.5,
            "previous_close": 148.0,
            "open": 149.0,
            "last_volume": 1000000,
        },
    )
    q = lp.get_quote("NVDA", "US")
    assert q.price == Decimal("150.5")
    assert q.prev_close == Decimal("148.0")
    assert q.volume == Decimal("1000000")
    assert q.turnover == Decimal("150.5") * Decimal("1000000")


def test_live_us_fundamentals_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """yfinance info/fast_info → Fundamentals (US). 투자자flow 는 항상 None."""
    lp = _live()
    monkeypatch.setattr(
        lp,
        "_yf_info",
        lambda ticker: {
            "trailingPE": 28.5,
            "priceToBook": 12.0,
            "trailingEps": 5.2,
            "fiftyTwoWeekHigh": 200.0,
            "fiftyTwoWeekLow": 100.0,
            "52WeekChange": 0.35,
            "sector": "Technology",
            "industry": "Semiconductors",
            "shortName": "NVIDIA Corp",
        },
    )
    monkeypatch.setattr(lp, "_yf_fast_info", lambda ticker: {"market_cap": 3.0e12})
    f = lp.get_fundamentals("NVDA", "US")
    assert isinstance(f, Fundamentals)
    assert f.per == Decimal("28.5")
    assert f.return_1y_pct == Decimal("35.000")  # 0.35 * 100
    assert f.sector == "Technology"
    assert f.name == "NVIDIA Corp"
    # US 투자자flow 는 None.
    assert lp.get_investor_flow("NVDA") is None


def test_live_us_return_1y_alt_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-C: 1년수익률 키가 'fiftyTwoWeekChange'(대체 표기)여도 파싱. 결측이면 None."""
    lp = _live()
    monkeypatch.setattr(lp, "_yf_fast_info", lambda ticker: {})
    # 대체 키만 존재.
    monkeypatch.setattr(lp, "_yf_info", lambda ticker: {"fiftyTwoWeekChange": 0.42})
    f = lp.get_fundamentals("NVDA", "US")
    assert f.return_1y_pct == Decimal("42.00")
    # 두 키 모두 결측 → None(엔진 OHLCV 폴백).
    monkeypatch.setattr(lp, "_yf_info", lambda ticker: {"trailingPE": 10.0})
    f2 = lp.get_fundamentals("NVDA", "US")
    assert f2.return_1y_pct is None


def test_live_kis_quote_glitch_persists_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-D: 재조회 후에도 ±50% 밖이면 LiveProviderError(무조건 채택 금지)."""
    lp = _live()

    def always_glitch(ticker: str) -> Quote:
        # 전일 100 대비 10배 = 지속 글리치.
        return Quote(price=Decimal("1000"), prev_close=Decimal("100"), asof=_now())

    monkeypatch.setattr(lp, "_kis_quote_once", always_glitch)
    with pytest.raises(LiveProviderError):
        lp.get_quote("005930", "KR")


def test_live_universe_kr_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-B: KR 유니버스는 pykrx KOSPI∪KOSDAQ 전 종목 + 1회 캐시."""
    lp = _live()
    calls = {"n": 0}

    def fake_fetch_kr() -> list[str]:
        calls["n"] += 1
        return ["005930", "000660", "035720"]

    monkeypatch.setattr(lp, "_fetch_universe_kr", fake_fetch_kr)
    kr1 = lp.list_universe("KR")
    kr2 = lp.list_universe("KR")
    assert kr1 == ["005930", "000660", "035720"]
    assert kr2 == kr1
    assert calls["n"] == 1  # 인스턴스 1회 캐시 → 2번째는 fetch 안 함


def test_live_universe_kr_fallback_to_themes(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-B: pykrx 실패 시 themes.yml 로 graceful fallback."""
    lp = _live()
    monkeypatch.setattr(lp, "_fetch_universe_kr", lambda: [])  # 실패 모사(빈 결과)
    kr = lp.list_universe("KR")
    assert "005930" in kr  # themes.yml 큐레이션
    assert len(kr) == len(set(kr))


def test_live_universe_us_nasdaq_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-B: US 유니버스는 NASDAQ Trader 심볼덤프(보통주만, 테스트이슈·ETF 제외)."""
    lp = _live()
    nasdaqlisted = (
        "Symbol|Security Name|Market Category|Test Issue"
        "|Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
        "ZTEST|Test Issue|Q|Y|N|100|N|N\n"  # 테스트이슈 → 제외
        "QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N\n"  # ETF → 제외
        "File Creation Time: 0601202512:00\n"
    )
    otherlisted = (
        "ACT Symbol|Security Name|Exchange|CQS Symbol"
        "|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "BRK.B|Berkshire Hathaway|N|BRK.B|N|100|N|BRK.B\n"  # '.' 포함 → 제외
        "JPM|JPMorgan|N|JPM|N|100|N|JPM\n"
        "SPY|SPDR S&P 500|P|SPY|Y|100|N|SPY\n"  # ETF → 제외
        "File Creation Time: 0601202512:00\n"
    )

    class FakeResp:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    def fake_client_get(url: str, timeout: float = 20.0) -> FakeResp:
        return FakeResp(nasdaqlisted if "nasdaqlisted" in url else otherlisted)

    monkeypatch.setattr(lp._client, "get", fake_client_get)
    us = lp.list_universe("US")
    assert "AAPL" in us
    assert "JPM" in us
    assert "ZTEST" not in us  # 테스트이슈 제외
    assert "QQQ" not in us  # ETF 제외
    assert "SPY" not in us
    assert "BRK.B" not in us  # 비보통주 표기('.') 제외
    assert len(us) == len(set(us))


def test_live_universe_us_fallback_to_themes(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX-B: NASDAQ 덤프 실패(HTTP 오류) 시 themes.yml fallback."""
    import httpx as _httpx

    lp = _live()

    def boom(url: str, timeout: float = 20.0) -> None:
        raise _httpx.ConnectError("network down")

    monkeypatch.setattr(lp._client, "get", boom)
    us = lp.list_universe("US")
    assert "NVDA" in us  # themes.yml 큐레이션


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _kis_bar(date_str: str, close: str) -> dict[str, str]:
    return {
        "stck_bsop_date": date_str,
        "stck_oprc": close,
        "stck_hgpr": close,
        "stck_lwpr": close,
        "stck_clpr": close,
        "acml_vol": "1000000",
    }
