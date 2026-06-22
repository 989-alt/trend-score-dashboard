"""평균회귀(RSI2) 슬리브 백테스트 — 올웨더 2단계 게이트.

yfinance 실데이터로 ``backend.sleeves.mean_reversion.simulate`` 를 종목별로 돌려 거래를 풀링하고,
승률·기대값·총복리·MDD 와 청산사유 분포를 buy&hold 와 비교한다. 데이터 로딩은 backtest_10y 의
유니버스·다운로드 헬퍼를 재사용(생존편향·수정주가 등 한계는 동일 — 해석 시 유의).

실행: uv run python scripts/backtest_mean_reversion.py [--market both|kr|us] [--years 5]
                                                       [--max-tickers N]
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
from backend.regime import classify_regime
from backend.schemas import OHLCVRow
from backend.sleeves.mean_reversion import MeanRevParams, Trade, simulate, summarize

_MIN_BARS = 210  # MA200 워밍업 + 여유
_INDEX = {"KR": "^KS11", "US": "^GSPC"}
_COST_BPS = {"KR": Decimal("41"), "US": Decimal("10")}  # 한쪽 bps(왕복=×2)


def _buy_hold(rows: list[OHLCVRow]) -> Decimal:
    if not rows or rows[0].close <= 0:
        return Decimal("0")
    return rows[-1].close / rows[0].close - Decimal("1")


def _pct(x: object) -> str:
    return f"{Decimal(str(x)) * 100:+.2f}%"


def _chop_dates(index_rows: list[OHLCVRow]) -> frozenset[date]:
    """지수 일봉으로 날짜별 레짐 판정 → CHOP_VOL 인 날짜 집합(히스테리시스 반영)."""
    out: set[date] = set()
    prev = None
    for k in range(len(index_rows)):
        r = classify_regime(index_rows[: k + 1], prev=prev)
        prev = r.regime
        if r.regime == "CHOP_VOL":
            out.add(index_rows[k].date)
    return frozenset(out)


def run_market(market: str, uni: dict[str, str], years: int, session: object | None) -> None:
    end = date.today()
    start = end - timedelta(days=int(years * 365.25) + 400)  # +MA200 워밍업 버퍼
    symbols = [c + suf for c, suf in uni.items()] if market == "KR" else list(uni)
    print(f"\n=== {market} 평균회귀 백테스트 ({len(symbols)}종목, {years}년) ===", flush=True)
    params = MeanRevParams(cost_bps=_COST_BPS[market])

    index_rows = bt._index_rows(_INDEX[market], start, end, session)
    chop = _chop_dates(index_rows)
    ndays = len({r.date for r in index_rows})
    frac = Decimal(len(chop)) / Decimal(max(ndays, 1))
    print(f"지수일수 {ndays}  |  CHOP_VOL {len(chop)}일 ({_pct(frac)} 기간)", flush=True)

    data = bt._download(symbols, start, end, session)
    gated: list[Trade] = []
    ungated: list[Trade] = []
    for _sym, frame in data.items():
        rows = bt._rows_from_yf(frame)
        if len(rows) < _MIN_BARS:
            continue
        gated.extend(simulate(rows, params, allowed_dates=chop))
        ungated.extend(simulate(rows, params))

    for label, trades in (("레짐게이트(CHOP만)", gated), ("무게이트(전구간)", ungated)):
        s = summarize(trades)
        print(f"\n[{label}] 거래수 {s['n']}")
        if s["n"]:
            print(f"  승률 {_pct(s['win_rate'])}  |  기대값/거래 {_pct(s['avg_ret'])}")
            print(f"  청산사유 {dict(Counter(t.reason for t in trades))}")
            edge = s["win_rate"] > Decimal("0.5") and s["avg_ret"] > Decimal("0")
            print(f"  게이트(양의 기대값): {'PASS' if edge else 'FAIL'}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 유니코드 가드
    p = argparse.ArgumentParser(description="평균회귀(RSI2) 슬리브 백테스트")
    p.add_argument("--market", default="both", choices=["both", "kr", "us"])
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--max-tickers", type=int, default=0, help="0=전체. 빠른 검증용 상한.")
    args = p.parse_args(argv)
    session = bt._session()

    if args.market in ("both", "us"):
        uni = bt.us_universe(args.max_tickers or 60)
        run_market("US", uni, args.years, session)
    if args.market in ("both", "kr"):
        uni = bt.kr_universe(100, 50)
        if args.max_tickers:
            uni = dict(list(uni.items())[: args.max_tickers])
        run_market("KR", uni, args.years, session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
