"""리밸런스 루프 + 포트폴리오 시뮬 + 이벤트스터디. 결정론.

각 리밸런스일 T: universe_asof → build_candidate(rows≤T) → scoring(무수정) → 상위 N 등가중.
forward-return 은 T 이후 가격으로만 평가(룩어헤드 0). 비용은 회전분에 bps 차감.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from backend import scoring as sc
from backend.backtest import metrics
from backend.backtest.panel import Panel
from backend.config import Settings, get_settings
from backend.factors import build_candidate

_REBAL_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    rebalance: str = "weekly"
    top_n: int = 20
    cost_bps: Decimal = Decimal("41")
    preset: str = "baseline"
    forward_horizons: tuple[int, ...] = (5, 20, 60)


@dataclass(frozen=True)
class EventStudyBucket:
    monotonicity: Decimal
    mae: Decimal
    win_rate: Decimal
    n: int


@dataclass(frozen=True)
class BacktestResult:
    portfolio_nav: list[Decimal]
    benchmark_nav: list[Decimal]
    rebalance_dates: list[date]
    event_study: dict[int, EventStudyBucket]
    turnover_count: int


def _rebalance_dates(panel: Panel, cfg: BacktestConfig) -> list[date]:
    trading = sorted(
        {r.date for s in panel.series.values() for r in s.rows if cfg.start <= r.date <= cfg.end}
    )
    if not trading:
        return []
    step = _REBAL_DAYS[cfg.rebalance]
    out: list[date] = []
    last: date | None = None
    for d in trading:
        if last is None or (d - last) >= timedelta(days=step):
            out.append(d)
            last = d
    return out


def _index_momentum(panel: Panel, t: date, settings: Settings) -> Decimal:
    idx = panel.index_rows_asof(t)[-settings.lookback_days :]
    return sc.compute_momentum(idx) if len(idx) >= 2 else Decimal("0")


def _score_at(panel: Panel, t: date, settings: Settings) -> list[tuple[str, Decimal]]:
    idx_mom = _index_momentum(panel, t, settings)
    cands: list[sc.Candidate] = []
    for ticker in panel.universe_asof(t):
        rows = panel.rows_asof(ticker, t)
        if len(rows) < settings.ma200_window:
            continue
        # I1: 라이브와 동일하게 '트레일링 52주(≈252거래일) 고가'를 분모로 사용
        # (None → 전체이력 최고가 fallback 은 라이브-백테스트 score drift 유발).
        trailing = rows[-252:]
        w52_high = max((r.high for r in trailing), default=None)
        cands.append(
            build_candidate(
                ticker=ticker,
                rows=rows,
                w52_high=w52_high,
                index_momentum=idx_mom,
                turnover=panel.turnover_asof(ticker, t),
                min_turnover=settings.min_turnover_krw,
                settings=settings,
            )
        )
    eligible = [c for c in cands if c.eligible]
    scored = sc.score_candidates(eligible, settings)
    return sorted(((tk, sv) for tk, (sv, _) in scored.items()), key=lambda x: x[1], reverse=True)


def _index_price_on_or_after(panel: Panel, d: date) -> Decimal | None:
    for r in panel.index_rows:
        if r.date >= d:
            return r.close
    return None


def _fwd_return(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    """entry=T+1 첫 봉, horizon 봉 뒤 종가 수익률. horizon 봉 미확보 시 None(무음절단 금지)."""
    future = [r for r in panel.series[ticker].rows if r.date > t]
    if len(future) <= horizon:
        return None
    entry = future[0].close
    if entry <= 0:
        return None
    return (future[horizon].close - entry) / entry


def _mae(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    """fwd-return 과 동일 구간(entry 후 1..horizon 봉)의 저가 기준 최대역행.

    horizon 미확보 시 None."""
    future = [r for r in panel.series[ticker].rows if r.date > t]
    if len(future) <= horizon:
        return None
    entry = future[0].close
    return metrics.max_adverse_excursion(entry, [r.low for r in future[1 : horizon + 1]])


def run_backtest(panel: Panel, cfg: BacktestConfig) -> BacktestResult:
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    nav = [Decimal("1")]
    benchmark_nav: list[Decimal] = [Decimal("1")]
    held: set[str] = set()
    turnover_count = 0
    es_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_fwd: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_mae: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}

    for i, t in enumerate(dates):
        ranked = _score_at(panel, t, settings)
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        for tk, sv in ranked:
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, tk, t, h)
                mae = _mae(panel, tk, t, h)
                if fr is not None and mae is not None:
                    es_scores[h].append(sv)
                    es_fwd[h].append(fr)
                    es_mae[h].append(mae)
        if i + 1 < len(dates) and picks:
            nxt = dates[i + 1]
            rets: list[Decimal] = []
            for tk in picks:
                a = panel.price_on_or_after(tk, t + timedelta(days=1))
                b = panel.price_on_or_after(tk, nxt + timedelta(days=1))
                if a and b and a > 0:
                    rets.append((b - a) / a)
            if rets:
                gross = sum(rets, Decimal("0")) / Decimal(len(rets))
                churn = len(set(picks) ^ held)
                cost = (cfg.cost_bps / Decimal("10000")) * (
                    Decimal(churn) / Decimal(max(len(picks), 1))
                )
                nav.append(nav[-1] * (Decimal("1") + gross - cost))
                turnover_count += churn
                held = set(picks)
                ia = _index_price_on_or_after(panel, t + timedelta(days=1))
                ib = _index_price_on_or_after(panel, nxt + timedelta(days=1))
                bench_ret = (ib / ia - Decimal("1")) if (ia and ib and ia > 0) else Decimal("0")
                benchmark_nav.append(benchmark_nav[-1] * (Decimal("1") + bench_ret))

    event_study = {
        h: EventStudyBucket(
            monotonicity=metrics.spearman_monotonicity(es_scores[h], es_fwd[h]),
            mae=(
                sum(es_mae[h], Decimal("0")) / Decimal(len(es_mae[h]))
                if es_mae[h]
                else Decimal("0")
            ),
            win_rate=metrics.win_rate(es_fwd[h]),
            n=len(es_fwd[h]),
        )
        for h in cfg.forward_horizons
    }
    return BacktestResult(
        portfolio_nav=nav,
        benchmark_nav=benchmark_nav,
        rebalance_dates=dates,
        event_study=event_study,
        turnover_count=turnover_count,
    )


__all__ = ["BacktestConfig", "BacktestResult", "EventStudyBucket", "run_backtest"]


def main(argv: list[str] | None = None) -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔 유니코드 인쇄 가드

    import argparse
    import json
    import os
    from datetime import datetime
    from pathlib import Path

    from backend.backtest.dart_client import DartClient
    from backend.backtest.loader import PanelLoader
    from backend.backtest.report import render_json, render_markdown

    p = argparse.ArgumentParser(description="KR 백테스트 검증 하니스 (오프라인)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--rebalance", default="weekly", choices=list(_REBAL_DAYS))
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--cost-bps", default="41")
    p.add_argument("--preset", default="baseline", choices=["baseline", "quality_tilt"])
    p.add_argument("--tickers", default="", help="콤마구분 6자리 코드. 비우면 유니버스 자동(느림)")
    p.add_argument("--out", default="data/backtest")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    dart_key = os.environ.get("DART_API_KEY")
    loader = PanelLoader(
        dart=DartClient(dart_key) if dart_key else None, cache_dir=Path(args.out) / "cache"
    )
    tickers = [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        from pykrx import stock

        tickers = [
            str(t).zfill(6)
            for t in stock.get_market_ticker_list(args.end.replace("-", ""), market="KOSPI")
        ]
    panel = loader.build(tickers, start, end)
    cfg = BacktestConfig(
        start=start,
        end=end,
        rebalance=args.rebalance,
        top_n=args.top_n,
        cost_bps=Decimal(args.cost_bps),
        preset=args.preset,
    )
    result = run_backtest(panel, cfg)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"report_{args.preset}.md").write_text(
        render_markdown(result, cfg), encoding="utf-8"
    )
    (out_dir / f"report_{args.preset}.json").write_text(
        json.dumps(render_json(result, cfg), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_markdown(result, cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
