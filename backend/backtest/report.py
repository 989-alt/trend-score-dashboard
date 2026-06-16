"""BacktestResult → 마크다운(사람) + JSON(기계). Decimal 은 문자열 직렬화."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.backtest.metrics import cagr, max_drawdown
from backend.backtest.run import BacktestConfig, BacktestResult


def _summary(result: BacktestResult, cfg: BacktestConfig) -> dict[str, Any]:
    nav = result.portfolio_nav
    years = Decimal(max(len(result.rebalance_dates), 1)) / Decimal(
        "52" if cfg.rebalance == "weekly" else "12"
    )
    return {
        "final_nav": nav[-1],
        "total_return": nav[-1] - Decimal("1"),
        "cagr": cagr(nav[0], nav[-1], years=years) if len(nav) > 1 else Decimal("0"),
        "mdd": max_drawdown(nav),
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
        "## 포트폴리오",
        f"- 최종 NAV {s['final_nav']:.4f} · 누적 {s['total_return']:.4f} "
        f"· CAGR {s['cagr']:.4f} · MDD {s['mdd']:.4f}",
        "",
        "## 이벤트스터디 (점수 vs forward-return)",
        "| 호라이즌 | 단조성(Spearman) | 평균 MAE | 승률 | N |",
        "|---|---|---|---|---|",
    ]
    for h, b in sorted(result.event_study.items()):
        lines.append(f"| {h}일 | {b.monotonicity} | {b.mae:.4f} | {b.win_rate} | {b.n} |")
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
                "mae": str(b.mae),
                "win_rate": str(b.win_rate),
                "n": b.n,
            }
            for h, b in result.event_study.items()
        },
    }


__all__ = ["render_json", "render_markdown"]
