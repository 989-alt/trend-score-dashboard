"""시점별 패널 — 모든 조회가 ≤T 만 반환(룩어헤드 가드의 단일 지점)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.schemas import OHLCVRow


@dataclass(frozen=True)
class AsOfFundamentals:
    """접수일(rcept_date) 시점에 공시된 재무 파생값."""

    rcept_date: date
    roe: Decimal | None = None
    op_margin: Decimal | None = None
    rev_growth: Decimal | None = None


@dataclass(frozen=True)
class Valuation:
    """시점별(거래일 기준) 밸류에이션 — pykrx 일자별 PER/PBR."""

    per: Decimal | None = None
    pbr: Decimal | None = None


@dataclass(frozen=True)
class TickerSeries:
    ticker: str
    rows: list[OHLCVRow]
    turnover_by_date: dict[date, Decimal] = field(default_factory=dict)
    valuation_by_date: dict[date, Valuation] = field(default_factory=dict)


@dataclass(frozen=True)
class Panel:
    series: dict[str, TickerSeries]
    fundamentals: dict[str, list[AsOfFundamentals]]
    listings: dict[str, tuple[date, date | None]]
    index_rows: list[OHLCVRow]

    def rows_asof(self, ticker: str, t: date) -> list[OHLCVRow]:
        s = self.series.get(ticker)
        if s is None:
            return []
        return [r for r in s.rows if r.date <= t]

    def index_rows_asof(self, t: date) -> list[OHLCVRow]:
        return [r for r in self.index_rows if r.date <= t]

    def turnover_asof(self, ticker: str, t: date) -> Decimal:
        s = self.series.get(ticker)
        if s is None:
            return Decimal("0")
        if t in s.turnover_by_date:
            return s.turnover_by_date[t]
        rows = [r for r in s.rows if r.date <= t]
        return s.turnover_by_date.get(rows[-1].date, Decimal("0")) if rows else Decimal("0")

    def universe_asof(self, t: date) -> list[str]:
        """t 시점 상장 중인 종목(상장일 ≤ t < 상폐일). 생존편향 차단."""
        out: list[str] = []
        for ticker, (listed, delisted) in self.listings.items():
            if listed <= t and (delisted is None or t < delisted):
                out.append(ticker)
        return sorted(out)

    def fundamentals_asof(self, ticker: str, t: date) -> AsOfFundamentals | None:
        """접수일 ≤ t 중 최신. 없으면 None(fail-open)."""
        items = [f for f in self.fundamentals.get(ticker, []) if f.rcept_date <= t]
        return max(items, key=lambda f: f.rcept_date) if items else None

    def valuation_asof(self, ticker: str, t: date) -> Valuation | None:
        s = self.series.get(ticker)
        if s is None or not s.valuation_by_date:
            return None
        dates = [d for d in s.valuation_by_date if d <= t]
        return s.valuation_by_date[max(dates)] if dates else None

    def price_on_or_after(self, ticker: str, t: date) -> Decimal | None:
        """t 당일 또는 이후 첫 종가(진입 T+1 시가 대용 — 평가/체결용)."""
        s = self.series.get(ticker)
        if s is None:
            return None
        for r in s.rows:
            if r.date >= t:
                return r.close
        return None


__all__ = ["AsOfFundamentals", "Panel", "TickerSeries", "Valuation"]
