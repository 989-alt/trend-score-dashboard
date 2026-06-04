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

    def fake_once(ticker: str, *, force: bool = False) -> Quote:
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


def test_live_kis_investor_flow_net_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX: inquire-investor 는 **순매수 거래대금**만 준다 → ``*_net`` 만 채우고 buy/sell=None.

    매수/매도 분리 거래대금 필드(``*_shnu_tr_pbmn``)는 응답에 없으므로, 순매수 필드
    ``frgn_ntby_tr_pbmn``/``orgn_ntby_tr_pbmn``/``prsn_ntby_tr_pbmn``(KRW)로 net 을 채운다.
    """
    lp = _live()

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        return {
            "rt_cd": "0",
            "output": [
                {
                    "stck_bsop_date": "20250103",
                    "frgn_ntby_tr_pbmn": "100000000000",  # 외국인 순매수 +1,000억
                    "orgn_ntby_tr_pbmn": "-30000000000",  # 기관 순매수 -300억
                    "prsn_ntby_tr_pbmn": "-70000000000",  # 개인 순매수 -700억
                }
            ],
        }

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    flow = lp.get_investor_flow("005930")
    assert isinstance(flow, InvestorFlow)
    assert flow.foreign_net == Decimal("100000000000")
    assert flow.institution_net == Decimal("-30000000000")
    assert flow.individual_net == Decimal("-70000000000")
    # 매수/매도 분리 금액은 이 API 가 주지 않음 → None(프론트는 net 만 표시로 폴백).
    assert flow.foreign_buy is None
    assert flow.foreign_sell is None
    assert flow.institution_buy is None
    assert flow.individual_sell is None
    # US 형태 심볼은 KIS 호출 전에 None.
    assert lp.get_investor_flow("NVDA") is None


def test_live_kis_investor_flow_all_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """FIX: 순매수 거래대금 세 필드가 모두 결측이면 None 반환(raise 금지)."""
    lp = _live()

    def fake_get(path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        # 순매수 거래대금 필드가 전부 부재.
        return {"rt_cd": "0", "output": [{"stck_bsop_date": "20250103", "frgn_ntby_qty": "5000"}]}

    monkeypatch.setattr(lp, "_kis_get", fake_get)
    assert lp.get_investor_flow("005930") is None


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

    def always_glitch(ticker: str, *, force: bool = False) -> Quote:
        # 전일 100 대비 10배 = 지속 글리치.
        return Quote(price=Decimal("1000"), prev_close=Decimal("100"), asof=_now())

    monkeypatch.setattr(lp, "_kis_quote_once", always_glitch)
    with pytest.raises(LiveProviderError):
        lp.get_quote("005930", "KR")


def test_live_universe_kr_pykrx_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """KR 유니버스 = 거래대금 상위 N + 인스턴스 1회 캐시."""
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


def test_live_fetch_universe_kr_top_n_by_turnover(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_fetch_universe_kr``: 벌크 거래대금 DataFrame → 내림차순 상위 N(6자리 zero-pad).

    pykrx/pandas 를 mock — 개별 종목 루프 없이 시장당 1콜(벌크)만 호출하는지 함께 검증.
    """
    pd = pytest.importorskip("pandas")
    lp = _live()
    settings = lp._settings.model_copy(update={"live_universe_top_n": 3})
    monkeypatch.setattr(lp, "_settings", settings)

    bulk_calls = {"n": 0}

    class FakeStock:
        @staticmethod
        def get_nearest_business_day_in_a_week(*_a: object, **_k: object) -> str:
            return "20250103"

        @staticmethod
        def get_market_ohlcv_by_ticker(_bday: str, market: str = "") -> object:
            bulk_calls["n"] += 1
            if market == "KOSPI":
                return pd.DataFrame(
                    {"거래대금": [900, 300]}, index=["5930", "5490"]
                )  # zero-pad 검증용 5자리 인덱스
            return pd.DataFrame({"거래대금": [800, 100]}, index=["247540", "035720"])

    # pykrx.stock 과 pandas import 를 가짜로 주입.
    import sys

    fake_pykrx = type(sys)("pykrx")
    fake_pykrx.stock = FakeStock  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pykrx", fake_pykrx)

    tickers = lp._fetch_universe_kr()
    # 거래대금 내림차순 상위 3: 5930(900)>247540(800)>5490(300). zero-pad 적용.
    assert tickers == ["005930", "247540", "005490"]
    assert bulk_calls["n"] == 2  # KOSPI·KOSDAQ 각 1콜(벌크) — 개별 루프 금지


def test_live_universe_kr_fallback_to_themes(monkeypatch: pytest.MonkeyPatch) -> None:
    """pykrx 실패(빈 결과) 시 themes.yml 로 graceful fallback."""
    lp = _live()
    monkeypatch.setattr(lp, "_fetch_universe_kr", lambda: [])  # 실패 모사(빈 결과)
    kr = lp.list_universe("KR")
    assert "005930" in kr  # themes.yml 큐레이션
    assert len(kr) == len(set(kr))


def test_live_universe_us_static_top_n() -> None:
    """US 유니버스 = 유동성 정적 화이트리스트 상위 N(거래대금 상위 근사)."""
    lp = _live()
    settings = lp._settings.model_copy(update={"live_universe_top_n": 5})
    lp._settings = settings
    us = lp.list_universe("US")
    assert len(us) == 5
    assert "AAPL" in us  # 최상위 유동성
    assert "NVDA" in us
    assert len(us) == len(set(us))


def test_live_universe_us_top_n_caps_default() -> None:
    """기본 top_n(300)으로도 정적 리스트 길이 이내에서 안전하게 동작."""
    lp = _live()  # 기본 live_universe_top_n=300
    us = lp.list_universe("US")
    assert 0 < len(us) <= 300
    assert "MSFT" in us


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
