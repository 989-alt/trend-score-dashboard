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
    # 유의성 모듈 (Layer B) 결정론 시드 및 반복 횟수
    bootstrap_seed: int = 12345
    n_resamples: int = 1000
    n_perms: int = 1000


@dataclass(frozen=True)
class EventStudyBucket:
    monotonicity: Decimal
    mae: Decimal
    win_rate: Decimal
    n: int
    # Layer B: 유의성 필드 — 기본값 제공하므로 factor_study 구축 코드 변경 불필요
    mono_ci_lo: Decimal = Decimal("0")
    mono_ci_hi: Decimal = Decimal("0")
    mono_pvalue: Decimal = Decimal("1")
    mae_ci_lo: Decimal = Decimal("0")
    mae_ci_hi: Decimal = Decimal("0")


@dataclass(frozen=True)
class BacktestResult:
    portfolio_nav: list[Decimal]
    benchmark_nav: list[Decimal]
    rebalance_dates: list[date]
    event_study: dict[int, EventStudyBucket]
    factor_study: dict[str, dict[int, EventStudyBucket]]
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


_QUALITY_FACTORS = ("roe", "op_margin", "rev_growth")
_VALUE_FACTORS = ("per", "pbr")
_STUDY_FACTORS = _QUALITY_FACTORS + _VALUE_FACTORS


def _quality_norm(panel: Panel, tickers: list[str], t: date) -> dict[str, Decimal]:
    """후보군 횡단면 퀄리티 합성 순위점수(0~1). as-of 퀄리티 팩터 평균의 min-max."""
    raw: dict[str, Decimal] = {}
    for tk in tickers:
        f = panel.fundamentals_asof(tk, t)
        if f is None:
            continue
        vals = [v for v in (f.roe, f.op_margin, f.rev_growth) if v is not None]
        if vals:
            raw[tk] = sum(vals, Decimal("0")) / Decimal(len(vals))
    if not raw:
        return {}
    lo, hi = min(raw.values()), max(raw.values())
    return {tk: sc.min_max_norm(v, lo, hi) for tk, v in raw.items()}


def _score_at(
    panel: Panel, t: date, settings: Settings, preset: str = "baseline"
) -> list[tuple[str, Decimal]]:
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
    base = {tk: sv for tk, (sv, _) in scored.items()}
    if preset == "quality_tilt":
        qnorm = _quality_norm(panel, list(base.keys()), t)
        w = Decimal("0.08")  # 리웨이트 스펙 prior — 퀄리티 틸트 가중
        base = {
            tk: sv * (Decimal("1") - w) + qnorm.get(tk, Decimal("0.5")) * w
            for tk, sv in base.items()
        }
    return sorted(base.items(), key=lambda x: x[1], reverse=True)


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
    # 풀링된 포인트 추정치용 (기존 코드와 동일)
    es_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_fwd: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_mae: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    # Layer B: 날짜-블록 그룹 수집
    # es_groups[h] = list over dates of (scores_at_date, fwds_at_date)
    es_groups: dict[int, list[tuple[list[Decimal], list[Decimal]]]] = {
        h: [] for h in cfg.forward_horizons
    }
    # mae_groups[h] = list over dates of [maes_at_date]
    mae_groups: dict[int, list[list[Decimal]]] = {h: [] for h in cfg.forward_horizons}

    fs_vals: dict[str, dict[int, list[Decimal]]] = {
        f: {h: [] for h in cfg.forward_horizons} for f in _STUDY_FACTORS
    }
    fs_fwd: dict[str, dict[int, list[Decimal]]] = {
        f: {h: [] for h in cfg.forward_horizons} for f in _STUDY_FACTORS
    }

    for i, t in enumerate(dates):
        ranked = _score_at(panel, t, settings, cfg.preset)
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        # 날짜별 임시 버킷
        date_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        date_fwds: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        date_maes: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        for tk, sv in ranked:
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, tk, t, h)
                mae = _mae(panel, tk, t, h)
                if fr is not None and mae is not None:
                    es_scores[h].append(sv)
                    es_fwd[h].append(fr)
                    es_mae[h].append(mae)
                    date_scores[h].append(sv)
                    date_fwds[h].append(fr)
                    date_maes[h].append(mae)
            fund = panel.fundamentals_asof(tk, t)
            val = panel.valuation_asof(tk, t)
            factor_vals: dict[str, Decimal | None] = {
                "roe": fund.roe if fund else None,
                "op_margin": fund.op_margin if fund else None,
                "rev_growth": fund.rev_growth if fund else None,
                "per": val.per if val else None,
                "pbr": val.pbr if val else None,
            }
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, tk, t, h)
                if fr is None:
                    continue
                for fname, fval in factor_vals.items():
                    if fval is not None:
                        fs_vals[fname][h].append(fval)
                        fs_fwd[fname][h].append(fr)
        # 날짜 그룹 추가 (비어있으면 추가 안 함)
        for h in cfg.forward_horizons:
            if date_scores[h]:
                es_groups[h].append((date_scores[h], date_fwds[h]))
                mae_groups[h].append(date_maes[h])
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

    # stat_fn 헬퍼 — (score, fwd) 튜플 리스트 → pooled Spearman
    def _spearman_stat(records: list[tuple[Decimal, Decimal]]) -> Decimal:
        s = [r[0] for r in records]
        f = [r[1] for r in records]
        return metrics.spearman_monotonicity(s, f)

    # stat_fn 헬퍼 — Decimal 리스트 → 평균 MAE
    def _mean_stat(values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal("0")
        return sum(values, Decimal("0")) / Decimal(len(values))

    event_study: dict[int, EventStudyBucket] = {}
    for h in cfg.forward_horizons:
        # 포인트 추정치 — 기존과 동일한 풀링 연산, 수치 불변
        mono = metrics.spearman_monotonicity(es_scores[h], es_fwd[h])
        mae_mean = (
            sum(es_mae[h], Decimal("0")) / Decimal(len(es_mae[h])) if es_mae[h] else Decimal("0")
        )
        wr = metrics.win_rate(es_fwd[h])
        n = len(es_fwd[h])

        # Layer B: 유의성 계산
        # bootstrap 그룹: (score, fwd) 튜플 리스트 per date
        boot_groups = [list(zip(sc_, fw_, strict=True)) for sc_, fw_ in es_groups[h]]
        if boot_groups:
            mono_ci_lo, mono_ci_hi = metrics.block_bootstrap_ci(
                boot_groups,
                _spearman_stat,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
            mono_pvalue = metrics.permutation_pvalue(
                es_groups[h],
                observed=mono,
                n_perms=cfg.n_perms,
                seed=cfg.bootstrap_seed,
            )
            mae_ci_lo, mae_ci_hi = metrics.block_bootstrap_ci(
                mae_groups[h],
                _mean_stat,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
        else:
            mono_ci_lo = mono_ci_hi = Decimal("0")
            mono_pvalue = Decimal("1")
            mae_ci_lo = mae_ci_hi = Decimal("0")

        event_study[h] = EventStudyBucket(
            monotonicity=mono,
            mae=mae_mean,
            win_rate=wr,
            n=n,
            mono_ci_lo=mono_ci_lo,
            mono_ci_hi=mono_ci_hi,
            mono_pvalue=mono_pvalue,
            mae_ci_lo=mae_ci_lo,
            mae_ci_hi=mae_ci_hi,
        )
    factor_study = {
        fname: {
            h: EventStudyBucket(
                monotonicity=metrics.spearman_monotonicity(fs_vals[fname][h], fs_fwd[fname][h]),
                mae=Decimal("0"),
                win_rate=metrics.win_rate(fs_fwd[fname][h]),
                n=len(fs_fwd[fname][h]),
            )
            for h in cfg.forward_horizons
        }
        for fname in _STUDY_FACTORS
    }
    return BacktestResult(
        portfolio_nav=nav,
        benchmark_nav=benchmark_nav,
        rebalance_dates=dates,
        event_study=event_study,
        factor_study=factor_study,
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
