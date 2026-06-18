from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
from backend.backtest.loader import PanelLoader

# ---------------------------------------------------------------------------
# 공통 헬퍼: yfinance Ticker mock 생성
# ---------------------------------------------------------------------------

_DATES_TZ = pd.to_datetime(["2023-01-02", "2023-01-03"]).tz_localize("Asia/Seoul")


def _make_yf_frame(dates=_DATES_TZ) -> pd.DataFrame:
    """yfinance .history() 반환형과 동일한 영문 컬럼 + tz-aware DatetimeIndex."""
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


def _make_yf_ticker_mock(frame: pd.DataFrame | None) -> MagicMock:
    """frame=None 이면 empty DataFrame 반환(실패 시뮬)."""
    m = MagicMock()
    if frame is None:
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        m.history.return_value = empty
    else:
        m.history.return_value = frame
    return m


# ---------------------------------------------------------------------------
# 1. 컬럼 매핑 + OHLCVRow 변환
# ---------------------------------------------------------------------------


def test_build_panel_from_mocked_sources(monkeypatch, tmp_path) -> None:
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    # _ohlcv 를 직접 패치(영문→한글 매핑 결과 시뮬)
    idx = pd.to_datetime(["2023-01-02", "2023-01-03"]).tz_localize("Asia/Seoul")
    ohlcv_kr = pd.DataFrame(
        {
            "시가": [100, 101],
            "고가": [102, 103],
            "저가": [99, 100],
            "종가": [101, 102],
            "거래량": [1000, 1100],
            "거래대금": [2e10, 2.1e10],
        },
        index=idx,
    )
    monkeypatch.setattr(loader, "_ohlcv", lambda ticker, s, e: ohlcv_kr)
    monkeypatch.setattr(loader, "_index_ohlcv", lambda s, e: ohlcv_kr)
    monkeypatch.setattr(loader, "_fundamentals", lambda ticker: [])
    monkeypatch.setattr(loader, "_valuation", lambda ticker, s, e: None)

    panel = loader.build(["000001"], date(2023, 1, 2), date(2023, 1, 3))
    rows = panel.rows_asof("000001", date(2023, 1, 3))
    assert len(rows) == 2
    assert rows[-1].close == Decimal("102")
    assert panel.turnover_asof("000001", date(2023, 1, 3)) == Decimal("21000000000")
    assert "000001" in panel.universe_asof(date(2023, 1, 2))


# ---------------------------------------------------------------------------
# 2. 거래대금 컬럼 없을 때 종가×거래량 fallback
# ---------------------------------------------------------------------------


def test_turnover_fallback_close_x_volume(monkeypatch, tmp_path) -> None:
    """yfinance OHLCV 에 '거래대금' 컬럼이 없으면 turnover = 종가×거래량 프록시로 채운다."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)
    idx = pd.to_datetime(["2024-01-30"]).tz_localize("Asia/Seoul")
    ohlcv = pd.DataFrame(
        {
            "시가": [75000],
            "고가": [75300],
            "저가": [73700],
            "종가": [74300],
            "거래량": [12244418],
        },
        index=idx,
    )
    monkeypatch.setattr(loader, "_ohlcv", lambda ticker, s, e: ohlcv)
    monkeypatch.setattr(loader, "_index_ohlcv", lambda s, e: ohlcv)
    monkeypatch.setattr(loader, "_fundamentals", lambda ticker: [])
    monkeypatch.setattr(loader, "_valuation", lambda ticker, s, e: None)

    panel = loader.build(["005930"], date(2024, 1, 30), date(2024, 1, 30))
    expected = Decimal("74300") * Decimal("12244418")
    assert panel.turnover_asof("005930", date(2024, 1, 30)) == expected


# ---------------------------------------------------------------------------
# 3. per-ticker fail-open: 한 종목 실패가 전체 build 를 막지 않는다
# ---------------------------------------------------------------------------


def test_per_ticker_fail_open(monkeypatch, tmp_path) -> None:
    """_ohlcv 가 None/empty 를 반환하는 종목은 skip, 나머지 종목은 정상 반영."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    idx = pd.to_datetime(["2023-06-01"]).tz_localize("Asia/Seoul")
    good_frame = pd.DataFrame(
        {"시가": [5000], "고가": [5100], "저가": [4900], "종가": [5050], "거래량": [500000]},
        index=idx,
    )

    def _ohlcv_stub(ticker: str, s: date, e: date) -> pd.DataFrame | None:
        return None if ticker == "BAD001" else good_frame

    monkeypatch.setattr(loader, "_ohlcv", _ohlcv_stub)
    monkeypatch.setattr(loader, "_index_ohlcv", lambda s, e: good_frame)
    monkeypatch.setattr(loader, "_fundamentals", lambda ticker: [])
    monkeypatch.setattr(loader, "_valuation", lambda ticker, s, e: None)

    panel = loader.build(["BAD001", "005930"], date(2023, 6, 1), date(2023, 6, 1))
    assert "BAD001" not in panel.series
    assert "005930" in panel.series


# ---------------------------------------------------------------------------
# 4. _ohlcv 내부: .KS 빈 결과 → .KQ fallback
# ---------------------------------------------------------------------------


def test_ohlcv_ks_empty_falls_back_to_kq(tmp_path) -> None:
    """.KS 가 empty 를 반환하면 .KQ 로 재시도해 데이터를 반환한다."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    kq_frame = _make_yf_frame()

    def _ticker_factory(symbol: str) -> MagicMock:
        if symbol.endswith(".KS"):
            return _make_yf_ticker_mock(None)  # empty → fallback 유발
        return _make_yf_ticker_mock(kq_frame)  # .KQ 는 정상 반환

    with patch("yfinance.Ticker", side_effect=_ticker_factory):
        result = loader._ohlcv("000660", date(2023, 1, 2), date(2023, 1, 3))

    assert result is not None
    assert not result.empty
    # 컬럼이 한글로 매핑됐는지 확인
    assert "종가" in result.columns
    assert "거래량" in result.columns


def test_ohlcv_ks_empty_kq_empty_returns_none(tmp_path) -> None:
    """.KS / .KQ 모두 empty → None 반환(fail-open)."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    with patch("yfinance.Ticker", return_value=_make_yf_ticker_mock(None)):
        result = loader._ohlcv("INVALID", date(2023, 1, 2), date(2023, 1, 3))

    assert result is None


def test_ohlcv_ks_returns_data_no_kq_call(tmp_path) -> None:
    """.KS 가 정상 데이터를 반환하면 .KQ 를 호출하지 않는다."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    ks_frame = _make_yf_frame()
    call_log: list[str] = []

    def _ticker_factory(symbol: str) -> MagicMock:
        call_log.append(symbol)
        return _make_yf_ticker_mock(ks_frame)

    with patch("yfinance.Ticker", side_effect=_ticker_factory):
        result = loader._ohlcv("005930", date(2023, 1, 2), date(2023, 1, 3))

    assert result is not None
    assert call_log == ["005930.KS"]  # .KQ 는 호출되지 않음
    assert "종가" in result.columns


# ---------------------------------------------------------------------------
# 5. _ohlcv 예외 → None (예외가 빌드를 터뜨리지 않음)
# ---------------------------------------------------------------------------


def test_ohlcv_exception_returns_none(tmp_path) -> None:
    """yfinance.Ticker().history() 가 예외를 던져도 None 반환."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    boom = MagicMock()
    boom.history.side_effect = RuntimeError("network error")

    with patch("yfinance.Ticker", return_value=boom):
        result = loader._ohlcv("005930", date(2023, 1, 2), date(2023, 1, 3))

    assert result is None


# ---------------------------------------------------------------------------
# 6. 디스크 캐시: 두 번째 호출은 네트워크 없이 캐시에서 반환
# ---------------------------------------------------------------------------


def _make_kr_frame(dates=_DATES_TZ) -> pd.DataFrame:
    """_fetch_ohlcv 가 반환하는 한글 컬럼 + tz-aware DatetimeIndex 형태."""
    return pd.DataFrame(
        {
            "시가": [100.0, 101.0],
            "고가": [102.0, 103.0],
            "저가": [99.0, 100.0],
            "종가": [101.0, 102.0],
            "거래량": [1000.0, 1100.0],
        },
        index=dates,
    )


def test_ohlcv_disk_cache_second_call_no_network(monkeypatch, tmp_path) -> None:
    """동일 (ticker, start, end) 로 _ohlcv 를 두 번 호출하면 _fetch_ohlcv 는
    정확히 1회만 호출되고, 두 반환값은 _rows 출력이 동일해야 한다."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    call_count = 0

    def _stub_fetch(ticker: str, start: date, end: date) -> pd.DataFrame:
        nonlocal call_count
        call_count += 1
        return _make_kr_frame()

    monkeypatch.setattr(loader, "_fetch_ohlcv", _stub_fetch)

    start = date(2023, 1, 2)
    end = date(2023, 1, 3)

    frame1 = loader._ohlcv("005930", start, end)
    frame2 = loader._ohlcv("005930", start, end)

    # 네트워크(fetch) 는 정확히 1회만
    assert call_count == 1

    # 두 반환값이 _rows 에서 동일하게 동작해야 한다
    rows1, _ = PanelLoader._rows(frame1)
    rows2, _ = PanelLoader._rows(frame2)
    assert len(rows1) == len(rows2) == 2
    assert [r.close for r in rows1] == [r.close for r in rows2]
    assert [r.date for r in rows1] == [r.date for r in rows2]


def test_ohlcv_corrupt_cache_fail_open(monkeypatch, tmp_path) -> None:
    """캐시 파일이 깨져 있어도 _fetch_ohlcv 로 폴백하고 예외가 빠져나오지 않는다."""
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    # 캐시 디렉터리에 corrupt 파일을 미리 심어 둔다
    cache_subdir = tmp_path / "ohlcv"
    cache_subdir.mkdir(parents=True, exist_ok=True)
    start = date(2023, 1, 2)
    end = date(2023, 1, 3)
    # parquet 과 json 둘 다 깨진 파일을 넣어 어떤 포맷이든 읽기 실패하게 함
    for ext in ("parquet", "json"):
        corrupt = cache_subdir / f"005930_{start.isoformat()}_{end.isoformat()}.{ext}"
        corrupt.write_bytes(b"NOT_VALID_DATA")

    call_count = 0

    def _stub_fetch(ticker: str, s: date, e: date) -> pd.DataFrame:
        nonlocal call_count
        call_count += 1
        return _make_kr_frame()

    monkeypatch.setattr(loader, "_fetch_ohlcv", _stub_fetch)

    result = loader._ohlcv("005930", start, end)

    assert result is not None
    assert call_count == 1  # 캐시 실패 → fetch 폴백 1회
