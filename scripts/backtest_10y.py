"""10년 추세추종 전략 백테스트 — yfinance 데이터로 기존 run_backtest 엔진 구동.

엔진(backend.backtest.run_backtest)·점수(backend.scoring)는 무수정 재사용. pykrx 로더가
KRX 차단으로 죽어 종목별 과거가를 못 받으므로, yfinance(.KS/.KQ)로 OHLCV 를 공급한다.
유니버스는 라이브와 동일 로직(네이버 시총순위 · ETF/우선주 제외)을 재사용.

한계(결과 해석 시 필수):
- **생존편향**: '현재' 시총 상위 종목으로 과거 10년을 돌린다(상폐·쇠퇴 종목 누락) → 낙관 편향.
- 가격은 yfinance **수정주가**(분할·배당 반영) — 라이브는 KIS 원주가라 단기 미세 차이 가능.
- 재무(DART)·밸류(PER/PBR) 렌즈는 비움 → baseline 프리셋만(quality_tilt 제외).
- 거래비용 41bps(회전분) 반영.

실행: uv run python scripts/backtest_10y.py [--years 10] [--rebalance monthly] [--kospi N]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest import metrics
from backend.backtest.panel import Panel, TickerSeries
from backend.backtest.run import BacktestConfig, run_backtest
from backend.config import Settings
from backend.market_data import LiveProvider
from backend.schemas import OHLCVRow


def _d(v: object) -> Decimal:
    return Decimal(str(v))


def _session() -> object | None:
    try:
        from curl_cffi import requests as cffi

        return cffi.Session(impersonate="chrome")
    except Exception:
        return None


def naver_universe(kospi_n: int, kosdaq_n: int) -> dict[str, str]:
    """{6자리코드: yfinance suffix(.KS/.KQ)} — 라이브 유니버스 로직 재사용(ETF/우선주 제외)."""
    tmp = Path(tempfile.mkdtemp(prefix="bt-uni-"))
    lp = LiveProvider(Settings(db_path=tmp / "t.db", kis_token_path=tmp / ".t.json"))
    etf = lp._naver_etf_codes()
    out: dict[str, str] = {}
    for sosok, quota, suffix in ((0, kospi_n, ".KS"), (1, kosdaq_n, ".KQ")):
        for code, _name in lp._naver_market_rows(sosok, quota, etf):
            out.setdefault(code, suffix)
    return out


def _rows_from_yf(frame: object) -> list[OHLCVRow]:
    rows: list[OHLCVRow] = []
    for ts, rec in frame.iterrows():  # type: ignore[attr-defined]
        o, h, low, c, v = rec["Open"], rec["High"], rec["Low"], rec["Close"], rec["Volume"]
        if any(x != x for x in (o, h, low, c, v)):  # NaN 스킵
            continue
        if float(c) <= 0:
            continue
        rows.append(
            OHLCVRow(date=ts.date(), open=_d(o), high=_d(h), low=_d(low), close=_d(c), volume=_d(v))
        )
    return rows


def _download(symbols: list[str], start: date, end: date, session: object | None) -> dict:
    import yfinance as yf

    data: dict = {}
    for i in range(0, len(symbols), 60):  # 60종목씩 배치(429 회피)
        batch = symbols[i : i + 60]
        df = yf.download(
            batch,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=True,
            session=session,
        )
        for sym in batch:
            try:
                sub = df[sym] if len(batch) > 1 else df
                if sub is not None and not sub.empty:
                    data[sym] = sub
            except Exception:
                continue
        print(f"  ...{min(i + 60, len(symbols))}/{len(symbols)} 다운로드", file=sys.stderr)
    return data


def _index_rows(symbol: str, start: date, end: date, session: object | None) -> list[OHLCVRow]:
    import yfinance as yf

    hist = yf.Ticker(symbol, session=session).history(
        start=start.isoformat(), end=end.isoformat(), auto_adjust=False
    )
    rows: list[OHLCVRow] = []
    for ts, rec in hist.iterrows():
        o, h, low, c, v = rec["Open"], rec["High"], rec["Low"], rec["Close"], rec["Volume"]
        if any(x != x for x in (o, h, low, c, v)) or float(c) <= 0:  # NaN(휴장)·비양수 스킵
            continue
        rows.append(
            OHLCVRow(date=ts.date(), open=_d(o), high=_d(h), low=_d(low), close=_d(c), volume=_d(v))
        )
    return rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="10년 추세추종 백테스트 (yfinance)")
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--rebalance", default="monthly", choices=["weekly", "biweekly", "monthly"])
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--kospi", type=int, default=200)
    p.add_argument("--kosdaq", type=int, default=100)
    args = p.parse_args()

    end = date.today()
    start = end - timedelta(days=int(args.years * 365.25) + 400)  # +버퍼(MA200 워밍업)
    eval_start = end - timedelta(days=int(args.years * 365.25))
    session = _session()

    print(
        f"유니버스 수집(네이버 시총순위, ETF/우선주 제외) KOSPI {args.kospi}+KOSDAQ {args.kosdaq}…"
    )
    uni = naver_universe(args.kospi, args.kosdaq)
    symbols = [c + s for c, s in uni.items()]
    print(f"  유니버스 {len(symbols)}종목. yfinance 10년+버퍼 OHLCV 다운로드…")
    raw = _download(symbols, start, end, session)

    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    for code, suffix in uni.items():
        sub = raw.get(code + suffix)
        if sub is None:
            continue
        rows = _rows_from_yf(sub)
        if len(rows) < 60:  # 최소 이력(스코어 게이트는 MA200=200봉, 여기선 패널 적재 기준)
            continue
        series[code] = TickerSeries(
            ticker=code, rows=rows, turnover_by_date={r.date: r.close * r.volume for r in rows}
        )
        listings[code] = (rows[0].date, None)
    print(f"  패널 적재 {len(series)}종목. 지수(^KS11/^KQ11) 수집…")

    ks_rows = _index_rows("^KS11", start, end, session)
    kq_rows = _index_rows("^KQ11", start, end, session)
    panel = Panel(series=series, fundamentals={}, listings=listings, index_rows=ks_rows)

    cfg = BacktestConfig(
        start=eval_start,
        end=end,
        rebalance=args.rebalance,
        top_n=args.top_n,
        cost_bps=Decimal("41"),
        forward_horizons=(20,),
    )
    print(f"백테스트 실행: {eval_start}~{end}, {args.rebalance} 리밸런스, top{args.top_n}…")
    res = run_backtest(panel, cfg)

    years = _d((end - eval_start).days / 365.25)
    p_nav, b_nav = res.portfolio_nav, res.benchmark_nav
    port_cagr = metrics.cagr(p_nav[0], p_nav[-1], years=years)
    kospi_cagr = metrics.cagr(b_nav[0], b_nav[-1], years=years)

    def _index_cagr(rows: list[OHLCVRow]) -> Decimal:
        ev = [r for r in rows if r.date >= eval_start]
        return (
            metrics.cagr(ev[0].close, ev[-1].close, years=years) if len(ev) >= 2 else Decimal("0")
        )

    kosdaq_cagr = _index_cagr(kq_rows)
    kospi_bh = _index_cagr(ks_rows)
    mdd = metrics.max_drawdown(p_nav)
    es20 = res.event_study.get(20)

    def pct(x: Decimal) -> str:
        return f"{x * 100:+.2f}%"

    print("\n" + "=" * 64)
    print(f"  추세추종 전략 10년 백테스트 ({eval_start} ~ {end})")
    print("=" * 64)
    print(
        f"  유니버스 {len(series)}종목(보통주) · {args.rebalance} {len(res.rebalance_dates)}회"
        f" · 동일가중 top{args.top_n} · 비용 41bps"
    )
    print("-" * 64)
    print(f"  포트폴리오 총수익     : {pct(p_nav[-1] - 1)}  (NAV {p_nav[-1]:.3f})")
    print(f"  포트폴리오 CAGR       : {pct(port_cagr)}")
    print(f"  KOSPI  CAGR(벤치마크) : {pct(kospi_cagr)}  (buy&hold {pct(kospi_bh)})")
    print(f"  KOSDAQ CAGR(buy&hold) : {pct(kosdaq_cagr)}")
    print(f"  초과수익(vs KOSPI)    : {pct(port_cagr - kospi_cagr)}/년")
    print(f"  최대낙폭(MDD)         : {pct(mdd)}")
    if es20:
        print(f"  20일 승률 / 단조성    : {pct(es20.win_rate)} / {es20.monotonicity} (n={es20.n})")
    print("=" * 64)
    print("  ※ 생존편향(현재 유니버스로 과거 평가)·수정주가·재무렌즈 제외 — 결과는 낙관 편향 가능")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
