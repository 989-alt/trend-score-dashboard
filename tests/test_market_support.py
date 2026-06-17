"""시장 인지(Panel.market / loader 접미사·지수 / 거래대금 임계) 단위 검증."""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from backend.backtest.panel import Panel, TickerSeries, Valuation
from backend.backtest.run import _score_at
from backend.config import get_settings
from backend.schemas import OHLCVRow

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_rows(start: date, closes: list[int]) -> list[OHLCVRow]:
    rows = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        close = cd * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
        rows.append(
            OHLCVRow(
                date=start + timedelta(days=i),
                open=cd,
                high=cd * Decimal("1.025"),
                low=cd * Decimal("0.975"),
                close=close,
                volume=Decimal("1000000"),
            )
        )
    return rows


def _make_yf_ticker_mock(frame: pd.DataFrame | None) -> MagicMock:
    m = MagicMock()
    if frame is None:
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        m.history.return_value = empty
    else:
        m.history.return_value = frame
    return m


def _make_yf_frame() -> pd.DataFrame:
    dates = pd.to_datetime(["2023-01-02", "2023-01-03"]).tz_localize("UTC")
    return pd.DataFrame(
        {
            "Open": [100, 101],
            "High": [102, 103],
            "Low": [99, 100],
            "Close": [101, 102],
            "Volume": [1000, 1100],
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. Panel.market 기본값 + 명시 생성
# ---------------------------------------------------------------------------


def test_panel_default_market_is_kr() -> None:
    """Panel 은 market 인수 없이 생성하면 KR 이 기본값이다."""
    panel = Panel(series={}, fundamentals={}, listings={}, index_rows=[])
    assert panel.market == "KR"


def test_panel_market_us_constructible() -> None:
    """Panel(market='US') 가 문제 없이 생성된다."""
    panel = Panel(series={}, fundamentals={}, listings={}, index_rows=[], market="US")
    assert panel.market == "US"


def test_panel_frozen_market_immutable() -> None:
    """frozen dataclass 이므로 market 을 직접 대입하면 FrozenInstanceError 가 발생한다."""
    import dataclasses

    panel = Panel(series={}, fundamentals={}, listings={}, index_rows=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        panel.market = "US"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Loader — US market: bare ticker, no .KS/.KQ
# ---------------------------------------------------------------------------


def test_loader_us_fetch_ohlcv_calls_bare_ticker(tmp_path) -> None:
    """market='US' 이면 _fetch_ohlcv 가 'AAPL' (suffix 없이) 로 yf.Ticker 를 호출한다."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="US")
    frame = _make_yf_frame()
    called_symbols: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        called_symbols.append(symbol)
        return _make_yf_ticker_mock(frame)

    with patch("yfinance.Ticker", side_effect=_ticker_factory):
        result = loader._fetch_ohlcv("AAPL", date(2023, 1, 2), date(2023, 1, 3))

    assert result is not None
    assert called_symbols == ["AAPL"], f"expected ['AAPL'], got {called_symbols}"


def test_loader_kr_fetch_ohlcv_calls_ks_first(tmp_path) -> None:
    """market='KR' 이면 _fetch_ohlcv 가 '005930.KS' 로 먼저 yf.Ticker 를 호출한다."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="KR")
    frame = _make_yf_frame()
    called_symbols: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        called_symbols.append(symbol)
        return _make_yf_ticker_mock(frame)

    with patch("yfinance.Ticker", side_effect=_ticker_factory):
        loader._fetch_ohlcv("005930", date(2023, 1, 2), date(2023, 1, 3))

    assert called_symbols[0] == "005930.KS"


def test_loader_us_fetch_ohlcv_no_ks_kq_suffix(tmp_path) -> None:
    """market='US' 이면 .KS/.KQ suffix 로 yf.Ticker 를 호출하지 않는다."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="US")
    frame = _make_yf_frame()
    called_symbols: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        called_symbols.append(symbol)
        return _make_yf_ticker_mock(frame)

    with patch("yfinance.Ticker", side_effect=_ticker_factory):
        loader._fetch_ohlcv("MSFT", date(2023, 1, 2), date(2023, 1, 3))

    assert not any(s.endswith(".KS") or s.endswith(".KQ") for s in called_symbols), (
        f".KS/.KQ suffix 호출 없어야 함, got {called_symbols}"
    )


# ---------------------------------------------------------------------------
# 3. Loader — _index_ohlcv: KR → ^KS11, US → ^GSPC
# ---------------------------------------------------------------------------


def test_loader_kr_index_uses_ks11(tmp_path) -> None:
    """market='KR' 이면 _index_ohlcv 가 '^KS11' 로 yf.Ticker 를 호출한다."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="KR")
    frame = _make_yf_frame()
    called_symbols: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        called_symbols.append(symbol)
        return _make_yf_ticker_mock(frame)

    # _rows 가 빈 frame 처리에서 실패해도 호출 기록은 남음
    with patch("yfinance.Ticker", side_effect=_ticker_factory), contextlib.suppress(Exception):
        loader._index_ohlcv(date(2023, 1, 2), date(2023, 1, 3))

    assert "^KS11" in called_symbols, f"^KS11 호출 없음: {called_symbols}"
    assert "^GSPC" not in called_symbols


def test_loader_us_index_uses_gspc(tmp_path) -> None:
    """market='US' 이면 _index_ohlcv 가 '^GSPC' 로 yf.Ticker 를 호출한다."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="US")
    frame = _make_yf_frame()
    called_symbols: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        called_symbols.append(symbol)
        return _make_yf_ticker_mock(frame)

    with patch("yfinance.Ticker", side_effect=_ticker_factory), contextlib.suppress(Exception):
        loader._index_ohlcv(date(2023, 1, 2), date(2023, 1, 3))

    assert "^GSPC" in called_symbols, f"^GSPC 호출 없음: {called_symbols}"
    assert "^KS11" not in called_symbols


# ---------------------------------------------------------------------------
# 4. Loader — cache key includes market (KR/US 충돌 방지)
# ---------------------------------------------------------------------------


def test_loader_cache_key_includes_market(tmp_path) -> None:
    """KR 과 US 가 같은 ticker+날짜에 별도 캐시 파일을 사용한다."""
    from backend.backtest.loader import PanelLoader

    loader_kr = PanelLoader(dart=None, cache_dir=tmp_path, market="KR")
    loader_us = PanelLoader(dart=None, cache_dir=tmp_path, market="US")
    frame = _make_yf_frame()

    fetch_calls: list[str] = []

    def _stub_fetch_kr(ticker: str, s: date, e: date) -> pd.DataFrame:
        fetch_calls.append(f"KR:{ticker}")
        return frame

    def _stub_fetch_us(ticker: str, s: date, e: date) -> pd.DataFrame:
        fetch_calls.append(f"US:{ticker}")
        return frame

    start, end = date(2023, 1, 2), date(2023, 1, 3)

    # 각 loader 로 한 번씩 — 캐시 miss 이므로 각각 fetch 호출
    loader_kr._fetch_ohlcv = _stub_fetch_kr  # type: ignore[method-assign]
    loader_us._fetch_ohlcv = _stub_fetch_us  # type: ignore[method-assign]

    loader_kr._ohlcv("TICKER", start, end)
    loader_us._ohlcv("TICKER", start, end)

    # 두 호출 모두 fetch 가 발생해야 함(캐시가 충돌하지 않음)
    assert "KR:TICKER" in fetch_calls
    assert "US:TICKER" in fetch_calls

    # 캐시 파일 이름에 market prefix 가 있는지 확인
    cache_files = list((tmp_path / "ohlcv").iterdir())
    names = [f.name for f in cache_files]
    assert any(n.startswith("KR_") for n in names), f"KR_ prefix 없음: {names}"
    assert any(n.startswith("US_") for n in names), f"US_ prefix 없음: {names}"


# ---------------------------------------------------------------------------
# 5. _score_at — US panel 은 USD 임계(3e7), KR panel 은 KRW 임계(1e10)
#    거래대금 = 1e8: USD 임계 통과, KRW 임계 실패 → 시장별 결과 상이
# ---------------------------------------------------------------------------


def _make_us_eligibility_panel(market: str) -> Panel:
    """거래대금 = 1e8 (USD 통과, KRW 실패) 인 합성 Panel.

    시계열 260봉(ma200_window 통과) + 적절한 변동성으로 다른 하드필터를 통과.
    """
    start = date(2023, 1, 2)
    n = 260
    closes = list(range(100, 100 + n))
    rows = _make_rows(start, closes)

    # 거래대금 1억 달러 = 1e8 (USD 임계 3e7 통과, KRW 임계 1e10 실패)
    mid_turnover = Decimal("100000000")  # 1e8

    series = TickerSeries(
        ticker="AAPL",
        rows=rows,
        turnover_by_date={r.date: mid_turnover for r in rows},
        valuation_by_date={r.date: Valuation(per=Decimal("20"), pbr=Decimal("3")) for r in rows},
    )
    listing_start = rows[0].date

    # 지수 행 — 합성 상승 추세
    idx_rows = _make_rows(start, list(range(4000, 4000 + n)))

    return Panel(
        series={"AAPL": series},
        fundamentals={"AAPL": []},
        listings={"AAPL": (listing_start, None)},
        index_rows=idx_rows,
        market=market,
    )


def test_score_at_us_panel_mid_turnover_is_eligible() -> None:
    """US panel: 거래대금 1e8 은 USD 임계(3e7)를 통과 → _score_at 결과에 AAPL 이 있어야 함."""
    panel = _make_us_eligibility_panel("US")
    settings = get_settings()
    t = panel.series["AAPL"].rows[-1].date

    result = _score_at(panel, t, settings, preset="baseline")
    tickers = [tk for tk, _ in result]
    assert "AAPL" in tickers, f"US 임계에서 AAPL 이 적격이어야 하는데 결과에 없음: {tickers}"


def test_score_at_kr_panel_mid_turnover_is_ineligible() -> None:
    """KR panel: 거래대금 1e8 은 KRW 임계(1e10) 미달 → _score_at 결과에 AAPL 이 없어야 함."""
    panel = _make_us_eligibility_panel("KR")
    settings = get_settings()
    t = panel.series["AAPL"].rows[-1].date

    result = _score_at(panel, t, settings, preset="baseline")
    tickers = [tk for tk, _ in result]
    assert "AAPL" not in tickers, f"KR 임계에서 AAPL 이 부적격이어야 하는데 결과에 있음: {tickers}"


def test_score_at_market_threshold_asymmetry() -> None:
    """동일 Panel 데이터에서 market 만 바꾸면 적격 여부가 반전된다."""
    panel_us = _make_us_eligibility_panel("US")
    panel_kr = _make_us_eligibility_panel("KR")
    settings = get_settings()
    t = panel_us.series["AAPL"].rows[-1].date

    us_tickers = {tk for tk, _ in _score_at(panel_us, t, settings)}
    kr_tickers = {tk for tk, _ in _score_at(panel_kr, t, settings)}

    assert "AAPL" in us_tickers
    assert "AAPL" not in kr_tickers
