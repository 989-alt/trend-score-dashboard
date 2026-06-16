"""pykrx(가격·밸류) + DART(재무) + ^KS11 → Panel.

Decimal 전면. pykrx DataFrame 한글 컬럼(시가/고가/저가/종가/거래량/거래대금)을 OHLCVRow 로 변환.

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
    def __init__(self, dart: DartClient | None, cache_dir: Path) -> None:
        self._dart = dart
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _ohlcv(self, ticker: str, start: date, end: date) -> Any:
        from pykrx import stock

        return stock.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
        )

    def _index_ohlcv(self, start: date, end: date) -> Any:
        import yfinance as yf

        hist = yf.Ticker("^KS11").history(
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
        corp = self._dart.corp_code(ticker)
        if corp is None:
            return []
        out: list[AsOfFundamentals] = []
        seen: set[str] = set()
        for yr in range(date.today().year - 6, date.today().year + 1):
            filing = self._dart.latest_filing_on_or_before(corp, date(yr, 12, 31))
            if not filing or filing["rcept_dt"] in seen:
                continue
            seen.add(filing["rcept_dt"])
            ratios = self._dart.ratios_for_filing(corp, filing)
            rd = filing["rcept_dt"]
            out.append(
                AsOfFundamentals(
                    rcept_date=date(int(rd[:4]), int(rd[4:6]), int(rd[6:8])),
                    roe=ratios.get("roe"),
                    op_margin=ratios.get("op_margin"),
                    rev_growth=ratios.get("rev_growth"),  # v1 성장 프록시(매출성장)
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
            series=series, fundamentals=fundamentals, listings=listings, index_rows=idx_rows
        )


__all__ = ["PanelLoader"]
