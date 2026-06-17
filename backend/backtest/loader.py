"""yfinance(.KS/.KQ) + DART(재무) + ^KS11 → Panel.

Decimal 전면. yfinance DataFrame 영문 컬럼(Open/High/Low/Close/Volume)을
한글 컬럼(시가/고가/저가/종가/거래량)으로 매핑해 OHLCVRow 로 변환.

KR 가격원: pykrx 는 KRX(data.krx.co.kr) 직접통신이라 방화벽 환경에서 SSLEOFError 발생.
yfinance .KS(KOSPI)/.KQ(KOSDAQ) suffix 로 대체 — 라이브 대시보드(KIS 사용)는 무관.

v1 생존편향 한계(명시): build() 는 호출 측이 준 ticker 리스트로 시계열을 만들고,
종목별 상장구간을 'OHLCV 존재구간(첫 거래일~)'으로 근사한다. 시점별 상폐 추적·
전체 유니버스 per-T 재구성은 v1 범위 밖(데이터 수집 예산상) — 설계서 §5 '근사' 참조.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.backtest.dart_client import DartClient
from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation
from backend.schemas import OHLCVRow


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _pos(v: Any) -> Decimal | None:
    try:
        d = _d(v)
    except (ArithmeticError, ValueError, TypeError):
        return None
    return d if d.is_finite() and d > 0 else None


class PanelLoader:
    def __init__(self, dart: DartClient | None, cache_dir: Path, market: str = "KR") -> None:
        self._dart = dart
        self._cache_dir = cache_dir
        self._market = market
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_ohlcv(self, ticker: str, start: date, end: date) -> Any:
        """yfinance 로 주가 OHLCV 조회 (네트워크). _ohlcv 캐시 래퍼가 호출한다.
        KR: .KS(KOSPI) 먼저, 빈 결과면 .KQ(KOSDAQ) fallback. 둘 다 빈/오류 → None.
        US: suffix 없이 bare ticker(예: AAPL).
        """
        import yfinance as yf

        _col = {"Open": "시가", "High": "고가", "Low": "저가", "Close": "종가", "Volume": "거래량"}

        def _fetch(suffix: str) -> Any:
            try:
                hist = yf.Ticker(f"{ticker}{suffix}").history(
                    start=start.isoformat(), end=end.isoformat(), auto_adjust=False
                )
                if hist is None or hist.empty:
                    return None
                return hist.rename(columns=_col)
            except Exception:
                return None

        suffixes = (".KS", ".KQ") if self._market == "KR" else ("",)
        for suffix in suffixes:
            result = _fetch(suffix)
            if result is not None:
                return result
        return None

    def _ohlcv(self, ticker: str, start: date, end: date) -> Any:
        """디스크 캐시 래퍼. HIT → 캐시 로드 반환. MISS → _fetch_ohlcv → 저장 → 반환.
        None(상폐/오류) 은 캐시하지 않아 다음 실행에서 재시도.
        캐시 읽기/쓰기 오류는 fail-open — 빌드를 막지 않는다.
        거래대금 컬럼 없음 → _rows 의 종가×거래량 프록시가 자동 적용됨.
        """
        import logging
        import warnings

        import pandas as pd

        cache_subdir = self._cache_dir / "ohlcv"
        cache_subdir.mkdir(parents=True, exist_ok=True)
        key = f"{self._market}_{ticker}_{start.isoformat()}_{end.isoformat()}"
        cache_path = cache_subdir / f"{key}.json"

        # --- 캐시 HIT 시도 ---
        if cache_path.exists():
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    frame = pd.read_json(cache_path, orient="table")
                # DatetimeIndex 복원 확인 — tz는 저장된 상태 그대로 유지(시장 중립).
                # Asia/Seoul tz_convert 는 KR-only 가정이라 US 일봉 날짜를 하루 이동시킬 수 있음.
                if not isinstance(frame.index, pd.DatetimeIndex):
                    frame.index = pd.to_datetime(frame.index, utc=True)
                return frame
            except Exception:
                logging.warning("OHLCV cache read failed for %s — re-fetching", key)

        # --- 캐시 MISS: 네트워크 fetch ---
        frame = self._fetch_ohlcv(ticker, start, end)
        if frame is None:
            return None

        # --- 캐시 저장 (fail-open) ---
        try:
            frame.to_json(cache_path, orient="table", date_format="iso")
        except Exception:
            logging.warning("OHLCV cache write failed for %s — continuing without cache", key)

        return frame

    def _index_ohlcv(self, start: date, end: date) -> Any:
        import yfinance as yf

        index_symbol = "^KS11" if self._market == "KR" else "^GSPC"
        hist = yf.Ticker(index_symbol).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=False
        )
        col_map = {
            "Open": "시가",
            "High": "고가",
            "Low": "저가",
            "Close": "종가",
            "Volume": "거래량",
        }
        return hist.rename(columns=col_map)

    def _fundamentals(self, ticker: str) -> list[AsOfFundamentals]:
        if self._dart is None:
            return []
        # DART 실패(레이트리밋·5xx)는 해당 종목 퀄리티만 비움(fail-open).
        try:
            corp = self._dart.corp_code(ticker)
        except Exception:
            return []
        if corp is None:
            return []
        out: list[AsOfFundamentals] = []
        seen: set[str] = set()
        for yr in range(date.today().year - 6, date.today().year + 1):
            try:
                filing = self._dart.latest_filing_on_or_before(corp, date(yr, 12, 31))
                if not filing or filing["rcept_dt"] in seen:
                    continue
                seen.add(filing["rcept_dt"])
                ratios = self._dart.ratios_for_filing(corp, filing)
            except Exception:
                continue
            rd = filing["rcept_dt"]
            out.append(
                AsOfFundamentals(
                    rcept_date=date(int(rd[:4]), int(rd[4:6]), int(rd[6:8])),
                    roe=ratios.get("roe"),
                    op_margin=ratios.get("op_margin"),
                    rev_growth=ratios.get("rev_growth"),  # v1 성장 프록시(매출성장)
                    gp=ratios.get("gp"),
                )
            )
        return sorted(out, key=lambda f: f.rcept_date)

    @staticmethod
    def _rows(frame: Any) -> tuple[list[OHLCVRow], dict[date, Decimal]]:
        rows: list[OHLCVRow] = []
        turnover: dict[date, Decimal] = {}
        for ts, rec in frame.iterrows():
            d = ts.date()
            close = _d(rec["종가"])
            volume = _d(rec["거래량"])
            rows.append(
                OHLCVRow(
                    date=d,
                    open=_d(rec["시가"]),
                    high=_d(rec["고가"]),
                    low=_d(rec["저가"]),
                    close=close,
                    volume=volume,
                )
            )
            # 거래대금 컬럼이 없으면(pykrx 버전/소스차) 종가×거래량 프록시.
            turnover[d] = _d(rec["거래대금"]) if "거래대금" in rec else close * volume
        return rows, turnover

    def _valuation(self, ticker: str, start: date, end: date) -> Any:
        # pykrx 밸류(PER/PBR)는 KRX 소스가 인증(KRX_ID/KRX_PW) 요구·일시 장애가 잦다.
        # 실패는 value 렌즈를 비우되(fail-open) 전체 빌드를 막지 않는다.
        # ※ KRX(data.krx.co.kr)가 네트워크에서 차단된 환경에서는 항상 None → value 렌즈 비어 있음.
        if self._market == "US":
            # US 종목은 KRX 밸류에이션 불가 — ~150 종목분 실패 라운드트립 방지.
            # _valuation_map(None) → {} 로 처리됨.
            return None
        from pykrx import stock

        try:
            return stock.get_market_fundamental_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
            )
        except Exception:
            return None

    @staticmethod
    def _valuation_map(frame: Any) -> dict[date, Valuation]:
        out: dict[date, Valuation] = {}
        if frame is None or getattr(frame, "empty", True):
            return out
        for ts, rec in frame.iterrows():
            out[ts.date()] = Valuation(per=_pos(rec.get("PER")), pbr=_pos(rec.get("PBR")))
        return out

    def build(self, tickers: list[str], start: date, end: date) -> Panel:
        """tickers 의 시계열을 만들어 Panel 조립. 상장구간=OHLCV 존재구간 근사(위 한계 참조)."""
        series: dict[str, TickerSeries] = {}
        listings: dict[str, tuple[date, date | None]] = {}
        fundamentals: dict[str, list[AsOfFundamentals]] = {}
        for t in tickers:
            frame = self._ohlcv(t, start, end)
            if frame is None or frame.empty:
                continue
            rows, turnover = self._rows(frame)
            series[t] = TickerSeries(
                ticker=t,
                rows=rows,
                turnover_by_date=turnover,
                valuation_by_date=self._valuation_map(self._valuation(t, start, end)),
            )
            listings[t] = (rows[0].date, None)
            fundamentals[t] = self._fundamentals(t)
        idx_rows, _ = self._rows(self._index_ohlcv(start, end))
        return Panel(
            series=series,
            fundamentals=fundamentals,
            listings=listings,
            index_rows=idx_rows,
            market=self._market,
        )


__all__ = ["PanelLoader"]
