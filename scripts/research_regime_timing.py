"""레짐 타이밍 재설계 연구 — 인버스 슬리브를 여러 '하락 신호'로 재백테스트해 비교.

기존 MA200+ADX 레짐은 지연돼 인버스가 하락 끝물에 진입·반등에 물렸다(기대값 −2.34%). 더 빠른
하락 신호(MA50/MA20 이탈·드로다운·모멘텀·데드크로스)로 바꾸면 개선되는지 지수 일봉만으로 실증한다
(인버스는 지수 1X 근사라 종목 다운로드 불필요 → 빠름).

실행: uv run python scripts/research_regime_timing.py [--market both|kr|us] [--years 7]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_10y as bt
from backend.schemas import OHLCVRow
from backend.sleeves.inverse import InverseParams, simulate_inverse
from backend.sleeves.mean_reversion import summarize

_INDEX = {"KR": "^KS11", "US": "^GSPC"}
_COST = {"KR": Decimal("41"), "US": Decimal("10")}


def _pct(x: object) -> str:
    return f"{Decimal(str(x)) * 100:+.2f}%"


def _sma(closes: list[Decimal], k: int, w: int) -> Decimal | None:
    if k + 1 < w:
        return None
    return sum(closes[k + 1 - w : k + 1], Decimal("0")) / Decimal(w)


def _down_sets(rows: list[OHLCVRow]) -> dict[str, frozenset[date]]:
    """여러 하락신호 규칙 → 각 규칙의 '하락' 날짜집합. baseline 은 None(내부 MA200+ADX)."""
    closes = [r.close for r in rows]
    highs = [r.high for r in rows]
    n = len(rows)
    ma20 = [_sma(closes, k, 20) for k in range(n)]
    ma50 = [_sma(closes, k, 50) for k in range(n)]
    ma200 = [_sma(closes, k, 200) for k in range(n)]

    def below(ma: list[Decimal | None]) -> frozenset[date]:
        return frozenset(rows[k].date for k in range(n) if ma[k] is not None and closes[k] < ma[k])

    def drawdown(pct: Decimal, win: int) -> frozenset[date]:
        out = set()
        for k in range(n):
            hi = max(highs[max(0, k - win + 1) : k + 1], default=None)
            if hi and closes[k] < hi * (Decimal("1") - pct):
                out.add(rows[k].date)
        return frozenset(out)

    def mom_neg(win: int) -> frozenset[date]:
        return frozenset(
            rows[k].date
            for k in range(n)
            if k >= win and closes[k - win] > 0 and closes[k] < closes[k - win]
        )

    def death() -> frozenset[date]:
        return frozenset(
            rows[k].date
            for k in range(n)
            if ma50[k] is not None and ma200[k] is not None and ma50[k] < ma200[k]
        )

    return {
        "ma50_이탈": below(ma50),
        "ma20_이탈": below(ma20),
        "dd10%_60일고점": drawdown(Decimal("0.10"), 60),
        "mom20_음수": mom_neg(20),
        "death_cross(MA50<MA200)": death(),
    }


def run_market(market: str, years: int, session: object | None) -> None:
    end = date.today()
    start = end - timedelta(days=int(years * 365.25) + 400)
    rows = bt._index_rows(_INDEX[market], start, end, session)
    params = InverseParams(cost_bps=_COST[market])
    print(f"\n=== {market} 인버스 타이밍 연구 (지수일수 {len(rows)}, {years}년) ===", flush=True)

    # baseline: 내부 MA200+ADX DOWN
    base = simulate_inverse(rows, params)
    sets = {"baseline(MA200+ADX)": None, **_down_sets(rows)}
    for name, dd in sets.items():
        trades = base if dd is None else simulate_inverse(rows, params, down_dates=dd)
        s = summarize(trades)
        edge = bool(s["n"]) and s["win_rate"] > Decimal("0.5") and s["avg_ret"] > Decimal("0")
        flag = "PASS" if edge else "FAIL"
        days = "" if dd is None else f" | 하락일 {len(dd)}"
        line = f"  [{name:24}] 거래 {s['n']:>3}{days}"
        if s["n"]:
            line += (
                f" | 승률 {_pct(s['win_rate'])} | 기대값 {_pct(s['avg_ret'])}"
                f" | 총복리 {_pct(s['total_return'])} | {flag}"
            )
            line += f" | 사유 {dict(Counter(t.reason for t in trades))}"
        print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="레짐 타이밍 재설계 연구(인버스)")
    p.add_argument("--market", default="both", choices=["both", "kr", "us"])
    p.add_argument("--years", type=int, default=7)
    args = p.parse_args(argv)
    session = bt._session()
    for mkt in ("US", "KR") if args.market == "both" else [args.market.upper()]:
        run_market(mkt, args.years, session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
