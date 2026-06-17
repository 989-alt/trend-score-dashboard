"""BacktestResult → 마크다운(사람) + JSON(기계). Decimal 은 문자열 직렬화."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.backtest.horserace import Leaderboard
from backend.backtest.metrics import annualized_volatility, cagr, max_drawdown
from backend.backtest.run import (
    BacktestConfig,
    BacktestResult,
    EventStudyBucket,
    WalkForwardConfig,
    WalkForwardResult,
)

_PERIODS_PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12}


def _summary(result: BacktestResult, cfg: BacktestConfig) -> dict[str, Any]:
    nav = result.portfolio_nav
    bench = result.benchmark_nav
    years = Decimal((cfg.end - cfg.start).days) / Decimal("365.25")
    if years <= 0:
        years = Decimal("1")
    periods = _PERIODS_PER_YEAR.get(cfg.rebalance, 12)
    period_returns = (
        [nav[i + 1] / nav[i] - Decimal("1") for i in range(len(nav) - 1)] if len(nav) > 1 else []
    )
    strat_cagr = cagr(nav[0], nav[-1], years=years) if len(nav) > 1 else Decimal("0")
    bench_cagr = cagr(bench[0], bench[-1], years=years) if len(bench) > 1 else Decimal("0")
    return {
        "final_nav": nav[-1],
        "total_return": nav[-1] - Decimal("1"),
        "cagr": strat_cagr,
        "volatility": annualized_volatility(period_returns, periods),
        "mdd": max_drawdown(nav),
        "benchmark_cagr": bench_cagr,
        "excess_cagr": strat_cagr - bench_cagr,
        "turnover_count": result.turnover_count,
    }


def _bucket_json(b: EventStudyBucket) -> dict[str, Any]:
    """EventStudyBucket → JSON-직렬화용 dict (render_json·render_walk_forward_json 공유)."""
    return {
        "monotonicity": str(b.monotonicity),
        "mono_ci_lo": str(b.mono_ci_lo),
        "mono_ci_hi": str(b.mono_ci_hi),
        "mono_pvalue": str(b.mono_pvalue),
        "mae": str(b.mae),
        "mae_ci_lo": str(b.mae_ci_lo),
        "mae_ci_hi": str(b.mae_ci_hi),
        "win_rate": str(b.win_rate),
        "n": b.n,
    }


def render_markdown(result: BacktestResult, cfg: BacktestConfig) -> str:
    s = _summary(result, cfg)
    lines = [
        f"# 백테스트 리포트 — preset={cfg.preset}",
        "",
        "## 가정",
        f"- 기간 {cfg.start}~{cfg.end} · 리밸런스 {cfg.rebalance} · 상위 {cfg.top_n} · 등가중 "
        f"· 비용 {cfg.cost_bps}bp/회전 · 무레버리지",
        f"- 벤치마크 ^KS11 · 리밸런스 {len(result.rebalance_dates)}회"
        f" · 회전 {result.turnover_count}",
        "",
        "## 포트폴리오 (vs ^KS11)",
        f"- 최종 NAV {s['final_nav']:.4f} · 누적 {s['total_return']:.4f} · CAGR {s['cagr']:.4f} "
        f"· 변동성 {s['volatility']:.4f} · MDD {s['mdd']:.4f}",
        f"- 벤치마크(^KS11) CAGR {s['benchmark_cagr']:.4f} · 초과 CAGR {s['excess_cagr']:.4f}",
        "",
        "## 이벤트스터디 (점수 vs forward-return)",
        "| 호라이즌 | 단조성(Spearman) | 단조성 95%CI | p-value | 평균 MAE | 승률 | N |",
        "|---|---|---|---|---|---|---|",
    ]
    for h, b in sorted(result.event_study.items()):
        ci_str = f"[{b.mono_ci_lo:.4f}, {b.mono_ci_hi:.4f}]"
        lines.append(
            f"| {h}일 | {b.monotonicity} | {ci_str}"
            f" | {b.mono_pvalue} | {b.mae:.4f} | {b.win_rate} | {b.n} |"
        )
    lines += [
        "",
        "## 후보 팩터 예측력 (forward-return 단조성)",
        "| 팩터 | 호라이즌 | 단조성 | 승률 | N |",
        "|---|---|---|---|---|",
    ]
    for fname, buckets in result.factor_study.items():
        for h, b in sorted(buckets.items()):
            lines.append(f"| {fname} | {h}일 | {b.monotonicity} | {b.win_rate} | {b.n} |")
    lines += ["", "> 수익 보장 없음. 룩어헤드 0(≤T 슬라이스)·생존편향 근사(상장구간) 가드 적용."]
    return "\n".join(lines)


def render_json(result: BacktestResult, cfg: BacktestConfig) -> dict[str, Any]:
    return {
        "config": {
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "rebalance": cfg.rebalance,
            "top_n": cfg.top_n,
            "cost_bps": str(cfg.cost_bps),
            "preset": cfg.preset,
        },
        "summary": {k: str(v) for k, v in _summary(result, cfg).items()},
        "event_study": {str(h): _bucket_json(b) for h, b in result.event_study.items()},
        "factor_study": {
            fname: {
                str(h): {"monotonicity": str(b.monotonicity), "win_rate": str(b.win_rate), "n": b.n}
                for h, b in buckets.items()
            }
            for fname, buckets in result.factor_study.items()
        },
    }


def _date_span(dates: list[Any]) -> str:
    """날짜 리스트 → 'YYYY-MM-DD~YYYY-MM-DD (N)' 또는 빈 표기."""
    if not dates:
        return "—"
    return f"{dates[0].isoformat()}~{dates[-1].isoformat()} ({len(dates)})"


def render_walk_forward_markdown(
    result: WalkForwardResult, cfg: BacktestConfig, wf: WalkForwardConfig
) -> str:
    """워크포워드 결과 → 마크다운. in-sample vs OOS(테스트) vs 홀드아웃 병렬 표시.

    호라이즌별로 단조성(+95%CI, p)과 평균 MAE(+CI)를 세 구간 나란히 보여 준다.
    OOS 가 in-sample 보다 약해지는 정도(과최적화 갭)를 한눈에 본다.
    """
    horizons = sorted(result.in_sample.keys())
    lines = [
        f"# 워크포워드 OOS 리포트 — preset={cfg.preset} · scheme={wf.scheme}",
        "",
        "## 설계",
        f"- 리밸런스 {cfg.rebalance} · 상위 {cfg.top_n} · 폴드 {wf.n_folds} "
        f"· 홀드아웃 {wf.holdout_frac}",
        f"- in-sample(전체): {_date_span(result.in_sample_dates)}",
        f"- OOS(테스트 폴드 합집합): {_date_span(result.oos_dates)}",
        f"- 홀드아웃(최종, 1회 소진): {_date_span(result.holdout_dates)}",
        "",
        "### 폴드 경계 (앵커드/확장형 — v1 미피팅)",
        "| 폴드 | train(prefix) | test |",
        "|---|---|---|",
    ]
    for i, (train, test) in enumerate(result.fold_ranges, start=1):
        lines.append(f"| {i} | {_date_span(train)} | {_date_span(test)} |")
    lines += [
        "",
        "## 단조성(Spearman) — in-sample vs OOS vs 홀드아웃",
        "| 호라이즌 | in-sample [95%CI] (p) | OOS [95%CI] (p) | 홀드아웃 [95%CI] (p) |",
        "|---|---|---|---|",
    ]

    def _mono_cell(b: EventStudyBucket) -> str:
        return f"{b.monotonicity} [{b.mono_ci_lo:.4f}, {b.mono_ci_hi:.4f}] (p={b.mono_pvalue})"

    for h in horizons:
        i_s = result.in_sample[h]
        oos = result.oos[h]
        ho = result.holdout[h]
        lines.append(f"| {h}일 | {_mono_cell(i_s)} | {_mono_cell(oos)} | {_mono_cell(ho)} |")
    lines += [
        "",
        "## 평균 MAE [95%CI] · 승률 · N — in-sample vs OOS vs 홀드아웃",
        "| 호라이즌 | in-sample MAE[CI] 승률 N | OOS MAE[CI] 승률 N | 홀드아웃 MAE[CI] 승률 N |",
        "|---|---|---|---|",
    ]

    def _mae_cell(b: EventStudyBucket) -> str:
        return f"{b.mae:.4f} [{b.mae_ci_lo:.4f}, {b.mae_ci_hi:.4f}] · {b.win_rate} · {b.n}"

    for h in horizons:
        i_s = result.in_sample[h]
        oos = result.oos[h]
        ho = result.holdout[h]
        lines.append(f"| {h}일 | {_mae_cell(i_s)} | {_mae_cell(oos)} | {_mae_cell(ho)} |")
    lines += [
        "",
        "> **수익 보장 없음.** 채택 판단은 in-sample 이 아니라 **OOS(테스트 폴드)** 지표로 한다 "
        "— in-sample 단조성은 과최적화로 부풀려질 수 있다. 홀드아웃은 최종 확인용 1회 소진."
        " v1 은 **미피팅**(train=관측 prefix 일 뿐, 파라미터 적합 없음) — 폴드 train 슬라이스는 "
        "T5(파라미터 피팅)에서 사용할 훅이다. 룩어헤드 0(≤T 슬라이스)·생존편향 근사(상장구간).",
    ]
    return "\n".join(lines)


def render_walk_forward_json(
    result: WalkForwardResult, cfg: BacktestConfig, wf: WalkForwardConfig
) -> dict[str, Any]:
    return {
        "config": {
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "rebalance": cfg.rebalance,
            "top_n": cfg.top_n,
            "preset": cfg.preset,
            "scheme": wf.scheme,
            "n_folds": wf.n_folds,
            "holdout_frac": str(wf.holdout_frac),
        },
        "ranges": {
            "in_sample": [d.isoformat() for d in result.in_sample_dates],
            "oos": [d.isoformat() for d in result.oos_dates],
            "holdout": [d.isoformat() for d in result.holdout_dates],
        },
        "folds": [
            {
                "fold": i,
                "train": [d.isoformat() for d in train],
                "test": [d.isoformat() for d in test],
                "event_study": {str(h): _bucket_json(b) for h, b in result.per_fold[i - 1].items()},
            }
            for i, (train, test) in enumerate(result.fold_ranges, start=1)
        ],
        "in_sample": {str(h): _bucket_json(b) for h, b in result.in_sample.items()},
        "oos": {str(h): _bucket_json(b) for h, b in result.oos.items()},
        "holdout": {str(h): _bucket_json(b) for h, b in result.holdout.items()},
    }


def _q4(v: Decimal) -> str:
    """표시용 4자리 양자화(원본 Decimal 불변)."""
    return f"{v.quantize(Decimal('0.0001'))}"


def render_horserace_markdown(lb: Leaderboard) -> str:
    """팩터 호스레이스 리더보드 → 마크다운(사람용).

    헤더 + 표(factor | mono | CI [lo, hi] | p | FDR | holdout | n | winner), lb 순서 1행/팩터.
    """
    lines = [
        "# 팩터 호스레이스 리더보드",
        "",
        f"- 호라이즌 {lb.horizon}일 · FDR q={lb.q}",
        "- winner = FDR 기각 AND OOS CI_lo>0 AND 홀드아웃 단조성>0",
        "  (유의한 양의 OOS 단조성 + 홀드아웃 재확인)",
        "",
        "| factor | mono | CI [lo, hi] | p | FDR | holdout | n | winner |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in lb.results:
        ci = f"[{_q4(r.ci_lo)}, {_q4(r.ci_hi)}]"
        fdr = "기각" if r.fdr_reject else "—"
        win = "winner" if r.winner else "—"
        lines.append(
            f"| {r.name} | {_q4(r.mono)} | {ci} | {_q4(r.pvalue)}"
            f" | {fdr} | {_q4(r.holdout_mono)} | {r.n} | {win} |"
        )
    lines += [
        "",
        "> **수익 보장 없음.** 룩어헤드 0(≤T 슬라이스)·생존편향 근사(상장구간). "
        "채택은 in-sample 이 아니라 OOS(테스트 폴드)·홀드아웃 재확인으로 한다.",
    ]
    return "\n".join(lines)


def render_horserace_json(lb: Leaderboard) -> dict[str, Any]:
    """팩터 호스레이스 리더보드 → JSON(기계용). Decimal 은 문자열."""
    return {
        "horizon": lb.horizon,
        "q": str(lb.q),
        "results": [
            {
                "name": r.name,
                "mono": str(r.mono),
                "ci_lo": str(r.ci_lo),
                "ci_hi": str(r.ci_hi),
                "pvalue": str(r.pvalue),
                "fdr_reject": r.fdr_reject,
                "holdout_mono": str(r.holdout_mono),
                "winner": r.winner,
                "n": r.n,
            }
            for r in lb.results
        ],
    }


__all__ = [
    "render_horserace_json",
    "render_horserace_markdown",
    "render_json",
    "render_markdown",
    "render_walk_forward_json",
    "render_walk_forward_markdown",
]
