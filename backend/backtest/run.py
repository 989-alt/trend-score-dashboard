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
        cands.append(
            build_candidate(
                ticker=ticker,
                rows=rows,
                w52_high=None,
                index_momentum=idx_mom,
                turnover=panel.turnover_asof(ticker, t),
                min_turnover=settings.min_turnover_krw,
                settings=settings,
            )
        )
    eligible = [c for c in cands if c.eligible]
    scored = sc.score_candidates(eligible, settings)
    return sorted(((tk, sv) for tk, (sv, _) in scored.items()), key=lambda x: x[1], reverse=True)


def _fwd_return(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    rows = panel.series[ticker].rows
    future = [r for r in rows if r.date > t]
    if not future:
        return None
    entry = future[0].close
    target = future[min(horizon, len(future)) - 1].close
    return (target - entry) / entry if entry > 0 else None


def _mae(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    rows = panel.series[ticker].rows
    future = [r for r in rows if r.date > t][:horizon]
    if not future:
        return None
    entry = future[0].close
    return metrics.max_adverse_excursion(entry, [r.low for r in future])


def run_backtest(panel: Panel, cfg: BacktestConfig) -> BacktestResult:
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    nav = [Decimal("1")]
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
                a = panel.price_on_or_after(tk, t)
                b = panel.price_on_or_after(tk, nxt)
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
        rebalance_dates=dates,
        event_study=event_study,
        turnover_count=turnover_count,
    )


__all__ = ["BacktestConfig", "BacktestResult", "EventStudyBucket", "run_backtest"]
