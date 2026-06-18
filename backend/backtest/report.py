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


def render_fallback_c_markdown(
    layer1_rows: list[tuple[str, str, str]], layer2: dict[str, dict[str, str]]
) -> str:
    """폴백 C → 마크다운(사람용). 레이어1 진입품질 ΔMAE + 레이어2 오버레이 위험조정.

    레이어1: near_52w 후보별 ΔMAE(20일)·CI_lo (점수 리웨이트 효과).
    레이어2: 고정 프리셋(baseline)에 오버레이 컴포넌트를 누적 토글한 config별 MDD/Sharpe/Calmar
             (오버레이 자체의 증분 위험조정 효과 — 점수 불변으로 분리). 값은 이미 문자열.
    """
    lines = [
        "# 폴백 C 리포트 — 레이어1 진입품질 + 레이어2 리스크 오버레이",
        "",
        "## 레이어1 — 진입품질 (ΔMAE 20일)",
        "| near_52w(w) | ΔMAE | ΔMAE CI_lo |",
        "|---|---|---|",
    ]
    for w52, dmae, ci_lo in layer1_rows:
        lines.append(f"| {w52} | {dmae} | {ci_lo} |")
    lines += [
        "",
        "## 레이어2 — 리스크 오버레이 (위험조정)",
        "| config | MDD | Sharpe | Calmar |",
        "|---|---|---|---|",
    ]
    for config, m in layer2.items():
        lines.append(f"| {config} | {m['mdd']} | {m['sharpe']} | {m['calmar']} |")
    lines += [
        "",
        "> **수익 보장 없음 · 경로지표 약한 유의성.** 레이어1 은 점수 리웨이트(진입품질) 효과, "
        "레이어2 는 고정 프리셋(baseline)에 오버레이를 누적 토글해 오버레이 자체의 증분 위험조정을 "
        "분리한다. 룩어헤드 0(≤T 슬라이스)·생존편향 근사(상장구간).",
    ]
    return "\n".join(lines)


def render_fallback_c_json(
    layer1_rows: list[tuple[str, str, str]], layer2: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """폴백 C → JSON(기계용). 값은 이미 문자열(CLI 가 경계에서 str() 처리)."""
    return {
        "layer1": [
            {"w52": w52, "dmae": dmae, "dmae_ci_lo": ci_lo} for w52, dmae, ci_lo in layer1_rows
        ],
        "layer2": layer2,
    }


def render_news_riskoff_markdown(
    ablation: dict[str, dict[str, str]], coverage_rows: dict[str, Any]
) -> str:
    """뉴스 리스크오프 → 마크다운. 객관 트리거 액션 ablation(위험조정) + 위기 커버리지.

    ablation: config(baseline/+regime/+vix/+fx/조합)별 MDD/Sharpe/Calmar(이미 문자열).
    coverage_rows: 큐레이션 위기 대비 객관 트리거 catch/miss + 한국 고유(kr) 갭.
    """
    lines = [
        "# 뉴스 리스크오프 리포트 — 객관 트리거 액션(fail-fast) + 위기 커버리지",
        "",
        "## 액션 가치 — 리스크오프 ablation (위험조정)",
        "| config | MDD | Sharpe | Calmar |",
        "|---|---|---|---|",
    ]
    for name, m in ablation.items():
        lines.append(f"| {name} | {m['mdd']} | {m['sharpe']} | {m['calmar']} |")
    missed = coverage_rows.get("missed", [])
    lines += [
        "",
        "## 커버리지 — 큐레이션 위기 대비 객관 트리거(VIX∪환율)",
        f"- 전체: {coverage_rows.get('caught')}/{coverage_rows.get('total')} catch",
        f"- 한국 고유(kr): {coverage_rows.get('kr_caught')}/{coverage_rows.get('kr_total')} catch",
        f"- 놓친 위기: {', '.join(missed) if missed else '없음'}",
        "",
        "> 판정 게이트(§5): ① 글로벌 트리거가 KR 레짐 대비 MDD 를 *추가로* 유의/명확히 "
        "줄이고 ② 한국 고유 미스가 크면 → 뉴스 탐지기(b) 구축 정당화. ① 이 아니면 "
        "파이프라인 미구축(fail-fast).",
    ]
    return "\n".join(lines)


def render_news_riskoff_json(
    ablation: dict[str, dict[str, str]], coverage_rows: dict[str, Any]
) -> dict[str, Any]:
    """뉴스 리스크오프 → JSON(기계용)."""
    return {"ablation": ablation, "coverage": coverage_rows}


__all__ = [
    "render_fallback_c_json",
    "render_fallback_c_markdown",
    "render_horserace_json",
    "render_horserace_markdown",
    "render_json",
    "render_markdown",
    "render_news_riskoff_json",
    "render_news_riskoff_markdown",
    "render_walk_forward_json",
    "render_walk_forward_markdown",
]
