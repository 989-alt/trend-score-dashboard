"""BacktestResult → 마크다운(사람) + JSON(기계). Decimal 은 문자열 직렬화."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.backtest.metrics import annualized_volatility, cagr, max_drawdown
from backend.backtest.run import BacktestConfig, BacktestResult

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
        "event_study": {
            str(h): {
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
            for h, b in result.event_study.items()
        },
        "factor_study": {
            fname: {
                str(h): {"monotonicity": str(b.monotonicity), "win_rate": str(b.win_rate), "n": b.n}
                for h, b in buckets.items()
            }
            for fname, buckets in result.factor_study.items()
        },
    }


__all__ = ["render_json", "render_markdown"]
