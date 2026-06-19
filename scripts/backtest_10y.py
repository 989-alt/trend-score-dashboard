"""10년 추세추종 전략 백테스트 — yfinance 데이터로 기존 run_backtest 엔진 구동 (국장·미장).

엔진(backend.backtest.run_backtest)·점수(backend.scoring)는 무수정 재사용. pykrx 로더가
KRX 차단으로 죽어 종목별 과거가를 못 받으므로, yfinance 로 OHLCV 를 공급한다.
- 국장(KR): 네이버 시총순위(ETF/우선주 제외) 보통주, .KS/.KQ, 벤치마크 KOSPI(^KS11)+KOSDAQ(^KQ11).
- 미장(US): _US_LIQUID(S&P500 대형주), 벤치마크 S&P500(^GSPC). 거래대금 임계는 USD 로 전환.

한계(결과 해석 시 필수):
- **생존편향**: '현재' 유니버스로 과거를 돌린다(상폐·쇠퇴 종목 누락) → 낙관 편향.
- yfinance **수정주가**(분할·배당 반영). 재무(DART)·밸류 렌즈 제외 → baseline 프리셋만.
- 거래비용 KR 41bps / US 10bps(회전분) 반영.

실행: uv run python scripts/backtest_10y.py [--market both|kr|us] [--years 10] [--rebalance monthly]
"""

from __future__ import annotations

import argparse
import os
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
from backend.market_data import _US_LIQUID, LiveProvider
from backend.schemas import OHLCVRow


def _d(v: object) -> Decimal:
    return Decimal(str(v))


def _session() -> object | None:
    try:
        from curl_cffi import requests as cffi

        return cffi.Session(impersonate="chrome")
    except Exception:
        return None


def kr_universe(kospi_n: int, kosdaq_n: int) -> dict[str, str]:
    """{6자리코드: yfinance suffix(.KS/.KQ)} — 라이브 유니버스 로직 재사용(ETF/우선주 제외)."""
    tmp = Path(tempfile.mkdtemp(prefix="bt-uni-"))
    lp = LiveProvider(Settings(db_path=tmp / "t.db", kis_token_path=tmp / ".t.json"))
    etf = lp._naver_etf_codes()
    out: dict[str, str] = {}
    for sosok, quota, suffix in ((0, kospi_n, ".KS"), (1, kosdaq_n, ".KQ")):
        for code, _name in lp._naver_market_rows(sosok, quota, etf):
            out.setdefault(code, suffix)
    return out


def us_universe(n: int) -> dict[str, str]:
    """{티커: ''} — _US_LIQUID(S&P500 대형주) 상위 n. yfinance 는 미국 티커에 suffix 불필요."""
    return {t: "" for t in _US_LIQUID[:n]}


def _rows_from_yf(frame: object) -> list[OHLCVRow]:
    rows: list[OHLCVRow] = []
    for ts, rec in frame.iterrows():  # type: ignore[attr-defined]
        o, h, low, c, v = rec["Open"], rec["High"], rec["Low"], rec["Close"], rec["Volume"]
        if any(x != x for x in (o, h, low, c, v)) or float(c) <= 0:  # NaN·비양수 스킵
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


def _pct(x: Decimal) -> str:
    return f"{x * 100:+.2f}%"


def run_market(
    market: str,
    label: str,
    uni: dict[str, str],
    primary: tuple[str, str],
    alts: list[tuple[str, str]],
    cost_bps: str,
    min_turnover: str,
    args: argparse.Namespace,
    session: object | None,
) -> None:
    """단일 시장 백테스트 + 리포트. primary/alts = (이름, yfinance 지수심볼)."""
    end = date.today()
    start = end - timedelta(days=int(args.years * 365.25) + 400)  # +버퍼(MA200 워밍업)
    eval_start = end - timedelta(days=int(args.years * 365.25))

    print(f"\n[{label}] 유니버스 {len(uni)}종목 · yfinance OHLCV 다운로드…", file=sys.stderr)
    raw = _download([c + s for c, s in uni.items()], start, end, session)
    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    for code, suffix in uni.items():
        sub = raw.get(code + suffix)
        if sub is None:
            continue
        rows = _rows_from_yf(sub)
        if len(rows) < 60:
            continue
        series[code] = TickerSeries(
            ticker=code, rows=rows, turnover_by_date={r.date: r.close * r.volume for r in rows}
        )
        listings[code] = (rows[0].date, None)
    idx_rows = _index_rows(primary[1], start, end, session)
    panel = Panel(series=series, fundamentals={}, listings=listings, index_rows=idx_rows)

    cfg = BacktestConfig(
        start=eval_start,
        end=end,
        rebalance=args.rebalance,
        top_n=args.top_n,
        cost_bps=Decimal(cost_bps),
        forward_horizons=(20,),
    )
    # 엔진은 min_turnover_krw 를 읽으므로, 시장별 임계를 env 로 주입(get_settings 가 픽업).
    os.environ["MIN_TURNOVER_KRW"] = min_turnover
    res = run_backtest(panel, cfg)

    years = _d((end - eval_start).days / 365.25)
    p_nav, b_nav = res.portfolio_nav, res.benchmark_nav
    port_cagr = metrics.cagr(p_nav[0], p_nav[-1], years=years)
    bench_cagr = metrics.cagr(b_nav[0], b_nav[-1], years=years)

    def idx_cagr(symbol: str) -> Decimal:
        rows = _index_rows(symbol, start, end, session)
        ev = [r for r in rows if r.date >= eval_start]
        return (
            metrics.cagr(ev[0].close, ev[-1].close, years=years) if len(ev) >= 2 else Decimal("0")
        )

    mdd = metrics.max_drawdown(p_nav)
    es20 = res.event_study.get(20)

    print("\n" + "=" * 64)
    print(f"  추세추종 {args.years}년 백테스트 — {label}  ({eval_start} ~ {end})")
    print("=" * 64)
    print(
        f"  유니버스 {len(series)}종목 · {args.rebalance} {len(res.rebalance_dates)}회"
        f" · 동일가중 top{args.top_n} · 비용 {cost_bps}bps"
    )
    print("-" * 64)
    print(f"  포트폴리오 총수익      : {_pct(p_nav[-1] - 1)}  (NAV {p_nav[-1]:.3f})")
    print(f"  포트폴리오 CAGR        : {_pct(port_cagr)}")
    print(f"  {primary[0]:<10} CAGR    : {_pct(bench_cagr)}")
    for name, symbol in alts:
        print(f"  {name:<10} CAGR    : {_pct(idx_cagr(symbol))}  (buy&hold)")
    print(f"  초과수익(vs {primary[0]}) : {_pct(port_cagr - bench_cagr)}/년")
    print(f"  최대낙폭(MDD)          : {_pct(mdd)}")
    if es20:
        print(
            f"  20일 승률 / 단조성     : {_pct(es20.win_rate)} / {es20.monotonicity} (n={es20.n})"
        )
    print("=" * 64)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="10년 추세추종 백테스트 (yfinance, 국장·미장)")
    p.add_argument("--market", default="both", choices=["both", "kr", "us"])
    p.add_argument("--years", type=int, default=10)
    p.add_argument("--rebalance", default="monthly", choices=["weekly", "biweekly", "monthly"])
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--kospi", type=int, default=200)
    p.add_argument("--kosdaq", type=int, default=100)
    p.add_argument("--us", type=int, default=400, help="미장 유니버스 상위 N(_US_LIQUID)")
    args = p.parse_args()
    session = _session()

    if args.market in ("kr", "both"):
        run_market(
            "KR",
            "국장(KR)",
            kr_universe(args.kospi, args.kosdaq),
            ("KOSPI", "^KS11"),
            [("KOSDAQ", "^KQ11")],
            cost_bps="41",
            min_turnover="10000000000",  # 100억 KRW
            args=args,
            session=session,
        )
    if args.market in ("us", "both"):
        run_market(
            "US",
            "미장(US)",
            us_universe(args.us),
            ("S&P500", "^GSPC"),
            [],
            cost_bps="10",
            min_turnover="30000000",  # 3천만 USD
            args=args,
            session=session,
        )
    print(
        "\n  ※ 생존편향(현재 유니버스로 과거 평가)·수정주가·재무렌즈 제외 — 결과는 낙관 편향 가능",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
